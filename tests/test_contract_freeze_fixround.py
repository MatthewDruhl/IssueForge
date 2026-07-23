"""Unit tests locking the S12 freeze fix-round fail-closed behaviors (PR #89 build-gate findings).

These pin correctness the authored acceptance suite does not exercise: a discovered-but-untracked
closure dependency fails the freeze closed (F12), and a committed symlink whose target is an excluded
editable SUT is not re-frozen through the link (F20). Each drives the REAL ``freeze_contract`` seam
over a real two-commit git repo, never a hand-fed manifest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import contract, store
from issueforge.adapters.pytest_adapter import PytestAdapter

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]
_CONFIG = 'baseline = ["-m", "pytest"]\nframework = "pytest"\n'


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))


def _provisioner():
    def _provision(worktree, frozen_deps=None):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        return SimpleNamespace(interpreter=sys.executable, env=env, network=False)

    return _provision


def _base_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(_CONFIG)
    _write(repo, {"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"})
    return repo


def _finish_commit(repo: Path) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "remote", "add", "origin", "git@github.com:Owner/IssueForge.git")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)
    return sha


def _seed_run(run_id: str, sha: str, *, write_scope=()) -> str:
    ws = [{"op": "edit", "path": p, "justification": "sut"} for p in write_scope]
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "status": "running",
            "shape": {"classification": "buildable", "write_scope": ws},
            "revision_ledger": {"op-1": "fp"},
            "red_proof": {
                "accepted": True,
                "reason": "behavioral_red",
                "base_sha": sha,
                "head_sha": sha,
                "added_ids": ["tests/test_new.py::test_x"],
                "records": [],
            },
            "contract_review": {
                "verdict": "done",
                "outcome": "done",
                "head_sha": sha,
                "reviewer_session_id": "rev-1",
                "authoring_session_id": "auth-1",
                "provider": "cli",
                "findings": [],
            },
        },
        create=True,
    )
    return run_id


def _freeze(run_id: str, repo: Path, sha: str):
    return contract.freeze_contract(
        run_id,
        candidate_worktree=repo,
        base_sha=sha,
        adapter=PytestAdapter(),
        provisioner=_provisioner(),
        approver=lambda _p: True,
    )


def test_untracked_discovered_dependency_fails_closed(tmp_path):
    """A closure dependency present on disk but NOT committed fails the freeze closed, naming it —
    never silently omitted from the protected set (F12)."""
    repo = _base_repo(tmp_path, "untracked")
    _write(repo, {"tests/conftest.py": "from helpers import H\n"})
    sha = _finish_commit(repo)
    # helpers.py appears on disk only AFTER the commit: present at collection (so discovery reaches
    # it through the conftest) yet UNTRACKED (git diff HEAD ignores it, so the dirty check passes).
    _write(repo, {"tests/helpers.py": "H = 1\n"})
    run = _seed_run("run-untracked", sha)
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, repo, sha)
    assert "tests/helpers.py" in str(excinfo.value)
    assert not store.RunStore().replay_events(run) or all(
        e.get("transition") != "freeze" for e in store.RunStore().replay_events(run)
    )


def test_symlink_to_excluded_sut_is_not_refrozen(tmp_path):
    """A committed symlink whose resolved target is an excluded editable SUT is not re-frozen through
    the link — the editable-SUT identity stays editable consistently (F20)."""
    repo = _base_repo(tmp_path, "symsut")
    _write(
        repo,
        {
            "app/calc.py": "def calc():\n    return 1\n",
            "tests/test_new.py": "from app.calc import calc\n\n\ndef test_x():\n    assert calc() == 2\n",
        },
    )
    os.symlink(
        os.path.relpath(repo / "app/calc.py", repo / "tests"),
        repo / "tests/calc_link.py",
    )
    sha = _finish_commit(repo)
    run = _seed_run("run-symsut", sha, write_scope=("app/calc.py",))
    res = _freeze(run, repo, sha)
    assert res.approved is True
    assert "app/calc.py" in res.manifest["excluded_sut"]
    assert "app/calc.py" not in res.manifest["contract_paths"]
    # The symlink to the excluded SUT is neither protected nor hashed (no re-freeze via the link).
    assert "tests/calc_link.py" not in res.manifest["contract_paths"]
    assert "tests/calc_link.py" not in res.manifest["dep_hashes"]
