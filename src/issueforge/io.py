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

import os
import subprocess
import tempfile
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

    def allow_scratch(self, root: Path) -> None:
        """Permit writes under a caller-owned EPHEMERAL scratch directory (transient working
        material, e.g. the review inputs materialized for a fresh reviewer session), which is
        neither the state root nor a registered worktree. No Git-worktree validation applies: the
        directory is caller-owned scratch, NOT persisted run state, so it carries no registered repo.
        """
        self._roots[Path(root).resolve()] = None

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

    def write_text_atomic(self, path: Path, data: str, encoding: str = "utf-8") -> Path:
        """Write ``data`` to ``path`` atomically: temp file in the SAME dir, fsync, os.replace.

        The temp file is created with ``mkstemp`` in ``path``'s parent so ``os.replace`` is a
        rename within one directory (atomic on POSIX). A failed replace leaves the previous file
        intact and drops no temp file. The target must resolve under an allowed root.
        """
        target = self._checked(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        # fsync the parent directory so the rename itself is durable (the dir entry survives a crash).
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return target

    def append_text(self, path: Path, data: str, encoding: str = "utf-8") -> Path:
        """Append ``data`` to ``path`` without rewriting any preceding committed byte (append-only).

        Self-heals a torn final line first: if the file is non-empty and does not end in a newline,
        a crash left an unterminated tail. Truncate back to the last newline (dropping only the torn
        tail) BEFORE appending, so the new line can never fuse onto a partial line and become a
        permanent malformed middle line. The caller serializes concurrent appends (store lock).
        """
        target = self._checked(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if existing and not existing.endswith(b"\n"):
                keep = existing.rfind(b"\n") + 1  # last newline + 1; 0 drops a lone torn line
                with open(target, "rb+") as handle:
                    handle.truncate(keep)
        with open(target, "a", encoding=encoding) as handle:
            handle.write(data)
        return target

    def open_lock(self, path: Path) -> int:
        """Open (creating if needed) an advisory-lock file under an allowed root; return its fd.

        Kept in the seam so the raw ``os.open`` stays inside the sanctioned write module. The
        caller owns the returned fd (flock + close).
        """
        target = self._checked(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return os.open(str(target), os.O_CREAT | os.O_RDWR, 0o644)
