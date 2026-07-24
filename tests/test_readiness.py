"""Committed PENDING acceptance suite for #112 (PoC-B) — the local-candidate readiness verdict.

Owns "given a persisted local-candidate run record, issue ONE deterministic ``ready`` /
``not_ready`` verdict, persisted as the nested record field ``record['readiness']``". The verdict
is a pure conjunction of five predicates, each named in a ``not_ready`` verdict so the failure is
never a generic ``False``:

    candidate_sha  — the record's ``candidate_sha`` is a present, non-symbolic, existing COMMIT
                     object that MATCHES the clean worktree HEAD, and its worktree is clean (no
                     tracked change AND no untracked source file). The locked ``detail`` values for a
                     candidate_sha refusal are ``missing`` / ``symbolic`` / ``non_commit`` / ``dirty``
                     / ``stale`` (``stale`` = candidate_sha names a commit other than the clean HEAD).
    acceptance     — the EXACT ``acceptance_command`` runs GREEN against the candidate.
    baseline       — the EXACT ``baseline_command`` runs GREEN against the candidate, SEPARATELY.
    contract_integrity — the existing ``contract.verify_contract_integrity`` seam reports ``ok``.
    scope          — every implementation-range changed path is inside the persisted
                     ``write_scope`` (the scope comes from the approved record, NEVER the diff).

``issueforge.verify.issue_readiness`` does not exist yet, so every test imports it inside its body
and is ``@pytest.mark.xfail(strict=True, reason="PENDING (#112)")``. The git/record scaffolding
below touches no not-yet-built IssueForge code, so it lives at module top.

Design contract the build implements (locked defaults, issue #112):
  * ``issue_readiness(run_id, *, adapter, provisioner=None, store=None, run_baseline=None,
    verify_integrity=None) -> dict`` returns the verdict dict AND persists the identical dict at
    ``record['readiness']`` via ``store.apply`` (a nested field; store.py/state.py are NOT edited,
    no new top-level run ``status``).
  * ``run_baseline`` / ``verify_integrity`` are injectable seams. When ``verify_integrity`` is not
    injected the readiness verdict CONSUMES the PUBLIC ``contract.verify_contract_integrity`` seam,
    resolved at call time (so replacing that public attribute is observed) — it never reimplements
    contract integrity. The build MAY name its internal default however it likes; the contract is the
    observable public-seam delegation, not a private constant.
  * The implementation range for the scope predicate is ``contract_commit..candidate_sha`` (the
    changes made AFTER the contract froze), NEVER ``base_sha..candidate_sha`` — otherwise the
    frozen acceptance-test commit would show up as an out-of-scope change.
  * The ``ready`` verdict is the verbatim JSON from the issue body:
    ``{"readiness":"ready","ready_sha":<candidate_sha>,"acceptance":"green","baseline":"green",
    "scope":"clean","contract_integrity":"clean"}``.
  * A ``not_ready`` verdict is ``{"readiness":"not_ready","predicate":<name>,"detail":<specifics>}``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]

RUN_ID = "run-112"
# The exact commands as they live in the persisted record (interpreter supplied by the runner).
ACCEPTANCE_COMMAND = ["-m", "pytest", "tests/test_accept.py"]
BASELINE_COMMAND = ["-m", "pytest"]
# The frozen contract path (the acceptance test committed at contract_commit) and the approved
# implementation write scope.
CONTRACT_PATHS = ["tests/test_accept.py"]
WRITE_SCOPE = ["src/app.py"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _rev_parse(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _candidate_repo(
    root: Path,
    *,
    name: str = "candidate",
    extra_impl_paths: tuple[str, ...] = (),
) -> tuple[Path, str, str, str]:
    """Build a candidate repo with three commits and return ``(repo, base, contract_commit, cand)``.

    base_sha          — the green-baseline commit (``src/app.py`` + ``tests/test_app.py``).
    contract_commit   — freezes the acceptance test ``tests/test_accept.py`` (a change to a path
                        that is NOT in ``WRITE_SCOPE``; it must NOT count against scope).
    candidate_sha     — the implementation commit: edits ``src/app.py`` (in scope), plus any
                        ``extra_impl_paths`` (used to force an out-of-scope change).
    """
    repo = root / name
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "app.py").write_text("VALUE = 1\n")
    (repo / "tests" / "test_app.py").write_text(
        "from src.app import VALUE\n\n\ndef test_value():\n    assert VALUE\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _rev_parse(repo, "HEAD")

    (repo / "tests" / "test_accept.py").write_text("def test_ready():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "freeze contract")
    contract_commit = _rev_parse(repo, "HEAD")

    (repo / "src" / "app.py").write_text("VALUE = 2\n")
    for rel in extra_impl_paths:
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text("EXTRA = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "impl")
    candidate = _rev_parse(repo, "HEAD")
    return repo, base, contract_commit, candidate


def _persist_record(
    run_id: str,
    *,
    repo: Path,
    base: str,
    contract_commit: str,
    candidate: str | None,
    write_scope: list[str] = WRITE_SCOPE,
    acceptance: list[str] = ACCEPTANCE_COMMAND,
    baseline: list[str] = BASELINE_COMMAND,
    contract_paths: list[str] = CONTRACT_PATHS,
) -> dict:
    """Persist a run record carrying exactly the fields #112 consumes read-only (the schema #114
    persists). ``candidate=None`` omits ``candidate_sha`` entirely (the 'missing' case)."""
    from issueforge.store import RunStore

    fields: dict = {
        "run_id": run_id,
        "base_sha": base,
        "contract_commit": contract_commit,
        "candidate_worktree": str(repo),
        "write_scope": list(write_scope),
        "contract_paths": list(contract_paths),
        "acceptance_command": list(acceptance),
        "baseline_command": list(baseline),
        "evidence": {"status": "green"},
    }
    if candidate is not None:
        fields["candidate_sha"] = candidate
    RunStore().apply(run_id, lambda _rec: fields, create=True)
    return fields


def _adapter():
    from issueforge.adapters.pytest_adapter import PytestAdapter

    return PytestAdapter()


def _provisioner():
    """A hermetic provisioner seam (forwarded to the injected runner/integrity seams, unused here)."""
    import os
    from types import SimpleNamespace

    def _provision(worktree, frozen_deps=None):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        return SimpleNamespace(interpreter=sys.executable, env=env, network=False)

    return _provision


def _runner(*, red_command: list[str] | None = None):
    """An injectable ``run_baseline`` seam that classifies GREEN, except for ``red_command`` which
    classifies BEHAVIORAL_RED. Records every call as ``(worktree, command)`` on ``.calls``."""
    from types import SimpleNamespace

    from issueforge.adapters.base import BaselineStatus

    calls: list[SimpleNamespace] = []

    def _rb(worktree, command, *, adapter=None, provisioner=None):
        calls.append(SimpleNamespace(worktree=Path(worktree), command=list(command)))
        status = (
            BaselineStatus.BEHAVIORAL_RED
            if red_command is not None and list(command) == list(red_command)
            else BaselineStatus.GREEN
        )
        return SimpleNamespace(status=status)

    _rb.calls = calls
    return _rb


def _integrity(*, ok: bool = True, violations: tuple = ()):
    """An injectable ``verify_integrity`` seam returning a real ``IntegrityReport``. Records the
    kwargs it was called with on ``.calls``."""
    from types import SimpleNamespace

    from issueforge.contract import IntegrityReport

    calls: list[SimpleNamespace] = []

    def _vi(run_id, *, candidate_worktree, base_sha, adapter, provisioner, store=None, **kw):
        calls.append(
            SimpleNamespace(
                run_id=run_id,
                candidate_worktree=Path(candidate_worktree),
                base_sha=base_sha,
                adapter=adapter,
                provisioner=provisioner,
            )
        )
        return IntegrityReport(ok=ok, violations=tuple(violations))

    _vi.calls = calls
    return _vi


def _read_readiness(run_id: str) -> dict:
    from issueforge.store import RunStore

    return RunStore().read(run_id)["readiness"]


# ============================================================= candidate_sha validity predicate


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_missing_candidate_sha_is_not_ready(tmp_path):
    """A record with no ``candidate_sha`` is refused before anything runs.

    technical (contract): a persisted record omitting ``candidate_sha`` -> issue_readiness returns
    and persists ``{"readiness":"not_ready","predicate":"candidate_sha","detail":"missing"}``.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=None)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "missing"
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_symbolic_candidate_sha_is_not_ready(tmp_path):
    """A branch NAME (symbolic ref) that resolves to the candidate commit is still refused — the
    verdict must bind an immutable commit id, not a movable ref.

    technical (contract): ``candidate_sha == "main"`` (resolves to the impl commit) ->
    ``not_ready`` with predicate ``candidate_sha`` and detail ``symbolic``. A naive
    ``git rev-parse`` impl that accepts any resolvable ref fails this.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate="main")

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "symbolic"
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_non_commit_candidate_sha_is_not_ready(tmp_path):
    """A 40-hex object id that names a TREE (not a commit) is refused.

    technical (contract): ``candidate_sha`` set to ``rev-parse candidate^{tree}`` (a real, existing
    object id whose type is ``tree``) -> ``not_ready`` with predicate ``candidate_sha`` and detail
    ``non_commit``. An impl that only checks 40-hex + existence fails this.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    tree_sha = _rev_parse(repo, f"{cand}^{{tree}}")
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=tree_sha)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "non_commit"
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_dirty_candidate_worktree_is_not_ready(tmp_path):
    """A valid candidate commit whose worktree carries an uncommitted tracked change is refused —
    a dirty tree is not the exact committed state the verdict certifies.

    technical (contract): ``candidate_sha`` is the real impl commit but ``src/app.py`` is modified
    without committing -> ``not_ready`` with predicate ``candidate_sha`` and detail ``dirty``.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    (repo / "src" / "app.py").write_text("VALUE = 999  # uncommitted\n")

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "dirty"
    assert _read_readiness(RUN_ID) == verdict


# ============================================================= acceptance / baseline predicates


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_acceptance_command_must_be_green(tmp_path):
    """When the EXACT acceptance command is not GREEN against the candidate, the verdict is
    ``not_ready`` naming the acceptance predicate.

    technical (contract): the injected runner classifies BEHAVIORAL_RED for the acceptance command
    (GREEN for the baseline) -> ``not_ready`` with predicate ``acceptance``. The runner is called
    with the record's exact ``acceptance_command`` against the candidate worktree.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    runner = _runner(red_command=ACCEPTANCE_COMMAND)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=runner,
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "acceptance"
    # The acceptance command was actually run, verbatim, against the candidate worktree.
    ran = {tuple(c.command) for c in runner.calls}
    assert tuple(ACCEPTANCE_COMMAND) in ran
    assert all(c.worktree == repo for c in runner.calls)
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_baseline_command_must_be_green(tmp_path):
    """When the EXACT full baseline command is not GREEN against the candidate, the verdict is
    ``not_ready`` naming the baseline predicate.

    technical (contract): the injected runner classifies BEHAVIORAL_RED for the baseline command
    (GREEN for acceptance) -> ``not_ready`` with predicate ``baseline``.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    runner = _runner(red_command=BASELINE_COMMAND)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=runner,
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "baseline"
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_acceptance_and_baseline_run_separately_with_exact_commands(tmp_path):
    """Acceptance and baseline are two SEPARATE runs, each launched with the record's exact command
    against the candidate worktree — not one run, and not a re-derived command.

    technical (contract): a GREEN runner over a ready record -> exactly the two record commands were
    run ({acceptance_command, baseline_command}), each in a distinct call, each in the candidate
    worktree.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    runner = _runner()

    issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=runner,
        verify_integrity=_integrity(),
    )
    ran = sorted(tuple(c.command) for c in runner.calls)
    assert ran == sorted([tuple(ACCEPTANCE_COMMAND), tuple(BASELINE_COMMAND)])
    assert all(c.worktree == repo for c in runner.calls)


# ============================================================= contract-integrity predicate


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_contract_integrity_uses_the_existing_seam(tmp_path):
    """The contract-integrity predicate is delegated to the injected integrity seam — readiness
    never reimplements integrity.

    technical (contract): the injected seam is called exactly once with the run_id, the candidate
    worktree, the record's base_sha, and the adapter/provisioner. (The NO-INJECTION default path —
    that the public ``contract.verify_contract_integrity`` seam is the call-time default — is proven
    by ``test_default_integrity_seam_is_the_public_contract_function`` below; this suite pins no
    private default-constant name, so a build may name its internal default however it likes.)
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    adapter = _adapter()
    provisioner = _provisioner()
    integrity = _integrity(ok=True)

    issue_readiness(
        RUN_ID,
        adapter=adapter,
        provisioner=provisioner,
        run_baseline=_runner(),
        verify_integrity=integrity,
    )
    assert len(integrity.calls) == 1
    call = integrity.calls[0]
    assert call.run_id == RUN_ID
    assert call.candidate_worktree == repo
    assert call.base_sha == base
    assert call.adapter is adapter
    assert call.provisioner is provisioner


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_contract_integrity_failure_names_the_predicate(tmp_path):
    """When the integrity seam reports a violation, the verdict is ``not_ready`` naming the
    contract_integrity predicate and surfacing the underlying violation — never a generic false.

    technical (contract): the seam returns ``IntegrityReport(ok=False,
    violations=(IntegrityViolation("dirty_worktree","tests/test_accept.py"),))`` -> ``not_ready``
    with predicate ``contract_integrity`` and a detail that references the fired violation
    (``dirty_worktree`` / ``tests/test_accept.py``).
    """
    from issueforge.contract import IntegrityViolation
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    viol = IntegrityViolation(predicate="dirty_worktree", detail="tests/test_accept.py")

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(ok=False, violations=(viol,)),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "contract_integrity"
    detail = str(verdict["detail"])
    assert "dirty_worktree" in detail
    assert "tests/test_accept.py" in detail
    assert _read_readiness(RUN_ID) == verdict


# ============================================================= scope predicate


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_out_of_scope_change_is_not_ready_and_scope_comes_from_approval(tmp_path):
    """A candidate that changes a path outside the approved ``write_scope`` is refused — and the
    scope set comes from the persisted approval, NEVER from the diff itself.

    technical (contract): the impl commit changes ``src/app.py`` (in scope) AND ``src/secret.py``
    (NOT in ``write_scope == ["src/app.py"]``) -> ``not_ready`` with predicate ``scope`` and a
    detail naming ``src/secret.py``. An impl that derives scope from the diff would find every
    changed path trivially in-scope and wrongly pass.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path, extra_impl_paths=("src/secret.py",))
    _persist_record(
        RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand, write_scope=["src/app.py"]
    )

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "scope"
    assert "src/secret.py" in str(verdict["detail"])
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_scope_range_is_contract_commit_not_base(tmp_path):
    """The implementation range is ``contract_commit..candidate_sha``: the frozen acceptance-test
    change made between ``base_sha`` and ``contract_commit`` must NOT count against scope.

    technical (contract): ``tests/test_accept.py`` changed between base and contract_commit and is
    NOT in ``write_scope``, while the impl commit only changes ``src/app.py`` (in scope) -> the
    scope predicate is clean and the verdict is ``ready``. An impl that diffs ``base_sha..candidate``
    would flag ``tests/test_accept.py`` and wrongly return ``not_ready``.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(
        RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand, write_scope=["src/app.py"]
    )

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "ready"
    assert verdict["scope"] == "clean"


# ============================================================= the ready verdict + invariants


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_all_predicates_pass_persists_the_verbatim_ready_verdict(tmp_path):
    """Only a candidate satisfying every predicate is ``ready``, and the persisted verdict is the
    exact JSON the issue specifies.

    technical (contract): valid clean commit + GREEN acceptance + GREEN baseline + ok integrity +
    in-scope diff -> the returned verdict AND ``record['readiness']`` both equal
    ``{"readiness":"ready","ready_sha":<candidate_sha>,"acceptance":"green","baseline":"green",
    "scope":"clean","contract_integrity":"clean"}`` (verbatim example fixture).
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    expected = {
        "readiness": "ready",
        "ready_sha": cand,
        "acceptance": "green",
        "baseline": "green",
        "scope": "clean",
        "contract_integrity": "clean",
    }
    assert verdict == expected
    assert _read_readiness(RUN_ID) == expected


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_ready_verdict_binds_the_exact_candidate_sha(tmp_path):
    """Changing the candidate SHA invalidates the verdict: ``ready_sha`` binds the EXACT commit the
    verdict certified, so a verdict for one candidate can never be reused for another.

    technical (contract): two distinct candidate commits, run separately -> each ``ready`` verdict's
    ``ready_sha`` equals its own ``candidate_sha`` (the two differ). A verdict is per-sha, not a
    reusable flag.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand_a = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand_a)
    verdict_a = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict_a["ready_sha"] == cand_a

    # Advance the candidate to a NEW commit (still in scope) and re-run.
    (repo / "src" / "app.py").write_text("VALUE = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "impl-2")
    cand_b = _rev_parse(repo, "HEAD")
    assert cand_b != cand_a
    from issueforge.store import RunStore

    RunStore().apply(RUN_ID, lambda _rec: {"candidate_sha": cand_b})
    verdict_b = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict_b["ready_sha"] == cand_b
    assert verdict_b["ready_sha"] != verdict_a["ready_sha"]


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_readiness_performs_no_provider_push_pr_merge_or_cleanup(tmp_path, monkeypatch):
    """This slice issues a verdict and nothing else: no provider invocation, no push/PR/merge, no
    cleanup. It leaves the candidate repo untouched.

    technical (contract): every callable in ``issueforge.github`` and ``issueforge.providers`` is
    replaced with a raiser; a full ``ready`` run completes without tripping any of them, the
    candidate HEAD is unchanged, and no new branch was created.
    """
    import issueforge.github as gh
    import issueforge.providers as pv
    from issueforge.verify import issue_readiness

    def _raiser(*_a, **_k):
        raise AssertionError("readiness must not invoke a provider / push / PR / merge")

    for mod in (gh, pv):
        for attr, value in list(vars(mod).items()):
            if callable(value) and not isinstance(value, type):
                monkeypatch.setattr(mod, attr, _raiser, raising=False)

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    branches_before = _git(repo, "branch", "--list").stdout

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "ready"
    assert _rev_parse(repo, "HEAD") == cand
    assert _git(repo, "branch", "--list").stdout == branches_before


# ===================================================== candidate_sha: divergence + unknown object


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_candidate_sha_stale_vs_clean_head_is_not_ready(tmp_path):
    """A ``candidate_sha`` naming a commit OTHER than the clean worktree HEAD is refused — the
    verdict certifies the state the worktree actually holds, so a stale field can never bind.

    technical (contract): the persisted ``candidate_sha`` is commit A while the worktree (clean, no
    uncommitted change) has advanced to commit B (``HEAD == B != A``) -> ``not_ready`` with predicate
    ``candidate_sha`` and detail ``stale``. An impl that runs every check against HEAD (B) but binds
    ``ready_sha`` to the persisted field (A) would wrongly pass — the field and HEAD must AGREE.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand_a = _candidate_repo(tmp_path)
    # Advance HEAD to a NEW clean commit B; persist the OLD commit A as candidate_sha.
    (repo / "src" / "app.py").write_text("VALUE = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "impl-2")
    cand_b = _rev_parse(repo, "HEAD")
    assert cand_b != cand_a
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand_a)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "stale"
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_unknown_full_length_sha_is_not_ready(tmp_path):
    """A syntactically valid 40-hex object id that does NOT resolve to any object is refused as a
    non-commit — distinct from the existing TREE-object case, and it must not crash the lookup.

    technical (contract): ``candidate_sha`` is a full-length 40-hex string naming no existing object
    -> ``not_ready`` with predicate ``candidate_sha`` and detail ``non_commit`` (the same locked
    detail as a non-commit tree object: neither names a commit). An impl that only checks 40-hex
    shape, or that lets ``git cat-file``/``rev-parse`` raise on the missing object, fails this.
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    unknown_sha = "1" * 40  # valid hex shape, resolves to no object in this repo
    assert unknown_sha != cand
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=unknown_sha)

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "non_commit"
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_untracked_source_file_is_dirty_and_not_ready(tmp_path):
    """An UNTRACKED source file in the candidate worktree makes it dirty and is refused — the tree is
    not the exact committed state the verdict certifies.

    technical (contract): ``candidate_sha`` is the real impl commit and HEAD matches it, but a new
    UNTRACKED file ``src/untracked.py`` (a real source file, not a cache/ignored artifact) sits in the
    worktree -> ``not_ready`` with predicate ``candidate_sha`` and detail ``dirty``. An impl that
    probes cleanliness with ``git diff --quiet HEAD`` (which IGNORES untracked files) would wrongly
    declare the tree clean; a correct impl uses ``git status --porcelain`` (or equivalent).
    """
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    (repo / "src" / "untracked.py").write_text("EXTRA = 1  # untracked, uncommitted\n")

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "candidate_sha"
    assert verdict["detail"] == "dirty"
    assert _read_readiness(RUN_ID) == verdict


# ===================================================== scope: full range + persisted provenance


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_scope_spans_the_full_contract_range_not_just_the_last_commit(tmp_path):
    """The scope diff covers the ENTIRE ``contract_commit..candidate_sha`` range, not just the tip
    commit — an out-of-scope change in an EARLIER post-contract commit is still caught.

    technical (contract): there are TWO post-contract impl commits: the FIRST changes ``src/app.py``
    (in scope) AND ``src/secret.py`` (out of scope), the SECOND (the candidate) changes only
    ``src/app.py`` (in scope). Diffing ``contract_commit..candidate`` surfaces ``src/secret.py``;
    diffing only ``candidate^..candidate`` would miss it. -> ``not_ready`` with predicate ``scope``
    and a detail naming ``src/secret.py``. An impl that diffs only the tip commit wrongly passes.
    """
    from issueforge.verify import issue_readiness

    # First post-contract commit carries the out-of-scope change (src/secret.py).
    repo, base, cc, _cand1 = _candidate_repo(tmp_path, extra_impl_paths=("src/secret.py",))
    # Second post-contract commit (the candidate) touches only the in-scope src/app.py.
    (repo / "src" / "app.py").write_text("VALUE = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "impl-2 (in-scope only)")
    cand = _rev_parse(repo, "HEAD")
    # Sanity: the tip commit alone changed only the in-scope path.
    tip_only = _git(repo, "diff", "--name-only", f"{cand}^..{cand}").stdout.split()
    assert tip_only == ["src/app.py"]
    _persist_record(
        RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand, write_scope=["src/app.py"]
    )

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "scope"
    assert "src/secret.py" in str(verdict["detail"])
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_scope_verdict_flips_with_the_persisted_scope_for_one_changed_path(tmp_path):
    """The scope set comes from the PERSISTED approval, proven by running the SAME changed path
    against two different persisted scopes and getting OPPOSITE verdicts.

    technical (contract): one candidate changes ``src/app.py`` AND a path unlike any module constant,
    ``lib/widget.py``. Persisted scope A approves both paths -> ``ready``; re-persisting scope B that
    OMITS ``lib/widget.py`` (same commit, same diff) -> ``not_ready`` predicate ``scope`` naming
    ``lib/widget.py``. An impl with a hardcoded/default scope, or one that derives scope from the
    diff, cannot produce both verdicts for the identical changed path.
    """
    from issueforge.store import RunStore
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path, extra_impl_paths=("lib/widget.py",))

    # Scope A approves the distinctive path -> ready.
    _persist_record(
        RUN_ID,
        repo=repo,
        base=base,
        contract_commit=cc,
        candidate=cand,
        write_scope=["src/app.py", "lib/widget.py"],
    )
    verdict_a = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict_a["readiness"] == "ready"
    assert verdict_a["scope"] == "clean"

    # Scope B excludes the distinctive path (same commit / same diff) -> not_ready naming it.
    RunStore().apply(RUN_ID, lambda _rec: {"write_scope": ["src/app.py"]})
    verdict_b = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict_b["readiness"] == "not_ready"
    assert verdict_b["predicate"] == "scope"
    assert "lib/widget.py" in str(verdict_b["detail"])
    assert _read_readiness(RUN_ID) == verdict_b


# ===================================================== contract-integrity default (no injection)


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_default_integrity_seam_is_the_public_contract_function(tmp_path, monkeypatch):
    """With NO ``verify_integrity`` injected, readiness delegates to the PUBLIC
    ``contract.verify_contract_integrity`` seam, resolved at call time — never a fabricated OK report.

    technical (contract): the public ``contract.verify_contract_integrity`` attribute is monkeypatched
    with a spy that returns a VIOLATION; issue_readiness is called WITHOUT the ``verify_integrity``
    kwarg -> the spy is invoked exactly once and the verdict is ``not_ready`` with predicate
    ``contract_integrity`` surfacing the violation. An impl that fabricates a success report when the
    seam is omitted, or that captured the default by value at import (so the public patch is unseen),
    fails this.
    """
    from types import SimpleNamespace

    from issueforge import contract
    from issueforge.contract import IntegrityReport, IntegrityViolation
    from issueforge.verify import issue_readiness

    calls: list[SimpleNamespace] = []

    def _spy(run_id, *, candidate_worktree, base_sha, adapter, provisioner, store=None, **kw):
        calls.append(SimpleNamespace(run_id=run_id, candidate_worktree=Path(candidate_worktree)))
        viol = IntegrityViolation(predicate="dirty_worktree", detail="tests/test_accept.py")
        return IntegrityReport(ok=False, violations=(viol,))

    monkeypatch.setattr(contract, "verify_contract_integrity", _spy)

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)

    # NOTE: no verify_integrity kwarg — the default path must run the public seam.
    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
    )
    assert len(calls) == 1
    assert calls[0].run_id == RUN_ID
    assert calls[0].candidate_worktree == repo
    assert verdict["readiness"] == "not_ready"
    assert verdict["predicate"] == "contract_integrity"
    assert "dirty_worktree" in str(verdict["detail"])
    assert _read_readiness(RUN_ID) == verdict


# ===================================================== re-run freshness (no cached verdict)


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_changing_candidate_reruns_every_check_not_a_cached_verdict(tmp_path):
    """After a first ``ready`` result, changing ``candidate_sha`` RE-RUNS acceptance, baseline, and
    integrity against the new candidate — the verdict is recomputed, never a cached flag with a
    swapped ``ready_sha``.

    technical (contract): a first run over candidate A is ``ready``; the candidate advances to a new
    commit B and ``candidate_sha`` is updated; a SECOND run with FRESH runner/integrity spies must
    show the two exact commands were run again ({acceptance, baseline}) AND integrity was called once
    more, with the new verdict binding ``ready_sha == B``. An impl that caches the first verdict and
    only rewrites ``ready_sha`` leaves the second-run spies untouched and fails.
    """
    from issueforge.store import RunStore
    from issueforge.verify import issue_readiness

    repo, base, cc, cand_a = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand_a)
    verdict_a = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict_a["readiness"] == "ready"
    assert verdict_a["ready_sha"] == cand_a

    # Advance to a new in-scope commit B and update the persisted candidate_sha.
    (repo / "src" / "app.py").write_text("VALUE = 3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "impl-2")
    cand_b = _rev_parse(repo, "HEAD")
    assert cand_b != cand_a
    RunStore().apply(RUN_ID, lambda _rec: {"candidate_sha": cand_b})

    # Fresh spies for the SECOND run — they must actually be exercised.
    runner_2 = _runner()
    integrity_2 = _integrity()
    verdict_b = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=runner_2,
        verify_integrity=integrity_2,
    )
    assert verdict_b["readiness"] == "ready"
    assert verdict_b["ready_sha"] == cand_b
    # The second run RE-RAN both commands and integrity — not a reused cached verdict.
    ran_2 = sorted(tuple(c.command) for c in runner_2.calls)
    assert ran_2 == sorted([tuple(ACCEPTANCE_COMMAND), tuple(BASELINE_COMMAND)])
    assert all(c.worktree == repo for c in runner_2.calls)
    assert len(integrity_2.calls) == 1


# ===================================================== persistence: injected store + no mutation


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_verdict_is_persisted_through_the_injected_store_apply(tmp_path):
    """The verdict is persisted through the INJECTED ``store`` via exactly one ``store.apply`` — the
    impl must not bypass the injected store, make its own ``RunStore``, or write the manifest directly.

    technical (contract): a store spy wrapping the real ``RunStore`` (with ``read`` + ``apply``) is
    injected; a full ``ready`` run calls ``store.apply`` EXACTLY once, and that call persists the
    nested ``record['readiness']`` equal to the returned verdict. An impl that instantiates its own
    store, or writes the manifest without ``apply``, records zero apply calls and fails.
    """
    from issueforge.store import RunStore
    from issueforge.verify import issue_readiness

    class _StoreSpy:
        def __init__(self) -> None:
            self._real = RunStore()
            self.apply_calls: list[tuple] = []
            self.read_calls: list[str] = []

        def read(self, run_id):
            self.read_calls.append(run_id)
            return self._real.read(run_id)

        def apply(self, run_id, transform, **kw):
            self.apply_calls.append((run_id, transform, kw))
            return self._real.apply(run_id, transform, **kw)

    repo, base, cc, cand = _candidate_repo(tmp_path)
    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    spy = _StoreSpy()

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        store=spy,
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "ready"
    # Exactly one apply, through the injected store, persisting the nested verdict.
    assert len(spy.apply_calls) == 1
    assert spy.apply_calls[0][0] == RUN_ID
    assert _read_readiness(RUN_ID) == verdict


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_ready_persist_adds_only_the_readiness_field_and_no_status(tmp_path):
    """Persisting the verdict adds ONLY ``record['readiness']`` — no new top-level ``status``, no
    sibling field is mutated. Guards the locked no-new-status design.

    technical (contract): the COMPLETE persisted record after a ``ready`` run equals the original
    fixture record PLUS exactly ``{"readiness": <verdict>}`` — nothing else changes, and no top-level
    ``status`` key is introduced. ``_read_readiness`` alone hides sibling mutations, so this reads the
    full record.
    """
    from issueforge.store import RunStore
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)
    original = _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)
    assert "readiness" not in original
    assert "status" not in original

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    expected_verdict = {
        "readiness": "ready",
        "ready_sha": cand,
        "acceptance": "green",
        "baseline": "green",
        "scope": "clean",
        "contract_integrity": "clean",
    }
    assert verdict == expected_verdict

    full = RunStore().read(RUN_ID)
    assert full == {**original, "readiness": expected_verdict}
    assert "status" not in full


# ===================================================== deeper no-side-effects guard


@pytest.mark.xfail(strict=True, reason="PENDING (#112)")
def test_readiness_has_no_side_effects_beyond_the_verdict(tmp_path, monkeypatch):
    """Readiness issues a verdict and mutates NOTHING else: no push to a real remote, no non-read-only
    git, no file/state mutation, no human-override — even against layered probes the weaker guards miss.

    technical (contract): with (1) a local bare remote whose refs must stay byte-for-byte unchanged,
    (2) the ``issueforge.process.run`` seam intercepted to allow ONLY read-only git subcommands and
    raise on anything else (a ``git push`` / ``gh`` / non-git call trips it), (3) the candidate
    worktree source files + the state ``runs`` set snapshotted, and (4) the provider/github callables
    AND the ``contract.override_contract_review`` human-override seam patched as raisers — a full
    ``ready`` run completes, the remote refs are unchanged, HEAD is unchanged, no worktree source file
    changed, and no new run was created.
    """
    import issueforge.github as gh
    import issueforge.providers as pv
    from issueforge import contract, process
    from issueforge.store import run_dir
    from issueforge.verify import issue_readiness

    repo, base, cc, cand = _candidate_repo(tmp_path)

    # (1) A local bare remote with main pushed; capture its refs.
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    refs_before = _git(remote, "for-each-ref").stdout

    _persist_record(RUN_ID, repo=repo, base=base, contract_commit=cc, candidate=cand)

    # (4) Provider / github callables and the human-override seam become raisers.
    def _raiser(*_a, **_k):
        raise AssertionError("readiness must not push / PR / merge / override / invoke a provider")

    for mod in (gh, pv):
        for attr, value in list(vars(mod).items()):
            if callable(value) and not isinstance(value, type):
                monkeypatch.setattr(mod, attr, _raiser, raising=False)
    monkeypatch.setattr(contract, "override_contract_review", _raiser, raising=False)

    # (2) The process seam allows ONLY read-only git; anything else (push/gh/rm) raises.
    _READONLY_GIT = frozenset(
        {
            "rev-parse",
            "cat-file",
            "status",
            "diff",
            "diff-tree",
            "diff-index",
            "ls-files",
            "ls-tree",
            "show",
            "log",
            "for-each-ref",
            "symbolic-ref",
            "name-rev",
            "merge-base",
            "rev-list",
            "describe",
        }
    )
    real_run = process.run

    def _guarded_run(argv, **kw):
        parts = list(argv)
        if not parts or parts[0] != "git":
            raise AssertionError(f"readiness ran a non-git process: {parts!r}")
        # Skip global options (``-C <path>``, ``-c k=v``, other ``-``-prefixed) to find the subcommand.
        i = 1
        while i < len(parts):
            tok = parts[i]
            if tok in ("-C", "-c"):
                i += 2
                continue
            if str(tok).startswith("-"):
                i += 1
                continue
            break
        sub = parts[i] if i < len(parts) else None
        if sub not in _READONLY_GIT:
            raise AssertionError(f"readiness ran a non-read-only git command: {parts!r}")
        return real_run(argv, **kw)

    monkeypatch.setattr(process, "run", _guarded_run)

    # (3) Snapshot worktree source files (excluding .git, which git reads may refresh) + state runs.
    def _snapshot(root: Path) -> dict:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.parts
        }

    files_before = _snapshot(repo)
    head_before = _rev_parse(repo, "HEAD")
    branches_before = _git(repo, "branch", "--list").stdout
    runs_root = run_dir(RUN_ID).parent
    runs_before = sorted(p.name for p in runs_root.iterdir())

    verdict = issue_readiness(
        RUN_ID,
        adapter=_adapter(),
        provisioner=_provisioner(),
        run_baseline=_runner(),
        verify_integrity=_integrity(),
    )
    assert verdict["readiness"] == "ready"
    assert _git(remote, "for-each-ref").stdout == refs_before
    assert _snapshot(repo) == files_before
    assert _rev_parse(repo, "HEAD") == head_before
    assert _git(repo, "branch", "--list").stdout == branches_before
    assert sorted(p.name for p in runs_root.iterdir()) == runs_before
