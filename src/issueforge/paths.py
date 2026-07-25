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


def providers_config() -> Path:
    """The operator-level providers TOML: ``ISSUEFORGE_PROVIDERS`` override, else the default home path."""
    override = os.environ.get("ISSUEFORGE_PROVIDERS")
    if override:
        return Path(override)
    return Path.home() / ".config" / "issueforge" / "providers.toml"


def run_dir(run_id: str) -> Path:
    """The directory holding one run's persisted manifest, events, and artifacts.

    Mirrors ``store.run_dir`` (the store writes here); exposed on the roots module so a reader can
    address a run's permanent artifacts (e.g. the frozen contract manifest) without importing the
    store's write path.
    """
    return Path(state_root()).resolve() / "runs" / run_id
