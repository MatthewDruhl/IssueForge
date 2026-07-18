"""Test doubles for the subprocess boundary.

``FakeRunner`` enforces a read-only ALLOWLIST, not a denylist: any command that is neither a
known read-only prefix nor explicitly registered raises ``AssertionError``. An unforeseen
destructive command (``git reset``, ``clean``, ``checkout``, ``rm``, ``update-ref``,
``gh api``) is caught by construction, because nothing outside the allowlist is ever allowed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# Read-only command prefixes safe to run in a test without registration. Anything that mutates
# state (including every ``gh`` write and any git subcommand not listed) must be registered
# explicitly, argv-for-argv.
_READ_ONLY_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("git", "status"),
    ("git", "log"),
    ("git", "show"),
    ("git", "diff"),
    ("git", "rev-parse"),
    ("git", "cat-file"),
    ("git", "ls-files"),
    ("git", "ls-remote"),
    ("git", "config", "--get"),
    ("ls",),
    ("cat",),
)


class FakeRunner:
    """A subprocess double that admits only allowlisted or exactly-registered argv."""

    def __init__(self, registered: Iterable[Sequence[str]] | None = None) -> None:
        self._registered = [list(cmd) for cmd in (registered or [])]

    def run(self, argv: Sequence[str]):
        command = list(argv)
        if command in self._registered:
            return None
        if any(_has_prefix(command, prefix) for prefix in _READ_ONLY_PREFIXES):
            return None
        raise AssertionError(
            f"FakeRunner refused {command!r}: not a read-only prefix and not registered"
        )


def _has_prefix(command: list[str], prefix: tuple[str, ...]) -> bool:
    return tuple(command[: len(prefix)]) == prefix
