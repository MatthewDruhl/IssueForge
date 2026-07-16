"""The single guarded write seam for IssueForge's own filesystem writes.

Every write the engine issues through Python goes through here. A target that does
not resolve under IssueForge's state root or a registered worktree raises
``BoundaryViolation`` — the control exists *before* any writing module, so the
MARVIN-write boundary (US-11.6) is structural, not observational.

Git subprocess operations are a separate boundary (S6's isolation proof); this seam
governs only IssueForge's own ``open``/``write`` calls, and is the one module the
write-surface lint exempts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from issueforge.paths import state_root


class BoundaryViolation(RuntimeError):
    """A write target resolved outside IssueForge's allowed roots."""


class WriteSeam:
    """Guards every filesystem write against a set of allowed roots."""

    def __init__(self) -> None:
        self._roots: dict[Path, Path | None] = {Path(state_root()).resolve(): None}

    def allow(self, root: Path, *, registered_repo: Path) -> None:
        """Register a linked Git worktree as an additional allowed root."""
        root = Path(root).resolve()
        registered_repo = Path(registered_repo).resolve()
        self._validate_worktree(root, registered_repo)
        self._roots[root] = registered_repo

    def _validate_worktree(self, root: Path, registered_repo: Path) -> None:
        worktree = self._git_facts(root)
        repository = self._git_facts(registered_repo)
        if worktree is None:
            raise BoundaryViolation(f"{root} is not a Git worktree")
        if repository is None or repository[0] != registered_repo or repository[1] != repository[2]:
            raise BoundaryViolation(f"{registered_repo} is not a normal Git checkout root")
        top, git_dir, common_dir = worktree
        if top != root or git_dir == common_dir:
            raise BoundaryViolation(f"{root} is not a linked Git worktree root")
        if common_dir != repository[2]:
            raise BoundaryViolation(f"{root} does not belong to {registered_repo}")
        if root not in self._registered_worktrees(registered_repo):
            raise BoundaryViolation(f"{root} is not registered with {registered_repo}")

    @classmethod
    def _git_facts(cls, root: Path) -> tuple[Path, Path, Path] | None:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--show-toplevel",
                "--git-dir",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = result.stdout.splitlines()
        if result.returncode != 0 or len(lines) != 3:
            return None
        return tuple(cls._git_path(root, value) for value in lines)

    @staticmethod
    def _registered_worktrees(repository: Path) -> set[Path]:
        result = subprocess.run(
            ["git", "-C", str(repository), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return set()
        return {
            Path(field.removeprefix("worktree ")).resolve()
            for field in result.stdout.split("\0")
            if field.startswith("worktree ")
        }

    @staticmethod
    def _git_path(root: Path, value: str) -> Path:
        path = Path(value)
        return (path if path.is_absolute() else root / path).resolve()

    def _checked(self, path: Path) -> Path:
        target = Path(path).resolve()
        for root, registered_repo in self._roots.items():
            if target == root or root in target.parents:
                if registered_repo is not None:
                    self._validate_worktree(root, registered_repo)
                return target
        raise BoundaryViolation(f"write to {target} is outside IssueForge's allowed roots")

    def write_text(self, path: Path, data: str, encoding: str = "utf-8") -> Path:
        target = self._checked(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding=encoding)
        return target
