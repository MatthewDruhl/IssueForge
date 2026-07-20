"""The pytest verification adapter.

This slice implements only ``probe`` (the reporter's pinned version + declared capabilities).
The other five operations are declared and deferred: they raise ``NotImplementedError`` rather
than a silent stub, so a caller can never mistake an unbuilt operation for a real answer.
``provision_environment``/``canonical_collect``/``classify`` land in S6,
``discover_contract_dependencies`` in S12, ``validate_invocation`` in S13.
"""

from __future__ import annotations

import importlib.metadata
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from issueforge import process
from issueforge.adapters.base import (
    BaselineStatus,
    ClassifyResult,
    NodeRecord,
    Outcome,
    registry,
)
from issueforge.io import WriteSeam
from issueforge.paths import state_root

# Report-log outcome vocabulary -> the phase-aware Outcome enum. Anything unknown is an error
# (BROKEN), never silently coerced to a benign value.
_OUTCOME_MAP = {
    "passed": Outcome.PASSED,
    "failed": Outcome.FAILED,
    "skipped": Outcome.SKIPPED,
}

_COLLECT_TIMEOUT = 120.0
_VENV_TIMEOUT = 180.0
_INSTALL_TIMEOUT = 300.0


def _to_outcome(value: object) -> Outcome:
    return _OUTCOME_MAP.get(value, Outcome.BROKEN)


def _provision_default(worktree: object, frozen_deps: object) -> SimpleNamespace:
    """Build a SEPARATE authoritative interpreter under IssueForge's owned state root.

    A fresh ``uv`` venv per invocation (network off for the eventual run), with pytest + the
    report-log reporter installed, so the baseline never runs on the host interpreter or the
    candidate's environment. ``frozen_deps`` (when given) pins additional packages.
    """
    unique = uuid.uuid4().hex
    env_root = Path(state_root()) / "envs" / unique
    artifact_dir = Path(state_root()) / "artifacts" / unique
    worktree_path = Path(worktree)
    process.run(["uv", "venv", str(env_root)], cwd=worktree_path, timeout=_VENV_TIMEOUT)
    venv_python = env_root / "bin" / "python"
    packages = ["pytest", "pytest-reportlog", *_frozen_specs(frozen_deps)]
    process.run(
        ["uv", "pip", "install", "--python", str(venv_python), *packages],
        cwd=worktree_path,
        timeout=_INSTALL_TIMEOUT,
    )
    # The venv's own bin/python is a SYMLINK to the shared base interpreter, so resolving it would
    # collapse back onto the host. A thin wrapper (a real file UNDER the owned root, never a
    # symlink) execs the working venv interpreter, so the authoritative interpreter path stays
    # provably under state_root() and is genuinely separate from sys.executable.
    interpreter = env_root / "authoritative-python"
    getattr(WriteSeam(), "write_text")(interpreter, f'#!/bin/sh\nexec "{venv_python}" "$@"\n')
    os.chmod(interpreter, 0o755)
    # A minimal allowlist env — never a copy of the candidate's os.environ — so a candidate's
    # PYTEST_ADDOPTS / PYTHONPATH / sabotage vars never reach the authoritative run.
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    return SimpleNamespace(
        interpreter=str(interpreter),
        env=env,
        artifact_dir=artifact_dir,
        env_root=env_root,
        network=False,
    )


def _frozen_specs(frozen_deps: object) -> list[str]:
    if not frozen_deps:
        return []
    if isinstance(frozen_deps, dict):
        return [f"{name}=={version}" for name, version in frozen_deps.items()]
    return [str(spec) for spec in frozen_deps]


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

    def provision_environment(
        self, worktree: object, frozen_deps: object = None, *, provisioner: object = None
    ) -> object:
        """Build the authoritative environment handle, delegating to ``provisioner`` when given.

        With an injected provisioner the seam receives ``(worktree, frozen_deps)`` and returns the
        handle verbatim; the DEFAULT path builds a real, separate venv under ``state_root()``.
        """
        if provisioner is not None:
            return provisioner(worktree, frozen_deps)
        return _provision_default(worktree, frozen_deps)

    def canonical_collect(self, invocation: object) -> object:
        """Return the EXACT repo-relative node-id set for ``invocation`` via ``--collect-only``.

        The ids are frozen (sorted, deduped) so they are stable across repeated collection and
        become the authoritative ``expected_ids`` the baseline is judged against — never derived
        from the execution report, which cannot show a node that vanished from the run.
        """
        worktree = Path(invocation.worktree)
        command = list(invocation.command)
        env = getattr(invocation, "env", None)
        argv = [str(invocation.interpreter), *command, "--collect-only", "-q"]
        result = process.run(argv, cwd=worktree, timeout=_COLLECT_TIMEOUT, env=env)
        ids = sorted({line.strip() for line in result.stdout.splitlines() if "::" in line})
        selection = SimpleNamespace(command=tuple(command), argv=tuple(argv))
        return SimpleNamespace(ids=tuple(ids), selection=selection)

    def classify(
        self,
        records: object,
        *,
        exit_code: object,
        timed_out: object,
        report_present: object,
        expected_ids: object,
    ) -> ClassifyResult:
        """Fuse report records, exit code, report presence, and the timeout flag into a verdict.

        GREEN is a CONJUNCTION, never a residual: exit 0 AND a present report AND something
        collected AND something executed AND every expected id present-and-passed. Every other
        outcome is a specific, non-collapsed reason the run is not a usable baseline.
        """
        nodes = tuple(
            NodeRecord(
                nodeid=record.get("nodeid"),
                phase=record.get("when"),
                outcome=_to_outcome(record.get("outcome")),
                longrepr=record.get("longrepr"),
            )
            for record in records
        )
        by_node: dict[str, list[NodeRecord]] = {}
        for node in nodes:
            by_node.setdefault(node.nodeid, []).append(node)
        collected = len(by_node)
        executed = sum(1 for recs in by_node.values() if any(n.phase == "call" for n in recs))
        has_failure = any(n.outcome in (Outcome.FAILED, Outcome.BROKEN) for n in nodes)
        expected = set(expected_ids or ())
        status = self._status(
            exit_code=exit_code,
            timed_out=timed_out,
            report_present=report_present,
            collected=collected,
            executed=executed,
            has_failure=has_failure,
            expected=expected,
            by_node=by_node,
        )
        return ClassifyResult(
            status=status,
            collected=collected,
            executed=executed,
            nodes=nodes,
            report_present=bool(report_present),
            exit_code=exit_code,
        )

    def _status(
        self,
        *,
        exit_code: object,
        timed_out: object,
        report_present: object,
        collected: int,
        executed: int,
        has_failure: bool,
        expected: set,
        by_node: dict,
    ) -> BaselineStatus:
        # A timeout dominates: the raw negative exit code is noise, the absence of a report is
        # expected signal, not an anomaly.
        if timed_out:
            return BaselineStatus.TIMEOUT
        # No captured exit at all means the process never launched to produce evidence.
        if exit_code is None:
            return BaselineStatus.LAUNCH_FAILED
        # pytest's BROKEN family — each a distinct diagnostic, never folded together.
        if exit_code == 5:
            return BaselineStatus.NO_TESTS_COLLECTED
        if exit_code == 2:
            return BaselineStatus.COLLECTION_ERROR
        if exit_code == 3:
            return BaselineStatus.INTERNAL_ERROR
        if exit_code == 4:
            return BaselineStatus.USAGE_ERROR
        if self._is_green(exit_code, report_present, collected, executed, expected, by_node):
            return BaselineStatus.GREEN
        if exit_code == 0 and executed == 0 and collected > 0 and not has_failure:
            return BaselineStatus.ALL_SKIPPED
        return BaselineStatus.BEHAVIORAL_RED

    def _is_green(
        self,
        exit_code: object,
        report_present: object,
        collected: int,
        executed: int,
        expected: set,
        by_node: dict,
    ) -> bool:
        if exit_code != 0 or not report_present:
            return False
        if collected <= 0 or executed <= 0 or not expected:
            return False
        return all(self._node_passed(nodeid, by_node) for nodeid in expected)

    def _node_passed(self, nodeid: str, by_node: dict) -> bool:
        recs = by_node.get(nodeid)
        if not recs:
            return False
        if any(n.outcome in (Outcome.FAILED, Outcome.BROKEN) for n in recs):
            return False
        call = [n for n in recs if n.phase == "call"]
        return bool(call) and all(n.outcome is Outcome.PASSED for n in call)

    def discover_contract_dependencies(self, collection: object) -> object:
        raise NotImplementedError("discover_contract_dependencies lands in S12")

    def validate_invocation(self, command: object) -> object:
        raise NotImplementedError("validate_invocation lands in S13")


registry.register(PytestAdapter())
