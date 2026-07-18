"""Shared fixtures for the acceptance suites.

``make_git_repo`` is the temp-git-repo factory named in issue #7 (S3): it builds a throwaway
Git repository with a chosen ``origin`` URL, a resolvable remote default branch
(``refs/remotes/origin/HEAD``), and an optionally-committed ``.issueforge.toml``, so registry
tests can register real clones without a network. ``isolated_state_home`` points
``ISSUEFORGE_STATE_HOME`` at a per-test directory so registration never reads or writes the
developer's real state root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_GIT_ID = ["-c", "user.name=IssueForge Tests", "-c", "user.email=tests@issueforge.invalid"]

DEFAULT_CONFIG = 'baseline = ["pytest"]\nframework = "pytest"\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path, monkeypatch) -> Path:
    """Isolate IssueForge's state root per test so registration never touches real home."""
    home = tmp_path / "state-home"
    home.mkdir()
    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(home))
    return home


@pytest.fixture
def make_git_repo(tmp_path):
    """Return a factory building a temp Git repo with a chosen origin, branch, and config.

    ``branch`` is the checked-out local branch. ``default_branch`` (falling back to ``branch``)
    is what ``refs/remotes/origin/HEAD`` points at — the offline source of a clone's remote
    default branch. ``set_origin_head=False`` leaves it unset (unresolvable default branch).
    ``dirty`` writes divergent working-tree content AFTER the commit, without staging it.
    """

    def _make(
        name: str = "repo",
        *,
        origin: str | None = "git@github.com:Owner/DandD.git",
        branch: str = "main",
        default_branch: str | None = None,
        set_origin_head: bool = True,
        config: str | None = DEFAULT_CONFIG,
        commit_config: bool = True,
        dirty: str | None = None,
        parent: Path | None = None,
    ) -> Path:
        repo = (parent or tmp_path) / name
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
        # A seed commit so HEAD exists even when the config is left uncommitted.
        subprocess.run(
            ["git", "-C", str(repo), *_GIT_ID, "commit", "--allow-empty", "-qm", "seed"],
            check=True,
        )
        if origin is not None:
            _git(repo, "remote", "add", "origin", origin)
            if set_origin_head:
                target = default_branch or branch
                _git(
                    repo,
                    "symbolic-ref",
                    "refs/remotes/origin/HEAD",
                    f"refs/remotes/origin/{target}",
                )
        if config is not None:
            (repo / ".issueforge.toml").write_text(config)
            if commit_config:
                _git(repo, "add", ".issueforge.toml")
                subprocess.run(
                    ["git", "-C", str(repo), *_GIT_ID, "commit", "-qm", "cfg"], check=True
                )
        if dirty is not None:
            (repo / ".issueforge.toml").write_text(dirty)
        return repo

    return _make
