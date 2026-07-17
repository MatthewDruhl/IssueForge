"""The verification-adapter interface: the PRD's portable seam (``prd.md:157``).

An exit code cannot tell a behavioral failure from a compile error, a collection error, zero
tests collected, a skipped suite, or a timeout, so the engine keys behavior on a framework +
reporter adapter, not on raw process output. This module ships the ``Outcome`` enum, the
``VerificationAdapter`` Protocol (all six operations, mandatory), and the registry keyed on
(framework, reporter). Only pytest's ``probe`` is implemented this slice; the other five land
in the slices that first need them (S6/S12/S13).
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class Outcome(Enum):
    """A phase-aware verification outcome.

    Deliberately has no truth value: an error outcome must never silently coerce to ``False``
    and read as "no". Callers compare explicitly (``outcome is Outcome.PASSED``); any attempt
    to use one as a boolean raises ``TypeError``.
    """

    PASSED = "passed"
    FAILED = "failed"
    BROKEN = "broken"
    TIMED_OUT = "timed_out"
    EMPTY = "empty"
    SKIPPED = "skipped"

    def __bool__(self) -> bool:
        raise TypeError(
            f"{type(self).__name__}.{self.name} has no truth value; compare it explicitly"
        )


@runtime_checkable
class VerificationAdapter(Protocol):
    """Six mandatory operations keyed on (framework, reporter)."""

    def probe(self, toolchain: object) -> object:
        """Return the reporter's capabilities and pinned version for ``toolchain``."""
        ...

    def provision_environment(self, worktree: object, frozen_deps: object = None) -> object:
        """Prepare a hermetic, separately-provisioned authoritative environment handle."""
        ...

    def canonical_collect(self, invocation: object) -> object:
        """Return canonical test IDs and selection metadata for an invocation."""
        ...

    def classify(self, native_events: object) -> object:
        """Turn native framework events into phase-aware outcomes."""
        ...

    def discover_contract_dependencies(self, collection: object) -> object:
        """Return the protected import closure: in-repo paths plus pinned external identities."""
        ...

    def validate_invocation(self, command: object) -> object:
        """Return a frozen, safe execution plan for a command/config."""
        ...


class AdapterRegistry:
    """Resolves an adapter from a (framework, reporter) pair, or ``None`` on a miss.

    Keying on both framework AND reporter (never language) keeps ``pytest`` and ``unittest``
    distinct even though both are Python.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], object] = {}

    def register(self, adapter: object) -> None:
        self._by_key[(adapter.framework, adapter.reporter)] = adapter

    def resolve(self, *, framework: str, reporter: str) -> object | None:
        return self._by_key.get((framework, reporter))


registry = AdapterRegistry()
