"""Filesystem roots for IssueForge.

This is the ONLY module permitted to derive a path from ``__file__``; the boundary
lint (issue #5) enforces that. Everything else asks this module for a root.
"""

from __future__ import annotations

import os
from pathlib import Path


def package_root() -> Path:
    """The installed ``issueforge`` package directory."""
    return Path(__file__).parent


def state_root() -> Path:
    """IssueForge's own state root (the seam always permits writes under it)."""
    override = os.environ.get("ISSUEFORGE_STATE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "issueforge"
