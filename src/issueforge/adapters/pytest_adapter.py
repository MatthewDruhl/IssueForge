"""The pytest verification adapter.

This slice implements only ``probe`` (the reporter's pinned version + declared capabilities).
The other five operations are declared and deferred: they raise ``NotImplementedError`` rather
than a silent stub, so a caller can never mistake an unbuilt operation for a real answer.
``provision_environment``/``canonical_collect``/``classify`` land in S6,
``discover_contract_dependencies`` in S12, ``validate_invocation`` in S13.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass

from issueforge.adapters.base import registry


class _HostToolchain:
    """Default toolchain seam: reads installed distribution versions from the host."""

    def version(self, name: str) -> str:
        return importlib.metadata.version(name)


@dataclass(frozen=True)
class ProbeResult:
    """A reporter's pinned version paired with the adapter's declared capabilities."""

    reporter_version: str
    capabilities: object


class PytestAdapter:
    """Verification adapter for the pytest framework, using pytest's own reporter."""

    framework = "pytest"
    reporter = "pytest"
    CAPABILITIES = frozenset(
        {
            "canonical_collect",
            "phase_aware_classify",
            "count_reconciliation",
        }
    )

    def probe(self, toolchain: object = None) -> ProbeResult:
        """Read ``toolchain`` and pin the reporter's exact version + declared capabilities."""
        source = toolchain if toolchain is not None else _HostToolchain()
        return ProbeResult(
            reporter_version=source.version(self.reporter),
            capabilities=self.CAPABILITIES,
        )

    def provision_environment(self, worktree: object, frozen_deps: object = None) -> object:
        raise NotImplementedError("provision_environment lands in S6")

    def canonical_collect(self, invocation: object) -> object:
        raise NotImplementedError("canonical_collect lands in S6")

    def classify(self, native_events: object) -> object:
        raise NotImplementedError("classify lands in S6")

    def discover_contract_dependencies(self, collection: object) -> object:
        raise NotImplementedError("discover_contract_dependencies lands in S12")

    def validate_invocation(self, command: object) -> object:
        raise NotImplementedError("validate_invocation lands in S13")


registry.register(PytestAdapter())
