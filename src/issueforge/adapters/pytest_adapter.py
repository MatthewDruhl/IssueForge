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


def _is_valid_node_id(line: str) -> bool:
    """A pytest node id is ``path::test`` optionally followed by a single trailing parametrization
    group ``[...]`` whose contents are arbitrary (an explicit param id may legally contain brackets
    and spaces, e.g. ``path::test[] space]``). Valid: ``path::test``, ``path::test[a b]``,
    ``path::test[] space]``. Invalid: ``path::test MALFORMED TRAILER`` (a well-formed id followed by
    unbracketed garbage) and ``path::test[a] TRAILER`` (garbage after the closing ``]``).

    The grammar splits at the FIRST ``[``: the head (``path::test``) must carry no whitespace, and
    the parametrization tail (from the first ``[`` to end) must CLOSE at the very end of the line
    (its last character is ``]``) — everything between is param content, brackets and spaces
    included. A naive per-character bracket-depth scan wrongly closes at the first ``]`` inside an
    explicit id and then rejects the legal trailing space (#58 second-round gap E), while still
    needing to reject genuine trailing garbage (the #5 gap — never regress it).
    """
    bracket = line.find("[")
    head = line if bracket == -1 else line[:bracket]
    # The head (path::test portion) must carry no whitespace.
    if any(ch in " \t" for ch in head):
        return False
    # A parametrization tail, when present, must terminate the id: it has to end at the closing ``]``.
    if bracket != -1 and not line.endswith("]"):
        return False
    return True


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
    venv = process.run(["uv", "venv", str(env_root)], cwd=worktree_path, timeout=_VENV_TIMEOUT)
    if venv.returncode != 0 or venv.timed_out:
        raise RuntimeError(f"authoritative venv creation failed: {venv.stderr.strip()!r}")
    venv_python = env_root / "bin" / "python"
    # Install the EXACT reporter plus the target's frozen manifest (#58/#11); an install
    # failure/timeout raises so run_baseline pauses with a typed non-green record rather than
    # silently running a baseline whose dependency environment was never built.
    packages = ["pytest", "pytest-reportlog", *_frozen_specs(frozen_deps)]
    install = process.run(
        ["uv", "pip", "install", "--python", str(venv_python), *packages],
        cwd=worktree_path,
        timeout=_INSTALL_TIMEOUT,
    )
    if install.returncode != 0 or install.timed_out:
        raise RuntimeError(f"authoritative dependency install failed: {install.stderr.strip()!r}")
    # Create AND verify the artifact directory the handle advertises (#58/#11): a computed-but-never
    # made path is a phantom. The write seam creates it under IssueForge's owned state root.
    getattr(WriteSeam(), "write_text")(artifact_dir / ".if-keep", "")
    if not artifact_dir.is_dir():
        raise RuntimeError(f"authoritative artifact_dir was not created: {artifact_dir}")
    # The venv's own bin/python is a SYMLINK to the shared base interpreter, so resolving it would
    # collapse back onto the host. A thin wrapper (a real file UNDER the owned root, never a
    # symlink) execs the working venv interpreter, so the authoritative interpreter path stays
    # provably under state_root() and is genuinely separate from sys.executable.
    interpreter = env_root / "authoritative-python"
    getattr(WriteSeam(), "write_text")(interpreter, f'#!/bin/sh\nexec "{venv_python}" "$@"\n')
    os.chmod(interpreter, 0o755)
    # A minimal allowlist env — never a copy of the candidate's os.environ — so a candidate's
    # PYTEST_ADDOPTS / PYTHONPATH / sabotage vars never reach the authoritative run. The
    # authoritative env_root/bin is prepended to PATH so a bare-name baseline (["pytest"]) resolves
    # to the AUTHORITATIVE pytest that was just installed, never a host pytest leaked from the
    # ambient PATH (#58/#1) — otherwise the separately-provisioned env is decorative and the baseline
    # can go green off host packages the authoritative env never installed.
    bin_dir = env_root / "bin"
    host_path = os.environ.get("PATH", "")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{host_path}" if host_path else str(bin_dir),
        "HOME": os.environ.get("HOME", ""),
    }
    return SimpleNamespace(
        interpreter=str(interpreter),
        env=env,
        artifact_dir=artifact_dir,
        env_root=env_root,
        network=False,
        # An explicit executor marker (NOT ``network``/``provisioner is None``): the authoritative
        # RUN for this handle must execute under OS-level network denial (a ``--network none``
        # container). Only the real default-provisioned handle carries it, so an injected test
        # provisioner (which never sets it) keeps running on the host — the shared default-path
        # tests stay host-run and green while the authoritative run is genuinely denied.
        denies_network=True,
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
        # Resolve the committed command's own executable in the authoritative env (#58/#1); a bare
        # interpreter-prefix turns ["pytest"] into a bogus script path.
        argv = [
            *process.build_launch_argv(invocation.interpreter, command, env=env),
            "--collect-only",
            "-q",
        ]
        result = process.run(argv, cwd=worktree, timeout=_COLLECT_TIMEOUT, env=env)
        raw_ids = [line.strip() for line in result.stdout.splitlines() if "::" in line]
        # A ``::`` line that is not a well-formed node id (trailing garbage after a real id) is broken
        # collection evidence: the malformed lines are dropped from the id set AND invalidate the
        # whole collection, so scraped ids can never be laundered into a green baseline (#58 gap #5).
        malformed = any(not _is_valid_node_id(rid) for rid in raw_ids)
        ids = sorted({rid for rid in raw_ids if _is_valid_node_id(rid)})
        selection = SimpleNamespace(command=tuple(command), argv=tuple(argv))
        # Typed collection evidence (#58/#7): a nonzero exit, a timeout, or malformed stdout means the
        # scraped ids came from a BROKEN collection and cannot seed a GREEN baseline. ``ok`` gates
        # execution/green.
        ok = result.returncode == 0 and not result.timed_out and not malformed
        return SimpleNamespace(
            ids=tuple(ids),
            selection=selection,
            returncode=result.returncode,
            timed_out=result.timed_out,
            ok=ok,
        )

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
        if self._is_green(
            exit_code, report_present, collected, executed, expected, by_node, has_failure
        ):
            return BaselineStatus.GREEN
        if exit_code == 0 and executed == 0 and collected > 0 and not has_failure:
            return BaselineStatus.ALL_SKIPPED
        # BEHAVIORAL_RED is reserved for a VALID report with a real call-phase failure of an
        # EXPECTED node at exit 1 (#58/#9). A signaled/aborted death (negative exit), a reportless
        # non-timeout exit, an unknown exit code, an INCOMPLETE report (an expected node produced no
        # record), or a setup/teardown infra failure has no such trustworthy evidence and is BROKEN —
        # never mislabeled a behavioral red baseline.
        if exit_code == 1 and report_present and self._is_behavioral_red(expected, by_node):
            return BaselineStatus.BEHAVIORAL_RED
        return BaselineStatus.BROKEN

    def _is_behavioral_red(self, expected: set, by_node: dict) -> bool:
        """A clean behavioral red needs a COMPLETE report (every expected node produced at least one
        terminal record), NO setup/teardown infrastructure failure anywhere, and at least one
        expected call-phase failure (#58 hardening / gap #8). An incomplete or infra-contaminated
        report is BROKEN, never a trustworthy red baseline."""
        if not expected:
            return False
        # Complete report: an expected node that produced NO record means the report was truncated —
        # the run never ran to a terminal record for it, so it is not a trustworthy red.
        if any(nodeid not in by_node for nodeid in expected):
            return False
        # Lifecycle-complete report (#58 second-round gap D): every expected node must have REACHED a
        # call phase (or terminated its lifecycle). A node that produced only a non-call record — a
        # setup:passed with no following call — never ran its test body, so the report is incomplete
        # and this is not a trustworthy red, even though another node has a call failure.
        if any(not self._reached_call_or_terminal(by_node.get(nodeid, ())) for nodeid in expected):
            return False
        # Infra contamination: a setup/teardown FAILED/BROKEN anywhere means the baseline is corrupted
        # by infrastructure breakage, not a clean behavioral failure.
        for records in by_node.values():
            for record in records:
                if record.phase in ("setup", "teardown") and record.outcome in (
                    Outcome.FAILED,
                    Outcome.BROKEN,
                ):
                    return False
        return self._has_expected_call_failure(expected, by_node)

    def _reached_call_or_terminal(self, records: object) -> bool:
        """True when a node's records show it reached a call phase or otherwise terminated its
        lifecycle: a ``call`` record (the body ran), a ``teardown`` record (the lifecycle completed),
        or a ``setup`` FAILED/BROKEN (the node terminated early without a call). A node with ONLY a
        passing ``setup`` and no call never ran its body — an incomplete, mid-lifecycle report."""
        for record in records:
            if record.phase in ("call", "teardown"):
                return True
            if record.phase == "setup" and record.outcome in (Outcome.FAILED, Outcome.BROKEN):
                return True
        return False

    def _has_expected_call_failure(self, expected: set, by_node: dict) -> bool:
        """True when some EXPECTED node has a call-phase FAILED record — a genuine behavioral red.

        A call-phase BROKEN (errored) or a setup/teardown failure is infra breakage, not a clean
        behavioral failure, so only Outcome.FAILED in the call phase of an expected node qualifies.
        """
        for nodeid in expected:
            for record in by_node.get(nodeid, ()):
                if record.phase == "call" and record.outcome is Outcome.FAILED:
                    return True
        return False

    def _is_green(
        self,
        exit_code: object,
        report_present: object,
        collected: int,
        executed: int,
        expected: set,
        by_node: dict,
        has_failure: bool,
    ) -> bool:
        if exit_code != 0 or not report_present:
            return False
        if collected <= 0 or executed <= 0 or not expected:
            return False
        # GREEN is a WHOLE-REPORT property (#58/#8): a failed/BROKEN record for ANY node — even an
        # unexpected one that never appeared in canonical_collect — forbids green, not just a
        # regression among the expected ids.
        if has_failure:
            return False
        # Collection/execution RECONCILIATION (#58 hardening / gap #7): GREEN requires the executed
        # set to be exactly the expected set. A "ghost" node that ran and passed but was never
        # collected (an id outside ``expected``) is a reconciliation failure — the run executed
        # something outside the frozen expected set — so it forbids green even though it passed.
        if any(nodeid not in expected for nodeid in by_node):
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
