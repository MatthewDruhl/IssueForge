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


# External identity (distribution + version) resolves inside the provisioned discovery subprocess
# (see ``_DISCOVER_SRC`` below), never in this parent process — so pins carry the PROVISIONED venv's
# versions, not the host interpreter's. The parent only consumes the pins the subprocess returns.


# The instrumented collection recorder: run in a FRESH subprocess so the conftest/plugin/config/test
# import graph is captured from a clean interpreter (no cross-repo sys.modules pollution). A meta-path
# finder records every (importer, importer_file, imported) edge at import time — catching dynamic
# ``importlib.import_module`` and defeating a same-name local rebinding, unlike an AST scan — then the
# graph is emitted between sentinels on stdout (pytest's own output is suppressed during collection).
_DISCOVER_START = "<<<IFDISCOVER_START>>>"
_DISCOVER_END = "<<<IFDISCOVER_END>>>"
_DISCOVER_SRC = r"""
import sys, os, json, builtins, importlib, importlib.abc, importlib.metadata, importlib.util

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


_recorder = _Recorder()
sys.meta_path.insert(0, _recorder)

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
        collect_rc = pytest.main([*sys.argv[2:], "--collect-only", "-q", "-p", "no:cacheprovider"])
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

# ---- external distribution resolution, IN THIS (PROVISIONED) INTERPRETER (D5) ----
# External identity + version resolve from THIS interpreter's installed distributions — the
# provisioned hermetic venv, never the parent process. Restore the import hook and drop the
# meta-path recorder first so the resolution's own imports never pollute the recorded edges.
builtins.__import__ = _orig_import
try:
    sys.meta_path.remove(_recorder)
except ValueError:
    pass

_md = importlib.metadata
_repo_real = os.path.realpath(_repo)
_SITE = ("site-packages", "dist-packages")


def _real(v):
    if not v:
        return None
    try:
        return os.path.realpath(str(v))
    except (OSError, ValueError):
        return None


def _under_repo(f):
    r = _real(f)
    if r is None:
        return False
    return r == _repo_real or r.startswith(_repo_real + os.sep)


_inrepo_pkgs = set()
for _n, _locs in _module_paths.items():
    for _loc in _locs or []:
        if _under_repo(_loc):
            _inrepo_pkgs.add(_n)
            break

# In-repo module NAMES: file-backed modules under the repo, PLUS any module that appears as an
# in-repo IMPORTER in the edges. The latter recovers an in-repo helper whose own import RAISED (so it
# was dropped from sys.modules and carries no __file__) — its body started executing, proving it is a
# repo file, so a ``from helpers import H`` where helpers.py failed is never misread as an external
# distribution to fail closed on.
_inrepo_names = set()
for _n, _f in _module_files.items():
    if _under_repo(_f):
        _inrepo_names.add(_n)
for _e in _edges:
    if _e[0] and _under_repo(_e[1]):
        _inrepo_names.add(_e[0])


def _is_stdlib(top, origin):
    if top not in sys.stdlib_module_names:
        return False
    r = _real(origin)
    if r is None:
        return True
    return not any(m in r for m in _SITE)


try:
    _pkg_dist = _md.packages_distributions()
except Exception:
    _pkg_dist = {}


def _module_origin(full_name):
    # The REAL file origin of an imported module. Prefer the __file__ recorded from sys.modules, but
    # fall back to find_spec when the module never landed there — a namespace SUBMODULE whose body
    # raised (e.g. ``sphinxcontrib.applehelp`` importing an absent ``sphinx``) is dropped from
    # sys.modules and carries no __file__, yet find_spec still LOCATES its origin without executing the
    # body, so a multi-dist namespace stays disambiguable by file origin. Runs after the import hook is
    # restored, so this lookup never pollutes the recorded edges.
    _o = _module_files.get(full_name)
    if _o:
        return _o
    try:
        _spec = importlib.util.find_spec(full_name)
    except BaseException:
        return None
    return getattr(_spec, "origin", None) if _spec is not None else None


def _owning_dist(dists, origin):
    # A namespace shared by several distributions is disambiguated to the ONE whose installed files
    # equal the imported module's REAL origin path; ambiguous ownership resolves to no owner.
    if len(dists) == 1:
        return dists[0]
    origin_real = _real(origin)
    owners = []
    if origin_real:
        for _d in dists:
            try:
                _meta = _md.distribution(_d)
            except Exception:
                continue
            _locate = getattr(_meta, "locate_file", None)
            for _entry in getattr(_meta, "files", None) or []:
                _loc = _locate(_entry) if _locate is not None else _entry
                if _real(str(_loc)) == origin_real:
                    owners.append(_d)
                    break
    return owners[0] if len(owners) == 1 else None


def _resolve(full_name):
    _top = full_name.split(".")[0]
    _dists = _pkg_dist.get(_top)
    if not _dists:
        return None
    _chosen = _owning_dist(_dists, _module_origin(full_name))
    if _chosen is None:
        return None
    try:
        return [_chosen, _md.version(_chosen)]
    except Exception:
        return None


def _is_external_mod(name):
    if name in _inrepo_pkgs or name in _inrepo_names or _under_repo(_module_files.get(name)):
        return False
    _top = name.split(".")[0]
    return not _is_stdlib(_top, _module_files.get(_top) or _module_files.get(name))


def _norm(name):
    # PEP 503 canonical distribution name: lowercase, runs of -_. collapse to a single '-'.
    out = ""
    sep = False
    for ch in str(name).lower():
        if ch in "-_.":
            if not sep:
                out += "-"
            sep = True
        else:
            out += ch
            sep = False
    return out


def _dist_record(name):
    # ``[canonical Name, version]`` for an installed distribution, or None if absent.
    try:
        _d = _md.distribution(name)
    except Exception:
        return None
    try:
        _nm = _d.metadata["Name"] or name
    except Exception:
        _nm = name
    try:
        return [_nm, _d.version]
    except Exception:
        return None


def _dep_names(name):
    # The MANDATORY runtime dependencies a distribution DECLARES (Requires-Dist), by name. An
    # extra-gated optional dependency (``; extra == '...'``) is skipped: it is not installed unless
    # its extra is requested, and pinning it would inventory the environment rather than the closure.
    try:
        _reqs = _md.distribution(name).requires or []
    except Exception:
        return []
    _out = []
    for _req in _reqs:
        if ";" in _req and "extra" in _req.split(";", 1)[1]:
            continue
        _nm = ""
        for _ch in _req.strip():
            if _ch.isalnum() or _ch in "._-":
                _nm += _ch
            else:
                break
        if _nm:
            _out.append(_nm)
    return _out


# External modules imported DIRECTLY by an in-repo contract file: the fail-closed roots. Each MUST
# resolve to an installed distribution (the provenance the freeze protects), or discovery fails
# closed naming it.
_direct = set()
for _e in _edges:
    _pn, _pf, _ch = _e[0], _e[1], _e[2]
    if (_e[3] if len(_e) > 3 else 0) != 0:
        continue
    if not (_under_repo(_pf) or _pn in _inrepo_pkgs or _pn in _inrepo_names):
        continue
    if _ch in _inrepo_pkgs or _ch in _inrepo_names or _under_repo(_module_files.get(_ch)):
        continue
    # NB: do NOT skip on ``_top in _inrepo_pkgs`` — a namespace (``sphinxcontrib``) can be shared by
    # an in-repo member AND an external submodule (``sphinxcontrib.applehelp``); the child itself was
    # already proven external above, so a mixed-namespace external part must still be pinned.
    _top = _ch.split(".")[0]
    if not _top or _top == "_pytest":
        continue
    if _is_stdlib(_top, _module_files.get(_top) or _module_files.get(_ch)):
        continue
    _direct.add(_ch)

_root_dists = set()
_unresolvable = []
for _r in sorted(_direct):
    _pin = _resolve(_r)
    if _pin is None:
        _unresolvable.append(_r.split(".")[0])
    else:
        _root_dists.add(_pin[0])

# Entry-point (autoloaded) pytest plugins present in THIS venv are external roots too — loaded by
# pytest during collection though no test file imports them (autoload is ON in the hermetic env).
try:
    _eps = list(_md.entry_points(group="pytest11"))
except Exception:
    _eps = []
for _ep in _eps:
    _mod = getattr(_ep, "module", None) or (getattr(_ep, "value", "") or "").split(":")[0]
    if not _mod or _mod.split(".")[0] in ("pytest", "_pytest"):
        continue
    if _mod in sys.modules and _is_external_mod(_mod):
        _pin = _resolve(_mod)
        if _pin is not None:
            _root_dists.add(_pin[0])

# Transitive external closure over DECLARED distribution dependencies (Requires-Dist), pinned at the
# EXACT version installed in THIS provisioned interpreter. This follows a dist's own external deps
# (markdown-it-py -> mdurl) and a plugin's declared deps (pytest-timeout -> pytest) while EXCLUDING a
# dist's incidental runtime imports of unrelated installed packages (pytest imports wcwidth only if
# present; wcwidth is no declared dependency, so a decoy never enters the closure).
_seen = {}
_stack = list(_root_dists)
while _stack:
    _name = _stack.pop()
    _k = _norm(_name)
    if _k in _seen:
        continue
    _rec = _dist_record(_name)
    if _rec is None:
        continue
    _seen[_k] = _rec
    for _dep in _dep_names(_name):
        if _norm(_dep) in _seen:
            continue
        try:
            _md.version(_dep)  # follow only deps actually installed in this env
        except Exception:
            continue
        _stack.append(_dep)

_pins = list(_seen.values())

sys.stdout.write(
    "<<<IFDISCOVER_START>>>"
    + json.dumps(
        {
            "edges": _edges,
            "module_files": _module_files,
            "module_paths": _module_paths,
            "collect_rc": collect_rc,
            "external": _pins,
            "unresolvable": _unresolvable,
        }
    )
    + "<<<IFDISCOVER_END>>>"
)
"""


def _collection_args(command: object) -> list[str]:
    """The configured baseline's own pytest CLI arguments — every token AFTER the ``pytest`` launcher
    token (however it is spelled: ``["-m", "pytest", "tests"]``, ``["pytest", "tests"]``,
    ``["uv", "run", "pytest", "tests"]`` all yield ``["tests"]``). These path filters / ``-p`` plugin
    flags decide WHICH modules the baseline collects, so the instrumented collection can reproduce the
    configured collection scope instead of a hardcoded bare pytest that would pull in modules the
    baseline excludes. An unrecognized command (no ``pytest`` token) yields no extra args."""
    tokens = [str(tok) for tok in command or ()]
    for index, tok in enumerate(tokens):
        leaf = tok.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if leaf in ("pytest", "py.test"):
            return tokens[index + 1 :]
    return []


def _run_discovery_collector(
    interpreter: object, worktree: Path, env: object, collect_args: object = ()
) -> dict:
    """Launch the instrumented collector and parse the emitted import graph.

    ``collect_args`` are the configured baseline's own pytest arguments (path filters, ``-p`` plugin
    flags); passing them through makes the instrumented collection select the SAME modules the
    configured baseline does, never a hardcoded bare ``pytest`` that would import modules the baseline
    excludes into the recorded graph."""
    argv = [
        str(interpreter),
        "-c",
        _DISCOVER_SRC,
        str(worktree),
        *[str(arg) for arg in collect_args or ()],
    ]
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
        wins on a collision. External identity (distribution + version) is resolved INSIDE the
        provisioned discovery subprocess (D5) — so pins carry the PROVISIONED interpreter's versions,
        never this parent process's — and the transitive external closure (a dist's own external
        deps, an autoloaded plugin's reached deps) is pinned too. An external import made by an
        in-repo contract file that resolves to no installed distribution fails the discovery CLOSED
        with ``DiscoveryError`` naming it — never a partial closure.
        """
        worktree = Path(getattr(collection, "worktree")).resolve()
        interpreter = getattr(collection, "interpreter")
        env = getattr(collection, "env", None)
        ids = tuple(getattr(collection, "ids", ()) or ())
        # The instrumented collection honors the CONFIGURED baseline command (its path filters / ``-p``
        # plugin flags), never a hardcoded bare ``pytest`` — so the discovered closure matches exactly
        # what the configured collection selects and cannot pull in modules the baseline excludes.
        collect_args = _collection_args(getattr(collection, "command", ()) or ())

        graph = _run_discovery_collector(interpreter, worktree, env, collect_args)
        edges = graph["edges"]
        module_files = graph["module_files"]
        # External pins are resolved in the provisioned subprocess (D5); the parent only consumes
        # them. A direct in-repo external import the subprocess could not resolve to an installed
        # distribution fails the discovery CLOSED here, naming the offender — never a partial closure.
        unresolvable = graph.get("unresolvable") or []
        if unresolvable:
            raise DiscoveryError(unresolvable[0])
        # A BROKEN instrumented ``collect_rc`` means the collection did not complete (a hard
        # collection/internal/usage error, a timeout, an aborted run): the recorded graph is PARTIAL,
        # so fail the discovery CLOSED rather than build a closure from a truncated import graph the
        # freeze would then approve. Exit 0 (clean) and exit 5 (a clean, EMPTY collection) are the only
        # complete outcomes — mirroring the freeze's own candidate-collection completion gate. (An
        # unresolvable import above names the specific offender first; this catches every OTHER broken
        # collection, including one with nothing unresolvable.)
        collect_rc = graph.get("collect_rc")
        if collect_rc not in (0, 5):
            raise RuntimeError(
                f"instrumented contract discovery did not complete cleanly "
                f"(collect_rc={collect_rc!r}); refusing a partial closure"
            )
        external = tuple(sorted({(str(d), str(v)) for d, v in graph.get("external") or []}))

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

        test_files = tuple(sorted({nid.split("::")[0] for nid in ids if "::" in nid}))
        test_set = set(test_files)

        # In-repo adjacency (parent_rel -> child_rel). A file is a ROOT when no in-repo file imported
        # it: pytest imports conftests/test modules by file spec (bypassing the import hooks) and loads
        # ``-p`` plugins / config modules itself, so those carry no in-repo importer. (External pins
        # are computed in the subprocess; the parent loop only builds the in-repo import graph.)
        adjacency: dict[str, set[str]] = {}
        has_inrepo_parent: set[str] = set()
        inrepo_nodes: set[str] = set(name_to_rel.values())
        for parent_name, parent_file, child_name, *_rest in edges:
            parent_rel = _relrepo(parent_file)
            if parent_rel is None:
                parent_rel = name_to_rel.get(parent_name)
            if parent_rel is not None:
                # A conftest name collides across directories (every ``conftest.py`` is module
                # ``conftest``), so a root conftest is knowable only via its edges' importer FILE —
                # record it as an in-repo node so it can still be a fixture root.
                inrepo_nodes.add(parent_rel)
            child_rel = name_to_rel.get(child_name)
            if child_rel is not None and parent_rel is not None:
                adjacency.setdefault(parent_rel, set()).add(child_rel)
                has_inrepo_parent.add(child_rel)

        roots = inrepo_nodes - has_inrepo_parent
        fixture_roots = roots - test_set
        fixture_closure = _reach(fixture_roots, adjacency)
        test_reach = _reach(test_set, adjacency)
        test_body_imports = test_reach - fixture_closure - test_set

        return ContractClosure(
            test_files=test_files,
            fixture_closure=tuple(sorted(fixture_closure)),
            test_body_imports=tuple(sorted(test_body_imports)),
            external=external,
        )

    def validate_invocation(self, command: object) -> object:
        raise NotImplementedError("validate_invocation lands in S13")


registry.register(PytestAdapter())
