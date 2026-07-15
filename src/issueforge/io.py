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

from pathlib import Path

from issueforge.paths import state_root


class BoundaryViolation(RuntimeError):
    """A write target resolved outside IssueForge's allowed roots."""


class WriteSeam:
    """Guards every filesystem write against a set of allowed roots."""

    def __init__(self, allowed_roots: list[Path] | None = None) -> None:
        self._roots = [Path(state_root())]
        for root in allowed_roots or []:
            self.allow(root)

    def allow(self, root: Path) -> None:
        """Register an additional allowed root (e.g. a target repo's worktree)."""
        self._roots.append(Path(root))

    def _checked(self, path: Path) -> Path:
        target = Path(path).resolve()
        for root in self._roots:
            resolved = root.resolve()
            if target == resolved or resolved in target.parents:
                return target
        raise BoundaryViolation(f"write to {target} is outside IssueForge's allowed roots")

    def write_text(self, path: Path, data: str, encoding: str = "utf-8") -> Path:
        target = self._checked(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding=encoding)
        return target
