"""Committed PENDING acceptance suite for #19 (S13) — invocation integrity.

REMEDIATION (post-Codex NO-SHIP, R4 — Matt's decision): ``adapter.validate_invocation(command)``
REJECTS dangerous invocations OUTRIGHT. It does not return an inspectable ``ExecutionPlan`` whose
internal fields this suite pins; it either raises ``ValueError`` naming the offending flag/config
token, or returns without raising for a clean command. This suite therefore asserts only OBSERVABLE
behavior: whether a call raises, and (via a spy or ``verify_contract_integrity``) what command the
adapter was actually invoked with — never invented fields like ``plan.argv`` or ``plan.file_hashes``,
and never an invented flag encoding like ``--postprocess`` (a candidate postprocessor is represented
as a candidate-specified reporter/plugin hook, e.g. ``-p <plugin>`` / ``--report``, that is not in
the frozen/sanctioned set).

``adapter.validate_invocation`` is a stub today (raises ``NotImplementedError("validate_invocation
lands in S13")`` — see ``src/issueforge/adapters/pytest_adapter.py``); ``contract.verify_contract_
integrity`` does not exist at all yet. Every test therefore calls/imports these INSIDE its body and
is ``@pytest.mark.xfail(strict=True, reason="PENDING (#19)")`` so the unbuilt surface reads as a red
proof (NotImplementedError/AttributeError/ImportError), mirroring ``tests/test_contract.py``.

Detail-token format note (a deliberate, flagged judgment call for surfaces the API contract
describes but does not fully pin): a dangerous mode sourced from a FROZEN CONFIG (``test_config``'s
``addopts``) rather than argv is named ``"addopts:<flag>"`` — this mirrors the canonical
``invocation`` detail example the contract addendum gives verbatim (R1: ``"addopts:-x"``). A
wrapper/config file that drifted at HEAD after being referenced by the frozen command is named by
its repo-relative path (there is no contract-given example for this case; if the real
implementation names it differently, these two tests need a one-line rename, not a redesign).

Fixtures are self-contained (mirroring, never importing, ``tests/test_contract.py``'s
``_provisioner``/``_mk_run``/``_seed_freeze_proof``/``_seed_done_review`` helpers) so this file has
no dependency on that module.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import store

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]
_CLEAN_TOML = 'baseline = ["pytest", "tests/"]\nframework = "pytest"\n'


# ============================================================================= git/repo scaffolding


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True)


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, name: str, files: dict[str, str] | None = None) -> tuple[Path, str]:
    """A real one-commit git repo, with a resolvable ``origin`` default branch at its own HEAD."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base_files = {".issueforge.toml": _CLEAN_TOML}
    base_files.update(files or {})
    sha = _commit(repo, base_files, "base")
    _git(repo, "remote", "add", "origin", "git@github.com:Owner/IssueForge.git")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return repo, sha


def _adapter():
    from issueforge.adapters.base import registry

    adapter = registry.resolve(framework="pytest", reporter="pytest")
    assert adapter is not None, "pytest adapter is not registered"
    return adapter


def _invocation(worktree: Path, command: list, *, env: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        worktree=Path(worktree), interpreter=sys.executable, command=list(command), env=env or {}
    )


def _invocation_with_config(
    worktree: Path, command: list, *, config_source: str, config_content: str
) -> SimpleNamespace:
    """An invocation whose frozen ``test_config`` (per API contract §2/§3: "the frozen manifest's
    ``command``/``test_config`` available to the verify caller") carries a config-sourced
    ``addopts`` line — the vehicle for a dangerous mode smuggled through config rather than argv."""
    return SimpleNamespace(
        worktree=Path(worktree),
        interpreter=sys.executable,
        command=list(command),
        env={},
        test_config={"source": config_source, "content": config_content},
    )


class _ValidateInvocationSpy:
    """Wraps a real adapter, recording every ``validate_invocation`` call while forwarding
    everything else (including the call itself) to the wrapped adapter — used to observe what
    command ``verify_contract_integrity`` actually supplies, per R4 (finding #11): command-from-
    manifest is observable ONLY through ``verify``, via a spy, never by inspecting a returned plan.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[object] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def validate_invocation(self, command: object) -> object:
        self.calls.append(command)
        return self._inner.validate_invocation(command)


# ================================================================ freeze/verify scaffolding (mirror)


def _provisioner():
    """A host-side provisioner seam (mirrors ``test_contract.py:_provisioner``): the host
    interpreter + an allowlist env, plugin autoload disabled so a developer's installed pytest
    plugins cannot alter collection."""
    counter = {"n": 0}

    def _provision(worktree, frozen_deps=None):
        counter["n"] += 1
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        artifact = Path(worktree).parent / f"if-env-{Path(worktree).name}-{counter['n']}"
        return SimpleNamespace(
            interpreter=sys.executable, env=env, artifact_dir=artifact, network=False
        )

    return _provision


def _mk_run(run_id: str) -> str:
    shape = {"classification": "buildable", "write_scope": []}
    record = {
        "status": "running",
        "shape": shape,
        "revision_ledger": {"op-1": "fingerprint"},
    }
    store.RunStore().apply(run_id, lambda _r: record, create=True)
    return run_id


def _seed_freeze_proof(run_id: str, *, base_sha: str, head_sha: str, added_id: str) -> None:
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "red_proof": {
                "accepted": True,
                "reason": "behavioral_red",
                "base_sha": base_sha,
                "head_sha": head_sha,
                "added_ids": [added_id],
                "records": [
                    {
                        "nodeid": added_id,
                        "exception_type": "AssertionError",
                        "assertion_line": 2,
                        "message": "assert 1 == 2",
                    }
                ],
            }
        },
    )


def _seed_done_review(run_id: str, *, head_sha: str) -> None:
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "contract_review": {
                "verdict": "done",
                "outcome": "done",
                "head_sha": head_sha,
                "reviewer_session_id": "rev-1",
                "authoring_session_id": "auth-1",
                "provider": "cli",
                "findings": [],
            }
        },
    )


def _approve_all(_payload) -> bool:
    return True


# ========================================================================================== tests


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_validate_invocation_accepts_clean_manifest_command(tmp_path):
    """A plain, sanctioned command from the manifest is accepted: no raise (R4 clean case — no
    internal-field/return-value inspection, only the absence of a raise is observable/pinned).

    technical: ["pytest", "tests/"] carries no prohibited flag and references no wrapper/config
    beyond the toml itself, so validate_invocation must return WITHOUT raising.
    """
    repo, _base_sha = _repo(
        tmp_path, "clean", {"tests/test_x.py": "def test_x():\n    assert True\n"}
    )
    adapter = _adapter()
    adapter.validate_invocation(_invocation(repo, ["pytest", "tests/"]))


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_verify_contract_integrity_supplies_frozen_manifest_command_not_head(tmp_path):
    """(Finding #11) The FROZEN manifest command drives ``validate_invocation`` through ``verify``,
    never a live re-read of candidate HEAD's ``.issueforge.toml`` — proven observably via a spy on
    ``adapter.validate_invocation``, never by inspecting a returned plan.

    technical: freeze with baseline ``["pytest", "tests/"]``; AFTER freeze, recommit
    ``.issueforge.toml`` at HEAD with a DIVERGENT baseline ``["pytest", "-x", "tests/"]``. Call
    ``verify_contract_integrity`` with a spy-wrapped adapter; the spy's recorded
    ``validate_invocation`` call must carry the FROZEN command, not the diverged HEAD one — a
    wrong-but-plausible impl that re-derives the command from HEAD's live toml would call the spy
    with the diverged, ``-x``-carrying command instead and fail this assertion.
    """
    repo, base_sha = _repo(
        tmp_path, "cmd-from-manifest", {"tests/test_x.py": "def test_x():\n    assert True\n"}
    )
    from issueforge import contract

    run_id = "run-cmd-from-manifest"
    _mk_run(run_id)
    _seed_freeze_proof(
        run_id, base_sha=base_sha, head_sha=base_sha, added_id="tests/test_x.py::test_x"
    )
    _seed_done_review(run_id, head_sha=base_sha)

    manifest_command = ["pytest", "tests/"]
    spy = _ValidateInvocationSpy(_adapter())
    freeze_result = contract.freeze_contract(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=spy,
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert freeze_result.approved is True
    assert freeze_result.manifest["command"] == [manifest_command]

    # Diverge HEAD's committed config AFTER the manifest command was frozen, so the manifest
    # command and HEAD's live toml genuinely disagree.
    _commit(
        repo,
        {".issueforge.toml": 'baseline = ["pytest", "-x", "tests/"]\nframework = "pytest"\n'},
        "diverge-toml",
    )

    spy.calls.clear()
    contract.verify_contract_integrity(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=spy,
        provisioner=_provisioner(),
    )
    assert spy.calls, "verify_contract_integrity never invoked adapter.validate_invocation"
    supplied_commands = [tuple(getattr(call, "command", ())) for call in spy.calls]
    assert tuple(manifest_command) in supplied_commands
    assert ("pytest", "-x", "tests/") not in supplied_commands


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_verify_contract_integrity_detects_wrapper_drift_as_invocation_violation(tmp_path):
    """(Finding #10) A wrapper script referenced by the FROZEN command that drifts at HEAD (after
    the freeze) is caught by re-running ``validate_invocation`` through ``verify`` — never proven
    by observing two fresh hashes from direct adapter calls.

    technical: ``scripts/wrap.py`` (a real, working delegator to ``sys.executable -m pytest``) is
    named in the frozen baseline command and lives OUTSIDE ``contract_paths`` (it holds no
    collected test and is not imported by one, so the general ``protected_path_diff``/``dep_hash``
    mechanism does not already cover it) — isolating that the drift is caught BY
    ``validate_invocation``'s own reference-hashing, not by the unrelated contract-diff gate.
    After freeze, ``scripts/wrap.py`` is recommitted with different content at HEAD;
    ``verify_contract_integrity(...).ok is False`` and ``.violations`` contains an exact
    ``("invocation", "scripts/wrap.py")`` entry.
    """
    wrap_v1 = (
        "import subprocess\nimport sys\n\n"
        "sys.exit(subprocess.run([sys.executable, '-m', 'pytest', *sys.argv[1:]]).returncode)\n"
    )
    repo, base_sha = _repo(
        tmp_path,
        "wrapper-drift",
        {
            "scripts/wrap.py": wrap_v1,
            "tests/test_x.py": "def test_x():\n    assert True\n",
            ".issueforge.toml": (
                f'baseline = ["{sys.executable}", "scripts/wrap.py", "tests/"]\n'
                'framework = "pytest"\n'
            ),
        },
    )
    from issueforge import contract

    run_id = "run-wrapper-drift"
    _mk_run(run_id)
    _seed_freeze_proof(
        run_id, base_sha=base_sha, head_sha=base_sha, added_id="tests/test_x.py::test_x"
    )
    _seed_done_review(run_id, head_sha=base_sha)

    adapter = _adapter()
    freeze_result = contract.freeze_contract(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert freeze_result.approved is True
    assert "scripts/wrap.py" not in freeze_result.manifest["contract_paths"]

    wrap_v2 = wrap_v1.replace("sys.argv[1:]", "sys.argv[1:]  # drifted")
    _commit(repo, {"scripts/wrap.py": wrap_v2}, "mutate-wrapper-post-freeze")

    report = contract.verify_contract_integrity(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
    )
    assert report.ok is False
    assert any(
        v.predicate == "invocation" and v.detail == "scripts/wrap.py" for v in report.violations
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_verify_contract_integrity_detects_custom_config_drift_as_invocation_violation(tmp_path):
    """(Finding #2, confirmation round) A custom config file REFERENCED by the frozen command via
    ``-c`` that is OTHERWISE UNPROTECTED (not a member of ``contract_paths`` on its own — it holds
    no collected test and is not imported by one, so the general ``protected_path_diff``/``dep_hash``
    mechanism does not already cover it) is caught by re-running ``validate_invocation`` through
    ``verify`` — never fabricated by a diff-only impl that happens to also protect the file.

    Deliberately NOT ``pytest.ini`` / ``.issueforge.toml``: those already sit inside
    ``contract_paths`` on their own, so mutating them would make a diff-only implementation pass
    this test for the wrong reason. ``configs/contract.ini`` is a standalone config file outside
    the collected/imported closure, isolating that the invocation layer freezes+compares its
    referenced config independently of the diff gate.

    technical: baseline command is ``["pytest", "-c", "configs/contract.ini", "tests/"]`` with
    ``configs/contract.ini`` holding a harmless ``[pytest]`` section. freeze_contract approves and
    ``configs/contract.ini`` is confirmed NOT in ``freeze_result.manifest["contract_paths"]``.
    After freeze, ``configs/contract.ini`` is recommitted with different content at HEAD;
    ``verify_contract_integrity(...).ok is False`` and ``.violations`` contains an exact
    ``("invocation", "configs/contract.ini")`` entry.
    """
    config_v1 = "[pytest]\n"
    repo, base_sha = _repo(
        tmp_path,
        "config-drift",
        {
            "configs/contract.ini": config_v1,
            "tests/test_x.py": "def test_x():\n    assert True\n",
            ".issueforge.toml": (
                'baseline = ["pytest", "-c", "configs/contract.ini", "tests/"]\n'
                'framework = "pytest"\n'
            ),
        },
    )
    from issueforge import contract

    run_id = "run-config-drift"
    _mk_run(run_id)
    _seed_freeze_proof(
        run_id, base_sha=base_sha, head_sha=base_sha, added_id="tests/test_x.py::test_x"
    )
    _seed_done_review(run_id, head_sha=base_sha)

    adapter = _adapter()
    freeze_result = contract.freeze_contract(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert freeze_result.approved is True
    assert "configs/contract.ini" not in freeze_result.manifest["contract_paths"]

    config_v2 = "[pytest]\naddopts = -x\n"
    _commit(repo, {"configs/contract.ini": config_v2}, "mutate-config-post-freeze")

    report = contract.verify_contract_integrity(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
    )
    assert report.ok is False
    assert any(
        v.predicate == "invocation" and v.detail == "configs/contract.ini"
        for v in report.violations
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_validate_invocation_rejects_candidate_postprocessor_via_reporter_hook(tmp_path):
    """(Finding #12) A candidate-specified postprocessor is represented as a candidate-specified
    reporter/plugin/report hook — NOT the invented ``--postprocess`` flag — and rejected.

    technical: command carries ``-p postprocess_plugin`` (a plugin name not in the frozen/
    sanctioned set) -> ValueError naming it.
    """
    repo, _base_sha = _repo(
        tmp_path,
        "postprocess",
        {"tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    command = ["pytest", "tests/", "-p", "postprocess_plugin"]
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, command))
    assert "postprocess_plugin" in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    "flag_tokens,needle",
    [
        (["-n", "4"], "-n"),
        (["-n4"], "-n"),
        (["--dist=load"], "--dist"),
        (["--dist", "load"], "--dist"),
    ],
)
def test_validate_invocation_rejects_sharding_xdist(tmp_path, flag_tokens, needle):
    """Sharding/xdist flags are prohibited-or-modelled: ``-n`` and ``--dist`` both raise, each
    naming the offending flag.
    """
    repo, _base_sha = _repo(
        tmp_path,
        f"shard-{needle.strip('-')}",
        {"tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    command = ["pytest", "tests/", *flag_tokens]
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, command))
    assert needle in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    "flag_tokens,needle",
    [(["-x"], "-x"), (["--maxfail=1"], "--maxfail"), (["--maxfail", "1"], "--maxfail")],
)
def test_validate_invocation_rejects_bail(tmp_path, flag_tokens, needle):
    """Bail flags are prohibited-or-modelled: ``-x`` and ``--maxfail`` both raise, each naming the
    offending flag.
    """
    repo, _base_sha = _repo(
        tmp_path,
        f"bail-{needle.strip('-')}",
        {"tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    command = ["pytest", "tests/", *flag_tokens]
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, command))
    assert needle in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_validate_invocation_rejects_force_exit(tmp_path):
    """The force-exit mode is prohibited-or-modelled: ``--force-exit`` raises, naming the flag."""
    repo, _base_sha = _repo(
        tmp_path, "force-exit", {"tests/test_x.py": "def test_x():\n    assert True\n"}
    )
    command = ["pytest", "tests/", "--force-exit"]
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, command))
    assert "--force-exit" in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_validate_invocation_rejects_pass_with_no_tests(tmp_path):
    """The pass-with-no-tests mode is prohibited-or-modelled: ``--suppress-no-test-exit-code``
    raises, naming the flag.
    """
    repo, _base_sha = _repo(
        tmp_path, "no-tests-ok", {"tests/test_x.py": "def test_x():\n    assert True\n"}
    )
    command = ["pytest", "tests/", "--suppress-no-test-exit-code"]
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, command))
    assert "--suppress-no-test-exit-code" in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    "flag_tokens,needle",
    [
        (["-p", "myreporter"], "myreporter"),
        (["--report=json"], "--report"),
        (["--report", "json"], "--report"),
    ],
)
def test_validate_invocation_rejects_custom_reporter(tmp_path, flag_tokens, needle):
    """Custom reporters are prohibited-or-modelled: ``-p <reporter>`` and ``--report`` both raise,
    each naming the offending flag/plugin.
    """
    repo, _base_sha = _repo(
        tmp_path,
        f"reporter-{needle.strip('-')}",
        {"tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    command = ["pytest", "tests/", *flag_tokens]
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, command))
    assert needle in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize("flag_tokens", [["--reruns=3"], ["--reruns", "3"]])
def test_validate_invocation_rejects_rerun_plugin_but_allows_disable_form(tmp_path, flag_tokens):
    """A rerun/retry plugin flag is prohibited-or-modelled (both combined and split spellings);
    the harmless ``-p no:randomly`` DISABLE form is explicitly fine (per the contract) and must
    not raise.

    technical: ``--reruns=3`` / ``--reruns 3`` -> ValueError naming ``--reruns``;
    ``-p no:randomly`` alone -> no raise.
    """
    repo, _base_sha = _repo(
        tmp_path,
        f"rerun-{'-'.join(flag_tokens).replace('=', '')}",
        {"tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, ["pytest", "tests/", *flag_tokens]))
    assert "--reruns" in str(excinfo.value)

    adapter.validate_invocation(_invocation(repo, ["pytest", "tests/", "-p", "no:randomly"]))


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_prohibited_mode_via_verify_contract_integrity_surfaces_as_invocation_violation(tmp_path):
    """A prohibited pytest mode that made it into a FROZEN manifest (freeze_contract itself does
    not gate on validate_invocation — it composes the command straight from the committed toml)
    is caught downstream: ``verify_contract_integrity`` re-validates the frozen command through
    ``validate_invocation`` and surfaces the rejection as an ``IntegrityViolation`` predicate
    ``"invocation"``.

    technical: candidate toml declares ``baseline = ["-m", "pytest", "tests/", "-x"]`` (bail, an
    inert flag at collection time so the freeze's own ``--collect-only`` still succeeds cleanly).
    freeze_contract approves and persists a manifest carrying that command verbatim.
    verify_contract_integrity(...).ok is False and .violations contains a predicate == "invocation"
    entry — never silently ok, never collapsed into a different predicate.
    """
    repo, base_sha = _repo(
        tmp_path,
        "integ-invocation",
        {
            "tests/test_new.py": "def test_new():\n    assert 1 == 2\n",
            ".issueforge.toml": 'baseline = ["-m", "pytest", "tests/", "-x"]\nframework = "pytest"\n',
        },
    )
    from issueforge import contract

    run_id = "run-integ-invocation"
    _mk_run(run_id)
    _seed_freeze_proof(
        run_id, base_sha=base_sha, head_sha=base_sha, added_id="tests/test_new.py::test_new"
    )
    _seed_done_review(run_id, head_sha=base_sha)

    adapter = _adapter()
    freeze_result = contract.freeze_contract(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert freeze_result.approved is True

    report = contract.verify_contract_integrity(
        run_id,
        candidate_worktree=repo,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
    )
    assert report.ok is False
    assert any(v.predicate == "invocation" and v.detail == "-x" for v in report.violations)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    "addopts_value,needle",
    [
        ("-n 4", "-n"),
        ("--dist=load", "--dist"),
        ("-x", "-x"),
        ("--maxfail=1", "--maxfail"),
        ("--force-exit", "--force-exit"),
        ("--suppress-no-test-exit-code", "--suppress-no-test-exit-code"),
        ("-p myreporter", "myreporter"),
        ("--report=json", "--report"),
        ("--reruns=3", "--reruns"),
    ],
)
def test_validate_invocation_rejects_dangerous_modes_from_frozen_config_addopts(
    tmp_path, addopts_value, needle
):
    """(Finding #14) Each dangerous mode is ALSO covered when supplied via a FROZEN CONFIG SOURCE
    (``pytest.ini`` ``addopts``, carried on the invocation's ``test_config`` — API contract §3's
    "frozen manifest's ``command``/``test_config`` available to the verify caller"), not only argv.

    technical: the command itself is clean (``["pytest", "tests/"]``); the dangerous mode arrives
    solely via ``test_config`` = ``{"source": "pytest.ini", "content": "[pytest]\\naddopts = <mode>\\n"}``.
    ``validate_invocation`` must still raise, naming the config-sourced token
    ``"addopts:<needle>"`` (R1's canonical config-sourced detail format) — a wrong-but-plausible
    impl that only inspects argv and ignores ``test_config`` would silently accept this.
    """
    repo, _base_sha = _repo(
        tmp_path,
        f"cfg-{needle.strip('-:')}",
        {"tests/test_x.py": "def test_x():\n    assert True\n"},
    )
    invocation = _invocation_with_config(
        repo,
        ["pytest", "tests/"],
        config_source="pytest.ini",
        config_content=f"[pytest]\naddopts = {addopts_value}\n",
    )
    adapter = _adapter()
    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(invocation)
    assert f"addopts:{needle}" in str(excinfo.value)


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_validate_invocation_never_invokes_the_ai_provider(tmp_path, monkeypatch):
    """(Finding #5, confirmation round / R7) ``validate_invocation`` is fully deterministic — it
    never delegates to the AI/provider seam, on EITHER the clean-accept path or the
    rejected-outright path.

    technical: ``issueforge.providers.invoke`` is monkeypatched to raise AssertionError if called.
    A CLEAN call (``["pytest", "tests/"]``) returns without raising the AssertionError; a REJECTED
    call (``["pytest", "tests/", "-x"]``) raises ``ValueError`` naming ``-x`` without ever tripping
    the provider seam.
    """
    import issueforge.providers as providers

    def _no_provider(*args, **kwargs):
        raise AssertionError("validate_invocation must never invoke the AI/provider seam")

    monkeypatch.setattr(providers, "invoke", _no_provider)

    repo, _base_sha = _repo(
        tmp_path, "no-ai", {"tests/test_x.py": "def test_x():\n    assert True\n"}
    )
    adapter = _adapter()

    adapter.validate_invocation(_invocation(repo, ["pytest", "tests/"]))

    with pytest.raises(ValueError) as excinfo:
        adapter.validate_invocation(_invocation(repo, ["pytest", "tests/", "-x"]))
    assert "-x" in str(excinfo.value)
