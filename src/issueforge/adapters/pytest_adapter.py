"""The pytest verification adapter.

This slice implements only ``probe`` (the reporter's pinned version + declared capabilities).
The other five operations are declared and deferred: they raise ``NotImplementedError`` rather
than a silent stub, so a caller can never mistake an unbuilt operation for a real answer.
``provision_environment``/``canonical_collect``/``classify`` land in S6,
``discover_contract_dependencies`` in S12, ``validate_invocation`` in S13.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
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


@dataclass(frozen=True)
class BaselineSelection:
    """The reconciliation of a candidate collection against the protected base id set.

    ``added`` is the COMPUTED ``candidate - base`` set (sorted, never a declared list nor a
    ``collected(base) - new_ids`` subtraction); ``missing`` names every base id absent from the
    candidate (a disappeared preexisting test); ``ok`` is True exactly when nothing disappeared.
    """

    added: tuple
    missing: tuple
    ok: bool


class DiscoveryError(Exception):
    """An import reached at collection resolves to neither a repo file, the stdlib, nor an installed
    distribution. Discovery fails CLOSED naming the offender — never a partial closure."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"unresolvable import {name!r}: not a repo file, the stdlib, nor an installed distribution"
        )


@dataclass(frozen=True)
class ContractClosure:
    """The provenance-tagged import closure discovery computes for a real collection.

    ``test_files`` — the collected test-module paths; ``fixture_closure`` — every in-repo file
    reached via the fixture/conftest/plugin/config route (always protected); ``test_body_imports``
    — in-repo files reached ONLY through a test-module body import (candidate SUTs). Every path is
    a sorted/dedup repo-relative string. ``external`` — sorted ``(distribution, version)`` pins.
    """

    test_files: tuple[str, ...]
    fixture_closure: tuple[str, ...]
    test_body_imports: tuple[str, ...]
    external: tuple[tuple[str, str], ...]


def _reach(roots: set[str], adjacency: dict[str, set[str]]) -> set[str]:
    """Every node reachable from ``roots`` over ``adjacency`` (roots included; cycle-safe)."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, ()))
    return seen


def _pin_external(candidates: dict[str, str | None]) -> set[tuple[str, str]]:
    """Pin each external candidate module to its DISTRIBUTION identity + version, in-process.

    Resolution reads ``importlib.metadata`` as module attributes so a test's monkeypatch of
    ``packages_distributions``/``version``/``distribution`` applies. A module a namespace maps to
    several distributions is disambiguated to the one whose files actually provide the imported
    module's origin. An unresolvable candidate fails CLOSED with ``DiscoveryError``.
    """
    pins: set[tuple[str, str]] = set()
    for top, origin in candidates.items():
        distributions = importlib.metadata.packages_distributions().get(top)
        if not distributions:
            raise DiscoveryError(top)
        chosen = _owning_distribution(top, distributions, origin)
        pins.add((chosen, importlib.metadata.version(chosen)))
    return pins


def _resolve_origin(value: str | None) -> str | None:
    """The real (symlink-resolved) filesystem path of a module origin, or ``None``."""
    if not value:
        return None
    try:
        return os.path.realpath(str(value))
    except (OSError, ValueError):
        return None


_SITE_MARKERS = ("site-packages", "dist-packages")


def _is_stdlib_origin(top: str, origin: str | None) -> bool:
    """True when ``top`` is a genuine stdlib import that needs no external pin.

    Exemption is by RESOLVED ORIGIN, not the bare name: the name must be a stdlib module name AND its
    origin must not sit under a ``site-packages``/``dist-packages`` tree. A builtin/frozen stdlib
    module (no ``__file__``) is exempt; a site-packages package that merely shares a stdlib name
    (a shadow) is NOT — it resolves to a real distribution and must be pinned.
    """
    if top not in sys.stdlib_module_names:
        return False
    resolved = _resolve_origin(origin)
    if resolved is None:
        return True
    return not any(marker in resolved for marker in _SITE_MARKERS)


def _owning_distribution(top: str, distributions: list[str], origin: str | None) -> str:
    """The single distribution whose committed files actually provide the imported module.

    A namespace maps to one distribution -> that one. Several -> the one whose files, resolved
    against the distribution's own install location, equal the module's REAL origin path (a bare
    ``str(entry) == origin`` never matches — ``files`` entries are location-relative while ``origin``
    is absolute). Ambiguous ownership (no owner, or more than one candidate owner) fails CLOSED with a
    ``DiscoveryError`` naming the import — never the alphabetical-first distribution.
    """
    if len(distributions) == 1:
        return distributions[0]
    origin_real = _resolve_origin(origin)
    owners: list[str] = []
    if origin_real:
        for dist in distributions:
            meta = importlib.metadata.distribution(dist)
            locate = getattr(meta, "locate_file", None)
            files = getattr(meta, "files", None) or []
            for entry in files:
                located = locate(entry) if locate is not None else entry
                if _resolve_origin(str(located)) == origin_real:
                    owners.append(dist)
                    break
    if len(owners) == 1:
        return owners[0]
    raise DiscoveryError(top)


# The instrumented collection recorder: run in a FRESH subprocess so the conftest/plugin/config/test
# import graph is captured from a clean interpreter (no cross-repo sys.modules pollution). A meta-path
# finder records every (importer, importer_file, imported) edge at import time — catching dynamic
# ``importlib.import_module`` and defeating a same-name local rebinding, unlike an AST scan — then the
# graph is emitted between sentinels on stdout (pytest's own output is suppressed during collection).
_DISCOVER_START = "<<<IFDISCOVER_START>>>"
_DISCOVER_END = "<<<IFDISCOVER_END>>>"
_DISCOVER_SRC = r"""
import sys, os, json, builtins, importlib, importlib.abc

_repo = sys.argv[1]
os.chdir(_repo)
_edges = []


def _stack_parent():
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__")
        if name and name != "__main__" and "importlib" not in name and "_bootstrap" not in name:
            return name, frame.f_globals.get("__file__")
        frame = frame.f_back
    return None, None


# The import statement always calls builtins.__import__ (even for an already-cached module), and its
# ``globals`` argument names the IMPORTER — so a conftest/test import of a module pytest pre-imported
# (e.g. pluggy) is still attributed to that in-repo file, unlike a meta-path finder which never fires
# on a cache hit.
_orig_import = builtins.__import__


def _rec_import(name, glb=None, loc=None, fromlist=(), level=0):
    pn = glb.get("__name__") if isinstance(glb, dict) else None
    pf = glb.get("__file__") if isinstance(glb, dict) else None
    _edges.append([pn, pf, name, 0])
    # Honor ``level`` (relative) and ``fromlist`` so a ``from . import shared`` / ``from .m import x``
    # fixture import records its real submodule edge (bare __import__ passes name="" for a relative
    # ``from . import ...``, losing the child otherwise). The resolved base anchors the fromlist
    # children. fromlist edges are tagged kind=1: they only ever add IN-REPO adjacency at build time
    # and never mint an external candidate, so a ``from helpers import make`` (``make`` is an
    # attribute, not a module) can never be misread as an unresolvable distribution.
    base = name
    if level and isinstance(glb, dict):
        anchor = glb.get("__package__")
        if anchor is None:
            anchor = pn or ""
        parts = anchor.split(".") if anchor else []
        if level > 1:
            parts = parts[: max(0, len(parts) - (level - 1))]
        base_anchor = ".".join(parts)
        base = base_anchor + "." + name if name else base_anchor
        if base:
            _edges.append([pn, pf, base, 1])
    for _from in fromlist or ():
        if _from and _from != "*":
            child = base + "." + _from if base else _from
            _edges.append([pn, pf, child, 1])
    return _orig_import(name, glb, loc, fromlist, level)


builtins.__import__ = _rec_import


# A meta-path finder supplements the wrapper for DYNAMIC imports (``importlib.import_module``), which
# bypass builtins.__import__; the importer is recovered from the stack.
class _Recorder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        pn, pf = _stack_parent()
        _edges.append([pn, pf, name, 0])
        return None


sys.meta_path.insert(0, _Recorder())

# Suppress pytest's collection chatter at the FILE-DESCRIPTOR level (never a builtin ``open`` write
# outside the io seam): dup2 the real stdout/stderr fds onto ``os.devnull`` for the duration, then
# restore them before the sentinel-framed graph is emitted. ``collect_rc`` carries pytest.main's exit
# code so a caller can fail closed on a broken collection rather than freeze a partial graph.
collect_rc = None
_devnull_fd = os.open(os.devnull, os.O_WRONLY)
_saved_out_fd = os.dup(1)
_saved_err_fd = os.dup(2)
os.dup2(_devnull_fd, 1)
os.dup2(_devnull_fd, 2)
try:
    import pytest
    try:
        collect_rc = pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider"])
    except SystemExit as _exc:
        collect_rc = _exc.code
except BaseException:
    collect_rc = "error"
finally:
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except BaseException:
        pass
    os.dup2(_saved_out_fd, 1)
    os.dup2(_saved_err_fd, 2)
    os.close(_devnull_fd)
    os.close(_saved_out_fd)
    os.close(_saved_err_fd)

_module_files = {}
_module_paths = {}
for _name, _mod in list(sys.modules.items()):
    _module_files[_name] = getattr(_mod, "__file__", None)
    _path = getattr(_mod, "__path__", None)
    if _path is not None:
        try:
            _module_paths[_name] = list(_path)
        except TypeError:
            _module_paths[_name] = []

sys.stdout.write(
    "<<<IFDISCOVER_START>>>"
    + json.dumps(
        {
            "edges": _edges,
            "module_files": _module_files,
            "module_paths": _module_paths,
            "collect_rc": collect_rc,
        }
    )
    + "<<<IFDISCOVER_END>>>"
)
"""


def _run_discovery_collector(interpreter: object, worktree: Path, env: object) -> dict:
    """Launch the instrumented collector and parse the emitted import graph."""
    argv = [str(interpreter), "-c", _DISCOVER_SRC, str(worktree)]
    result = process.run(argv, cwd=Path(worktree), timeout=_COLLECT_TIMEOUT, env=env)
    stdout = result.stdout or ""
    start = stdout.find(_DISCOVER_START)
    end = stdout.find(_DISCOVER_END)
    if start == -1 or end == -1:
        raise RuntimeError(
            f"discovery collection produced no import graph (rc={result.returncode}, "
            f"timed_out={result.timed_out}): {result.stderr.strip()!r}"
        )
    payload = stdout[start + len(_DISCOVER_START) : end]
    return json.loads(payload)


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

    def select_baseline(self, base_ids: object, candidate_ids: object) -> BaselineSelection:
        """Reconcile a candidate collection against the protected base ids.

        ``added`` is the sorted COMPUTED ``candidate - base`` set — the genuinely new ids, never a
        declared value and never a ``base - new`` subtraction (which would silently launder a
        reused base id out of the protected set). ``missing`` is every base id the candidate no
        longer collects (a disappeared preexisting test); ``ok`` is True only when none disappeared.
        """
        base = set(base_ids)
        candidate = set(candidate_ids)
        added = tuple(sorted(candidate - base))
        missing = tuple(sorted(base - candidate))
        return BaselineSelection(added=added, missing=missing, ok=not missing)

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
            # Provenance carriers so ``discover_contract_dependencies`` (S12) can re-run an
            # instrumented collection over the SAME worktree/interpreter/env this collection used.
            worktree=worktree,
            interpreter=invocation.interpreter,
            env=env,
            command=tuple(command),
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
                wasxfail=record.get("wasxfail"),
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

    def discover_contract_dependencies(self, collection: object) -> ContractClosure:
        """Compute the provenance-tagged import closure of a real collection (S12, US-5/D5/G16).

        Re-runs the collection under an instrumented import recorder in a FRESH subprocess (so the
        conftest/plugin/config/test import graph is the REAL runtime graph, never an AST scan), then
        classifies every in-repo file by PROVENANCE: reached via the fixture/conftest/plugin/config
        route (``fixture_closure``, always protected) versus reached ONLY through a test-module body
        import (``test_body_imports``, a candidate SUT the freeze may exclude). Fixture provenance
        wins on a collision. External imports made BY an in-repo contract file are pinned by their
        DISTRIBUTION identity + version (in-process, so ``importlib.metadata`` monkeypatches apply);
        an import owned by no repo file, no stdlib module, and no installed distribution fails the
        discovery CLOSED with ``DiscoveryError`` naming it — never a partial closure.
        """
        worktree = Path(getattr(collection, "worktree")).resolve()
        interpreter = getattr(collection, "interpreter")
        env = getattr(collection, "env", None)
        ids = tuple(getattr(collection, "ids", ()) or ())

        graph = _run_discovery_collector(interpreter, worktree, env)
        edges = graph["edges"]
        module_files = graph["module_files"]
        module_paths = graph.get("module_paths", {})

        def _relrepo(fpath: object) -> str | None:
            if not fpath:
                return None
            try:
                resolved = Path(fpath).resolve()
            except (OSError, ValueError):
                return None
            try:
                return str(resolved.relative_to(worktree))
            except ValueError:
                return None

        def _under_repo(fpath: object) -> bool:
            return _relrepo(fpath) is not None

        # File-backed in-repo modules map to a repo-relative path. A fileless in-repo PACKAGE (a PEP
        # 420 namespace package, ``__file__ is None`` but ``__path__`` under the repo) has no blob to
        # protect: it is neither a closure member nor an external — its file-backed submodules carry
        # their own edges.
        name_to_rel: dict[str, str] = {}
        for name, fpath in module_files.items():
            rel = _relrepo(fpath)
            if rel is not None:
                name_to_rel[name] = rel
        # An in-repo module that FAILED to import (e.g. a helper whose own import raised) is dropped
        # from ``sys.modules``, so it is absent from ``module_files`` — but it appears as a PARENT with
        # an in-repo importer FILE, which proves it is a repo file (its body started executing). Seed
        # the name->path map from those parent files so it is never misread as an external distribution.
        for parent_name, parent_file, _child, *_rest in edges:
            parent_rel = _relrepo(parent_file)
            if parent_rel is not None and parent_name:
                name_to_rel.setdefault(parent_name, parent_rel)
        inrepo_pkgs = {
            name
            for name, locations in module_paths.items()
            if any(_under_repo(loc) for loc in (locations or []))
        }

        test_files = tuple(sorted({nid.split("::")[0] for nid in ids if "::" in nid}))
        test_set = set(test_files)

        # In-repo adjacency (parent_rel -> child_rel). A file is a ROOT when no in-repo file imported
        # it: pytest imports conftests/test modules by file spec (bypassing the import hooks) and loads
        # ``-p`` plugins / config modules itself, so those carry no in-repo importer. External
        # candidates are the (top-level) external modules imported DIRECTLY by an in-repo contract file.
        adjacency: dict[str, set[str]] = {}
        has_inrepo_parent: set[str] = set()
        inrepo_nodes: set[str] = set(name_to_rel.values())
        external_candidates: dict[str, str | None] = {}
        for parent_name, parent_file, child_name, *_rest in edges:
            kind = _rest[0] if _rest else 0
            parent_rel = _relrepo(parent_file)
            if parent_rel is None:
                parent_rel = name_to_rel.get(parent_name)
            if parent_rel is not None:
                # A conftest name collides across directories (every ``conftest.py`` is module
                # ``conftest``), so a root conftest is knowable only via its edges' importer FILE —
                # record it as an in-repo node so it can still be a fixture root.
                inrepo_nodes.add(parent_rel)
            parent_inrepo = parent_rel is not None or parent_name in inrepo_pkgs
            child_rel = name_to_rel.get(child_name)
            if child_rel is not None:
                if parent_rel is not None:
                    adjacency.setdefault(parent_rel, set()).add(child_rel)
                    has_inrepo_parent.add(child_rel)
                continue
            if child_name in inrepo_pkgs:
                continue  # fileless in-repo package: nothing to protect, submodules carry edges
            # A fromlist/relative edge (kind 1) only ever contributes IN-REPO adjacency (handled
            # above); it never mints an external candidate, because a ``from pkg import name`` cannot
            # tell an attribute from a submodule and the base ``import pkg`` edge already pins the
            # distribution. This keeps ``from helpers import make`` from forging a bogus ``make`` dist.
            if kind == 1:
                continue
            # child is external: pin it only when an IN-REPO contract file imported it directly.
            if not parent_inrepo:
                continue
            top = child_name.split(".")[0]
            if not top or top in inrepo_pkgs:
                continue
            # Stdlib exemption is by RESOLVED ORIGIN, not the import NAME: a site-packages package that
            # shadows a stdlib name (its origin under site-packages) is a real external dependency and
            # must be pinned, while a genuine stdlib module (origin in the stdlib tree, or a builtin/
            # frozen module with no file) needs no pin.
            if _is_stdlib_origin(top, module_files.get(top) or module_files.get(child_name)):
                continue
            # ``_pytest`` is the assertion rewriter's own injected import (``@pytest_ar``), attributed
            # to every rewritten conftest/test module — machinery, not a declared contract dependency.
            if top == "_pytest":
                continue
            external_candidates.setdefault(
                top, module_files.get(top) or module_files.get(child_name)
            )

        roots = inrepo_nodes - has_inrepo_parent
        fixture_roots = roots - test_set
        fixture_closure = _reach(fixture_roots, adjacency)
        test_reach = _reach(test_set, adjacency)
        test_body_imports = test_reach - fixture_closure - test_set

        external = _pin_external(external_candidates)

        return ContractClosure(
            test_files=test_files,
            fixture_closure=tuple(sorted(fixture_closure)),
            test_body_imports=tuple(sorted(test_body_imports)),
            external=tuple(sorted(external)),
        )

    def validate_invocation(self, command: object) -> object:
        raise NotImplementedError("validate_invocation lands in S13")


registry.register(PytestAdapter())
