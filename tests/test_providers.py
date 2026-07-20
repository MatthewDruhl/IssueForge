"""Committed PENDING acceptance suite for #11 (S7) — the AI provider layer.

The S7 surfaces this suite authors do not exist yet (``issueforge.providers`` is a new module; the
provider ``Profile``/``RateLimit``/``Roles`` dataclasses and ``load_roles`` are new on
``issueforge.config``; ``provider check`` is a new CLI command), so each test FAILS while pending —
an ``ImportError`` for the missing module or an ``AttributeError`` for a missing attribute (or, for
the CLI test, a "no such command" exit) — and is therefore
``@pytest.mark.xfail(strict=True, reason="PENDING (#11)")``. Every import of a pending surface lives
INSIDE its test so a module-top import cannot turn the whole file into a collection ERROR. Dual-layer
docstrings: plain behavior first, then the ``technical (contract):`` golden values.

Design decisions resolved with the human before authoring (2026-07-20):
- **Session identity is an ENGINE-MINTED token.** ``invoke`` mints a fresh ``uuid4().hex`` each time
  it starts a session (``session=None``), records it on the ``AIResult``, and reuses a supplied id on
  resume. A review whose recorded ``session_id`` equals the authoring result's is rejected. Fully
  deterministic under the fake provider — no real CLI and no vendor session format is coupled.
- **Rate-limit detection is a CONFIG MATCHER, retry bound defaults to 2.** A profile declares a
  ``RateLimit`` (a stderr substring and/or an exit code). ``invoke`` matches it, retries up to
  ``profile.rate_limit_retries`` (default 2) times, then returns a DISTINCT ``RATE_LIMITED`` status —
  never a crash, never a review failure, and never an API-key fallback.

Scope of S7 (this slice BUILDS the invocation seam and exercises no AI judgment): the guarded launch,
session identity, role separation, REDACTED persistence of its own captured results, and the
``materialize_inputs`` building block. Network-off ENFORCEMENT and the hermetic provisioned
environment are S6/S15's. The ORDERING guarantee "inputs are materialized BEFORE the review is
invoked" is a property of the review orchestration that S15 owns (it is what sequences
``materialize_inputs`` then ``invoke``); S7 owns only that ``materialize_inputs`` writes every input
to local disk under ``dest`` through the guarded seam.

The S7 interface this suite authors (ATDD):

- ``issueforge.config`` gains provider configuration (argv-validated like every other command field):
  - ``RateLimit(stderr_contains: str | None = None, exit_code: int | None = None)`` — a per-profile
    rate-limit signal; ``.matches(result)`` is True when the exit code equals ``exit_code`` OR
    ``stderr_contains`` is a substring of the result's stderr (either signal fires).
  - ``Profile(name, executable, start, resume, auth, rate_limit=None, rate_limit_retries=2)`` — a
    resolved provider profile. ``executable`` is the base argv; ``start``/``resume``/``auth`` are argv
    TEMPLATES (each a list of strings) in which the tokens ``"{prompt}"`` and ``"{session}"`` are
    substituted per element at launch. EVERY command field (executable/start/resume/auth) is an argv
    array; a shell STRING in any of them is rejected with a ``ConfigError``.
  - ``Roles(primary: Profile, secondary: Profile | None = None)``.
  - ``load_roles(data: dict) -> Roles`` — resolve ``[providers.<name>]`` tables and a ``[roles]``
    table (``primary`` required, ``secondary`` optional) into ``Roles``.

- ``issueforge.providers`` (new):
  - ``InvocationStatus`` — an enum whose members are EXACTLY ``OK``, ``FAILED``, ``RATE_LIMITED``.
  - ``AIResult(role, provider, session_id, prompt, stdout, stderr, returncode, duration_ms, status,
    timed_out, artifact_path)`` — the recorded outcome of one invocation. ``role``, ``provider`` (the
    profile name), and ``session_id`` are recorded on EVERY result (success, failure, timeout,
    rate-limited, resumed, secondary). ``artifact_path`` is the persisted redacted transcript.
  - ``invoke(profile, prompt, *, cwd, timeout, run_id, secrets=frozenset(), runner=process.run,
    role="primary", session=None) -> AIResult`` — the ONE invocation seam (no provider ABC, no
    adapter registry). It renders the ``start`` template (minting a fresh ``session_id`` when
    ``session is None``) or the ``resume`` template (reusing ``session``), launches the argv LIST
    through ``runner`` — never a shell — with a SCRUBBED env (every ``*_API_KEY`` variable removed)
    and an in-process wall-clock ``timeout`` (never the macOS-absent ``timeout(1)`` binary), and maps
    the result: ``RATE_LIMITED`` when the profile's ``rate_limit`` still matches after the bounded
    retries; otherwise ``FAILED`` when the exit is non-zero OR the run timed out OR stdout is empty;
    otherwise ``OK``. Full stdout/stderr are preserved verbatim (never tail-truncated). On EVERY
    terminal path it persists ONE redacted transcript (prompt + stdout + stderr + metadata) through
    S4's redacting artifact writer (``store.write_artifact(run_id, ..., secrets=secrets)``) and records
    its path on ``AIResult.artifact_path``; NO persisted artifact anywhere under the state root — this
    transcript, or the ``process`` invocation record whose argv carries the prompt — may contain a
    secret listed in ``secrets``. The provider's own auth file is never read or logged by the engine.
  - ``review_profile(roles) -> Profile`` — ``roles.secondary`` if configured, else ``roles.primary``
    (US-9.5: with no secondary, the review runs on the primary provider).
  - ``validate_review_independence(review, authoring) -> None`` — raises ``SharedSessionError`` when
    ``review.session_id == authoring.session_id``; returns ``None`` when they differ.
  - ``SharedSessionError`` — a review that shares the authoring session identity.
  - ``authenticated(profile, *, runner=process.run) -> bool`` — run ``executable + auth`` through the
    scrubbed-env guarded launch; True iff it exits 0 and did not time out; False on non-zero or
    timeout. Never reads an API key; never falls back to a metered API.
  - ``materialize_inputs(dest, *, diff, contract, manifest, red_evidence, proof_command, head_sha,
    prior_verdicts=None) -> dict[str, Path]`` — write each review input to a file UNDER ``dest``
    (through the guarded write seam). ``prior_verdicts`` is omitted from the mapping when ``None`` and
    included when supplied.

- ``issueforge.cli`` gains ``provider check --config <toml>`` — verify the primary provider's CLI is
  authenticated and print the resolved profile and role bindings; fail closed (exit 1) with NO
  fallback launch when it is not authenticated.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

# Four canaries for the redaction tests: a token, a credential path, an env value, a synthetic secret.
CANARY_TOKEN = "TOKEN-aaa111"
CANARY_CRED_PATH = "/home/u/.secret/creds.json"
CANARY_ENV_VALUE = "ENVVAL-ccc333"
CANARY_SYNTHETIC = "synthetic-ddd444"


# --- shared helpers (import the pending surfaces lazily, so they anchor xfail) -------------------


def _fake_profile(
    script: Path, start: list[str], *, name: str = "fake", resume: list[str] | None = None
):
    """Build a Profile whose executable runs the fake provider script through the real subprocess seam."""
    from issueforge.config import Profile

    return Profile(
        name=name,
        executable=[sys.executable, str(script)],
        start=start,
        resume=resume if resume is not None else ["--out", "ok", "{prompt}"],
        auth=["--exit", "0"],
    )


def _spy_runner(results):
    """A runner recording every (argv, cwd, timeout, env) call and returning queued CommandResults.

    ``results`` is a list consumed one per call; the last is repeated if calls exceed the list.
    """
    calls: list[dict] = []

    def runner(argv, *, cwd, timeout, env=None):
        calls.append({"argv": argv, "cwd": cwd, "timeout": timeout, "env": env})
        index = min(len(calls) - 1, len(results) - 1)
        return results[index]

    runner.calls = calls
    return runner


def _command_result(*, returncode=0, stdout="ok", stderr="", timed_out=False):
    from issueforge.process import CommandResult

    return CommandResult(
        argv=["ignored"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1.0,
        timed_out=timed_out,
    )


def _all_state_artifacts() -> list[Path]:
    """Every file persisted anywhere under the isolated state root."""
    from issueforge.paths import state_root

    return [p for p in Path(state_root()).rglob("*") if p.is_file()]


def _assert_no_secret_in_any_artifact(secrets: set[str]) -> None:
    """Assert none of ``secrets`` appears in ANY persisted artifact (transcript, invocation record, ...)."""
    inspected = 0
    for path in _all_state_artifacts():
        inspected += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for secret in secrets:
            assert secret not in text, f"secret leaked into {path}"
    assert inspected > 0, "no artifacts were persisted to scan"


# ---------------------------------------------------------------------------
# Configuration: two roles bind to provider profiles; argv-validated on all four fields
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_two_roles_bind_to_provider_profiles_declared_in_configuration(isolated_state_home):
    """Two roles (primary, secondary) each bind to a named provider profile whose executable, start, resume, and auth commands are configuration variables.

    technical (contract): load_roles over [providers.alpha] + [providers.beta] and [roles]
    primary="alpha" secondary="beta" returns a Roles resolving BOTH profiles fully — primary.name=="alpha",
    executable==["alpha-cli"], start==["exec","{prompt}"], resume==["exec","--resume","{session}",
    "{prompt}"], auth==["whoami"]; secondary.name=="beta", executable==["beta-cli"], start==["run",
    "{prompt}"], resume==["run","-r","{session}"], auth==["auth","status"].
    """
    from issueforge.config import Roles, load_roles

    data = {
        "providers": {
            "alpha": {
                "executable": ["alpha-cli"],
                "start": ["exec", "{prompt}"],
                "resume": ["exec", "--resume", "{session}", "{prompt}"],
                "auth": ["whoami"],
            },
            "beta": {
                "executable": ["beta-cli"],
                "start": ["run", "{prompt}"],
                "resume": ["run", "-r", "{session}"],
                "auth": ["auth", "status"],
            },
        },
        "roles": {"primary": "alpha", "secondary": "beta"},
    }

    roles = load_roles(data)
    assert isinstance(roles, Roles)
    assert roles.primary.name == "alpha"
    assert roles.primary.executable == ["alpha-cli"]
    assert roles.primary.start == ["exec", "{prompt}"]
    assert roles.primary.resume == ["exec", "--resume", "{session}", "{prompt}"]
    assert roles.primary.auth == ["whoami"]
    assert roles.secondary is not None
    assert roles.secondary.name == "beta"
    assert roles.secondary.executable == ["beta-cli"]
    assert roles.secondary.start == ["run", "{prompt}"]
    assert roles.secondary.resume == ["run", "-r", "{session}"]
    assert roles.secondary.auth == ["auth", "status"]


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
@pytest.mark.parametrize("field", ["executable", "start", "resume", "auth"])
def test_a_shell_string_in_any_command_field_is_rejected(field, isolated_state_home):
    """Every provider command field must be an argv array; a shell string in ANY of them (executable, start, resume, auth) is rejected, so no field can smuggle in shell execution.

    technical (contract): a provider whose <field> is the shell STRING "do a thing" (all other fields
    valid argv arrays) raises config.ConfigError from load_roles.
    """
    from issueforge.config import ConfigError, load_roles

    provider = {
        "executable": ["alpha-cli"],
        "start": ["exec", "{prompt}"],
        "resume": ["exec", "-r", "{session}"],
        "auth": ["whoami"],
    }
    provider[field] = "do a thing"
    with pytest.raises(ConfigError):
        load_roles({"providers": {"alpha": provider}, "roles": {"primary": "alpha"}})


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_review_role_falls_back_to_the_primary_provider_when_no_secondary_is_configured(
    isolated_state_home,
):
    """With no secondary provider configured, the review role runs on the primary provider — independence comes from a fresh session, and a different vendor is an optional strengthening.

    technical (contract): load_roles over [roles] primary="alpha" with NO secondary key yields
    roles.secondary is None, and providers.review_profile(roles) is (the same object as) roles.primary.
    """
    from issueforge import providers
    from issueforge.config import load_roles

    roles = load_roles(
        {
            "providers": {
                "alpha": {
                    "executable": ["alpha-cli"],
                    "start": ["exec", "{prompt}"],
                    "resume": ["exec", "-r", "{session}"],
                    "auth": ["whoami"],
                }
            },
            "roles": {"primary": "alpha"},
        }
    )

    assert roles.secondary is None
    assert providers.review_profile(roles) is roles.primary


# ---------------------------------------------------------------------------
# Structure: exactly one invoke seam, no provider ABC/registry, exact status set
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_invocation_status_members_are_exactly_ok_failed_rate_limited(isolated_state_home):
    """The invocation status is a closed set of exactly three outcomes: OK, FAILED, RATE_LIMITED.

    technical (contract): set(providers.InvocationStatus.__members__) == {"OK", "FAILED", "RATE_LIMITED"}.
    """
    from issueforge import providers

    assert set(providers.InvocationStatus.__members__) == {"OK", "FAILED", "RATE_LIMITED"}


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_there_is_one_invoke_seam_and_no_provider_abc_or_registry(isolated_state_home):
    """There is ONE invocation seam and no provider class hierarchy: configuration is the polymorphism, not a provider ABC or an adapter registry.

    technical (contract): the AST of issueforge.providers declares a module-level function ``invoke``
    (and no OTHER module-level function whose name contains "invoke"); it declares no class inheriting
    ABC/ABCMeta whether the base is bare (``ABC``) or qualified (``abc.ABC``), no @abstractmethod, and
    no class whose name ends with Provider/Adapter/Registry/Backend (a second provider is a config
    table, not a subclass).
    """
    from issueforge import providers

    tree = ast.parse(Path(providers.__file__).read_text(encoding="utf-8"))
    module_funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "invoke" in module_funcs
    # No second invocation seam smuggled in beside the one `invoke`.
    assert {n for n in module_funcs if "invoke" in n} == {"invoke"}

    def _base_names(bases):
        names = set()
        for base in bases:
            if isinstance(base, ast.Name):
                names.add(base.id)
            elif isinstance(base, ast.Attribute):  # e.g. abc.ABC
                names.add(base.attr)
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert not ({"ABC", "ABCMeta"} & _base_names(node.bases)), f"{node.name} is an ABC"
            assert not node.name.endswith(("Provider", "Adapter", "Registry", "Backend")), node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            deco = {d.id for d in node.decorator_list if isinstance(d, ast.Name)}
            deco |= {d.attr for d in node.decorator_list if isinstance(d, ast.Attribute)}
            assert "abstractmethod" not in deco, f"{node.name} is abstract"


# ---------------------------------------------------------------------------
# No hardcoded vendor name; no metered API key read or passed to a child
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_no_provider_vendor_name_appears_anywhere_in_the_engine_source(isolated_state_home):
    """No provider/vendor product name is hardcoded anywhere in the engine package — the vendor lives only in a user's configuration.

    technical (contract): importing issueforge.providers succeeds; scanning every .py file under the
    issueforge package, no source contains any banned vendor token in {codex, claude, anthropic,
    openai, chatgpt, gemini, copilot, ollama} (case-insensitive). (A heuristic backstop; the structural
    guard that selection derives only from Profile config is test_there_is_one_invoke_seam...)
    """
    from issueforge import providers

    package_dir = Path(providers.__file__).resolve().parent
    banned = ("codex", "claude", "anthropic", "openai", "chatgpt", "gemini", "copilot", "ollama")
    pattern = re.compile("|".join(banned), re.IGNORECASE)

    offenders: dict[str, list[str]] = {}
    for path in package_dir.rglob("*.py"):
        hits = pattern.findall(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(package_dir))] = sorted({h.lower() for h in hits})

    assert offenders == {}, f"vendor names hardcoded in engine source: {offenders}"


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_no_api_key_is_read_in_source_nor_passed_to_any_child(
    fake_provider_script, monkeypatch, isolated_state_home
):
    """The engine never reads an API key and never leaks one into a child process: it uses subscription CLI auth only, never a metered API.

    technical (contract): (static) no .py file under the issueforge package contains a case-insensitive
    "api_key" substring; (dynamic) with THREE arbitrary *_API_KEY variables seeded in the environment,
    both invoke and authenticated still succeed and the env passed to every child launch contains no
    variable whose name ends with API_KEY (a scrubbed env, not None).
    """
    from issueforge import providers

    package_dir = Path(providers.__file__).resolve().parent
    static_offenders = [
        str(p.relative_to(package_dir))
        for p in package_dir.rglob("*.py")
        if "api_key" in p.read_text(encoding="utf-8").lower()
    ]
    assert static_offenders == [], f"API-key reads in engine source: {static_offenders}"

    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SOMEVENDOR_API_KEY"):
        monkeypatch.setenv(name, "must-not-reach-a-child")

    profile = _fake_profile(fake_provider_script, ["--out", "ready", "{prompt}"])
    spy = _spy_runner([_command_result(stdout="ready")])

    providers.invoke(
        profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-nokey-invoke", runner=spy
    )
    providers.authenticated(profile, runner=spy)

    assert spy.calls, "no child was launched"
    for call in spy.calls:
        assert call["env"] is not None, (
            "invoke/authenticated must pass an explicit scrubbed env, not None"
        )
        assert not any(k.upper().endswith("API_KEY") for k in call["env"])


# ---------------------------------------------------------------------------
# invoke: session identity, argv rendering, role/provider on every result
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_invoke_renders_start_and_records_a_fresh_session_role_and_provider(
    fake_provider_script, isolated_state_home
):
    """A fresh invocation launches the rendered START command, records its role, provider, and a fresh session identity, and two fresh invocations get two distinct identities.

    technical (contract): with a spy runner, invoke(profile, "PROMPT", role="primary", session=None)
    launches argv == executable + start with "{prompt}"->"PROMPT" (no "{session}"/"{prompt}" tokens
    left) and returns status OK, role=="primary", provider=="fake", a non-empty session_id; a second
    fresh invoke returns a DIFFERENT session_id.
    """
    from issueforge import providers

    profile = _fake_profile(fake_provider_script, ["--out", "ready", "{prompt}"])
    spy = _spy_runner([_command_result(stdout="ready")])

    first = providers.invoke(
        profile,
        "PROMPT",
        cwd=Path.cwd(),
        timeout=10,
        run_id="run-start-1",
        runner=spy,
        role="primary",
    )
    second = providers.invoke(
        profile, "PROMPT", cwd=Path.cwd(), timeout=10, run_id="run-start-2", runner=spy
    )

    assert spy.calls[0]["argv"] == [
        sys.executable,
        str(fake_provider_script),
        "--out",
        "ready",
        "PROMPT",
    ]
    assert first.status is providers.InvocationStatus.OK
    assert first.role == "primary"
    assert first.provider == "fake"
    assert first.session_id
    assert first.session_id != second.session_id


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_a_review_that_shares_the_authoring_session_identity_is_rejected(
    fake_provider_script, isolated_state_home
):
    """A review whose session identity equals the authoring session's is rejected; a review in a distinct session is accepted — and resuming renders the RESUME command with the supplied session.

    technical (contract): invoke(..., session=SID) launches argv == executable + resume with
    "{session}"->SID and "{prompt}"->prompt (proving the id is really used, not just copied onto the
    result), and records session_id==SID; validate_review_independence(review, authoring) raises
    providers.SharedSessionError when the ids are equal and returns None when they differ.
    """
    from issueforge import providers

    profile = _fake_profile(
        fake_provider_script,
        ["--out", "ok", "{prompt}"],
        resume=["--out", "resumed", "--session", "{session}", "{prompt}"],
    )

    authoring = providers.invoke(
        profile, "author", cwd=Path.cwd(), timeout=10, run_id="run-auth", role="primary"
    )

    spy = _spy_runner([_command_result(stdout="resumed")])
    shared = providers.invoke(
        profile,
        "review",
        cwd=Path.cwd(),
        timeout=10,
        run_id="run-shared",
        runner=spy,
        role="secondary",
        session=authoring.session_id,
    )
    assert spy.calls[0]["argv"] == [
        sys.executable,
        str(fake_provider_script),
        "--out",
        "resumed",
        "--session",
        authoring.session_id,
        "review",
    ]
    assert shared.session_id == authoring.session_id
    assert shared.role == "secondary"
    with pytest.raises(providers.SharedSessionError):
        providers.validate_review_independence(shared, authoring)

    fresh = providers.invoke(
        profile, "review", cwd=Path.cwd(), timeout=10, run_id="run-fresh", role="secondary"
    )
    assert fresh.session_id != authoring.session_id
    assert providers.validate_review_independence(fresh, authoring) is None


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_a_no_secondary_review_runs_in_a_brand_new_session_on_the_primary_provider(
    fake_provider_script, isolated_state_home
):
    """When no secondary is configured, the review runs on the primary provider but in a brand-new session — never the authoring session — and that independent review is accepted.

    technical (contract): with roles.secondary None, review_profile(roles) is the primary; an authoring
    invoke then a review invoke (session=None) on that profile yield the SAME provider name ("solo") but
    DIFFERENT session_ids, both results carry role/provider/session_id, and
    validate_review_independence(review, authoring) does not raise.
    """
    from issueforge import providers
    from issueforge.config import load_roles

    profile_data = {
        "executable": [sys.executable, str(fake_provider_script)],
        "start": ["--out", "ok", "{prompt}"],
        "resume": ["--out", "ok", "{prompt}"],
        "auth": ["--exit", "0"],
    }
    roles = load_roles({"providers": {"solo": profile_data}, "roles": {"primary": "solo"}})

    review_profile = providers.review_profile(roles)
    authoring = providers.invoke(
        roles.primary, "author", cwd=Path.cwd(), timeout=10, run_id="run-ns-auth", role="primary"
    )
    review = providers.invoke(
        review_profile, "review", cwd=Path.cwd(), timeout=10, run_id="run-ns-rev", role="secondary"
    )

    assert review.provider == authoring.provider == "solo"
    assert review.role == "secondary"
    assert review.session_id and authoring.session_id
    assert review.session_id != authoring.session_id
    assert providers.validate_review_independence(review, authoring) is None


# ---------------------------------------------------------------------------
# The guarded-launch contract, as properties of the subprocess invocation
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_empty_output_is_a_failure_and_still_records_metadata(
    fake_provider_script, isolated_state_home
):
    """An empty result is a failure, never read as a clean pass — the single highest-blast-radius weakening the gate must refuse — and the result still records role, provider, and session identity.

    technical (contract): a fake provider that exits 0 with EMPTY stdout yields status FAILED
    (exit 0 alone is not success), returncode 0, a non-empty role/provider/session_id, and a persisted
    transcript at artifact_path (the empty-output case is a terminal path too).
    """
    from issueforge import providers

    profile = _fake_profile(fake_provider_script, ["--exit", "0", "{prompt}"])
    result = providers.invoke(profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-empty")

    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert result.status is providers.InvocationStatus.FAILED
    assert result.role == "primary"
    assert result.provider == "fake"
    assert result.session_id
    assert result.artifact_path.exists()


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_a_nonzero_exit_is_a_failure_and_still_records_metadata(
    fake_provider_script, isolated_state_home
):
    """A non-zero exit is a failure, never a pass, even when the CLI printed some output — and the result still records its metadata.

    technical (contract): a fake provider that prints "partial" then exits 3 yields status FAILED,
    returncode 3, stdout "partial", and a non-empty role/provider/session_id.
    """
    from issueforge import providers

    profile = _fake_profile(fake_provider_script, ["--out", "partial", "--exit", "3", "{prompt}"])
    result = providers.invoke(profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-nonzero")

    assert result.returncode == 3
    assert result.stdout.strip() == "partial"
    assert result.status is providers.InvocationStatus.FAILED
    assert result.role == "primary"
    assert result.provider == "fake"
    assert result.session_id


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_a_timeout_is_a_distinct_bounded_failure_and_still_records_metadata(
    fake_provider_script, isolated_state_home
):
    """A hung provider is killed by an in-process wall-clock timeout (never the absent-on-macOS timeout(1) binary) and recorded as a distinct failure that still carries its metadata.

    technical (contract): a fake provider that emits "started" then sleeps 30s, invoked with
    timeout=0.5, returns within a few seconds with timed_out True, status FAILED, duration_ms < 5000
    (killed, not awaited), and a non-empty role/provider/session_id.
    """
    from issueforge import providers

    profile = _fake_profile(fake_provider_script, ["--out", "started", "--sleep", "30", "{prompt}"])
    result = providers.invoke(profile, "hi", cwd=Path.cwd(), timeout=0.5, run_id="run-timeout")

    assert result.timed_out is True
    assert result.status is providers.InvocationStatus.FAILED
    assert result.duration_ms < 5000
    assert result.role == "primary"
    assert result.provider == "fake"
    assert result.session_id


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_the_launch_closes_stdin_with_devnull_and_uses_an_argv_list_never_a_shell(
    fake_provider_script, monkeypatch, isolated_state_home
):
    """The provider subprocess is launched with a real argv list (no shell) and with its stdin set to DEVNULL, so a CLI that reads stdin without a TTY gets immediate EOF instead of hanging.

    technical (contract): (a) monkeypatching subprocess.Popen, the launch passes stdin is
    subprocess.DEVNULL; (b) a real fake provider that reads stdin reports STDIN_BYTES=0 and does not
    time out within a 10s timeout; (c) invoke hands its runner a LIST argv (never a shell string).
    """
    import subprocess

    from issueforge import providers

    # (a) the guarded launch sets stdin=DEVNULL — pytest's captured stdin would give EOF anyway, so
    # assert the flag directly, not just the behavior.
    seen_stdin: list[object] = []
    real_popen = subprocess.Popen

    def spy_popen(*args, **kwargs):
        seen_stdin.append(kwargs.get("stdin"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy_popen)

    profile = _fake_profile(fake_provider_script, ["--read-stdin", "{prompt}"])
    result = providers.invoke(profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-stdin")

    assert seen_stdin and all(s is subprocess.DEVNULL for s in seen_stdin)
    assert result.timed_out is False
    assert "STDIN_BYTES=0" in result.stdout

    # (c) invoke hands the runner a LIST argv (no shell string).
    spy = _spy_runner([_command_result(stdout="ok")])
    providers.invoke(
        profile, "PROMPT", cwd=Path.cwd(), timeout=10, run_id="run-stdin-argv", runner=spy
    )
    assert isinstance(spy.calls[0]["argv"], list)
    assert spy.calls[0]["argv"] == [
        sys.executable,
        str(fake_provider_script),
        "--read-stdin",
        "PROMPT",
    ]


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_full_stdout_and_stderr_are_preserved_untruncated(
    fake_provider_script, isolated_state_home
):
    """Both the full stdout and the full stderr are preserved verbatim — never tail-truncated (truncation would silently drop a reviewer's finding list) — and both land in the persisted transcript.

    technical (contract): a fake provider emitting 500 distinct "out-<i>" lines to stdout and 500
    "err-<i>" lines to stderr yields result.stdout/result.stderr each containing its first and last
    line and exactly 500 occurrences; the persisted transcript at result.artifact_path contains all 500
    of each.
    """
    from issueforge import providers

    out_block = "\n".join(f"out-{i}" for i in range(500))
    err_block = "\n".join(f"err-{i}" for i in range(500))
    profile = _fake_profile(
        fake_provider_script, ["--out", out_block, "--err", err_block, "{prompt}"]
    )
    result = providers.invoke(profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-fulloutput")

    assert "out-0" in result.stdout and "out-499" in result.stdout
    assert result.stdout.count("out-") == 500
    assert "err-0" in result.stderr and "err-499" in result.stderr
    assert result.stderr.count("err-") == 500

    transcript = result.artifact_path.read_text(encoding="utf-8")
    assert transcript.count("out-") == 500
    assert transcript.count("err-") == 500


# ---------------------------------------------------------------------------
# Rate limit: a distinct recoverable state, bounded retry, recovery, no over-retry
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_a_persistent_rate_limit_retries_the_default_two_times_then_reports_a_distinct_state(
    monkeypatch, isolated_state_home
):
    """A rate-limit refusal is its own recoverable state; a persistently-limited provider is retried the default two times (three launches total) and then reported as RATE_LIMITED, never FAILED and never an API-key fallback.

    technical (contract): a profile whose rate_limit matches stderr "rate limit", with
    rate_limit_retries OMITTED (defaulting to 2), against an always-rate-limited runner is called
    EXACTLY 3 times (1 + 2 retries) and returns status RATE_LIMITED; the result carries role/provider/
    session_id and a persisted transcript (this is a terminal path too); even with OPENAI_API_KEY set,
    no child receives a variable ending in API_KEY, and a canary in the limited output is redacted from
    every artifact.
    """
    from issueforge import providers
    from issueforge.config import Profile, RateLimit

    monkeypatch.setenv("OPENAI_API_KEY", "should-not-reach-the-child")
    spy = _spy_runner(
        [_command_result(returncode=1, stdout="", stderr=f"rate limit exceeded {CANARY_ENV_VALUE}")]
    )

    profile = Profile(
        name="fake",
        executable=["ignored"],
        start=["{prompt}"],
        resume=["{prompt}"],
        auth=["whoami"],
        rate_limit=RateLimit(stderr_contains="rate limit"),
    )

    result = providers.invoke(
        profile,
        "hi",
        cwd=Path.cwd(),
        timeout=10,
        run_id="run-rl",
        runner=spy,
        secrets={CANARY_ENV_VALUE},
    )

    assert len(spy.calls) == 3
    assert result.status is providers.InvocationStatus.RATE_LIMITED
    # Metadata + persistence hold on the RATE_LIMITED terminal path too.
    assert result.role == "primary"
    assert result.provider == "fake"
    assert result.session_id
    assert (
        result.artifact_path.exists() and result.artifact_path.read_text(encoding="utf-8").strip()
    )
    _assert_no_secret_in_any_artifact({CANARY_ENV_VALUE})
    for call in spy.calls:
        assert call["env"] is not None
        assert not any(k.upper().endswith("API_KEY") for k in call["env"])


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_a_rate_limit_recovers_on_retry_and_a_nonmatching_failure_is_not_retried(
    isolated_state_home,
):
    """A spurious ("phantom") rate limit that clears on retry recovers to a normal result; a plain failure that is NOT a rate limit is returned immediately, never retried and never mislabeled RATE_LIMITED.

    technical (contract): (recovery) an exit-code rate_limit (exit_code=429) with a runner that returns
    429 twice then a clean exit-0 with output is called EXACTLY 3 times and returns status OK; (no
    over-retry) the same profile against a runner returning a plain exit-1 (no 429, no matching stderr)
    is called EXACTLY once and returns status FAILED.
    """
    from issueforge import providers
    from issueforge.config import Profile, RateLimit

    profile = Profile(
        name="fake",
        executable=["ignored"],
        start=["{prompt}"],
        resume=["{prompt}"],
        auth=["whoami"],
        rate_limit=RateLimit(exit_code=429),
        rate_limit_retries=2,
    )

    recovering = _spy_runner(
        [
            _command_result(returncode=429, stdout="", stderr="limited"),
            _command_result(returncode=429, stdout="", stderr="limited"),
            _command_result(returncode=0, stdout="finally ok", stderr=""),
        ]
    )
    recovered = providers.invoke(
        profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-rl-recover", runner=recovering
    )
    assert len(recovering.calls) == 3
    assert recovered.status is providers.InvocationStatus.OK

    plain_fail = _spy_runner(
        [_command_result(returncode=1, stdout="nope", stderr="some other error")]
    )
    failed = providers.invoke(
        profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-rl-plainfail", runner=plain_fail
    )
    assert len(plain_fail.calls) == 1
    assert failed.status is providers.InvocationStatus.FAILED


# ---------------------------------------------------------------------------
# authenticated() and `provider check`: fail-closed, no fallback, subscription only
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_authenticated_runs_the_exact_auth_command_and_fails_closed(
    fake_provider_script, isolated_state_home
):
    """`authenticated` runs exactly the profile's executable + auth command and reports success only on a clean exit 0, failing closed on a non-zero exit or a timeout — never guessing from configuration.

    technical (contract): with a spy runner, authenticated(profile) launches argv == executable + auth
    with a scrubbed env; it returns True for an exit-0 result, False for a non-zero exit, and False for
    a timed-out result. On the non-zero (unauthenticated) result the runner is called EXACTLY ONCE — no
    second, metered fallback launch happens.
    """
    from issueforge import providers

    profile = _fake_profile(fake_provider_script, ["--out", "ok", "{prompt}"])

    ok_spy = _spy_runner([_command_result(returncode=0, stdout="authed")])
    assert providers.authenticated(profile, runner=ok_spy) is True
    assert ok_spy.calls[0]["argv"] == [sys.executable, str(fake_provider_script), "--exit", "0"]
    assert ok_spy.calls[0]["env"] is not None

    unauth_spy = _spy_runner([_command_result(returncode=1, stdout="no")])
    assert providers.authenticated(profile, runner=unauth_spy) is False
    assert len(unauth_spy.calls) == 1  # fail closed: no second (fallback) launch

    timeout_spy = _spy_runner([_command_result(timed_out=True, stdout="")])
    assert providers.authenticated(profile, runner=timeout_spy) is False
    assert len(timeout_spy.calls) == 1


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_provider_check_reports_the_authenticated_profile_and_fails_closed_without_fallback(
    fake_provider_script, tmp_path, isolated_state_home
):
    """`issueforge provider check` verifies the configured CLI is authenticated and prints the resolved profile and role bindings; with the CLI unauthenticated it fails closed and makes NO second (fallback) launch.

    technical (contract): a provider config whose auth command exits 0 -> `provider check --config <f>`
    exits 0 and stdout names the primary profile ("solo") and its role ("primary"); the same config
    whose auth command exits 1 -> exit code 1 (fail closed). No metered API fallback exists in source.
    """
    from typer.testing import CliRunner

    from issueforge.cli import app

    def _config(auth_exit: int) -> Path:
        text = (
            "[providers.solo]\n"
            f'executable = ["{sys.executable}", "{fake_provider_script}"]\n'
            'start = ["--out", "ok", "{prompt}"]\n'
            'resume = ["--out", "ok", "{prompt}"]\n'
            f'auth = ["--exit", "{auth_exit}"]\n'
            "\n"
            "[roles]\n"
            'primary = "solo"\n'
        )
        path = tmp_path / f"providers-{auth_exit}.toml"
        path.write_text(text)
        return path

    runner = CliRunner()

    ok = runner.invoke(app, ["provider", "check", "--config", str(_config(0))])
    assert ok.exit_code == 0, ok.stdout
    assert "solo" in ok.stdout
    assert "primary" in ok.stdout

    unauth = runner.invoke(app, ["provider", "check", "--config", str(_config(1))])
    assert unauth.exit_code == 1


# ---------------------------------------------------------------------------
# Redacted persistence: invoke OWNS it, on every path, over ALL artifacts
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
@pytest.mark.parametrize(
    "flags, timeout, run_id",
    [
        (
            [
                "--echo-args",
                "--out",
                CANARY_CRED_PATH,
                "--out",
                "SENTINEL-OUT",
                "--err",
                CANARY_ENV_VALUE,
                "--err",
                CANARY_SYNTHETIC,
                "--err",
                "SENTINEL-ERR",
                "{prompt}",
            ],
            10,
            "run-canary-ok",
        ),
        (
            [
                "--echo-args",
                "--out",
                CANARY_CRED_PATH,
                "--out",
                "SENTINEL-OUT",
                "--err",
                CANARY_ENV_VALUE,
                "--err",
                CANARY_SYNTHETIC,
                "--err",
                "SENTINEL-ERR",
                "--exit",
                "7",
                "{prompt}",
            ],
            10,
            "run-canary-nonzero",
        ),
        (
            [
                "--echo-args",
                "--out",
                CANARY_CRED_PATH,
                "--out",
                "SENTINEL-OUT",
                "--err",
                CANARY_ENV_VALUE,
                "--err",
                CANARY_SYNTHETIC,
                "--err",
                "SENTINEL-ERR",
                "--sleep",
                "30",
                "{prompt}",
            ],
            0.5,
            "run-canary-timeout",
        ),
    ],
    ids=["success", "nonzero-exit", "timeout"],
)
def test_invoke_persists_a_redacted_transcript_with_no_secret_in_any_artifact(
    flags, timeout, run_id, fake_provider_script, isolated_state_home
):
    """On every terminal path — clean success, a completed non-zero exit, and a killed timeout — invoke persists ONE redacted transcript (carrying the real prompt/output/metadata, not a stripped blank), and no seeded secret appears in ANY persisted artifact, including the process invocation record whose argv holds the prompt.

    technical (contract): the prompt carries CANARY_TOKEN and "SENTINEL-PROMPT"; stdout carries
    CANARY_CRED_PATH + "SENTINEL-OUT"; stderr carries CANARY_ENV_VALUE, CANARY_SYNTHETIC + "SENTINEL-ERR".
    invoke(..., secrets={the four canaries}) first surfaces all four on the AIResult (the canaries truly
    reached captured output); the transcript at result.artifact_path contains the three NON-secret
    sentinels AND the metadata (role, provider, session_id, str(returncode), str(timed_out)) AND
    "[REDACTED]"; and NO artifact anywhere under the state root contains any of the four canaries.
    """
    from issueforge import providers

    prompt = f"{CANARY_TOKEN} SENTINEL-PROMPT"
    secrets = {CANARY_TOKEN, CANARY_CRED_PATH, CANARY_ENV_VALUE, CANARY_SYNTHETIC}
    profile = _fake_profile(fake_provider_script, flags)

    result = providers.invoke(
        profile, prompt, cwd=Path.cwd(), timeout=timeout, run_id=run_id, secrets=secrets
    )

    # Precondition: the canaries actually reached the captured result (else absence is meaningless).
    assert CANARY_TOKEN in result.prompt
    assert CANARY_CRED_PATH in result.stdout
    assert CANARY_ENV_VALUE in result.stderr
    assert CANARY_SYNTHETIC in result.stderr

    transcript = result.artifact_path.read_text(encoding="utf-8")
    for sentinel in ("SENTINEL-PROMPT", "SENTINEL-OUT", "SENTINEL-ERR"):
        assert sentinel in transcript, f"transcript dropped {sentinel} (not a real full transcript)"
    for meta in (
        result.role,
        result.provider,
        result.session_id,
        str(result.returncode),
        str(result.timed_out),
    ):
        assert meta in transcript, f"transcript missing metadata {meta!r}"
    assert "[REDACTED]" in transcript

    _assert_no_secret_in_any_artifact(secrets)


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_the_provider_auth_file_is_never_read_by_the_engine(
    fake_provider_script, monkeypatch, isolated_state_home
):
    """The engine never reads (or logs) the provider's own auth/credential file: authentication is delegated to the CLI, which reads its own credentials out of process.

    technical (contract): with a sentinel auth file whose path and contents are known, EVERY read API
    (builtins.open, Path.open, Path.read_text, Path.read_bytes) is instrumented to RAISE if that exact
    file is accessed; both authenticated() and invoke() complete without touching it, and neither the
    sentinel path nor its contents appear in any persisted artifact.
    """
    import builtins
    import os

    from issueforge import providers

    auth_file = Path(isolated_state_home) / "provider-auth.json"
    auth_secret = "AUTHFILE-SECRET-zzz999"
    auth_file.write_text(f'{{"token": "{auth_secret}"}}')

    def _is_auth_file(target) -> bool:
        try:
            return os.fspath(target) == os.fspath(auth_file)
        except TypeError:
            return False

    real_open = builtins.open
    real_path_open = Path.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def guarded_open(file, *args, **kwargs):
        assert not _is_auth_file(file), f"engine opened the provider auth file {file}"
        return real_open(file, *args, **kwargs)

    def guarded_path_open(self, *args, **kwargs):
        assert not _is_auth_file(self), f"engine opened the provider auth file {self}"
        return real_path_open(self, *args, **kwargs)

    def guarded_read_text(self, *args, **kwargs):
        assert not _is_auth_file(self), f"engine read the provider auth file {self}"
        return real_read_text(self, *args, **kwargs)

    def guarded_read_bytes(self, *args, **kwargs):
        assert not _is_auth_file(self), f"engine read the provider auth file {self}"
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    profile = _fake_profile(fake_provider_script, ["--out", "ready", "{prompt}"])
    providers.authenticated(profile)
    providers.invoke(profile, "hi", cwd=Path.cwd(), timeout=10, run_id="run-authfile")

    monkeypatch.undo()
    for path in _all_state_artifacts():
        text = path.read_text(encoding="utf-8", errors="replace")
        assert str(auth_file) not in text
        assert auth_secret not in text


# ---------------------------------------------------------------------------
# No-network reviewer: inputs materialized to local disk under dest
# (the ORDERING — materialize BEFORE invoke — is S15's review orchestration)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#11)")
def test_review_inputs_are_materialized_to_files_under_dest(isolated_state_home):
    """Because the reviewer runs with no network, every input it needs is written to a file under the destination directory, so the reviewer reads them from local disk.

    technical (contract): materialize_inputs(dest, diff=..., contract=..., manifest=..., red_evidence=...,
    proof_command=..., head_sha=..., prior_verdicts="PRIOR") returns a mapping whose keys are exactly
    {diff, contract, manifest, red_evidence, proof_command, head_sha, prior_verdicts}; each value is an
    existing file UNDER dest whose content round-trips its input. With prior_verdicts=None the key is
    omitted and the other six still round-trip under dest.
    """
    from issueforge import providers
    from issueforge.paths import state_root

    dest = Path(state_root()) / "review-inputs"
    base = {
        "diff": "DIFF-BODY",
        "contract": "CONTRACT",
        "manifest": "MANIFEST",
        "red_evidence": "RED",
        "proof_command": "pytest -q tests/test_x.py",
        "head_sha": "deadbeef",
    }

    with_prior = providers.materialize_inputs(dest, prior_verdicts="PRIOR", **base)
    assert set(with_prior) == set(base) | {"prior_verdicts"}
    for key, value in {**base, "prior_verdicts": "PRIOR"}.items():
        assert with_prior[key].exists()
        assert with_prior[key].resolve().is_relative_to(dest.resolve())
        assert with_prior[key].read_text(encoding="utf-8") == value

    without_prior = providers.materialize_inputs(dest, prior_verdicts=None, **base)
    assert set(without_prior) == set(base)  # prior_verdicts omitted when None
    for key, value in base.items():
        assert without_prior[key].read_text(encoding="utf-8") == value
