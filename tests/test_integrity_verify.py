"""Committed PENDING acceptance suite for #19 (S13) — ``contract.verify_contract_integrity``, the
absolute contract-integrity gate that runs AFTER a manifest is frozen (S12). Where the AST backstop
(``tests/test_integrity_ast_backstop.py``) is a source-diff heuristic and DEMOTED to defense-in-
depth, this is the load-bearing control: it diffs the real committed contract_commit..HEAD range, the
per-file committed-blob hashes, the re-resolved external pins, the re-collected unit-id set, and git
ancestry — against the REAL frozen manifest, never a candidate's self-report.

``issueforge.contract.verify_contract_integrity`` does not exist yet, so every test imports it
(plus ``IntegrityReport``/``IntegrityViolation``) INSIDE its body and is
``@pytest.mark.xfail(strict=True, reason="PENDING (#19)")`` — an ImportError while pending
(``issueforge.contract`` itself already exists via S12's ``freeze_contract``, so the module import
alone would succeed; only the S13 names are missing).

Governing design: every scenario is a REAL two-commit-plus git repo. A manifest is frozen for real
via ``contract.freeze_contract`` (S12, already built) with an approving approver, over a real
provisioner and the real ``PytestAdapter`` — never a hand-built manifest dict — so each test binds to
the actual frozen schema. The candidate worktree is then mutated and RE-COMMITTED (git, never a dirty
working tree) to trigger each predicate; ``verify_contract_integrity`` is called against that new real
HEAD. This file is self-contained: it defines its own ``_frozen``/git/provisioner/adapter helpers
rather than importing test_contract.py's.

Covers (see the locked API contract, `IntegrityReport`/`IntegrityViolation` doc):
  - "protected_path_diff" — any contract_paths member changed (incl. deletion) in contract_commit..HEAD
  - "dep_hash" — a frozen dependency's recomputed sha at HEAD differs
  - "external_pin" — a frozen external pin re-resolved in the authoritative env differs
  - "recollection" — the re-collected unit-id set != frozen collected_ids
  - "ancestry" — contract_commit is no longer an ancestor of HEAD
Not covered here (out of scope for this file): "invocation" (adapter-level, §3) and "ast_backstop"
(tests/test_integrity_ast_backstop.py).

Pending convention: literal per-test ``xfail(strict=True)`` (repo standard).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import store

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]

_CONFIG = 'baseline = ["-m", "pytest"]\nframework = "pytest"\n'

# The standard base suite: two genuinely-green tests, committed on the base commit.
_BASE_FILE = "tests/test_base.py"
_BASE_A = "tests/test_base.py::test_base_a"
_BASE_B = "tests/test_base.py::test_base_b"
_BASE_FILES = {
    _BASE_FILE: "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert True\n"
}

# The default candidate: one genuine red the freeze's red-proof precondition binds to.
_NEW_FILE = "tests/test_new.py"
_NEW_X = "tests/test_new.py::test_x"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _scenario(root: Path, name: str, *, candidate_files: dict[str, str]) -> SimpleNamespace:
    """A REAL two-commit repo: a base commit (the standard green base suite + .issueforge.toml) with
    an ``origin`` whose default branch resolves to it, then a candidate commit overlaying
    ``candidate_files``. Mirrors ``tests/test_contract.py``'s ``_scenario`` (self-contained copy)."""
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(_CONFIG)
    _write(repo, _BASE_FILES)
    base_sha = _commit_all(repo, "base")
    _git(repo, "remote", "add", "origin", "git@github.com:Owner/IssueForge.git")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    _write(repo, candidate_files)
    candidate_sha = _commit_all(repo, "candidate")
    return SimpleNamespace(candidate_worktree=repo, base_sha=base_sha, candidate_sha=candidate_sha)


def _provisioner():
    """A host-side provisioner: the host interpreter + an allowlist env, plugin autoload disabled.
    Mirrors ``tests/test_contract.py``'s ``_provisioner`` (self-contained copy)."""
    counter = {"n": 0}

    def _provision(worktree, frozen_deps=None):
        import os
        import sys

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


def _real_pin_provisioner(pins: dict[str, str]):
    """A provisioner that builds a REAL, separate venv with ``pins`` installed, so external identity
    resolves from the provisioned interpreter, never the parent process. Mirrors
    ``tests/test_contract.py``'s ``_real_pin_provisioner`` (self-contained copy)."""

    def _provision(worktree, frozen_deps=None):
        merged = dict(pins)
        if frozen_deps:
            merged.update(frozen_deps)
        return _adapter().provision_environment(worktree, merged)

    return _provision


def _adapter():
    from issueforge.adapters.pytest_adapter import PytestAdapter

    return PytestAdapter()


def _mk_run(run_id: str) -> str:
    shape = {"classification": "buildable", "write_scope": []}
    record = {"status": "running", "shape": shape, "revision_ledger": {"op-1": "fingerprint"}}
    store.RunStore().apply(run_id, lambda _r: record, create=True)
    return run_id


def _seed_freeze_proof(run_id: str, scen, *, added_ids: tuple[str, ...]) -> None:
    first = added_ids[0]
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "red_proof": {
                "accepted": True,
                "reason": "behavioral_red",
                "base_sha": scen.base_sha,
                "head_sha": scen.candidate_sha,
                "added_ids": list(added_ids),
                "records": [
                    {
                        "nodeid": first,
                        "exception_type": "AssertionError",
                        "assertion_line": 2,
                        "message": "assert 1 == 2",
                    }
                ],
            }
        },
    )


def _seed_done_review(run_id: str, scen) -> None:
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "contract_review": {
                "verdict": "done",
                "outcome": "done",
                "head_sha": scen.candidate_sha,
                "reviewer_session_id": "rev-1",
                "authoring_session_id": "auth-1",
                "provider": "cli",
                "findings": [],
            }
        },
    )


def _freeze(run_id: str, scen, *, provisioner=None):
    """Freeze a REAL manifest via S12's already-built ``contract.freeze_contract`` (approving
    approver), so every test in this file binds to the real frozen schema rather than a hand-built
    manifest dict."""
    from issueforge import contract

    return contract.freeze_contract(
        run_id,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha,
        adapter=_adapter(),
        provisioner=provisioner or _provisioner(),
        approver=lambda _payload: True,
    )


def _frozen(tmp_path: Path, name: str, *, extra: dict[str, str] | None = None, provisioner=None):
    """Build a real, frozen S13 scenario: a two-commit repo, a seeded S10 red-proof + a done S9
    contract review (the freeze preconditions), and a REAL approved ``freeze_contract`` manifest.
    Returns the pieces every test needs to mutate the candidate worktree and then call
    ``verify_contract_integrity`` against it."""
    files = {_NEW_FILE: "def test_x():\n    assert 1 == 2\n"}
    if extra:
        files.update(extra)
    scen = _scenario(tmp_path, name, candidate_files=files)
    run_id = _mk_run(f"run-{name}")
    _seed_freeze_proof(run_id, scen, added_ids=(_NEW_X,))
    _seed_done_review(run_id, scen)
    res = _freeze(run_id, scen, provisioner=provisioner)
    assert res.approved is True, (
        "fixture setup: freeze_contract did not approve — cannot build a fixture"
    )
    return SimpleNamespace(run_id=run_id, scen=scen, manifest=res.manifest)


def _verify(fz, *, provisioner=None):
    from issueforge.contract import IntegrityReport, IntegrityViolation, verify_contract_integrity

    report = verify_contract_integrity(
        fz.run_id,
        candidate_worktree=fz.scen.candidate_worktree,
        base_sha=fz.scen.base_sha,
        adapter=_adapter(),
        provisioner=provisioner or _provisioner(),
    )
    assert isinstance(report, IntegrityReport)
    for v in report.violations:
        assert isinstance(v, IntegrityViolation)
    return report


def _spy(base_provisioner):
    """Wrap any provisioner so ``.calls`` records every ``(worktree, frozen_deps)`` invocation — the
    R3 proof that verify actually CALLS the provisioner (a genuine re-resolution in the authoritative
    env) rather than trusting any candidate-side declaration."""
    calls: list[tuple[object, object]] = []

    def _provision(worktree, frozen_deps=None):
        calls.append((worktree, frozen_deps))
        return base_provisioner(worktree, frozen_deps)

    _provision.calls = calls
    return _provision


def _collect_ids(worktree) -> set[str]:
    """The real, independent collected-id oracle (mirrors ``tests/test_contract.py``'s
    ``_collect_ids``) — used to independently PROVE the recollected set, never assumed."""
    adapter = _adapter()
    handle = adapter.provision_environment(worktree, None, provisioner=_provisioner())
    invocation = SimpleNamespace(
        worktree=Path(worktree),
        interpreter=handle.interpreter,
        command=["-m", "pytest"],
        env=getattr(handle, "env", None),
    )
    return set(adapter.canonical_collect(invocation).ids)


def _frozen_with_symlink(
    tmp_path: Path, name: str, *, link_rel: str, target_rel: str, target_content: str
):
    """Like ``_frozen`` but the candidate commit also carries a committed in-repo symlink ``link_rel``
    -> ``target_rel`` whose target lives OUTSIDE any test directory, so only the LINK itself (frozen
    automatically as a boundary member because git mode ``120000`` always qualifies) enters
    ``contract_paths`` — never the target. This is the R2 fixture that proves ``dep_hash`` fires
    independently of the diff gate: mutating the target's content is invisible to a diff scoped to
    ``contract_paths`` (the target path is not a member) but MUST still be caught by dep_hash's
    independent content recompute, keyed on the symlink's own path per S12's manifest schema."""
    files = {_NEW_FILE: "def test_x():\n    assert 1 == 2\n", target_rel: target_content}
    scen = _scenario(tmp_path, name, candidate_files=files)
    link_path = scen.candidate_worktree / link_rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    target_path = scen.candidate_worktree / target_rel
    os.symlink(os.path.relpath(target_path, link_path.parent), link_path)
    _git(scen.candidate_worktree, "add", "-A")
    _git(scen.candidate_worktree, "commit", "-qm", "add symlink")
    scen.candidate_sha = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
    run_id = _mk_run(f"run-{name}")
    _seed_freeze_proof(run_id, scen, added_ids=(_NEW_X,))
    _seed_done_review(run_id, scen)
    res = _freeze(run_id, scen)
    assert res.approved is True, (
        "fixture setup: freeze_contract did not approve — cannot build a fixture"
    )
    return SimpleNamespace(run_id=run_id, scen=scen, manifest=res.manifest)


# =============================================================== protected_path_diff


@pytest.mark.slow
def test_protected_path_diff_flags_edited_frozen_test_file(tmp_path):
    """Editing a frozen contract test file after freeze — even though it still names the same test —
    is caught by the absolute diff gate.

    technical (contract): freeze binds tests/test_new.py into contract_paths; a later COMMITTED edit
    to it -> report.ok False and a violation predicate "protected_path_diff" whose detail names
    "tests/test_new.py". A wrong-but-plausible impl that only re-checks dep_hashes (and treats a
    same-length edit as a non-event) would still be caught here because this predicate is diff-based,
    not hash-based.
    """
    fz = _frozen(tmp_path, "diff-edit")
    assert _NEW_FILE in fz.manifest["contract_paths"]
    (fz.scen.candidate_worktree / _NEW_FILE).write_text("def test_x():\n    assert 1 == 1\n")
    _commit_all(fz.scen.candidate_worktree, "edit frozen test")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "protected_path_diff" and v.detail == _NEW_FILE for v in report.violations
    )


@pytest.mark.slow
def test_protected_path_diff_flags_deleted_frozen_test_file(tmp_path):
    """Deleting a frozen contract file is not an escape hatch — deletion is still a protected-path
    diff, not a clean candidate.

    technical (contract): tests/test_new.py deleted and the deletion committed -> report.ok False and
    a violation predicate "protected_path_diff" detail "tests/test_new.py". A wrong-but-plausible impl
    that only diffs EXISTING files (skipping any path missing from the new tree) would miss this.
    Deletion is ALSO a dep_hash violation for the same path (finding #2): its recomputed content hash
    can never match the frozen blob once the file no longer exists, so a violating "dep_hash" entry
    naming the exact deleted path is required alongside "protected_path_diff", not merely implied.
    """
    fz = _frozen(tmp_path, "diff-delete")
    (fz.scen.candidate_worktree / _NEW_FILE).unlink()
    _commit_all(fz.scen.candidate_worktree, "delete frozen test")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "protected_path_diff" and v.detail == _NEW_FILE for v in report.violations
    )
    assert any(v.predicate == "dep_hash" and v.detail == _NEW_FILE for v in report.violations)


@pytest.mark.slow
def test_protected_path_diff_no_marker_carve_out_for_comment_only_edit(tmp_path):
    """Unlike MARVIN's AST backstop (which allows a comment/whitespace-only edit), the S13 absolute
    diff gate has NO carve-out: any committed byte change to a contract file fails, even a
    comment-only edit that leaves every assertion identical.

    technical (contract): append a trailing comment line to tests/test_new.py, commit it -> report.ok
    False and a violation predicate "protected_path_diff" detail "tests/test_new.py". An impl that
    reuses the AST-backstop's allowed/weakened classifier here (which would call this "allowed")
    fails this test.
    """
    fz = _frozen(tmp_path, "diff-comment")
    original = (fz.scen.candidate_worktree / _NEW_FILE).read_text()
    (fz.scen.candidate_worktree / _NEW_FILE).write_text(original + "\n# just a comment\n")
    _commit_all(fz.scen.candidate_worktree, "comment-only edit")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "protected_path_diff" and v.detail == _NEW_FILE for v in report.violations
    )


@pytest.mark.slow
def test_protected_path_diff_fires_identically_regardless_of_how_the_edit_was_written(tmp_path):
    """The check is diff-based against the committed blob — it never inspects HOW the working tree
    got its new content, only what landed in the commit. A change written by directly writing the
    file and an equivalent change written through a separate ``python3`` subprocess (a stand-in for
    "some other tool wrote it") land as the SAME committed diff and trigger an IDENTICAL violation
    set. There is no session-provenance signal for a candidate to spoof around.

    technical (contract): two independent frozen scenarios, mutated to the SAME new content for
    tests/test_new.py via two different write mechanisms, each committed -> both reports have
    ok False and an IDENTICAL {(predicate, detail)} violation set containing
    ("protected_path_diff", "tests/test_new.py").
    """
    new_content = "def test_x():\n    assert 1 == 1\n"
    violation_sets = []
    for label, write in (
        ("direct_write", lambda p: p.write_text(new_content)),
        (
            "subprocess_write",
            lambda p: subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])",
                    str(p),
                    new_content,
                ],
                check=True,
            ),
        ),
    ):
        fz = _frozen(tmp_path, f"diff-unforge-{label}")
        write(fz.scen.candidate_worktree / _NEW_FILE)
        _commit_all(fz.scen.candidate_worktree, f"edit via {label}")
        report = _verify(fz)
        assert report.ok is False
        violation_sets.append({(v.predicate, v.detail) for v in report.violations})

    assert violation_sets[0] == violation_sets[1]
    assert ("protected_path_diff", _NEW_FILE) in violation_sets[0]


@pytest.mark.slow
def test_clean_candidate_reports_ok_true_with_no_violations(tmp_path):
    """A candidate whose HEAD is exactly the frozen contract_commit (no contract-path change) passes
    cleanly — the gate is not a permanent block, only a change detector.

    technical (contract): verify_contract_integrity called against the just-frozen HEAD, untouched ->
    report.ok is True and report.violations == () (empty, not merely falsy-shaped).
    """
    fz = _frozen(tmp_path, "clean")
    report = _verify(fz)
    assert report.ok is True
    assert report.violations == ()


# =============================================================== dep_hash


@pytest.mark.slow
def test_dep_hash_flags_mutated_frozen_dependency_content(tmp_path):
    """A frozen dependency's CONTENT hash is independently recomputed at HEAD and compared against the
    manifest's dep_hashes entry — content drift is caught directly, not only inferred from a path diff.

    technical (contract): tests/helpers.py frozen with dep_hashes["tests/helpers.py"] == sha256 of its
    committed blob; mutate its content and commit -> report.ok False and a violation predicate
    "dep_hash" detail "tests/helpers.py". Independently: the new committed blob's sha256 differs from
    the frozen hash (an oracle proving the mutation is real, not incidental).
    """
    fz = _frozen(tmp_path, "dephash", extra={"tests/helpers.py": "H = 1\n"})
    assert "tests/helpers.py" in fz.manifest["dep_hashes"]
    frozen_hash = fz.manifest["dep_hashes"]["tests/helpers.py"]
    (fz.scen.candidate_worktree / "tests/helpers.py").write_text("H = 2\n")
    _commit_all(fz.scen.candidate_worktree, "mutate dependency content")

    new_blob = subprocess.run(
        ["git", "-C", str(fz.scen.candidate_worktree), "show", "HEAD:tests/helpers.py"],
        capture_output=True,
    ).stdout
    assert hashlib.sha256(new_blob).hexdigest() != frozen_hash

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "dep_hash" and v.detail == "tests/helpers.py" for v in report.violations
    )


@pytest.mark.slow
def test_dep_hash_names_only_the_mutated_file_not_every_frozen_dependency(tmp_path):
    """The dep_hash check is PER-FILE — mutating one frozen dependency must not spuriously name a
    sibling frozen dependency whose bytes never changed (a wrong-but-plausible impl that flags the
    whole dep_hashes map on any single drift would fail this).

    technical (contract): two frozen dependencies (tests/helpers.py, tests/other.py); only
    tests/helpers.py is mutated and committed -> a "dep_hash" violation names "tests/helpers.py", and
    NO "dep_hash" violation names "tests/other.py".
    """
    fz = _frozen(
        tmp_path,
        "dephash-scope",
        extra={"tests/helpers.py": "H = 1\n", "tests/other.py": "O = 1\n"},
    )
    assert {"tests/helpers.py", "tests/other.py"} <= set(fz.manifest["dep_hashes"])
    (fz.scen.candidate_worktree / "tests/helpers.py").write_text("H = 2\n")
    _commit_all(fz.scen.candidate_worktree, "mutate only helpers.py")

    report = _verify(fz)
    dep_hash_details = {v.detail for v in report.violations if v.predicate == "dep_hash"}
    assert "tests/helpers.py" in dep_hash_details
    assert "tests/other.py" not in dep_hash_details


@pytest.mark.slow
def test_dep_hash_flags_symlink_target_mutation_outside_contract_paths(tmp_path):
    """R2: dep_hash proven INDEPENDENT of the diff gate. The two tests above mutate a file already IN
    contract_paths, so a diff-only impl that fabricates dep_hash results would still pass them. Here a
    committed symlink's resolved TARGET lives OUTSIDE contract_paths entirely — only the symlink's own
    path is a boundary member (any committed symlink qualifies automatically per S12's freeze rule).
    Mutating the target's content is invisible to a diff scoped to contract_paths members, so
    protected_path_diff must NOT fire; only dep_hash's independent content recompute (keyed on the
    symlink's path, per S12's ``dep_hashes[link] = sha256(target blob)`` schema) catches it.

    technical (contract): tests/link_outside.py is a committed symlink -> vendor/helper_target.py
    (outside any test directory, never itself a contract_paths member); mutate vendor/helper_target.py
    content and commit -> report.ok False, ("dep_hash", "tests/link_outside.py") present, and NO
    ("protected_path_diff", ...) violation fires at all.
    """
    link_rel = "tests/link_outside.py"
    target_rel = "vendor/helper_target.py"
    fz = _frozen_with_symlink(
        tmp_path,
        "dephash-symlink",
        link_rel=link_rel,
        target_rel=target_rel,
        target_content="H = 1\n",
    )
    assert link_rel in fz.manifest["contract_paths"]
    assert target_rel not in fz.manifest["contract_paths"]
    assert fz.manifest["symlinks"].get(link_rel) == target_rel
    frozen_hash = fz.manifest["dep_hashes"][link_rel]

    (fz.scen.candidate_worktree / target_rel).write_text("H = 2\n")
    _commit_all(fz.scen.candidate_worktree, "mutate symlink target outside contract_paths")

    new_blob = subprocess.run(
        ["git", "-C", str(fz.scen.candidate_worktree), "show", f"HEAD:{target_rel}"],
        capture_output=True,
    ).stdout
    assert hashlib.sha256(new_blob).hexdigest() != frozen_hash

    report = _verify(fz)
    assert report.ok is False
    assert any(v.predicate == "dep_hash" and v.detail == link_rel for v in report.violations)
    assert not any(v.predicate == "protected_path_diff" for v in report.violations)


# =============================================================== external_pin


@pytest.mark.slow
def test_external_pin_flags_re_resolved_dependency_version_delta(tmp_path):
    """A frozen external pin, discovered from the provisioned env at freeze time, is compared at
    verify time against a re-resolution in a REAL, separately-provisioned env — a genuine version
    delta is caught, never read from any candidate-declared metadata (the candidate worktree/HEAD is
    unchanged; only the authoritative env's resolution differs).

    R3: the provisioner is INSTRUMENTED (a spy) and verify must actually CALL it — proving the
    version comes from a genuine re-resolution, not a cached/skipped check. A REAL candidate-side
    package-metadata decoy — a committed ``platformdirs-4.10.0.dist-info/METADATA`` naming
    ``Name: platformdirs`` / ``Version: 4.10.0``, the exact artifact shape a naive impl might trust
    (e.g. by scanning the worktree for installed-looking dist-info) — is planted and must be
    ignored: the authoritative re-resolved version is what verify reports, never the decoy.

    technical (contract): freeze with platformdirs pinned to 4.10.0 in a real venv -> manifest
    external_pins contains ("platformdirs", "4.10.0"). verify_contract_integrity driven by a spied
    provisioner that resolves platformdirs 4.9.1 in a DIFFERENT real venv (same candidate HEAD, with a
    committed platformdirs-4.10.0.dist-info/METADATA decoy falsely claiming 4.10.0 is installed) -> the
    spy was called, report.ok False, and an EXACT violation ("external_pin", "platformdirs
    4.10.0->4.9.1") is present (R1 canonical format).
    """
    fz = _frozen(
        tmp_path,
        "pin",
        extra={"tests/conftest.py": "import platformdirs  # noqa: F401\n"},
        provisioner=_real_pin_provisioner({"platformdirs": "4.10.0"}),
    )
    pins = {(d, v) for d, v in fz.manifest["external_pins"]}
    assert ("platformdirs", "4.10.0") in pins

    decoy_dist_info = fz.scen.candidate_worktree / "platformdirs-4.10.0.dist-info" / "METADATA"
    decoy_dist_info.parent.mkdir(parents=True, exist_ok=True)
    decoy_dist_info.write_text("Metadata-Version: 2.1\nName: platformdirs\nVersion: 4.10.0\n")
    _commit_all(
        fz.scen.candidate_worktree,
        "plant a real package-metadata decoy claiming the frozen version",
    )

    spy = _spy(_real_pin_provisioner({"platformdirs": "4.9.1"}))
    report = _verify(fz, provisioner=spy)
    assert spy.calls, "expected verify to CALL the provisioner (authoritative re-resolution)"
    assert report.ok is False
    assert any(
        v.predicate == "external_pin" and v.detail == "platformdirs 4.10.0->4.9.1"
        for v in report.violations
    )


@pytest.mark.slow
def test_external_pin_flags_dependency_no_longer_resolvable_as_missing(tmp_path):
    """R1/R4 finding: a frozen external pin that no longer resolves in the authoritative env at verify
    time is a "missing" pin, using the canonical "<dist> missing" detail — never silently dropped from
    the report. The import is wrapped in a try/except so real collection stays green either way,
    isolating this from a collection failure and pinning ONLY the external_pin re-resolution behavior.

    technical (contract): freeze with platformdirs installed in a real venv -> manifest external_pins
    contains ("platformdirs", "4.10.0"). verify driven by a provisioner whose real venv has NO
    platformdirs installed -> report.ok False and an EXACT violation
    ("external_pin", "platformdirs missing").
    """
    fz = _frozen(
        tmp_path,
        "pin-missing",
        extra={
            "tests/conftest.py": (
                "try:\n"
                "    import platformdirs  # noqa: F401\n"
                "except ImportError:\n"
                "    platformdirs = None\n"
            )
        },
        provisioner=_real_pin_provisioner({"platformdirs": "4.10.0"}),
    )
    pins = {(d, v) for d, v in fz.manifest["external_pins"]}
    assert ("platformdirs", "4.10.0") in pins

    report = _verify(fz, provisioner=_real_pin_provisioner({}))
    assert report.ok is False
    assert any(
        v.predicate == "external_pin" and v.detail == "platformdirs missing"
        for v in report.violations
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_external_pin_flags_newly_required_dependency_as_added(tmp_path):
    """R1/R4 finding: a distribution newly required at HEAD, discovered in the authoritative env but
    entirely absent from the frozen external_pins, surfaces its own "<dist> added" violation. The
    triggering commit also edits tests/conftest.py (a frozen contract path), so protected_path_diff and
    dep_hash co-fire on it too — this asserts "added" is AMONG the violations (R1), not the only one,
    since the scenario does not isolate it from the code-diff gate.

    technical (contract): freeze with only platformdirs pinned; a later commit adds
    ``import packaging`` to the frozen tests/conftest.py, and the authoritative env at verify time has
    BOTH platformdirs and packaging installed -> report.ok False and an EXACT violation
    ("external_pin", "packaging added") present alongside protected_path_diff/dep_hash on
    tests/conftest.py.
    """
    conftest_rel = "tests/conftest.py"
    fz = _frozen(
        tmp_path,
        "pin-added",
        extra={conftest_rel: "import platformdirs  # noqa: F401\n"},
        provisioner=_real_pin_provisioner({"platformdirs": "4.10.0"}),
    )
    pins = {d for d, _v in fz.manifest["external_pins"]}
    assert pins == {"platformdirs"}

    (fz.scen.candidate_worktree / conftest_rel).write_text(
        "import platformdirs  # noqa: F401\nimport packaging  # noqa: F401\n"
    )
    _commit_all(fz.scen.candidate_worktree, "newly import packaging")

    report = _verify(
        fz, provisioner=_real_pin_provisioner({"platformdirs": "4.10.0", "packaging": "23.2"})
    )
    assert report.ok is False
    assert any(
        v.predicate == "external_pin" and v.detail == "packaging added" for v in report.violations
    )
    assert any(
        v.predicate == "protected_path_diff" and v.detail == conftest_rel for v in report.violations
    )


# =============================================================== recollection


@pytest.mark.slow
def test_recollection_flags_added_unit_id_that_appears_after_freeze(tmp_path):
    """A brand-new test file added AFTER freeze (never part of the frozen contract boundary, so it
    trips no protected_path_diff/dep_hash) still changes the collected-id set — recollection is its
    own independent check, isolated from the path/hash checks here.

    technical (contract): frozen collected_ids == {test_base_a, test_base_b, test_x} (3 ids); a new
    committed tests/test_added.py::test_added appears at HEAD -> the recollected set has 4 members ->
    report.ok False and an EXACT violation ("recollection", "+tests/test_added.py::test_added") (R1
    canonical added-id format). No "protected_path_diff" violation fires (the new file was never a
    frozen contract path).
    """
    fz = _frozen(tmp_path, "recollect-add")
    assert set(fz.manifest["collected_ids"]) == {_BASE_A, _BASE_B, _NEW_X}
    added_id = "tests/test_added.py::test_added"
    (fz.scen.candidate_worktree / "tests/test_added.py").write_text(
        "def test_added():\n    assert True\n"
    )
    _commit_all(fz.scen.candidate_worktree, "add a new collected test")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "recollection" and v.detail == f"+{added_id}" for v in report.violations
    )
    assert not any(v.predicate == "protected_path_diff" for v in report.violations)


@pytest.mark.slow
def test_recollection_flags_removed_unit_id_that_disappears_after_freeze(tmp_path):
    """An id present at freeze time that vanishes from the recollected set at HEAD is also caught —
    the check is exact set equality, not "the set only grows". Because every frozen test file is
    ITSELF a protected contract path, removing a previously-collected id necessarily also edits a
    protected path (protected_path_diff/dep_hash co-fire here); recollection still fires
    independently and names the id that disappeared — the isolated single-predicate case is the
    ADDITION scenario above.

    technical (contract): frozen collected_ids == {test_base_a, test_base_b, test_x}; tests/test_new.py
    is edited so it no longer defines test_x, committed -> the recollected set loses
    "tests/test_new.py::test_x" -> report.ok False and an EXACT violation
    ("recollection", "-tests/test_new.py::test_x") (R1 canonical removed-id format).
    """
    fz = _frozen(tmp_path, "recollect-remove")
    assert _NEW_X in fz.manifest["collected_ids"]
    (fz.scen.candidate_worktree / _NEW_FILE).write_text("# test_x removed\n")
    _commit_all(fz.scen.candidate_worktree, "remove the frozen test")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "recollection" and v.detail == f"-{_NEW_X}" for v in report.violations
    )


@pytest.mark.slow
def test_recollection_no_violation_when_recollected_set_is_identical(tmp_path):
    """R2/finding #5: the identical-set control MUST advance HEAD — an unrelated, NON-CONTRACT
    implementation file is committed so HEAD genuinely differs from contract_commit — and independently
    PROVE (via the real ``_collect_ids`` oracle, not an assumption) that the recollected set is
    byte-for-byte identical to the frozen set. This kills a cheat impl that emits "recollection"
    whenever HEAD != contract_commit: HEAD moves here, yet no violation fires because the set truly
    never changed.

    technical (contract): frozen collected_ids == {test_base_a, test_base_b, test_x}; a forward commit
    adds an unrelated, non-contract impl_unrelated.py (HEAD != contract_commit); the independent
    _collect_ids(worktree) oracle confirms the recollected set still equals the frozen set exactly ->
    verify_contract_integrity -> no violation carries predicate "recollection" (report.ok is True).
    """
    fz = _frozen(tmp_path, "recollect-identical")
    frozen_ids = set(fz.manifest["collected_ids"])
    worktree = fz.scen.candidate_worktree
    (worktree / "impl_unrelated.py").write_text("UNRELATED = 1\n")
    _commit_all(worktree, "unrelated, non-contract implementation commit")
    new_head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert new_head != fz.manifest["contract_commit"], "fixture setup: HEAD did not advance"

    assert _collect_ids(worktree) == frozen_ids, (
        "fixture setup: real collect oracle disagrees with the frozen collected_ids"
    )

    report = _verify(fz)
    assert not any(v.predicate == "recollection" for v in report.violations)
    assert report.ok is True


# =============================================================== ancestry


@pytest.mark.slow
def test_ancestry_flags_history_rewrite_that_orphans_contract_commit(tmp_path):
    """A rebase/amend that rewrites history so the frozen contract_commit is no longer reachable from
    HEAD is caught by an independent git ancestry check — verified against ``git merge-base
    --is-ancestor`` as an oracle, so this cannot be spoofed by a candidate that merely looks similar.

    technical (contract): after freeze, the candidate branch is hard-reset to base_sha (discarding the
    frozen contract_commit) and a NEW sibling commit is made from there -> the independent oracle
    ``git merge-base --is-ancestor <contract_commit> HEAD`` fails (nonzero) and
    verify_contract_integrity -> report.ok False and an EXACT violation ("ancestry", <contract_commit
    sha>) (R1 canonical format: the detail names the orphaned contract_commit itself).
    """
    fz = _frozen(tmp_path, "ancestry-rewrite")
    contract_commit = fz.manifest["contract_commit"]
    worktree = fz.scen.candidate_worktree
    _git(worktree, "reset", "--hard", fz.scen.base_sha)
    (worktree / "tests/test_new.py").write_text("def test_x():\n    assert 1 == 2\n")
    (worktree / "sibling.py").write_text("SIBLING = 1\n")
    _commit_all(worktree, "sibling history, contract_commit orphaned")

    oracle = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", contract_commit, "HEAD"]
    )
    assert oracle.returncode != 0, "fixture setup: contract_commit is still an ancestor"

    report = _verify(fz)
    assert report.ok is False
    assert any(v.predicate == "ancestry" and v.detail == contract_commit for v in report.violations)


@pytest.mark.slow
def test_ancestry_no_violation_when_contract_commit_still_ancestor_of_head(tmp_path):
    """A normal forward commit on top of the frozen contract_commit (the ordinary implementation-work
    case) never fires ancestry — the check only fires on a history REWRITE, not on HEAD simply moving
    forward.

    technical (contract): a genuine descendant commit is added after freeze (adding an unrelated
    implementation file, no contract-path touched); the independent oracle confirms contract_commit IS
    an ancestor of the new HEAD -> no violation carries predicate "ancestry".
    """
    fz = _frozen(tmp_path, "ancestry-forward")
    contract_commit = fz.manifest["contract_commit"]
    worktree = fz.scen.candidate_worktree
    (worktree / "impl.py").write_text("X = 1\n")
    _commit_all(worktree, "ordinary forward implementation commit")

    oracle = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", contract_commit, "HEAD"]
    )
    assert oracle.returncode == 0, "fixture setup: contract_commit should still be an ancestor"

    report = _verify(fz)
    assert not any(v.predicate == "ancestry" for v in report.violations)


# =============================================================== every violated predicate collected


@pytest.mark.slow
def test_all_violated_predicates_collected_not_only_the_first_one_found(tmp_path):
    """The report collects EVERY predicate that fired in one pass, never stopping at the first
    violation found — a single candidate commit that simultaneously edits a frozen dependency AND adds
    a new collected test surfaces BOTH "dep_hash"/"protected_path_diff" AND "recollection" in the same
    report.

    technical (contract): one commit both mutates tests/helpers.py (a frozen dependency) and adds a
    brand-new tests/test_added.py::test_added -> report.ok False and
    {v.predicate for v in report.violations} == {"protected_path_diff", "dep_hash", "recollection"}
    exactly (no ancestry/external_pin noise in this unrelated-history, unrelated-pin scenario). A
    short-circuiting impl that returns after the first predicate fires would produce a strict SUBSET
    and fail the exact-set assertion.
    """
    fz = _frozen(tmp_path, "multi-violation", extra={"tests/helpers.py": "H = 1\n"})
    worktree = fz.scen.candidate_worktree
    (worktree / "tests/helpers.py").write_text("H = 2\n")
    (worktree / "tests/test_added.py").write_text("def test_added():\n    assert True\n")
    _commit_all(worktree, "simultaneous dependency edit and new collected test")

    report = _verify(fz)
    assert report.ok is False
    assert {v.predicate for v in report.violations} == {
        "protected_path_diff",
        "dep_hash",
        "recollection",
    }


# =============================================================== unforgeable (finding #7)


@pytest.mark.slow
def test_verify_ignores_forged_candidate_side_manifest_claiming_intact(tmp_path):
    """R3 finding #7: verify loads the HARNESS-RETAINED manifest (the store's own
    ``contract-manifest.json`` under the run dir) — never anything a candidate committed inside its
    own worktree. A forged file at the same conventional name, plus a forged session-annotation
    marker, both falsely claiming the contract is intact, are planted directly in the candidate
    worktree and committed; they must not suppress a real, independently-caused violation.

    technical (contract): after freeze, the candidate commits a worktree-root "contract-manifest.json"
    containing a forged {"ok": true, "violations": []} claim, PLUS a ".issueforge-integrity-ok" marker
    file, while ALSO editing the real frozen tests/test_new.py -> verify still reports ok False and the
    EXACT ("protected_path_diff", "tests/test_new.py") violation, proving the forged in-worktree
    artifacts were never consulted as a source of truth.
    """
    fz = _frozen(tmp_path, "unforgeable-manifest")
    worktree = fz.scen.candidate_worktree
    (worktree / "contract-manifest.json").write_text(
        json.dumps(
            {"ok": True, "violations": [], "contract_commit": fz.manifest["contract_commit"]}
        )
    )
    (worktree / ".issueforge-integrity-ok").write_text("intact\n")
    (worktree / _NEW_FILE).write_text("def test_x():\n    assert 1 == 1\n")
    _commit_all(worktree, "forge an intact claim while editing the real frozen test")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "protected_path_diff" and v.detail == _NEW_FILE for v in report.violations
    )


# =============================================================== ast_backstop integration (finding #8)


@pytest.mark.slow
def test_ast_backstop_flags_weakened_pending_acceptance_test_through_verify(tmp_path):
    """Finding #8: proves verify_contract_integrity actually RUNS the AST backstop
    (``issueforge.integrity.classify_acceptance_change``) over the final committed diff of a contract
    test file — defense-in-depth behind the absolute diff/hash gates, not merely unit-tested in
    isolation (tests/test_integrity_ast_backstop.py covers the classifier itself; this proves verify
    actually WIRES it in). Weakening a committed PENDING acceptance test's assertion to a no-op while
    its xfail decorator stays byte-identical is exactly the vector the AST classifier exists to catch.

    technical (contract): a frozen contract test file carries a real
    ``@pytest.mark.xfail(strict=True, reason="PENDING (#19)")`` marker; its body is weakened from a
    real check (``assert 1 == 2``) to a no-op (``assert True``), decorator untouched, and committed ->
    report.ok False, and among the co-firing protected_path_diff/dep_hash violations on
    tests/test_ast_guard.py, an EXACT violation ("ast_backstop", "test_guard") is present. No
    "recollection" violation fires (the node-id itself is unchanged).
    """
    guard_rel = "tests/test_ast_guard.py"
    old_src = (
        "import pytest\n\n\n"
        '@pytest.mark.xfail(strict=True, reason="PENDING (#19)")\n'
        "def test_guard():\n"
        "    assert 1 == 2\n"
    )
    fz = _frozen(tmp_path, "ast-backstop", extra={guard_rel: old_src})
    assert guard_rel in fz.manifest["contract_paths"]

    new_src = (
        "import pytest\n\n\n"
        '@pytest.mark.xfail(strict=True, reason="PENDING (#19)")\n'
        "def test_guard():\n"
        "    assert True\n"
    )
    (fz.scen.candidate_worktree / guard_rel).write_text(new_src)
    _commit_all(fz.scen.candidate_worktree, "weaken the pending acceptance test to a no-op")

    report = _verify(fz)
    assert report.ok is False
    assert any(
        v.predicate == "ast_backstop" and v.detail == "test_guard" for v in report.violations
    )
    assert any(
        v.predicate == "protected_path_diff" and v.detail == guard_rel for v in report.violations
    )
    assert not any(v.predicate == "recollection" for v in report.violations)


# =============================================================== permanent verdict (finding #25)


def _commit_binding(event: dict) -> object:
    """Every event this codebase writes identifies its kind with a ``"transition"`` key (see
    ``freeze_contract``'s ``{"transition": "freeze", ...}`` in contract.py and every other
    ``append_event`` call site in contract.py/engine.py/shaper.py — there is no ``"type"``/``"kind"``
    convention anywhere). The exact commit-field NAME an integrity-verdict event will use is not yet
    determinable from source (S13 doesn't exist), so this checks the conventional candidates in the
    order a faithful implementation would plausibly pick."""
    for key in ("commit", "head_sha", "candidate_sha", "sha", "head"):
        if key in event:
            return event[key]
    return None


def _is_integrity_verdict_event(event: dict) -> bool:
    """Select a verdict event by STRUCTURE (an ``"ok": bool`` verdict flag plus a ``"violations"``
    list — the minimal stable shape ``IntegrityReport`` implies), never by substring-matching the
    serialized JSON blob. This is deliberately NOT keyed on a specific ``"transition"`` value: that
    name is not pinned by the (locked) API contract, only the ``IntegrityReport``/``IntegrityViolation``
    shape is. A wrong-but-plausible impl that never persists a verdict record at all — only returning
    the report in-memory — produces no event matching this shape and fails this test.
    """
    return (
        isinstance(event, dict)
        and isinstance(event.get("ok"), bool)
        and isinstance(event.get("violations"), list)
    )


@pytest.mark.slow
def test_permanent_verdict_persists_exact_violation_and_commit_binding(tmp_path):
    """R7 finding #25: every integrity verdict is PERMANENT — inspect the persisted run event stream
    (``store.RunStore().replay_events``, the same append-only mechanism ``freeze_contract`` itself uses
    for its own "freeze" event) after BOTH a clean verify and a later violating verify, and prove a
    SPECIFIC, structurally-selected integrity-verdict record (an event carrying an ``ok`` bool and a
    ``violations`` list — see ``_is_integrity_verdict_event``) exists for each, binding the exact
    ``ok`` value, the checked commit, and the structured (predicate, detail) pairs — never merely
    returned in-memory and discarded, and never merely an ARBITRARY event that happens to contain the
    right substrings (a candidate-forged or unrelated event naming the same sha/predicate string would
    pass a substring check but is excluded here by requiring the verdict-shaped event itself).

    ASSUMPTION flagged: the exact field name binding the verdict to its checked commit is not
    determinable from the locked API contract (only ``IntegrityReport``/``IntegrityViolation`` are
    pinned, not the persisted event's field names) — ``_commit_binding`` checks the conventional
    candidate key names. The `violations` list items are asserted structurally (each a dict/mapping
    exposing `predicate` and `detail`, mirroring `IntegrityViolation`'s two fields), not by a specific
    container type.

    technical (contract): a clean verify against the just-frozen HEAD is followed, after an edit to the
    frozen tests/test_new.py, by a violating verify against the new HEAD -> a fresh
    store.RunStore().replay_events(fz.run_id) contains a verdict-shaped event with ok is True, an empty
    violations list, and a commit binding equal to the clean HEAD sha; and a LATER verdict-shaped event
    with ok is False, a commit binding equal to the violating HEAD sha, and a violations entry whose
    (predicate, detail) == ("protected_path_diff", "tests/test_new.py").
    """
    fz = _frozen(tmp_path, "permanent-verdict")
    worktree = fz.scen.candidate_worktree
    clean_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    clean_report = _verify(fz)
    assert clean_report.ok is True

    (worktree / _NEW_FILE).write_text("def test_x():\n    assert 1 == 1\n")
    _commit_all(worktree, "edit the frozen test after the clean verify")
    violating_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert violating_sha != clean_sha

    violating_report = _verify(fz)
    assert violating_report.ok is False

    events = store.RunStore().replay_events(fz.run_id)
    verdict_events = [e for e in events if _is_integrity_verdict_event(e)]
    assert verdict_events, (
        "expected at least one persisted, structurally-shaped integrity verdict event"
    )

    clean_events = [
        e for e in verdict_events if e["ok"] is True and _commit_binding(e) == clean_sha
    ]
    assert clean_events, (
        "expected a persisted verdict event: ok is True, bound to the CLEAN head sha, via one of "
        "the conventional commit-binding field names"
    )
    assert any(e["violations"] == [] for e in clean_events), (
        "expected the clean verdict's violations list to be exactly empty"
    )

    violating_events = [
        e for e in verdict_events if e["ok"] is False and _commit_binding(e) == violating_sha
    ]
    assert violating_events, (
        "expected a persisted verdict event: ok is False, bound to the VIOLATING head sha, via one "
        "of the conventional commit-binding field names"
    )
    assert any(
        any(
            isinstance(v, dict)
            and v.get("predicate") == "protected_path_diff"
            and v.get("detail") == _NEW_FILE
            for v in e["violations"]
        )
        for e in violating_events
    ), (
        "expected the violating verdict's violations list to contain a structured "
        '("protected_path_diff", "tests/test_new.py") entry'
    )


# =============================================================== dirty-tree (finding #1)


@pytest.mark.slow
def test_verify_flags_uncommitted_assertion_weakening_in_frozen_test(tmp_path):
    """Finding #1: ``protected_path_diff`` diffs committed(contract_commit) vs committed(HEAD), so an
    UNCOMMITTED edit to a frozen test file is invisible to it, and ``recollection`` only set-compares
    node ids (which an ``assert True`` swap leaves unchanged). A candidate that weakens a frozen
    acceptance test's assertion to a no-op WITHOUT committing it therefore slips every predicate today.

    technical (contract): the frozen tests/test_new.py (``assert 1 == 2``) is weakened in the WORKING
    TREE to ``assert True`` and NEVER committed — the node id tests/test_new.py::test_x is unchanged and
    the independent ``_collect_ids`` oracle confirms the recollected set equals the frozen set exactly.
    verify_contract_integrity must still catch the uncommitted weakening: report.ok is False and an EXACT
    violation ("protected_path_diff", "tests/test_new.py") names the offending frozen path (verify reads
    the working tree / refuses a dirty tree). A committed-vs-committed impl misses it (no diff, no
    recollection delta) and wrongly reports ok True.
    """
    fz = _frozen(tmp_path, "dirty-weaken")
    assert _NEW_FILE in fz.manifest["contract_paths"]

    # An UNCOMMITTED weakening of the frozen assertion to a no-op — nothing is committed, so the
    # committed contract_commit..HEAD diff is empty and the collected-id set is byte-for-byte identical.
    (fz.scen.candidate_worktree / _NEW_FILE).write_text("def test_x():\n    assert True\n")
    assert _collect_ids(fz.scen.candidate_worktree) == set(fz.manifest["collected_ids"]), (
        "fixture setup: the node-id set must stay identical so recollection cannot be what catches this"
    )

    # The OBSERVABLE contract: an uncommitted weakening of a frozen test is caught and the offending
    # path is NAMED. verify_contract_integrity returns an IntegrityReport for every predicate, so a
    # dirty-tree weakening surfaces the same structured way: report.ok is False with SOME violation
    # whose .detail names the frozen path. We deliberately do NOT pin which predicate (a dedicated
    # dirty-tree predicate is as valid as ``protected_path_diff``), but we DO require the structured
    # report rather than a bare ``except Exception`` that any incidental crash naming the path would
    # satisfy. A committed-vs-committed impl catches neither (no diff, no recollection delta) and
    # wrongly returns ok True.
    report = _verify(fz)
    assert report.ok is False, (
        "verify silently accepted an uncommitted weakening of a frozen acceptance test"
    )
    assert any(v.detail == _NEW_FILE for v in report.violations), (
        "verify reported not-ok but no violation named the offending frozen path"
    )


# =============================================================== boundary addition (finding #2)


@pytest.mark.slow
def test_verify_flags_new_higher_precedence_config_added_after_freeze(tmp_path):
    """Finding #2: there is no full-closure recompute at HEAD. A brand-new committed tests/conftest.py
    carrying an autouse fixture that alters runtime outcomes does NOT change the collected-id set and
    was never a frozen boundary member, so it slips protected_path_diff (not a frozen path), dep_hash
    (not a frozen path), and recollection (the id set is unchanged).

    technical (contract): freeze a scenario whose closure has NO tests/conftest.py; at HEAD COMMIT a new
    tests/conftest.py with an autouse fixture, and prove via the independent ``_collect_ids`` oracle that
    the collected-id set is unchanged. verify_contract_integrity must recompute the frozen boundary/
    config closure at HEAD, notice it gained a member, and report it: report.ok is False and an EXACT
    violation ("dep_hash", "tests/conftest.py"). (D-T2 resolved: a new in-closure config file maps to
    the dep_hash family — the closure hash-set gained a path — not an 8th boundary predicate.) A
    recollection/frozen-path-only impl misses it entirely (ok True).
    """
    conftest_rel = "tests/conftest.py"
    fz = _frozen(tmp_path, "boundary-config-add")
    assert conftest_rel not in fz.manifest["contract_paths"]
    frozen_ids = set(fz.manifest["collected_ids"])
    worktree = fz.scen.candidate_worktree

    # A collection-time config hook added AFTER freeze that neuters every test's runtime outcome. It
    # imports NO distribution (so it introduces no external_pin) and adds/removes no collected node id
    # (so recollection is inert) — it slips every current predicate yet silently rewrites behaviour.
    (worktree / conftest_rel).write_text(
        "def pytest_runtest_setup(item):\n"
        "    # Neuter every test's outcome at runtime — a real behaviour change added after freeze,\n"
        "    # invisible to the collected-id set and importing nothing external.\n"
        "    item.obj = lambda *a, **k: None\n"
    )
    _commit_all(worktree, "add a new higher-precedence tests/conftest.py after freeze")

    assert _collect_ids(worktree) == frozen_ids, (
        "fixture setup: the collected-id set must stay identical so recollection cannot catch this"
    )

    report = _verify(fz)
    assert report.ok is False
    assert any(v.predicate == "dep_hash" and v.detail == conftest_rel for v in report.violations)


# =============================================================== tracked bytecode (finding #12)


@pytest.mark.slow
def test_dirty_refusal_does_not_exempt_tracked_bytecode(tmp_path):
    """Finding #12: ``_is_cache_noise`` exempts ``.pyc``/``__pycache__`` members UNCONDITIONALLY, even
    when TRACKED (committed via ``git add -A``). A sourceless bytecode artifact that is TRACKED is not
    cache noise — it can execute altered logic during collection while the integrity gate blanket-skips
    it. The exemption must apply only to UNTRACKED cache noise; a tracked bytecode change must be caught.

    technical (contract): (1) a tracked ``tests/__pycache__/helper.pyc`` is committed into the frozen
    tree (never a boundary member) and then its committed bytes are CHANGED at HEAD -> verify reports
    report.ok False and a violation whose detail names the exact tracked path "tests/__pycache__/
    helper.pyc" (predicate left open per the finding: the change is caught, path named). (2) CONTROL: an
    UNTRACKED ``.pyc`` in a separate frozen scenario is genuine cache noise and stays exempted — verify
    reports report.ok True and no violation names a ``.pyc`` (no false refusal). Today the unconditional
    exemption misses the tracked change (ok True) and thus fails part (1).
    """
    from issueforge import contract

    pyc_rel = "tests/__pycache__/helper.pyc"

    # (1) TRACKED bytecode committed into the FROZEN tree (contract_commit == HEAD, a clean freeze),
    # then MODIFIED-BUT-UNCOMMITTED in the worktree at verify time. This is what exercises the
    # ``_dirty_refusal`` tracked-cache exemption (contract.py:1509 ``_is_cache_noise``) — NOT the
    # committed contract_commit..HEAD diff. A wrong fix that only handles a COMMITTED .pyc change at
    # HEAD (a diff-range fix) would still miss this uncommitted tracked-cache mutation and wrongly pass.
    fz = _frozen(tmp_path, "tracked-pyc", extra={pyc_rel: "original tracked bytecode\n"})
    worktree = fz.scen.candidate_worktree
    assert pyc_rel in set(_git(worktree, "ls-files", "--", pyc_rel).stdout.split()), (
        "fixture setup: the bytecode artifact must be TRACKED at the frozen contract_commit"
    )
    assert pyc_rel not in fz.manifest["contract_paths"], (
        "fixture setup: a .pyc is never a frozen boundary member — this proves the exemption, not the "
        "boundary, is what must catch it"
    )
    # Modify the tracked .pyc IN THE WORKTREE and DO NOT commit it.
    (worktree / pyc_rel).write_text("altered tracked bytecode\n")
    # Prove it is genuinely TRACKED and genuinely uncommitted-dirty at assert time.
    dirty = set(_git(worktree, "diff", "HEAD", "--name-only").stdout.split())
    assert pyc_rel in dirty, (
        "fixture setup: the tracked .pyc must be uncommitted-dirty (differ from HEAD) at verify time — "
        "this is what hits the _dirty_refusal tracked-cache exemption, not the commit-range diff"
    )

    # PRIMARY (pins the EXACT finding — the tracked-cache exemption in ``_dirty_refusal`` — not merely
    # "verify lacks dirty-tree integration"): call ``_dirty_refusal`` DIRECTLY. It must NAME the tracked
    # dirty .pyc, because a TRACKED sourceless bytecode artifact is not cache noise. Today
    # ``_is_cache_noise`` filters it unconditionally, so ``_dirty_refusal`` returns None here — the bug.
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    refusal = contract._dirty_refusal(worktree, head)
    assert refusal is not None and pyc_rel in refusal, (
        "a TRACKED .pyc byte change must be caught by _dirty_refusal and its path named, never exempted "
        f"as cache noise (got refusal={refusal!r})"
    )

    # INTEGRATION: the same tracked dirty .pyc must also be caught end-to-end through verify —
    # report.ok False naming the .pyc path, OR a typed refusal whose message names it.
    try:
        report = _verify(fz)
    except Exception as exc:  # a typed dirty-tree refusal is an acceptable fail-closed outcome
        assert pyc_rel in str(exc), (
            "verify refused the dirty tree but its message did not name the tracked .pyc path"
        )
    else:
        assert report.ok is False, (
            "verify silently exempted an uncommitted TRACKED .pyc byte change as cache noise"
        )
        assert any(v.detail == pyc_rel for v in report.violations), (
            "a TRACKED .pyc byte change must be caught and its path named, never exempted as cache noise"
        )

    # (2) CONTROL: an UNTRACKED .pyc is real cache noise and must stay exempted — no false refusal.
    ctl = _frozen(tmp_path, "untracked-pyc")
    noise = ctl.scen.candidate_worktree / "tests/__pycache__/noise.pyc"
    noise.parent.mkdir(parents=True, exist_ok=True)
    noise.write_text("uncommitted cache noise\n")  # NEVER git-added
    assert not _git(
        ctl.scen.candidate_worktree,
        "ls-files",
        "--",
        str(noise.relative_to(ctl.scen.candidate_worktree)),
    ).stdout.strip(), (
        "control setup: the noise .pyc must be UNTRACKED so it exercises the legitimate cache exemption"
    )
    ctl_report = _verify(ctl)
    assert ctl_report.ok is True
    assert not any(v.detail.endswith(".pyc") for v in ctl_report.violations)


# =============================================================== gate provisioner (finding #8)


@pytest.mark.slow
def test_engine_gate_resolves_deps_in_provisioned_authoritative_env_not_host(tmp_path, monkeypatch):
    """Finding #8: the engine's integrity gate provisions its collection under ``_gate_provisioner``
    (engine.py:481), which returns ``interpreter=sys.executable`` — the HOST. External pins must be
    re-resolved in the TARGET's provisioned authoritative env (a real venv via ``provision_environment``),
    never the host, or a host that happens to match the frozen version masks a real authoritative drift.

    D-T8 resolved: rather than a heavy engine-driven venv-diff, this pins the OBSERVABLE that the gate's
    authoritative pin re-resolution runs in a GENUINELY ISOLATED provisioned environment, not the host.
    A spy wraps ``contract._authoritative_versions`` (delegating to the real resolver so behavior is
    untouched) and records which interpreter each re-resolution ran under; the real engine gate is
    driven via ``engine.apply_revision`` (which derives its context entirely from persisted run state).

    Strengthening note (over the earlier draft): a bare ``interpreter != sys.executable`` is a TAUTOLOGY
    — a symlink/console-script wrapper/other path into the SAME host environment satisfies it while
    still resolving pins from the host's site-packages. The version-delta form (a frozen 4.10.0 vs an
    authoritative 4.9.0 producing ``("external_pin", "platformdirs 4.10.0->4.9.0")``) is NOT constructible
    as a currently-red / correctly-passable test on this branch: the only channel that can supply a 4.9.0
    authoritative env to the gate is ``engine._gate_provisioner`` itself (verify already resolves
    correctly in whatever provisioner it is handed — see
    ``test_external_pin_flags_re_resolved_dependency_version_delta``), so injecting that env MASKS the
    very host-vs-authoritative bug under test (the gate would pass today). This test therefore proves
    isolation directly: every re-resolution must run under an interpreter whose ``sys.prefix`` differs
    from the host's — a wrapper/symlink to the host shares the host prefix and is caught.

    technical (contract): freeze with platformdirs pinned 4.10.0 in a real venv and a frozen
    tests/conftest.py importing platformdirs; persist the run's candidate_worktree; drive
    ``engine.apply_revision(run_id, [], gateway, approver=...)``. The gate must re-resolve external pins
    at least once, under a provisioned interpreter whose ``sys.prefix`` is NOT the host's. Today the host
    provisioner re-resolves under the host interpreter (the host ``sys.prefix``), so this fails now; a
    provisioned-venv impl (its own prefix) passes.
    """
    from issueforge import contract, engine

    fz = _frozen(
        tmp_path,
        "gate-authoritative-env",
        extra={"tests/conftest.py": "import platformdirs  # noqa: F401\n"},
        provisioner=_real_pin_provisioner({"platformdirs": "4.10.0"}),
    )
    assert ("platformdirs", "4.10.0") in {(d, v) for d, v in fz.manifest["external_pins"]}

    # The engine gate takes no worktree kwarg — it derives context entirely from persisted run state.
    store.RunStore().apply(
        fz.run_id,
        lambda _r: {"candidate_worktree": str(fz.scen.candidate_worktree)},
    )

    # Spy the authoritative pin-resolution seam: record WHICH interpreter each re-resolution runs under.
    used_interpreters: list[object] = []
    real_authoritative = contract._authoritative_versions

    def _recording_authoritative(interpreter, env, names):
        used_interpreters.append(interpreter)
        return real_authoritative(interpreter, env, names)

    monkeypatch.setattr(contract, "_authoritative_versions", _recording_authoritative)

    class _ExplodingGateway:
        def __getattr__(self, _name):
            raise AssertionError("gateway must not be touched")

    try:
        engine.apply_revision(fz.run_id, [], _ExplodingGateway(), approver=lambda _plan: True)
    except Exception:
        # The gate may block (or a downstream step may raise); the observable is which interpreter the
        # authoritative pin re-resolution ran under, captured above BEFORE any raise.
        pass

    assert used_interpreters, (
        "the integrity gate never re-resolved external pins in a provisioned authoritative env "
        "(it provisioned the host interpreter, which cannot see the frozen dependency)"
    )

    # STRONG (closes the flagged tautology precisely): a bare ``interp != sys.executable`` proves only
    # TEXTUAL inequality, and even a differing ``sys.prefix`` is an imperfect proxy (a stray
    # system-site interpreter differs; an isolated container could share it). The EXACT observable for
    # "re-resolved in the provisioned authoritative env" is that the interpreter lives UNDER
    # IssueForge's OWNED STATE ROOT: ``_provision_default`` builds every authoritative venv at
    # ``state_root()/envs/...`` and hands back a wrapper file under that root, whereas the host
    # ``sys.executable`` is NEVER under the state root. Both sides are resolved so the macOS
    # ``/tmp -> /private/tmp`` normalization applies consistently. Today the gate re-resolves under the
    # host interpreter (outside the state root), so this fails now; a provisioned-venv impl passes.
    from issueforge.paths import state_root

    owned_root = state_root().resolve()
    for interp in used_interpreters:
        resolved = Path(str(interp)).resolve()
        assert owned_root == resolved or owned_root in resolved.parents, (
            f"the integrity gate re-resolved external pins under {resolved}, which is NOT within "
            f"IssueForge's owned state root {owned_root} — a host interpreter (sys.executable) is never "
            "under the state root, so the gate provisioned the host rather than the authoritative env"
        )


# ============================================================ round-2 remediation (#105, Codex NO-SHIP/6)
# Finding #4: _is_cache_noise exempts a tracked .pyc whenever its source .py EXISTS, so a forged-header
# mod.pyc + innocuous mod.py bypasses the dirty-tree guard. Finding #2: the gate's external-pin drift
# detection is tautological (it installs the frozen versions, then "re-resolves" them), so a candidate
# that changes a DECLARED dependency version is never caught; env-defining files must be frozen+hashed.
# Finding #1: the freeze provisioner leaves plugin autoload ON while the gate disables it, so a plugin
# discovered at freeze reads as a missing pin at verify (asymmetry). Committed xfail(strict=True) until
# Phase B flips them.


@pytest.mark.xfail(strict=True, reason="PENDING (#105 finding #4) — deferred, complete fix pending")
def test_dirty_refusal_does_not_exempt_tracked_bytecode_when_source_present(tmp_path):
    """Finding #4: a TRACKED ``.pyc`` whose source ``.py`` also exists is still cache-EXEMPTED today
    (``_is_cache_noise`` returns True the moment ``<mod>.py`` is present), so a forged-bytecode
    ``mod.pyc`` shipped alongside an innocuous ``mod.py`` slips the dirty-tree guard. A tracked bytecode
    byte-change is a contract mutation regardless of whether a source module exists, so it must be
    caught and its path named. (The sourceless case is already covered by
    ``test_dirty_refusal_does_not_exempt_tracked_bytecode``; this is the sourced sibling.)"""
    from issueforge import contract

    src_rel = "pkg/mod.py"
    pyc_rel = "pkg/__pycache__/mod.cpython-311.pyc"
    fz = _frozen(
        tmp_path,
        "tracked-pyc-sourced",
        extra={src_rel: "VALUE = 1\n", pyc_rel: "original tracked bytecode\n"},
    )
    worktree = fz.scen.candidate_worktree
    assert pyc_rel in set(_git(worktree, "ls-files", "--", pyc_rel).stdout.split())
    assert (worktree / src_rel).exists(), (
        "fixture: the source module must be present (the sourced case)"
    )
    assert pyc_rel not in fz.manifest["contract_paths"]

    (worktree / pyc_rel).write_text("altered tracked bytecode\n")
    dirty = set(_git(worktree, "diff", "HEAD", "--name-only").stdout.split())
    assert pyc_rel in dirty, "fixture: the tracked .pyc must be uncommitted-dirty at verify time"

    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    refusal = contract._dirty_refusal(worktree, head)
    assert refusal is not None and pyc_rel in refusal, (
        "a TRACKED .pyc byte change must be caught even when its source .py exists, never exempted as "
        f"cache noise (got refusal={refusal!r})"
    )


@pytest.mark.slow
def test_verify_flags_declared_dependency_version_drift_in_env_defining_file(tmp_path):
    """Finding #2: the external-pin gate installs the FROZEN versions into its venv and then re-resolves
    them, so the check is tautological — a candidate that changes a DECLARED dependency version is never
    caught. The env-defining files (requirements.txt / pyproject.toml / uv.lock / setup.cfg / Pipfile*)
    must be frozen+hashed at freeze and byte-compared at HEAD, so a changed declared pin fires
    ``external_pin`` naming the drifted file. Today the env file is not in the manifest's hashed set and
    a version change sails through clean."""
    fz = _frozen(tmp_path, "env-file-drift", extra={"requirements.txt": "platformdirs==4.10.0\n"})
    worktree = fz.scen.candidate_worktree
    (worktree / "requirements.txt").write_text("platformdirs==4.9.0\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "bump declared dep")

    report = _verify(fz)
    assert report.ok is False, (
        "a changed DECLARED dependency version in a frozen env-defining file must be caught"
    )
    assert any(
        v.predicate == "external_pin" and "requirements.txt" in v.detail for v in report.violations
    ), (
        "the env-file drift must fire external_pin naming the drifted env-defining file "
        f"(got {[(v.predicate, v.detail) for v in report.violations]})"
    )


def test_gate_provisioner_uses_symmetric_plugin_autoload_policy_with_freeze(tmp_path):
    """Finding #1: freeze and the integrity gate must provision under the SAME plugin-autoload policy.
    Freeze runs with autoload ON (``_provision_default``) and intentionally pins entry-point plugins as
    external deps (see ``test_freeze_pins_externally_autoloaded_plugin_from_provisioned_env``), but the
    gate wrapper DISABLED autoload — so a plugin freeze discovered+pinned read as a MISSING pin at verify,
    false-flagging a clean candidate. The gate provisioner's autoload policy must match freeze's exactly."""
    from issueforge import engine
    from issueforge.adapters.pytest_adapter import _provision_default

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    freeze_env = getattr(_provision_default(tmp_path / "a", None), "env", {}) or {}
    gate_env = getattr(engine._gate_provisioner()(tmp_path / "b", None), "env", {}) or {}
    assert freeze_env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == gate_env.get(
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
    ), (
        "the gate provisioner must apply the SAME plugin-autoload policy as freeze; today the gate "
        f"disables autoload ({gate_env.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD')!r}) while freeze does not "
        f"({freeze_env.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD')!r})"
    )
    # Equality alone is not enough (round-2 Codex #6): disabling autoload on BOTH paths would satisfy it
    # yet silently drop every entry-point plugin dependency from both closures. The shared policy must be
    # autoload ON — neither side sets PYTEST_DISABLE_PLUGIN_AUTOLOAD — so freeze genuinely pins
    # entry-point plugins (see test_freeze_pins_externally_autoloaded_plugin_...) and the gate re-resolves
    # them, rather than both blindly agreeing to see nothing.
    assert freeze_env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1", (
        "freeze must keep plugin autoload ON so entry-point plugin dependencies are discovered and pinned"
    )
    assert gate_env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") != "1", (
        "the gate must keep plugin autoload ON to re-resolve the plugin dependencies freeze pinned"
    )
