"""Committed PENDING acceptance suite for #193 — ``engine._collect_ids`` must source its pytest
collection command from the target's COMMITTED baseline (the same seam #189 built in ``contract``),
not the hardcoded bare repo-root ``["-m", "pytest"]``. That fourth bare-root seam makes the
engine-computed ``targeted_ids`` disagree with ``prove_red``'s ``added`` on a subdir-layout repo,
which reddens a live run as ``missing_targeted_id``.

Each test is ``@pytest.mark.xfail(strict=True, reason="PENDING (#193)")`` — it fails today for a
BEHAVIORAL reason (the bare-root command is captured, no fail-closed ``ValueError`` is raised, the
engine does not pause with the precise reason) and flips green when the fix lands. Symbols under
test are imported inside each test body. Dual-layer docstrings: a plain-English behavior line first,
then the ``technical (contract):`` golden values.

Fixtures are DEDICATED local copies modeled on ``tests/test_contract.py`` (``_subdir_scenario`` at
~L208, ``_invalid_baseline_scenario`` at ~L714, ``_provisioner``, ``_adapter``, ``_mk_run``) and
``tests/test_engine_candidate_poc.py`` (the ``run_candidate`` record/registry/store setup). No
SHARED acceptance helper is modified.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import store

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]

# --- subdir scenario (committed -o testpaths scoping, root-broken sibling module) ----------------
# Modeled on test_contract.py::_subdir_scenario. The committed baseline scopes pytest to a
# subdirectory via an ``-o testpaths`` OPTION (no positional path), a collection-broken module sits
# at the repo ROOT, and the working-tree config is poisoned to a bare-root command and left DIRTY in
# BOTH worktrees — the #189 committed-provenance trap.
_SUBDIR_CONFIG = 'baseline = ["-m", "pytest", "-o", "testpaths=pkg/tests"]\nframework = "pytest"\n'
_SUBDIR_BROKEN_CONFIG = 'baseline = ["-m", "pytest"]\nframework = "pytest"\n'
_SUBDIR_BASE_FILES = {
    "pkg/tests/test_base.py": (
        "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert True\n"
    ),
    "test_root_broken.py": "import _if_nonexistent_module_\n",
}
_SUBDIR_NEW_FILE = {"pkg/tests/test_new.py": "def test_new():\n    assert 1 == 2\n"}
_SUBDIR_NEW_ID = "pkg/tests/test_new.py::test_new"
# The command ``contract._committed_command`` yields for the subdir scenario: the committed baseline
# plus the force-loaded report-log reporter.
_GOLDEN_COMMAND = ["-m", "pytest", "-o", "testpaths=pkg/tests", "-p", "pytest_reportlog"]

# --- import-divergence variant (revised Test 2; amendment logged on #193) ------------------------
# The committed baseline ALSO carries ``-o pythonpath=pkg/src`` and the candidate's new test imports
# ``mylib`` from there. Bare-root collection cannot resolve ``mylib`` (no committed pythonpath), so
# ``pkg/tests/test_new.py`` COLLECTION-ERRORs and its id is ABSENT from a bare-root collect — unlike
# a plain sibling test, whose id current pytest still emits past a root-broken module. Under the
# committed command the id collects and fails at CALL phase (``assert mylib.X == 2`` vs ``X = 1``).
_IMPORTDIV_CONFIG = (
    'baseline = ["-m", "pytest", "-o", "testpaths=pkg/tests", "-o", "pythonpath=pkg/src"]\n'
    'framework = "pytest"\n'
)
_IMPORTDIV_BASE_FILES = {**_SUBDIR_BASE_FILES, "pkg/src/mylib.py": "X = 1\n"}
_IMPORTDIV_NEW_FILE = {
    "pkg/tests/test_new.py": "import mylib\n\n\ndef test_new():\n    assert mylib.X == 2\n"
}

# --- invalid committed-baseline forms (Test 3) ---------------------------------------------------
# Four committed ``.issueforge.toml`` shapes for which ``verify._committed_baseline`` -> (True, None)
# on a real committed git tree (verified against verify.py:507).
_INVALID_CONFIGS = {
    "missing_key": 'framework = "pytest"\n',
    "empty_list": 'baseline = []\nframework = "pytest"\n',
    "non_list": 'baseline = "pytest"\nframework = "pytest"\n',
    "malformed_toml": "baseline = [\n",
}
# A committed config with a valid baseline, for the invalid-baseline scenario's base suite so a
# genuine two-test green base exists. Only the INVALID form is committed in Test 3's tree, so this is
# never used there; the invalid-baseline run_candidate scenario (Test 4) uses the missing-key form.
_NO_BASELINE_CONFIG = 'framework = "pytest"\n'

_BASE_FILES = {
    "tests/test_base.py": "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert True\n"
}
_NEW_ID = "tests/test_new.py::test_new"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _init_repo_with_origin(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)


def _add_origin(repo: Path, sha: str) -> None:
    _git(repo, "remote", "add", "origin", "git@github.com:Owner/IssueForge.git")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)


def _subdir_scenario(
    root: Path,
    name: str,
    *,
    candidate_files: dict[str, str],
    config: str = _SUBDIR_CONFIG,
    base_files: dict[str, str] | None = None,
) -> SimpleNamespace:
    """A REAL two-commit repo whose committed baseline scopes pytest to ``pkg/tests`` via an
    ``-o testpaths`` option while a collection-broken module sits at the repo ROOT; after the base is
    committed (so the git OBJECT holds the good baseline) the working-tree ``.issueforge.toml`` is
    poisoned to the bare-root command and left DIRTY in BOTH the candidate and the copied base
    checkout. Dedicated local copy of test_contract.py::_subdir_scenario. ``config``/``base_files``
    default to the Test 1 shape; the import-divergence variant (Test 2) overrides both."""
    repo = root / name
    repo.mkdir(parents=True)
    _init_repo_with_origin(repo)
    (repo / ".issueforge.toml").write_text(config)
    _write(repo, _SUBDIR_BASE_FILES if base_files is None else base_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _add_origin(repo, base_sha)
    base_checkout = root / f"{name}-base"
    shutil.copytree(repo, base_checkout)
    _write(repo, candidate_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    candidate_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # Committed-provenance trap: poison the filesystem config in BOTH worktrees, leaving the good
    # committed git object as the only source of the scoped command.
    (repo / ".issueforge.toml").write_text(_SUBDIR_BROKEN_CONFIG)
    (base_checkout / ".issueforge.toml").write_text(_SUBDIR_BROKEN_CONFIG)
    return SimpleNamespace(
        base_checkout=base_checkout,
        candidate_worktree=repo,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )


def _invalid_committed_scenario(root: Path, name: str, *, config: str) -> SimpleNamespace:
    """A REAL two-commit repo whose COMMITTED ``.issueforge.toml`` is one of the invalid baseline
    forms, so ``verify._committed_baseline`` -> (True, None). A candidate adds a genuine red under
    ``tests/``. Dedicated local copy modeled on test_contract.py::_invalid_baseline_scenario."""
    repo = root / name
    repo.mkdir(parents=True)
    _init_repo_with_origin(repo)
    (repo / ".issueforge.toml").write_text(config)
    _write(repo, _BASE_FILES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _add_origin(repo, base_sha)
    base_checkout = root / f"{name}-base"
    shutil.copytree(repo, base_checkout)
    _write(repo, {"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    candidate_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return SimpleNamespace(
        base_checkout=base_checkout,
        candidate_worktree=repo,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )


def _provisioner():
    """A host-side provisioner seam: the host interpreter + an allowlist env, no venv, no network.
    Local copy of test_contract.py::_provisioner. Plugin autoload is disabled so a developer's
    installed pytest plugins cannot alter collection."""
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


def _adapter():
    from issueforge.adapters.pytest_adapter import PytestAdapter

    return PytestAdapter()


def _mk_run(run_id: str, *, buildable: bool = True, revision_applied: bool = True) -> str:
    """Mint a running, buildable, revision-applied run record so ``prove_red`` has a real RunStore
    run to persist onto. Local copy of test_contract.py::_mk_run."""
    shape = {"classification": "buildable" if buildable else "blocked", "write_scope": []}
    record = {"status": "running", "shape": shape}
    if revision_applied:
        record["revision_ledger"] = {"op-1": "fingerprint"}
    store.RunStore().apply(run_id, lambda _r: record, create=True)
    return run_id


class _RecordingAdapter:
    """Delegates the real ``PytestAdapter`` seam while capturing every ``canonical_collect``
    invocation command and forcing HOST-side provisioning (no venv) so collection is fast. Used to
    prove ``_collect_ids`` sources its command from the committed baseline, command-for-command."""

    def __init__(self) -> None:
        self._real = _adapter()
        self.recorded: list[tuple[str, tuple[str, ...]]] = []

    def provision_environment(self, worktree, frozen_deps=None, *, provisioner=None):
        return self._real.provision_environment(worktree, frozen_deps, provisioner=_provisioner())

    def canonical_collect(self, invocation):
        self.recorded.append((str(Path(invocation.worktree).resolve()), tuple(invocation.command)))
        return self._real.canonical_collect(invocation)

    def select_baseline(self, base_ids, candidate_ids):
        return self._real.select_baseline(base_ids, candidate_ids)


class _PoisonAdapter:
    """A recording adapter that counts ``canonical_collect`` invocations (and returns an empty,
    non-crashing collection) so a test can prove targeted-id computation FAILS CLOSED before any
    collection runs. Host-side provisioning, no venv."""

    def __init__(self) -> None:
        self._real = _adapter()
        self.collect_calls = 0

    def provision_environment(self, worktree, frozen_deps=None, *, provisioner=None):
        return self._real.provision_environment(worktree, frozen_deps, provisioner=_provisioner())

    def canonical_collect(self, invocation):
        self.collect_calls += 1
        return SimpleNamespace(ids=(), returncode=0, timed_out=False)

    def select_baseline(self, base_ids, candidate_ids):
        return self._real.select_baseline(base_ids, candidate_ids)


# =============================================================== Test 1: committed-command sourcing


def test_added_node_ids_runs_committed_command_on_both_worktrees(tmp_path, monkeypatch):
    """The engine's targeted-id computation collects with the target's COMMITTED baseline command on
    a subdir-layout repo, verified command-for-command on BOTH worktrees (the throwaway detached base
    worktree and the candidate), not a bare repo-root pytest.

    technical (contract): subdir scenario (committed baseline ``["-m", "pytest", "-o",
    "testpaths=pkg/tests"]``, root-broken ``test_root_broken.py``, working-tree config poisoned DIRTY
    to ``["-m", "pytest"]`` in BOTH worktrees, candidate adds ``pkg/tests/test_new.py::test_new``)
    with a RECORDING adapter delegating to the real ``PytestAdapter.canonical_collect`` while
    capturing each ``invocation.command``, plus a delegation spy wrapping
    ``contract._committed_command``: ``engine._added_node_ids(candidate_worktree, base_sha, adapter)``
    -> ``("pkg/tests/test_new.py::test_new",)`` AND exactly TWO distinct worktrees were collected (one
    == the candidate worktree) AND every recorded collection command ==
    ``["-m", "pytest", "-o", "testpaths=pkg/tests", "-p", "pytest_reportlog"]`` AND the
    ``contract._committed_command`` spy saw the SAME two worktrees. Today's bare-root impl records
    ``["-m", "pytest"]`` and never calls the committed-command seam, so the command assertion fails.
    """
    from issueforge import contract, engine

    scen = _subdir_scenario(tmp_path, "t1", candidate_files=_SUBDIR_NEW_FILE)
    adapter = _RecordingAdapter()

    seen_worktrees: list[str] = []
    original = contract._committed_command

    def _spy(worktree):
        seen_worktrees.append(str(Path(worktree).resolve()))
        return original(worktree)

    monkeypatch.setattr(contract, "_committed_command", _spy)

    added = engine._added_node_ids(scen.candidate_worktree, scen.base_sha, adapter)

    assert added == (_SUBDIR_NEW_ID,)
    assert adapter.recorded, "no collection was recorded"
    recorded_worktrees = {wt for wt, _ in adapter.recorded}
    assert len(recorded_worktrees) == 2, (
        "expected two distinct worktrees collected (base + candidate)"
    )
    assert str(Path(scen.candidate_worktree).resolve()) in recorded_worktrees
    for _wt, command in adapter.recorded:
        assert list(command) == _GOLDEN_COMMAND, (
            f"collection ran {list(command)!r}, not the committed command {_GOLDEN_COMMAND!r}"
        )
    assert set(seen_worktrees) == recorded_worktrees, (
        "contract._committed_command was not called for each collected worktree"
    )


# =============================================================== Test 2: the live failure ends


def test_prove_red_accepts_engine_computed_targeted_ids_on_subdir_layout(tmp_path):
    """The live ``missing_targeted_id`` failure ends: ``prove_red`` accepts the ENGINE-computed
    targeted ids on a subdir layout whose committed baseline carries a pythonpath the new test needs
    to even collect (the import-divergence fixture; contract amendment logged on #193).

    technical (contract): committed baseline ``["-m", "pytest", "-o", "testpaths=pkg/tests", "-o",
    "pythonpath=pkg/src"]`` with ``pkg/src/mylib.py`` (``X = 1``) committed at base, root-broken
    ``test_root_broken.py`` at the repo root, working-tree config poisoned DIRTY in both worktrees;
    the candidate adds ``pkg/tests/test_new.py`` = ``import mylib`` + ``assert mylib.X == 2``.
    Bare-root collection cannot resolve ``mylib`` so the new id is ABSENT from a bare-root collect;
    the committed command collects it and it fails at CALL phase. Real adapter, real prove_red, host
    provisioner: ``contract.prove_red(run_id, targeted_ids=engine._added_node_ids(candidate_worktree,
    base_sha, adapter), base_checkout=..., candidate_worktree=..., base_sha=..., adapter=...,
    provisioner=...)`` -> ``accepted is True`` and ``reason == "behavioral_red"`` — never
    ``"missing_targeted_id"``. Today ``_added_node_ids`` -> ``()`` on this fixture and prove_red
    rejects ``missing_targeted_id`` (the run-acfe49b557fd live rejection), so this test fails.
    """
    from issueforge import contract, engine

    scen = _subdir_scenario(
        tmp_path,
        "t2",
        candidate_files=_IMPORTDIV_NEW_FILE,
        config=_IMPORTDIV_CONFIG,
        base_files=_IMPORTDIV_BASE_FILES,
    )
    adapter = _adapter()
    run_id = _mk_run("run-193-t2")

    targeted = engine._added_node_ids(scen.candidate_worktree, scen.base_sha, adapter)
    rp = contract.prove_red(
        run_id,
        targeted_ids=targeted,
        base_checkout=scen.base_checkout,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha,
        adapter=adapter,
        provisioner=_provisioner(),
    )

    assert rp.accepted is True, (
        f"prove_red rejected with reason {rp.reason!r} (targeted={targeted!r})"
    )
    assert rp.reason == "behavioral_red"


# =============================================================== Test 3: fail-closed on invalid baseline


@pytest.mark.parametrize("form", list(_INVALID_CONFIGS))
def test_added_node_ids_fails_closed_on_invalid_committed_baseline(tmp_path, form):
    """Targeted-id computation FAILS CLOSED on every invalid committed-baseline form, raising rather
    than falling back to a bare-root collection, and never runs any collection at all.

    technical (contract): the committed ``.issueforge.toml`` is one of four invalid forms — missing
    ``baseline`` key, ``baseline = []``, non-list ``baseline = "pytest"``, malformed TOML
    ``baseline = [`` — each committed at HEAD of a real git tree (``verify._committed_baseline`` ->
    ``(True, None)``): ``engine._added_node_ids(candidate_worktree, base_sha, adapter)`` raises
    ``ValueError`` AND the poison/recording adapter proves ``canonical_collect`` ran ZERO times.
    Today's bare-root impl ignores the committed config, collects normally, and returns an id set
    without raising.
    """
    from issueforge import engine

    scen = _invalid_committed_scenario(tmp_path, f"t3-{form}", config=_INVALID_CONFIGS[form])
    adapter = _PoisonAdapter()

    with pytest.raises(ValueError):
        engine._added_node_ids(scen.candidate_worktree, scen.base_sha, adapter)

    assert adapter.collect_calls == 0, (
        "no collection may run when the committed baseline is invalid"
    )


# =============================================================== Test 4: engine pauses at the seam


class _ProveRedSentinel:
    """Records every ``prove_red`` call so a test can prove the engine paused BEFORE the red proof."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(accepted=False, reason="not_red", records=(), added_ids=())


class _RecordingApprover:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, review):
        self.calls += 1
        return True


def _authoring_invoke(candidate_worktree: Path):
    """A fake provider ``invoke`` that writes a genuine UNTRACKED test file with a call-phase
    assertion failure into the candidate worktree, mirroring test_engine_candidate_poc.py's authoring
    stand-in. It never commits."""

    def _invoke(profile, prompt, *, cwd, run_id=None, role="primary", timeout=None, **_kw):
        target = Path(cwd) / "tests" / "test_new.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_new():\n    assert 1 == 2\n")
        return SimpleNamespace(role=role, provider="fake", stdout="authored", returncode=0)

    return _invoke


def test_run_candidate_pauses_baseline_command_missing_at_engine_boundary(tmp_path):
    """``run_candidate`` pauses with pause_reason ``baseline_command_missing`` at the engine boundary,
    BEFORE ``prove_red``, when the committed baseline is invalid — instead of computing bare-root
    targeted ids and pressing on into the red proof.

    technical (contract): a dedicated fixture (candidate worktree ``HEAD == base_sha``, the invalid
    ``framework = "pytest"``-only config committed at HEAD, a registered normal checkout for adapter
    resolution), a fake provider invoke that writes a genuine UNTRACKED failing test file, a
    call-recording ``prove_red`` sentinel, and a recording approver:
    ``engine.run_candidate(record, profile=..., approver=..., invoke=..., prove_red=sentinel)`` ->
    a ``CandidateResult`` with ``paused is True`` and ``pause_reason == "baseline_command_missing"``,
    the prove_red sentinel called ZERO times, the approver ZERO times, the candidate HEAD still ==
    base_sha, and no unhandled exception. Today the run computes bare-root targeted ids, invokes the
    prove_red sentinel, and pauses later as ``red_proof_rejected`` — the wrong reason.
    """
    from issueforge import engine, registry

    # A registered normal checkout (committed pytest config + origin) so run_candidate can resolve the
    # adapter from the registry entry.
    base_checkout = tmp_path / "registered"
    base_checkout.mkdir()
    _init_repo_with_origin(base_checkout)
    (base_checkout / ".issueforge.toml").write_text('baseline = ["pytest"]\nframework = "pytest"\n')
    _write(base_checkout, _BASE_FILES)
    _git(base_checkout, "add", "-A")
    _git(base_checkout, "commit", "-qm", "base")
    _add_origin(base_checkout, _git(base_checkout, "rev-parse", "HEAD").stdout.strip())
    alias = "DandD"
    registry.register(alias, str(base_checkout))

    # The candidate worktree: HEAD == base_sha, invalid (no-baseline) config committed at HEAD.
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    _init_repo_with_origin(candidate)
    (candidate / ".issueforge.toml").write_text(_NO_BASELINE_CONFIG)
    _write(candidate, _BASE_FILES)
    _git(candidate, "add", "-A")
    _git(candidate, "commit", "-qm", "base")
    base_sha = _git(candidate, "rev-parse", "HEAD").stdout.strip()
    _add_origin(candidate, base_sha)

    run_id = "run-193-t4"
    fields = {
        "run_id": run_id,
        "status": "running",
        "repo": alias,
        "issue": "DandD#111 GREET-BEHAVIOR: greet(name) must return 'hi <name>'",
        "candidate_worktree": str(candidate),
        "base_sha": base_sha,
        "write_scope": ["src/dandd/greet.py"],
        "contract_paths": ["tests/test_new.py"],
        "acceptance_command": ["pytest", "tests/test_new.py"],
        "baseline_command": ["pytest"],
    }
    store.RunStore().apply(run_id, lambda _r: fields, create=True)
    record = store.RunStore().read(run_id)

    sentinel = _ProveRedSentinel()
    approver = _RecordingApprover()

    result = engine.run_candidate(
        record,
        profile=SimpleNamespace(name="fake"),
        approver=approver,
        invoke=_authoring_invoke(candidate),
        prove_red=sentinel,
    )

    assert result.paused is True
    assert result.pause_reason == "baseline_command_missing", (
        f"expected baseline_command_missing at the engine seam, got {result.pause_reason!r}"
    )
    assert sentinel.calls == 0, "prove_red must never run once the committed baseline is invalid"
    assert approver.calls == 0, "the approver must never run on an invalid committed baseline"
    assert (
        subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        == base_sha
    )
