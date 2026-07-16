"""Acceptance tests for the runtime IO write seam (issue #5, slice S25)."""

import subprocess
from pathlib import Path

import pytest

from issueforge.io import BoundaryViolation, WriteSeam


def commit_empty(repo: Path) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=IssueForge Tests",
            "-c",
            "user.email=tests@issueforge.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "base",
        ],
        check=True,
    )


def test_seam_writes_under_state_root(tmp_path: Path, monkeypatch) -> None:
    """Tracer: a write whose target resolves under an allowed root goes through."""
    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path))
    seam = WriteSeam()

    target = seam.write_text(tmp_path / "runs" / "r1.json", '{"ok": true}')

    assert target.read_text() == '{"ok": true}'


def test_seam_blocks_write_outside_allowed_roots(tmp_path: Path, monkeypatch) -> None:
    """A target outside every allowed root raises and never touches disk."""
    allowed = tmp_path / "state"
    allowed.mkdir()
    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(allowed))
    seam = WriteSeam()
    outside = tmp_path / "elsewhere" / "leak.txt"

    with pytest.raises(BoundaryViolation) as exc:
        seam.write_text(outside, "nope")

    assert str(outside) in str(exc.value)
    assert not outside.exists()


def test_seam_allows_registered_worktree(tmp_path: Path) -> None:
    """A worktree registered via allow() becomes a permitted write root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit_empty(repo)
    worktree = tmp_path / "worktrees" / "repo-run1"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True)
    seam = WriteSeam()
    seam.allow(worktree, registered_repo=repo)

    target = seam.write_text(worktree / "src" / "new.py", "x = 1\n")

    assert target.read_text() == "x = 1\n"


def test_seam_rejects_normal_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    with pytest.raises(BoundaryViolation, match="not a linked Git worktree root"):
        WriteSeam().allow(repo, registered_repo=repo)


def test_seam_rejects_unrelated_linked_worktree(tmp_path: Path) -> None:
    registered = tmp_path / "registered"
    unrelated = tmp_path / "unrelated"
    subprocess.run(["git", "init", "-q", str(registered)], check=True)
    subprocess.run(["git", "init", "-q", str(unrelated)], check=True)
    commit_empty(unrelated)
    worktree = tmp_path / "unrelated-worktree"
    subprocess.run(
        ["git", "-C", str(unrelated), "worktree", "add", "-q", str(worktree)], check=True
    )

    with pytest.raises(BoundaryViolation, match="does not belong"):
        WriteSeam().allow(worktree, registered_repo=registered)


def test_seam_rejects_copied_worktree_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit_empty(repo)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True)
    counterfeit = tmp_path / "counterfeit"
    counterfeit.mkdir()
    (counterfeit / ".git").write_text((worktree / ".git").read_text())

    with pytest.raises(BoundaryViolation, match="not registered"):
        WriteSeam().allow(counterfeit, registered_repo=repo)


def test_seam_revalidates_worktree_before_each_write(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    commit_empty(repo)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True)
    seam = WriteSeam()
    seam.allow(worktree, registered_repo=repo)
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(worktree)], check=True)

    with pytest.raises(BoundaryViolation, match="not a Git worktree"):
        seam.write_text(worktree / "recreated.txt", "nope")
    assert not worktree.exists()


def test_seam_raises_in_real_subprocess_against_marvin_shaped_target(tmp_path: Path) -> None:
    """The guard is live at runtime: a real child process cannot write a MARVIN-shaped target."""
    import os
    import subprocess
    import sys

    state = tmp_path / "state"
    state.mkdir()
    # MARVIN-shaped path (contains marvin/state/projects.md) but safely under tmp, outside allowed roots.
    marvin_target = tmp_path / "marvin" / "state" / "projects.md"
    code = (
        "from issueforge.io import WriteSeam\n"
        f"WriteSeam().write_text(r'{marvin_target}', 'pwned')\n"
    )
    env = {**os.environ, "ISSUEFORGE_STATE_HOME": str(state)}

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)

    assert result.returncode != 0
    assert "BoundaryViolation" in result.stderr
    assert not marvin_target.exists()


def test_state_root_honors_env_override(tmp_path: Path, monkeypatch) -> None:
    from issueforge.paths import state_root

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "custom"))
    assert state_root() == tmp_path / "custom"


def test_state_root_defaults_to_xdg_under_home(monkeypatch) -> None:
    from issueforge.paths import state_root

    monkeypatch.delenv("ISSUEFORGE_STATE_HOME", raising=False)
    assert state_root() == Path.home() / ".local" / "state" / "issueforge"
