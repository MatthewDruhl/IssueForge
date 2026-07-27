"""Committed PENDING acceptance suite for #140 (non-interactive ``issueforge run``): make a headless
``issueforge run ALIAS#N --scope ... --yes`` reach delivery without touching stdin, while the
interactive TTY gates stay EXACTLY as they are.

Today ``engine._poc_composed_stage`` has two human gates that read stdin (``_poc_scope_approver`` at
``engine.py:1096`` and ``_poc_approver`` at ``engine.py:1078``). Launched without a TTY both hit
``EOFError`` and auto-REJECT, so a real ``issueforge run DandD#111`` pauses at ``scope_rejected``
before any provider launches. ``cli.run`` takes no options at all and ``engine.run`` has no parameter
to carry an approved scope or a pre-approved contract — that is the plumbing gap this suite drives out.

The three behaviors pinned here (from the issue):

1. ``issueforge run DandD#111 --scope <paths> --yes`` runs end-to-end with NO stdin prompt and reaches
   ``waiting-for-merge`` (T1 through the real CLI, T4 through ``engine.run``'s headless API).
2. A NON-TTY run WITHOUT the flags FAILS LOUD naming ``--scope``/``--yes`` and never silently
   auto-rejects to ``scope_rejected`` (T2), including each HALF-flagged case: ``--scope`` without
   ``--yes`` (T6) and ``--yes`` without ``--scope`` (T7).
3. Interactive TTY behavior is UNCHANGED when no flags are passed: both prompts still happen, and — the
   part that matters — they still GATE. A rejection at EITHER prompt still stops the run with no
   delivery (T3 accepts, T3b rejects the scope, T3c rejects the contract), all driven through the REAL,
   UNPATCHED ``_poc_scope_approver``/``_poc_approver``.

Contract shapes this suite pins by name, per the decisions taken before authoring:

- ``engine._poc_composed_stage(record, *, approved_scope=None, auto_approve_contract=False)`` — EXPLICIT
  KEYWORD-ONLY parameters WITH DEFAULTS (T5), never new record keys, so the four committed suites that
  call ``stage(record)`` and monkeypatch the two approvers keep working with NO edit.
- ``engine.run(spec, *, approved_scope=..., auto_approve_contract=...)`` — the plumbing from the CLI (T4).
- ``cli._isatty()`` — a module-level ZERO-ARG callable seam so both TTY branches are testable. The
  fail-loud lives in the ``cli.run`` ENTRY POINT, not in ``engine``: ``sys.stdin.isatty()`` is False
  under Typer's ``CliRunner`` (proved by the green guard below), which every CLI test uses, so a hard
  ``sys.stdin.isatty()`` call inside the engine would make the interactive path untestable. T8 pins the
  seam's DEFAULT behavior in a real subprocess (pipe vs. ``pty``), so an implementation cannot satisfy
  the seam with a hardcoded ``lambda: True``.

Anti-gaming design. An implementation that AUTO-APPROVES everything (``--yes`` unconditional) fails T3
and T3b/T3c, which drive the real prompts and require a rejection to still stop the run, and fails T2,
which requires the loud exit. An implementation that ACCEPTS ``--scope`` but ignores it fails T1/T4: the
persisted ``write_scope`` must equal a PER-RUN UUID sentinel list, so a hardcoded constant, the issue's
stated files (faked to ``[]``), or a dropped value all miss. "No stdin prompt" is proven three ways:
POISONING ``builtins.input`` / ``typer.prompt`` / ``typer.confirm``, replacing the two approver seams
with spies that RAISE if called at all, and (where ``CliRunner`` does not own the stream) poisoning the
``sys.stdin`` object itself. ``click.prompt``/``click.confirm`` are NOT covered because this environment
has no ``click`` module at all (typer 0.26.8 vendors its own; ``import click`` raises
``ModuleNotFoundError``), so no implementation here can reach them; the approver spies close that hole
regardless of mechanism.

Deliberate NON-pins (kept loose so a correct implementation is not failed for style): the persisted
scope is compared ORDER-INSENSITIVELY, and no exit code is asserted on the interactive rejection paths.
The one CLI-syntax choice this suite does pin is that multiple paths are given as a REPEATED ``--scope``
option; that spelling has to be fixed somewhere for an acceptance test to invoke it at all, and it is
the spelling named in the issue.

Design (self-contained; the SAME locked external boundaries as the #129/#135 suites MINUS the two
approvers — patching those is exactly what hid this bug). The tests fake ONLY these four external
boundaries; everything else runs REAL, including both human gates, ``config.load_roles``, the git
worktrees, and the pytest baseline/acceptance runs:

1. ``issueforge.providers.invoke`` — fake Claude that writes the authored test / the implementation.
2. ``issueforge.github.GhWriteGateway`` — the recording gateway the composed stage constructs.
3. ``issueforge.github.issue_is_open`` — offline open-check.
4. ``issueforge.github.read_issue_body`` — returns ``files=[]`` (like the real DandD#111), so a persisted
   write scope can ONLY have come from the ``--scope`` flag or the human gate.

The real repo/origin harness (``_seed_dandd``) is REUSED from ``tests/test_poc_real_seams.py`` without
modifying it, the way ``tests/test_poc_composed_unit.py`` reuses ``tests/test_poc_integration.py``.

Because ``xfail(strict=True)`` also swallows SETUP errors, a broken harness would look green. The five
UNMARKED green guards at the top run the same harness end to end TODAY — a full delivery, both real
rejection paths, the non-tty ``CliRunner`` fact the ``_isatty`` seam exists for, and the poisoning /
spy machinery itself — so a harness regression fails loudly instead of hiding behind the xfails.

Every PENDING test fails TODAY at a meaningful OUTCOME assertion and carries the literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#140)")`` marker, so the suite is GREEN now and flips
to real green when #140 builds the wiring.
"""

from __future__ import annotations

import inspect
import json
import os
import pty
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from test_poc_real_seams import (
    CONTRACT_PATH,
    EXPECTED_SLUG,
    ISSUE_NUMBER,
    SPEC,
    WRITE_SCOPE_PATH,
    _make_fake_invoke,
    _make_gateway_class,
    _open_pr_count,
    _seed_dandd,
)

PENDING = "PENDING (#140)"

PROVIDER_NAME = "claudecli"
PROVIDER_EXECUTABLE = ["claude"]


# --------------------------------------------------------------------------- harness


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # click >= 8.2 always separates stderr
        return CliRunner()


def _cli_text(result) -> str:
    """Everything the CLI wrote, stdout and stderr, across click versions."""
    parts = [result.output or ""]
    try:
        parts.append(result.stderr or "")
    except ValueError:  # stderr not separately captured
        pass
    return "".join(parts)


def _sentinel_scope() -> list[str]:
    """A PER-RUN approved scope: the file the fake implementation edits, plus a unique allowed-but-
    unchanged path. A hardcoded or dropped scope cannot reproduce the uuid component."""
    return [WRITE_SCOPE_PATH, f"src/dandd/allowed_{uuid.uuid4().hex}.py"]


def _install_headless_seams(monkeypatch, *, impl_mode: str = "fix") -> SimpleNamespace:
    """Fake ONLY the four external boundaries. Deliberately does NOT patch ``_poc_approver`` or
    ``_poc_scope_approver``: the real gates must run, since patching them is what hid this gap."""
    from issueforge import github, providers

    providers_toml = Path(tempfile.mkdtemp()) / "providers.toml"
    providers_toml.write_text(
        f"[providers.{PROVIDER_NAME}]\n"
        f"executable = {PROVIDER_EXECUTABLE!r}\n"
        'start = ["-p", "{prompt}"]\n'
        'resume = ["-p", "{prompt}", "--resume", "{session}"]\n'
        'auth = ["auth", "status"]\n'
        "\n[roles]\n"
        f'primary = "{PROVIDER_NAME}"\n'
    )
    monkeypatch.setenv("ISSUEFORGE_PROVIDERS", str(providers_toml))

    seq: list = []
    invoker = _make_fake_invoke(seq, impl_mode=impl_mode)
    gateways: list = []

    def fake_issue_open(slug, number, **_kw):
        return True

    def fake_read_issue_body(slug, number, **_kw):
        return {
            "body": f"{EXPECTED_SLUG}#{ISSUE_NUMBER} GREET: greet(name) must return 'hi <name>'",
            "files": [],
            "contract_paths": [CONTRACT_PATH],
        }

    monkeypatch.setattr(providers, "invoke", invoker.invoke)
    monkeypatch.setattr(github, "GhWriteGateway", _make_gateway_class(gateways))
    monkeypatch.setattr(github, "issue_is_open", fake_issue_open)
    monkeypatch.setattr(github, "read_issue_body", fake_read_issue_body, raising=False)

    return SimpleNamespace(seq=seq, invoker=invoker, gateways=gateways)


def _poison_stdin_prompts(monkeypatch) -> list:
    """Make ANY interactive prompt loud: record it and raise. Returns the recorded prompt list.

    ``click.prompt``/``click.confirm`` are not patched because ``click`` is not installed in this
    environment at all (typer 0.26.8 vendors its own); ``_forbid_approvers`` covers any mechanism."""
    prompts: list = []

    def _prompt(prompt: str = "", *_args, **_kwargs):
        prompts.append(prompt)
        raise AssertionError(f"headless run prompted on stdin: {prompt!r}")

    monkeypatch.setattr("builtins.input", _prompt)
    # Cover the other way a prompt could reach the operator (typer's own helpers), so "no stdin
    # prompt" is proven for the mechanism the impl actually chooses, not just for ``input()``.
    monkeypatch.setattr(typer, "prompt", _prompt, raising=False)
    monkeypatch.setattr(typer, "confirm", _prompt, raising=False)
    return prompts


def _poison_stdin_stream(monkeypatch) -> list:
    """Replace ``sys.stdin`` itself, so a raw ``sys.stdin.readline()`` is caught too. Only usable where
    ``CliRunner`` does not install its own stdin for the duration of the call."""
    reads: list = []

    class _Poisoned:
        def isatty(self) -> bool:
            return False

        def _boom(self, *_args, **_kwargs):
            reads.append("read")
            raise AssertionError("headless run read sys.stdin directly")

        read = readline = readlines = _boom
        __iter__ = _boom
        __next__ = _boom

    monkeypatch.setattr(sys, "stdin", _Poisoned())
    return reads


def _forbid_approvers(monkeypatch) -> list:
    """Replace BOTH human gates with spies that RAISE. Mechanism-independent proof that a headless run
    never consults a human: it does not matter how the gate would have read stdin."""
    from issueforge import engine

    calls: list = []

    def _scope_spy(stated_files=None, *_args, **_kwargs):
        calls.append("scope")
        raise AssertionError("headless run consulted the human scope gate")

    def _contract_spy(review=None, *_args, **_kwargs):
        calls.append("contract")
        raise AssertionError("headless run consulted the human contract gate")

    monkeypatch.setattr(engine, "_poc_scope_approver", _scope_spy, raising=False)
    monkeypatch.setattr(engine, "_poc_approver", _contract_spy, raising=False)
    return calls


def _answer_stdin_prompts(monkeypatch, answers: list[str]) -> list:
    """Drive the REAL interactive gates: answer each ``input()`` in order, recording every prompt."""
    prompts: list = []

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        index = len(prompts) - 1
        return answers[index] if index < len(answers) else ""

    monkeypatch.setattr("builtins.input", _input)
    return prompts


def _pin_non_tty(monkeypatch) -> None:
    """Pin the non-TTY branch. ``CliRunner``'s stdin is already not a tty, so this is belt-and-braces
    and does not force the seam to exist — the non-TTY tests assert on OUTCOMES, not on the seam."""
    from issueforge import cli

    monkeypatch.setattr(cli, "_isatty", lambda: False, raising=False)


def _pin_tty(monkeypatch) -> None:
    """Pin the TTY branch. Here the seam is load-bearing: ``sys.stdin.isatty()`` is False under
    ``CliRunner``, so without an injectable seam the interactive path is untestable."""
    from issueforge import cli

    assert callable(getattr(cli, "_isatty", None)), (
        "cli must expose a module-level zero-arg _isatty() seam (default sys.stdin.isatty) so both "
        "the TTY and non-TTY branches are testable under CliRunner"
    )
    monkeypatch.setattr(cli, "_isatty", lambda: True)


def _records() -> list[dict]:
    """Every persisted run manifest under this test's isolated state root."""
    from issueforge import paths

    runs = Path(paths.state_root()).resolve() / "runs"
    return [json.loads(p.read_text()) for p in sorted(runs.glob("*/manifest.json"))]


def _sole_record() -> dict:
    records = _records()
    assert len(records) == 1, f"expected exactly one persisted run, got {len(records)}"
    return records[0]


# ==================================================================== green harness guards
#
# UNMARKED and green TODAY. ``xfail(strict=True)`` swallows setup errors, so without these a broken
# fixture would present as a healthy PENDING suite. These prove the harness itself really drives the
# system under test, and they double as the regression floor for behavior #140 must NOT change.


@pytest.mark.slow
def test_green_guard_harness_reaches_delivery_today(tmp_path, monkeypatch):
    """The harness in this file can drive a whole run to delivery TODAY, through the REAL human gates.

    technical: seeded repo + the four faked boundaries + the real approvers answered over
    ``builtins.input`` reach ``waiting-for-merge`` with the per-run sentinel scope persisted, exactly
    two prompts read, both provider phases invoked, and one PR opened. If any of that stops being
    true, the PENDING tests below are testing nothing and this guard says so."""
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    scope = _sentinel_scope()
    prompts = _answer_stdin_prompts(monkeypatch, [" ".join(scope), "y"])

    from issueforge import engine

    engine.run(SPEC)

    record = _sole_record()
    assert record["status"] == "waiting-for-merge", record
    assert sorted(record.get("write_scope") or []) == sorted(scope)
    assert len(prompts) == 2, prompts
    assert [call["phase"] for call in handles.invoker.calls] == ["author", "impl"]
    assert _open_pr_count(handles.gateways) == 1


def test_green_guard_scope_rejection_stops_the_run_today(tmp_path, monkeypatch):
    """Rejecting the scope gate stops the run before anything happens — the floor T3b must preserve.

    technical: an empty answer at the first prompt pauses with
    ``"scope_rejected (pre-authoring scope gate)"``, invokes no provider, and opens no PR."""
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    prompts = _answer_stdin_prompts(monkeypatch, [""])

    from issueforge import engine

    engine.run(SPEC)

    record = _sole_record()
    assert record["status"] == "paused", record
    assert "scope" in str(record.get("pause_reason", "")).lower(), record
    assert len(prompts) == 1, prompts
    assert handles.invoker.calls == []
    assert _open_pr_count(handles.gateways) == 0


@pytest.mark.slow
def test_green_guard_contract_rejection_stops_the_run_today(tmp_path, monkeypatch):
    """Rejecting the authored contract stops the run after authoring — the floor T3c must preserve.

    technical: answering the scope prompt then ``"n"`` pauses after the ``author`` phase only, with no
    ``impl`` phase and no PR."""
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    scope = _sentinel_scope()
    prompts = _answer_stdin_prompts(monkeypatch, [" ".join(scope), "n"])

    from issueforge import engine

    engine.run(SPEC)

    record = _sole_record()
    assert record["status"] == "paused", record
    assert len(prompts) == 2, prompts
    assert [call["phase"] for call in handles.invoker.calls] == ["author"]
    assert _open_pr_count(handles.gateways) == 0


def test_green_guard_cli_runner_stdin_is_not_a_tty():
    """The reason ``cli._isatty()`` has to be an injectable seam: under ``CliRunner`` — which every CLI
    test uses — ``sys.stdin.isatty()`` is False, so a direct call would make the TTY branch untestable.

    technical: a throwaway Typer command invoked through ``_runner()`` records
    ``sys.stdin.isatty() is False``."""
    seen: list[bool] = []
    probe = typer.Typer()

    @probe.command()
    def check() -> None:
        seen.append(sys.stdin.isatty())

    result = _runner().invoke(probe, [])

    assert result.exit_code == 0, _cli_text(result)
    assert seen == [False], seen


def test_green_guard_prompt_poisoning_and_approver_spies_are_wired(monkeypatch):
    """The anti-gaming machinery really fires. Otherwise "no prompt was recorded" would be vacuous.

    technical: after ``_poison_stdin_prompts`` every prompt mechanism raises and is recorded; after
    ``_poison_stdin_stream`` a raw ``sys.stdin.readline()`` raises; after ``_forbid_approvers`` calling
    either approver seam raises."""
    prompts = _poison_stdin_prompts(monkeypatch)
    for call in (
        lambda: input("in? "),
        lambda: typer.prompt("tp? "),
        lambda: typer.confirm("tc? "),
    ):
        with pytest.raises(AssertionError):
            call()
    assert prompts == ["in? ", "tp? ", "tc? "]

    reads = _poison_stdin_stream(monkeypatch)
    with pytest.raises(AssertionError):
        sys.stdin.readline()
    assert reads == ["read"]

    calls = _forbid_approvers(monkeypatch)
    from issueforge import engine

    with pytest.raises(AssertionError):
        engine._poc_scope_approver([])
    with pytest.raises(AssertionError):
        engine._poc_approver(None)
    assert calls == ["scope", "contract"]


# =========================================================================== the suite


@pytest.mark.slow
def test_headless_cli_run_with_scope_and_yes_delivers_without_any_stdin_prompt(
    tmp_path, monkeypatch
):
    """Running the CLI with an approved scope and a pre-approved contract finishes the whole job on a
    machine with no keyboard attached: nothing is ever asked, the files the operator listed on the
    command line are the ones the run is allowed to touch, and the run ends ready for a human to merge.

    technical (contract): with ``cli._isatty()`` pinned False, stdin prompting POISONED and BOTH human
    gates replaced by raising spies, ``run DandD#111 --scope <WRITE_SCOPE_PATH> --scope <per-run uuid
    path> --yes`` exits 0; exactly one run is persisted with ``status == "waiting-for-merge"`` and a
    ``write_scope`` equal (as a set) to that per-run list; no prompt and no gate call was recorded; and
    ``providers.invoke`` ran both phases (``["author", "impl"]``). An impl that ignores ``--scope``
    persists ``[]`` (``read_issue_body`` returns ``files=[]``), making the edited ``greet.py`` an
    out-of-scope offender -> ``paused``; an impl that still asks a human trips a spy or a poison.
    """
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    scope = _sentinel_scope()
    _pin_non_tty(monkeypatch)
    prompts = _poison_stdin_prompts(monkeypatch)
    gate_calls = _forbid_approvers(monkeypatch)

    from issueforge.cli import app

    result = _runner().invoke(app, ["run", SPEC, "--scope", scope[0], "--scope", scope[1], "--yes"])

    assert result.exit_code == 0, _cli_text(result)
    assert prompts == [], "a headless run must never read stdin"
    assert gate_calls == [], "a headless run must never consult a human gate"
    record = _sole_record()
    assert record["status"] == "waiting-for-merge"
    assert sorted(record.get("write_scope") or []) == sorted(scope)
    assert [call["phase"] for call in handles.invoker.calls] == ["author", "impl"]


def test_non_tty_run_without_flags_fails_loud_instead_of_silently_rejecting(tmp_path, monkeypatch):
    """Started without a keyboard and without the new flags, the command stops immediately and says
    what to pass, instead of quietly pretending the human said "no" and parking the issue.

    technical (contract): with ``cli._isatty()`` pinned False and NO flags, ``run DandD#111`` exits
    NON-ZERO; its combined stdout+stderr names both ``--scope`` and ``--yes``; NO run manifest is
    persisted at all (the fail-loud is in the ``cli.run`` entry point, before admission), and in
    particular no persisted run is ``paused`` with a scope-rejection reason; and ``providers.invoke``
    was never called. Today this run exits 0 and persists ``paused`` /
    ``"scope_rejected (pre-authoring scope gate)"``, which is precisely the silent failure #140 forbids.
    """
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    _pin_non_tty(monkeypatch)

    from issueforge.cli import app

    result = _runner().invoke(app, ["run", SPEC])
    text = _cli_text(result)

    assert result.exit_code != 0, text
    assert "--scope" in text and "--yes" in text, text
    records = _records()
    assert not [r for r in records if "scope" in str(r.get("pause_reason", "")).lower()], (
        "a non-TTY run without the flags silently auto-rejected the scope gate"
    )
    assert records == [], "the fail-loud must happen before any run is admitted"
    assert handles.invoker.calls == []


@pytest.mark.slow
def test_interactive_tty_run_without_flags_still_prompts_and_gates(tmp_path, monkeypatch):
    """Run from a real terminal with no flags, nothing changes: IssueForge still asks which files it
    may write and still asks the human to approve the authored contract, and the answers typed at those
    two prompts are what the run actually uses.

    technical (contract): with ``cli._isatty()`` pinned True, NO flags, and the REAL (UNPATCHED)
    ``engine._poc_scope_approver``/``engine._poc_approver`` running, ``run DandD#111`` with stdin
    answering ``"<WRITE_SCOPE_PATH> <per-run uuid path>"`` then ``"y"`` exits 0; EXACTLY TWO prompts are
    read (the count is unchanged from today's behavior); the persisted ``write_scope`` matches the
    two-element list the HUMAN typed; and ``status == "waiting-for-merge"``. An impl that makes
    ``--yes``/auto-approval unconditional reads fewer than two prompts; an impl that ignores the typed
    answer misses the per-run uuid path.
    """
    _seed_dandd(tmp_path)
    _install_headless_seams(monkeypatch)
    scope = _sentinel_scope()
    _pin_tty(monkeypatch)
    prompts = _answer_stdin_prompts(monkeypatch, [" ".join(scope), "y"])

    from issueforge.cli import app

    result = _runner().invoke(app, ["run", SPEC])

    assert result.exit_code == 0, _cli_text(result)
    assert len(prompts) == 2, f"interactive gates changed: prompts={prompts!r}"
    record = _sole_record()
    assert sorted(record.get("write_scope") or []) == sorted(scope)
    assert record["status"] == "waiting-for-merge"


def test_interactive_tty_scope_rejection_still_stops_the_run(tmp_path, monkeypatch):
    """Saying no at the "which files may I write?" question still stops the run dead: no AI is
    launched, nothing is written, no pull request appears.

    technical (contract): with ``cli._isatty()`` pinned True, NO flags, the REAL gates, and an EMPTY
    answer at the first prompt, the persisted run is ``paused`` with a scope-rejection reason,
    ``providers.invoke`` was never called, and ZERO PRs were opened. Exit code is deliberately NOT
    pinned. An impl that treats the new headless path as the default — or that auto-approves when
    stdin looks unhelpful — delivers here instead of pausing.
    """
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    _pin_tty(monkeypatch)
    prompts = _answer_stdin_prompts(monkeypatch, [""])

    from issueforge.cli import app

    _runner().invoke(app, ["run", SPEC])

    assert len(prompts) == 1, prompts
    record = _sole_record()
    assert record["status"] == "paused", record
    assert "scope" in str(record.get("pause_reason", "")).lower(), record
    assert handles.invoker.calls == []
    assert _open_pr_count(handles.gateways) == 0


@pytest.mark.slow
def test_interactive_tty_contract_rejection_still_stops_the_run(tmp_path, monkeypatch):
    """Saying no to the authored contract still stops the run: the test that was written is not
    implemented, and no pull request appears.

    technical (contract): with ``cli._isatty()`` pinned True, NO flags, the REAL gates, an approved
    scope at the first prompt and ``"n"`` at the second, the persisted run is ``paused``,
    ``providers.invoke`` ran the ``author`` phase ONLY (no ``impl``), and ZERO PRs were opened.
    ``pause_reason`` is deliberately NOT pinned (today this path leaves it unset). An impl that lets
    ``auto_approve_contract`` default to True, or that ignores the rejection once a scope is approved,
    reaches ``impl`` and delivers.
    """
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    scope = _sentinel_scope()
    _pin_tty(monkeypatch)
    prompts = _answer_stdin_prompts(monkeypatch, [" ".join(scope), "n"])

    from issueforge.cli import app

    _runner().invoke(app, ["run", SPEC])

    assert len(prompts) == 2, prompts
    record = _sole_record()
    assert record["status"] == "paused", record
    assert [call["phase"] for call in handles.invoker.calls] == ["author"]
    assert _open_pr_count(handles.gateways) == 0


@pytest.mark.slow
def test_engine_run_carries_the_approved_scope_and_contract_approval(tmp_path, monkeypatch):
    """The headless answers are carried by the engine itself, not just handled in the command-line
    layer, so any caller (the CLI, a worker, a future daemon) can drive a run with no keyboard.

    technical (contract): ``engine.run("DandD#111", approved_scope=<per-run two-element list>,
    auto_approve_contract=True)`` with stdin prompting POISONED, ``sys.stdin`` itself poisoned and both
    gates spied returns a record whose ``status`` is ``"waiting-for-merge"`` and whose persisted
    ``write_scope`` matches that list, with ZERO prompts, ZERO raw stdin reads and ZERO gate calls
    recorded. Today ``engine.run`` has no such parameters (TypeError -> explicit failure).
    """
    _seed_dandd(tmp_path)
    _install_headless_seams(monkeypatch)
    scope = _sentinel_scope()
    prompts = _poison_stdin_prompts(monkeypatch)
    reads = _poison_stdin_stream(monkeypatch)
    gate_calls = _forbid_approvers(monkeypatch)

    from issueforge import engine, store

    try:
        result = engine.run(SPEC, approved_scope=list(scope), auto_approve_contract=True)
    except TypeError as exc:
        pytest.fail(f"engine.run must accept approved_scope/auto_approve_contract: {exc}")

    record = store.RunStore().read(result["run_id"])
    assert prompts == [], "a headless engine.run must never read stdin"
    assert reads == [], "a headless engine.run must never read sys.stdin directly"
    assert gate_calls == [], "a headless engine.run must never consult a human gate"
    assert record["status"] == "waiting-for-merge"
    assert sorted(record.get("write_scope") or []) == sorted(scope)


def test_composed_stage_takes_headless_answers_as_optional_keyword_parameters():
    """The headless answers are passed to the run stage as ordinary optional settings, so every existing
    caller that just says "run this record" keeps working exactly as before.

    technical (contract): ``inspect.signature(engine._poc_composed_stage)`` has parameters
    ``approved_scope`` and ``auto_approve_contract``, BOTH ``KEYWORD_ONLY``, with defaults ``None`` and
    ``False`` respectively, and the signature still binds a single positional ``record``
    (``sig.bind({"run_id": "r"})``) — so the four committed suites that call ``stage(record)`` and
    monkeypatch the approvers need NO edit and cannot trip the acceptance weaken-guard. Threading the
    answers through new ``record`` keys instead would leave these parameters absent.
    """
    from issueforge import engine

    sig = inspect.signature(engine._poc_composed_stage)
    params = sig.parameters

    for name, default in (("approved_scope", None), ("auto_approve_contract", False)):
        assert name in params, f"_poc_composed_stage must take an explicit {name} parameter"
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is default, name

    sig.bind({"run_id": "r"})  # legacy stage(record) call sites keep binding


def test_non_tty_scope_without_yes_still_fails_loud_at_the_contract_gate(tmp_path, monkeypatch):
    """Supplying only the file list is not enough: the contract-approval question is still unanswered,
    so a keyboard-less run stops and says so rather than approving the contract on the human's behalf.

    technical (contract): with ``cli._isatty()`` pinned False, ``run DandD#111 --scope
    <WRITE_SCOPE_PATH>`` (no ``--yes``) exits NON-ZERO, names ``--yes`` in its combined output, persists
    NO run manifest, and never calls ``providers.invoke``. An impl that treats a supplied ``--scope`` as
    blanket consent auto-approves the authored contract and reaches ``waiting-for-merge`` instead.
    """
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    _pin_non_tty(monkeypatch)

    from issueforge.cli import app

    result = _runner().invoke(app, ["run", SPEC, "--scope", WRITE_SCOPE_PATH])
    text = _cli_text(result)

    assert result.exit_code != 0, text
    assert "--yes" in text, text
    assert _records() == [], "the fail-loud must happen before any run is admitted"
    assert handles.invoker.calls == []


def test_non_tty_yes_without_scope_still_fails_loud_before_admission(tmp_path, monkeypatch):
    """The mirror case: pre-approving the contract without saying which files may be written is not
    enough either. IssueForge never invents a write scope for itself.

    technical (contract): with ``cli._isatty()`` pinned False, ``run DandD#111 --yes`` (no ``--scope``)
    exits NON-ZERO, names ``--scope`` in its combined output, persists NO run manifest, and never calls
    ``providers.invoke``. An impl that defaults the missing scope to the issue's stated files (faked to
    ``[]``) or to "everything" would run anyway; an impl that falls back to the stdin gate would pause
    at ``scope_rejected`` — both are the silent failure this issue exists to remove.
    """
    _seed_dandd(tmp_path)
    handles = _install_headless_seams(monkeypatch)
    _pin_non_tty(monkeypatch)

    from issueforge.cli import app

    result = _runner().invoke(app, ["run", SPEC, "--yes"])
    text = _cli_text(result)

    assert result.exit_code != 0, text
    assert "--scope" in text, text
    assert _records() == [], "the fail-loud must happen before any run is admitted"
    assert handles.invoker.calls == []


def test_isatty_seam_reports_the_real_stdin_of_the_process_by_default():
    """The "is a human watching?" check has to tell the truth by default. If it were hardcoded, every
    real headless run would either hang on a prompt or bypass the human gates.

    technical (contract): in a FRESH subprocess (no monkeypatching), ``cli._isatty()`` prints ``False``
    when stdin is a pipe/``/dev/null`` and ``True`` when stdin is a real ``pty`` slave. This is what the
    other tests' injected seam stands in for; an implementation that satisfies them with
    ``_isatty = lambda: True`` (or ``lambda: False``) fails here. Today ``cli`` has no ``_isatty`` at
    all, so the subprocess exits non-zero.
    """
    code = "from issueforge import cli; print(cli._isatty())"
    argv = [sys.executable, "-c", code]

    piped = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert piped.returncode == 0, f"cli._isatty() is not importable/callable: {piped.stderr}"
    assert piped.stdout.strip() == "False", piped.stdout

    master, slave = pty.openpty()
    try:
        on_tty = subprocess.run(argv, stdin=slave, capture_output=True, text=True)
    finally:
        os.close(slave)
        os.close(master)

    assert on_tty.returncode == 0, on_tty.stderr
    assert on_tty.stdout.strip() == "True", (
        f"cli._isatty() must reflect the process's real stdin, got {on_tty.stdout!r}"
    )
