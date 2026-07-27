"""PENDING acceptance suite for #142 — the wedged worker slot.

The bug: a run REJECTED at the pre-authoring scope gate (``engine._poc_scope_approver`` returning
None) calls ``engine._poc_pause`` today, which persists ``paused`` and leaves ``queue.active`` set.
``_finalize`` then sees a non-running status and does nothing, so the worker slot is held FOREVER by
a run that produced no candidate and cannot be resumed — every future run wedges behind it.

The #142 dispatch decisions (Matt, 2026-07-26), binding contract:

1. **Scope-gate rejection RELEASES the slot.** A run rejected at the pre-authoring scope gate
   produced no candidate and is not resumable: it transitions to a TERMINAL state, frees the worker
   slot, and auto-advances the queue. Other (resumable) pauses — a missing provider config,
   ``baseline_not_green``, a rejected contract — KEEP the slot exactly as today.
2. **Drain dispatches the COMPOSED stage for a promoted never-started queued run.** A queued run
   promoted to the freed slot goes through the same composed PoC default stage a fresh
   ``engine.run`` dispatch uses — NOT the no-op stub ``engine._default_stage`` it runs today.
   Resuming a PARTIALLY-executed run stays deferred (#124); drain handles only fresh promotions.
3. **The 'queued behind active run <id>' feedback is ENGINE-side.** ``engine.run`` emits it when a
   run lands queued; ``cli.py`` is untouched (keeps #142 file-disjoint from #152 in the same wave).

RED-today PENDING tests carry EXACTLY ``@pytest.mark.xfail(strict=True, reason="PENDING (#142)")``.
The unmarked tests are GREEN GUARDS: behavior true TODAY that #142 must not break (every non-scope
composed pause keeps the slot; cancel still frees the slot). Pending surfaces are imported INSIDE
each test so a missing attribute cannot turn the whole file into a collection ERROR. Every state read
uses the real ``store.RunStore().read(...)`` / ``store.RunStore().read_queue()`` API (via the helpers
below). Dual-layer docstrings: plain behavior first, then the ``technical (contract):`` golden values.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

ISSUE_1 = 148
ISSUE_2 = 149
STATED_FILES = ["src/x.py"]  # non-empty, so a scope rejection is the gate's None, not empty files

# An operator-level providers config (#135) valid enough for ``_config.load_roles`` to resolve a
# primary role, so a composed pause AFTER provider-config resolution can be reached.
_PROVIDERS_CONFIG = (
    "[providers.claudecli]\n"
    'executable = ["claude"]\n'
    'start = ["-p", "{prompt}"]\n'
    'resume = ["-p", "{prompt}", "--resume", "{session}"]\n'
    'auth = ["auth", "status"]\n'
    "\n"
    "[roles]\n"
    'primary = "claudecli"\n'
)


# --- helpers ---------------------------------------------------------------


def _register(make_git_repo, alias="DandD"):
    from typer.testing import CliRunner

    from issueforge.cli import app

    result = CliRunner().invoke(app, ["repo", "add", f"{alias}:{make_git_repo()}"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)


def _queue():
    from issueforge import store

    path = store.queue_path()
    if not path.exists():
        return {"active": None, "queue": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _status(run_id):
    from issueforge import store

    return store.RunStore().read(run_id)["status"]


def _slug(run_id):
    from issueforge import store

    return store.RunStore().read(run_id)["slug"]


def _transitions(run_id):
    from issueforge import store

    return [e["transition"] for e in store.RunStore().replay_events(run_id)]


def _is_subsequence(sub, seq):
    """True iff every element of ``sub`` appears in ``seq`` in order (extra events allowed between)."""
    it = iter(seq)
    return all(item in it for item in sub)


def _admit_paused(make_git_repo, run_id, number=ISSUE_1, alias="DandD"):
    """Admit an issue and leave its run PAUSED in the worker slot (holds the slot, no advance).

    The pausing stage is a bare ``engine.pause``, NOT the composed stage — so admitting run-1 this
    way never itself reads the issue body, touches the scope gate, or invokes the composed callable.
    """
    from issueforge import engine

    return engine.run(
        f"{alias}#{number}",
        issue_open=lambda s, n: True,
        new_run_id=lambda: run_id,
        stage=lambda record: engine.pause(record["run_id"]),
    )


def _enqueue(run_id, number, alias="DandD"):
    """Admit an issue while the slot is held; it LANDS queued (its stage never runs at admission)."""
    from issueforge import engine

    return engine.run(f"{alias}#{number}", issue_open=lambda s, n: True, new_run_id=lambda: run_id)


def _install_scope_seams(monkeypatch, *, scope_answer, files=None, body="issue body"):
    """Fake the two seams the composed stage hits BEFORE any fetch/worktree/baseline, and RECORD both
    the full ``(slug, number)`` of every ``read_issue_body`` call and the ``stated_files`` of every
    ``_poc_scope_approver`` call.

    ``read_issue_body`` is offline (never shells ``gh``) and returns NON-EMPTY stated files by default,
    so a scope rejection is provably the gate's ``None`` answer, not an empty-stated-files shortcut.
    ``_poc_scope_approver`` returns ``scope_answer`` verbatim — ``None`` REJECTS, a file list APPROVES.
    Returns ``SimpleNamespace(read_calls, scope_calls)``.
    """
    from issueforge import engine, github

    read_calls: list[tuple[str, int]] = []
    scope_calls: list[list[str]] = []
    stated = list(files if files is not None else STATED_FILES)

    def fake_read_issue_body(slug, number, **_kw):
        read_calls.append((slug, number))
        return {"body": body, "files": list(stated), "contract_paths": []}

    def fake_scope_approver(stated_files):
        scope_calls.append(list(stated_files))
        return scope_answer

    monkeypatch.setattr(github, "read_issue_body", fake_read_issue_body, raising=False)
    monkeypatch.setattr(engine, "_poc_scope_approver", fake_scope_approver, raising=False)
    return SimpleNamespace(read_calls=read_calls, scope_calls=scope_calls)


def _spy_on_composed_stage(monkeypatch):
    """Replace ``engine._poc_composed_stage`` with a wrapper that RECORDS each dispatched run_id then
    delegates to the REAL composed stage. Proves the exact composed callable was invoked (not merely
    that a side effect resembling it occurred). Returns the recording list."""
    from issueforge import engine

    dispatched: list[str] = []
    real = engine._poc_composed_stage

    def spy(record, *args, **kwargs):
        dispatched.append(record["run_id"])
        return real(record, *args, **kwargs)

    monkeypatch.setattr(engine, "_poc_composed_stage", spy)
    return dispatched


# ---------------------------------------------------------------------------
# Decision 1 — scope-gate rejection is terminal, frees the slot, auto-advances
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#142)")
def test_scope_rejection_is_terminal_frees_the_slot_and_auto_advances(
    make_git_repo, isolated_state_home, monkeypatch
):
    """A run rejected at the pre-authoring scope gate does not sit paused holding the worker slot: it
    ends terminal, the slot is released, and a run queued behind it is promoted and run — with the
    queue left un-wedged afterward.

    technical (contract): run-1 is admitted paused (holds the slot) with run-2 queued behind it; the
    scope gate is set to reject (``_poc_scope_approver`` -> None) while ``read_issue_body`` returns a
    NON-EMPTY stated-files list. ``engine.continue_run("run-1", stage=engine._poc_composed_stage)``
    drives run-1 through the real composed stage. Afterward: ``read("run-1")["status"]`` is a TERMINAL
    state (``State(status) in state.TERMINAL``) and specifically ``"failed"`` (the only
    ``running -> terminal`` edge that is not a semantic lie: ``running -> cancelled`` is not a declared
    edge, and ``completed`` / ``waiting-for-merge`` would falsely claim success — see summary); the
    scope gate WAS consulted with the real stated files (``scope_calls[0] == ["src/x.py"]``), proving
    the rejection came THROUGH the gate; the queued run-2 was auto-advanced and ran to a terminal state
    (no longer in the FIFO); ``read_queue() == {"active": None, "queue": []}`` (no re-wedge); and
    run-1's lifecycle transitions CONTAIN the subsequence ``["queued","running","failed"]``. TODAY the
    run persists ``"paused"`` and the slot stays ``active == "run-1"`` with run-2 stranded queued.
    """
    from issueforge import engine
    from issueforge.state import TERMINAL, State

    _register(make_git_repo)
    seams = _install_scope_seams(monkeypatch, scope_answer=None)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", ISSUE_2)

    engine.continue_run("run-1", stage=engine._poc_composed_stage)

    # Terminal, via the scope gate (not an empty-stated-files shortcut).
    assert State(_status("run-1")) in TERMINAL
    assert _status("run-1") == "failed"
    assert seams.scope_calls and seams.scope_calls[0] == STATED_FILES
    assert seams.read_calls, "the composed stage never reached the issue read / scope gate"
    # Auto-advance: the queued waiter was promoted and ran to a terminal state, not stranded.
    assert State(_status("run-2")) in TERMINAL
    assert "run-2" not in _queue()["queue"]
    # No re-wedge on the drain path.
    assert _queue() == {"active": None, "queue": []}
    # Lifecycle containment, not exact equality (diagnostic events allowed).
    assert _is_subsequence(["queued", "running", "failed"], _transitions("run-1"))


# ---------------------------------------------------------------------------
# Decision 2 — drain dispatches the COMPOSED stage for a promoted queued run
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#142)")
def test_drain_dispatches_the_exact_composed_stage_for_a_promoted_queued_run(
    make_git_repo, isolated_state_home, monkeypatch
):
    """When the worker frees and promotes a never-started queued run, the drain path runs the EXACT
    composed build callable for it — the same one a fresh ``issueforge run`` dispatch uses — not the
    no-op stub that today completes it without ever doing its build.

    technical (contract): ``engine._poc_composed_stage`` is wrapped by a spy that records each
    dispatched run_id then delegates to the real function. run-1 is admitted paused (holding the slot
    via a NON-composed pausing stage) with run-2 (issue 149) queued behind it; ``engine.cancel("run-1")``
    releases the slot through the cancel path (never invoking the composed stage for run-1) and promotes
    run-2. Afterward the spy recorded ``"run-2"`` (the drain dispatched the composed callable for the
    promotion) and NOT ``"run-1"`` (released via cancel); the composed stage read the FULL resolved
    issue identity ``(slug, 149)``; and ``read_queue()["active"] is None`` (the promoted run terminated
    without re-wedging the drain). TODAY the drain runs the stub ``_default_stage``, so the spy is never
    called and 149 is never read — every assertion is red.
    """
    from issueforge import engine

    _register(make_git_repo)
    seams = _install_scope_seams(monkeypatch, scope_answer=None)
    dispatched = _spy_on_composed_stage(monkeypatch)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", ISSUE_2)

    engine.cancel("run-1")

    assert "run-2" in dispatched
    assert "run-1" not in dispatched
    assert (_slug("run-2"), ISSUE_2) in seams.read_calls
    assert _queue()["active"] is None


# ---------------------------------------------------------------------------
# Decision 3 — 'queued behind active run <id>' feedback is engine-side
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#142)")
def test_engine_run_emits_queued_behind_feedback_only_when_landing_a_run_queued(
    make_git_repo, isolated_state_home, capsys
):
    """Landing a run behind the active worker is not silent — the engine tells the operator the run is
    queued and which active run it waits behind — while an immediately-admitted run gets NO such
    message. The feedback is engine-side (emitted by a direct ``engine.run`` call, no CLI in the path).

    technical (contract): NEGATIVE case — admitting run-1 to the FREE slot (it is admitted and pauses,
    status ``"paused"``, never landing queued) prints NO ``"queued behind"`` text on stdout+stderr
    combined. POSITIVE case — admitting run-2 while run-1 holds the slot lands it genuinely queued
    (the returned record and ``read("run-2")["status"]`` are ``"queued"``, ``"run-2"`` is in
    ``read_queue()["queue"]``, and ``read_queue()["active"] == "run-1"``) AND prints combined
    stdout/stderr containing ``"queued behind"`` and the active id ``"run-1"``. Capturing combined
    streams accepts either an stdout or stderr emission (the contract does not fix the stream). TODAY
    ``engine.run`` returns the queued record silently, so the positive case carries no message.
    """
    _register(make_git_repo)

    # NEGATIVE: an immediately-admitted run emits no queued-behind message.
    capsys.readouterr()
    admitted = _admit_paused(make_git_repo, "run-1")
    neg = capsys.readouterr()
    assert admitted["status"] == "paused"  # admitted to the free slot, not queued
    assert "queued behind" not in (neg.out + neg.err).lower()

    # POSITIVE: run-2 lands genuinely queued behind the active run-1, with engine-side feedback.
    capsys.readouterr()
    queued = _enqueue("run-2", ISSUE_2)
    pos = capsys.readouterr()
    combined = (pos.out + pos.err).lower()

    assert queued["status"] == "queued"
    assert _status("run-2") == "queued"
    assert "run-2" in _queue()["queue"]
    assert _queue()["active"] == "run-1"
    assert "queued behind" in combined
    assert "run-1" in combined


# ---------------------------------------------------------------------------
# Green guards — behavior true TODAY that #142 must preserve
# ---------------------------------------------------------------------------


def _drive_composed_to_pause(monkeypatch, isolated_state_home, reason):
    """Install the minimum module-level seam fakes to drive the REAL composed default stage to a
    NON-scope pause of the given class, cheaply (no real git/pytest). Every path leaves the scope gate
    APPROVED so the pause is provably not the scope rejection."""
    from issueforge import config as _config
    from issueforge import engine, store, workspace
    from issueforge import verify as _verify
    from issueforge.adapters.base import BaselineStatus
    from issueforge.state import State

    _install_scope_seams(monkeypatch, scope_answer=list(STATED_FILES))

    if reason == "missing_provider":
        # Pauses at provider-config resolution, BEFORE any fetch/worktree.
        monkeypatch.setenv(
            "ISSUEFORGE_PROVIDERS", str(isolated_state_home / "nonexistent-providers.toml")
        )
        return

    # For the deeper pauses, provider config must resolve; fetch/worktree/repo-config are faked so the
    # stage reaches the pause without real network/git.
    providers = isolated_state_home / "providers.toml"
    providers.write_text(_PROVIDERS_CONFIG)
    monkeypatch.setenv("ISSUEFORGE_PROVIDERS", str(providers))

    worktree = isolated_state_home / "wt"
    worktree.mkdir(exist_ok=True)
    monkeypatch.setattr(
        workspace,
        "fetch_default_sha",
        lambda checkout: SimpleNamespace(ok=True, sha="0" * 40, reason=None),
    )
    monkeypatch.setattr(
        workspace,
        "create_isolated_worktree",
        lambda checkout, base_sha: SimpleNamespace(
            ok=True, isolated=True, path=worktree, reason=None
        ),
    )
    monkeypatch.setattr(
        _config,
        "load_config",
        lambda p: SimpleNamespace(baseline=["pytest"], acceptance=["pytest"]),
    )

    if reason == "baseline_not_green":
        monkeypatch.setattr(
            _verify,
            "run_baseline",
            lambda *a, **k: SimpleNamespace(
                status=BaselineStatus.USAGE_ERROR, exit_code=1, stdout="", stderr=""
            ),
        )
        return

    if reason == "rejected_contract":
        monkeypatch.setattr(
            _verify,
            "run_baseline",
            lambda *a, **k: SimpleNamespace(
                status=BaselineStatus.GREEN, exit_code=0, stdout="", stderr=""
            ),
        )

        def fake_run_candidate(record, **_kw):
            # Mirror run_candidate's own contract-rejection pause: persist paused, report it.
            store.RunStore().apply(record["run_id"], lambda _r: {"status": State.PAUSED.value})
            return engine.CandidateResult(
                paused=True,
                pause_reason="rejected_by_approver",
                contract_commit=None,
                candidate_sha=None,
                evidence=None,
            )

        monkeypatch.setattr(engine, "run_candidate", fake_run_candidate)
        return

    raise AssertionError(f"unknown pause reason {reason!r}")


@pytest.mark.parametrize("reason", ["missing_provider", "baseline_not_green", "rejected_contract"])
def test_a_non_scope_composed_pause_keeps_the_worker_slot(
    reason, make_git_repo, isolated_state_home, monkeypatch
):
    """Every composed-stage pause that is NOT a scope-gate rejection stays resumable and KEEPS the
    worker slot — #142 makes ONLY the scope rejection terminal, never every pause. All three preserved
    pause classes (missing provider config, ``baseline_not_green``, rejected contract) are covered.

    technical (contract): with the scope gate APPROVED, driving ``engine.run("DandD#148")`` to each
    non-scope pause leaves ``read("run-1")["status"] == "paused"`` and ``read_queue()["active"] ==
    "run-1"`` — the slot is KEPT (the run is resumable), not released. Holds today and must keep
    holding after #142.
    """
    from issueforge import engine

    _register(make_git_repo)
    _drive_composed_to_pause(monkeypatch, isolated_state_home, reason)

    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1")

    assert _status("run-1") == "paused"
    assert _queue()["active"] == "run-1"


def test_cancelling_the_paused_run_still_frees_the_worker_slot(make_git_repo, isolated_state_home):
    """Cancelling the current paused run still releases the worker slot — the existing terminal-release
    path #142's scope-rejection change must not regress.

    technical (contract): with run-1 paused and holding the slot (no waiter),
    ``engine.cancel("run-1")`` lands run-1 ``"cancelled"`` and ``read_queue() == {"active": None,
    "queue": []}`` (the slot is freed, nothing left to advance). True today; must stay true.
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")

    engine.cancel("run-1")

    assert _status("run-1") == "cancelled"
    assert _queue() == {"active": None, "queue": []}
