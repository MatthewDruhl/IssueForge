"""The green-baseline runner: establish a provably-GREEN baseline before any AI touches a file.

The load-bearing point of S6 is that "exit 0 is not green". ``run_baseline`` provisions a separate
authoritative environment, runs the baseline command as an argv array (no shell) through the
subprocess seam with a per-invocation ``--report-log`` written to a FRESH directory OUTSIDE the
repo, parses that report, takes the expected-id set from ``canonical_collect`` (never from the
execution report), and classifies the fused evidence. ``establish_green_baseline`` orchestrates
fetch -> isolate -> baseline and PAUSES on anything not provably green — a failed fetch, unprovable
isolation, or a non-green baseline — before it ever dispatches AI.
"""

from __future__ import annotations

import json
import os
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from issueforge import process
from issueforge import workspace as _workspace_mod
from issueforge.adapters.base import BaselineStatus, NodeRecord
from issueforge.io import WriteSeam
from issueforge.paths import state_root

_DEFAULT_TIMEOUT = 600.0
# Errors raised when a provisioned interpreter cannot be launched at all — distinct from a run
# that launched and produced a (possibly bad) exit code.
_LAUNCH_ERRORS = (FileNotFoundError, NotADirectoryError, PermissionError, OSError)


@dataclass(frozen=True)
class Evidence:
    """Everything fused into a baseline verdict, plus the authoritative-environment provenance."""

    status: BaselineStatus
    collected: int
    executed: int
    nodes: tuple[NodeRecord, ...]
    report_present: bool
    report_dir: Path | None
    exit_code: int | None
    interpreter: object
    env_root: object
    network: object


@dataclass(frozen=True)
class BaselineOutcome:
    """The orchestration result: whether the run paused, and the worktree/evidence it produced."""

    paused: bool
    status: BaselineStatus | None
    worktree: Path | None
    evidence: Evidence | None
    pause_reason: str | None


def _parse_report_log(text: str, exit_code: int | None = None) -> tuple[list[dict], bool]:
    """Extract per-phase TestReport records from a pytest report-log JSONL body.

    Returns ``(records, well_formed)``. A report is ``well_formed`` only when EVERY non-empty line
    parses as JSON, a ``SessionFinish`` record is the LAST (terminal) record, and its ``exitstatus``
    is consistent with the subprocess ``exit_code`` (#58 hardening / gap #6 + second-round gap C).
    A report cut off mid-flush (a dropped/garbage final line, a missing session-end marker), or one
    whose session-end marker does not TERMINATE the log (a later record follows it — a spliced or
    reordered report), or one whose recorded ``exitstatus`` contradicts the process exit, is
    untrustworthy evidence and must not be laundered into GREEN off the passing records that
    survived. A malformed line is no longer silently swallowed — it flips ``well_formed`` False.
    """
    records: list[dict] = []
    well_formed = True
    last_report_type: str | None = None
    session_finish_exitstatus: object = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            well_formed = False
            continue
        report_type = data.get("$report_type")
        last_report_type = report_type
        if report_type == "SessionFinish":
            session_finish_exitstatus = data.get("exitstatus")
        elif report_type == "TestReport":
            records.append(
                {
                    "nodeid": data.get("nodeid"),
                    "when": data.get("when"),
                    "outcome": data.get("outcome"),
                    "longrepr": data.get("longrepr"),
                    # Retain pytest's ``wasxfail`` marker so an XPASS (a non-strict xfail that
                    # PASSED) stays distinguishable from a genuine pass downstream (S10).
                    "wasxfail": data.get("wasxfail"),
                }
            )
    # The SessionFinish must be the TERMINAL record: a session-end marker followed by any later
    # record is a spliced/reordered/truncated log, not a clean session end.
    terminal_session_finish = last_report_type == "SessionFinish"
    # The recorded session exit status, when present, must agree with the real subprocess exit code —
    # a report claiming a different exit than the process actually returned is contradictory evidence.
    exit_consistent = (
        session_finish_exitstatus is None
        or exit_code is None
        or session_finish_exitstatus == exit_code
    )
    return records, (well_formed and terminal_session_finish and exit_consistent)


# Leading tokens that are pip include/editable/option DIRECTIVES, not package specs: a
# requirements line beginning with one of these (or any other ``-``-prefixed option) carries no
# installable package name and must be skipped, never passed to ``uv pip install`` as a spec.
_REQUIREMENTS_DIRECTIVES = frozenset(
    {
        "-r",
        "--requirement",
        "-c",
        "--constraint",
        "-e",
        "--editable",
        "-f",
        "--find-links",
        "-i",
        "--index-url",
        "--extra-index-url",
        "--no-index",
        "--pre",
        "--hash",
    }
)


def _parse_requirement_specs(text: str) -> list[str]:
    """Parse a pip/pip-freeze ``requirements.txt`` body into installable package specs.

    Handles the standard shapes a naive line-splitter breaks on (#58 second-round gap A):
      * line continuations — a spec whose line ends with ``\\`` continues on the next physical line;
        the continuation is JOINED before parsing so a hashed pin is ONE spec, not a backslash-bearing
        package plus a stray ``--hash`` arg.
      * hash / option lines — ``--hash=sha256:...`` and any other ``-``/``--`` option token are
        dropped, never handed to the installer as a package spec.
      * include / editable / constraint directives — ``-r base.txt``, ``-e .``, ``-c c.txt`` carry no
        package name and are skipped rather than passed through as bogus specs.
    Each surviving line contributes its leading non-option tokens (the requirement spec, including any
    PEP 508 marker) as a single spec.
    """
    joined = text.replace("\\\r\n", " ").replace("\\\n", " ")
    specs: list[str] = []
    for raw in joined.splitlines():
        # Drop a full-line or inline comment (pip requires a space before an inline ``#``).
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        first = tokens[0]
        # An include/editable/constraint directive or a bare option line has no package spec.
        if first in _REQUIREMENTS_DIRECTIVES or first.startswith("-"):
            continue
        # The spec is the run of leading non-option tokens; a ``-``-prefixed token (``--hash=...``)
        # ends it. Markers (``; python_version < "3.11"``) carry no ``-`` and stay in the spec.
        spec_tokens: list[str] = []
        for tok in tokens:
            if tok.startswith("-"):
                break
            spec_tokens.append(tok)
        if spec_tokens:
            specs.append(" ".join(spec_tokens))
    return specs


def _discover_frozen_manifest(worktree: Path) -> list[str] | None:
    """Discover the target's committed frozen dependency manifest (#58 hardening / gap #2).

    The authoritative environment must install the SAME pinned deps the target committed, so a
    baseline that imports a pinned dep is importable in the separate run. The manifest is read from
    the committed git object (``git show HEAD:<file>``), NEVER the worktree filesystem — consistent
    with the #2 committed-baseline discipline, so a post-checkout-mutated or symlinked manifest can
    not substitute deps. Prefers a committed ``requirements.txt`` (parsed robustly for continuations,
    hashes, and include/editable directives), falling back to a committed ``pyproject.toml``'s
    ``[project].dependencies`` list. Returns the discovered specs, or ``None`` when the target commits
    no usable manifest (the provisioner then installs only the reporter toolchain).
    """
    wt = Path(worktree)
    scrubbed = _workspace_mod._scrubbed_git_env()

    def _show(path: str) -> str | None:
        shown = process.run(
            ["git", "-C", str(wt), "show", f"HEAD:{path}"],
            cwd=wt,
            timeout=_GIT_TIMEOUT,
            env=scrubbed,
        )
        return shown.stdout if shown.returncode == 0 else None

    requirements = _show("requirements.txt")
    if requirements is not None:
        specs = _parse_requirement_specs(requirements)
        if specs:
            return specs
    pyproject = _show("pyproject.toml")
    if pyproject is not None:
        try:
            data = tomllib.loads(pyproject)
        except tomllib.TOMLDecodeError:
            return None
        deps = data.get("project", {}).get("dependencies")
        if isinstance(deps, list) and deps and all(isinstance(spec, str) for spec in deps):
            return [str(spec) for spec in deps]
    return None


def run_baseline(
    worktree: Path,
    base_command: list[str],
    *,
    adapter: object,
    provisioner: object = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Evidence:
    """Provision, run the baseline with a fresh ``--report-log``, and classify the fused evidence."""
    worktree = Path(worktree)
    # Discover the target's committed frozen manifest and hand it to the provisioner so the
    # authoritative env installs the SAME pinned deps the baseline imports (#58 hardening / gap #2).
    frozen_deps = _discover_frozen_manifest(worktree)
    # A provisioning failure (missing uv, a write failure, a provisioner exception) must PAUSE with
    # typed non-green evidence, never crash the engine before AI is gated (#58/#12).
    try:
        handle = adapter.provision_environment(worktree, frozen_deps, provisioner=provisioner)
    except Exception:  # noqa: BLE001 — any provisioning failure becomes a paused, non-green record.
        return _non_green_evidence(BaselineStatus.LAUNCH_FAILED)
    interpreter = handle.interpreter
    env = getattr(handle, "env", None)

    # A fresh report directory OUTSIDE the repo per invocation, created through the write seam so
    # no stale report from a previous run can ever be consumed.
    report_dir = Path(state_root()) / "reports" / uuid.uuid4().hex
    report_file = report_dir / "report.jsonl"
    getattr(WriteSeam(), "write_text")(report_dir / ".if-keep", "")

    invocation = SimpleNamespace(
        worktree=worktree, interpreter=interpreter, command=list(base_command), env=env
    )

    # Expected ids come from a real --collect-only, never from the execution report — a node that
    # vanished from the run must still count against green.
    launch_failed = False
    expected_ids: set = set()
    try:
        collection = adapter.canonical_collect(invocation)
        expected_ids = set(getattr(collection, "ids", ()) or ())
    except _LAUNCH_ERRORS:
        launch_failed = True
    except Exception:  # noqa: BLE001 — a collection/runner exception pauses, never crashes (#58/#12).
        return _non_green_evidence(BaselineStatus.COLLECTION_ERROR, handle=handle)

    # A collection that exited nonzero or timed out cannot be trusted to seed the expected-id set,
    # so it can NEVER establish GREEN (#58/#7). The baseline still runs so its OWN exit code drives
    # the classification (a bad flag is USAGE_ERROR, an empty suite is NO_TESTS_COLLECTED, ...); an
    # untrusted (emptied) expected set forces a non-green verdict for any exit-0 run — a broken
    # collection can never be laundered into green via ids scraped from its stdout.
    if not launch_failed and not getattr(collection, "ok", True):
        expected_ids = set()

    exit_code: int | None = None
    timed_out = False
    if not launch_failed:
        try:
            result = _execute_baseline(handle, base_command, worktree, report_file, timeout, env)
            exit_code = result.returncode
            timed_out = result.timed_out
        except _LAUNCH_ERRORS:
            launch_failed = True
        except Exception:  # noqa: BLE001 — a denied-network executor failure pauses, never crashes.
            launch_failed = True

    records: list[dict] = []
    report_present = False
    if not launch_failed and not timed_out and report_file.exists():
        text = report_file.read_text()
        records, well_formed = _parse_report_log(text, exit_code)
        # A report only counts as PRESENT evidence when it is well-formed and cleanly terminated: a
        # truncated/corrupt report with no terminal SessionFinish is not trustworthy behavioral
        # evidence and must not seed GREEN (#58 hardening / gap #6).
        report_present = bool(text.strip()) and well_formed

    classification = adapter.classify(
        records,
        exit_code=None if launch_failed else exit_code,
        timed_out=timed_out,
        report_present=report_present,
        expected_ids=expected_ids,
    )
    return Evidence(
        status=classification.status,
        collected=classification.collected,
        executed=classification.executed,
        nodes=classification.nodes,
        report_present=report_present,
        # A timeout leaves no trustworthy report; surface no report_dir so a flushed partial can
        # never be mistaken for evidence.
        report_dir=None if timed_out else report_dir,
        exit_code=None if launch_failed else exit_code,
        interpreter=interpreter,
        env_root=getattr(handle, "env_root", None),
        network=getattr(handle, "network", None),
    )


def _non_green_evidence(
    status: BaselineStatus, *, handle: object = None, exit_code: int | None = None
) -> Evidence:
    """A typed non-green Evidence for a paused run — a provisioning/collection/runner failure that
    produced no trustworthy behavioral report."""
    return Evidence(
        status=status,
        collected=0,
        executed=0,
        nodes=(),
        report_present=False,
        report_dir=None,
        exit_code=exit_code,
        interpreter=getattr(handle, "interpreter", None),
        env_root=getattr(handle, "env_root", None),
        network=getattr(handle, "network", None),
    )


def _execute_baseline(
    handle: object,
    base_command: list[str],
    worktree: Path,
    report_file: Path,
    timeout: float,
    env: dict | None,
) -> process.CommandResult:
    """Run the baseline, resolving the committed command's OWN executable in the authoritative env
    (#58/#1) instead of blindly prepending the provisioned interpreter.

    When the handle marks the authoritative run for OS-level network denial (``denies_network`` —
    set only by the real default provisioner, never by an injected test seam) AND the baseline is
    invoked through the interpreter (a leading ``-m``/``-c``/``-X`` flag), the baseline executes
    inside a ``docker run --network none`` container so egress is denied by the OS, not merely
    recorded on the handle. Provisioning already ran on the host with the network ON; only EXECUTION
    is denied here (#58/#10). A bare-console-script baseline (``['pytest']``) resolves and runs on the
    host under the authoritative ``env_root`` so its executable provenance stays checkable — the
    container denies via ``argv[0] == 'docker'``, which cannot also be an under-``env_root`` path."""
    denies = getattr(handle, "denies_network", False)
    interpreter_form = bool(base_command) and str(base_command[0]).startswith("-")
    if denies and interpreter_form:
        return _execute_denied_network(handle, base_command, worktree, report_file, timeout)
    argv = [
        *process.build_launch_argv(handle.interpreter, base_command, env=env),
        f"--report-log={report_file}",
    ]
    return process.run(argv, cwd=worktree, timeout=timeout, env=env)


_DOCKER_INSPECT_TIMEOUT = 60.0
_DOCKER_PULL_TIMEOUT = 300.0
# In-container mount points (absolute paths are composed inline; a bare ``/``-leading literal here
# would trip the class-4 checkout-relative-default lint).
_CONTAINER_WORKDIR = "if-work"
_CONTAINER_REPORTDIR = "if-report"
_CONTAINER_SITE_PACKAGES = "if-deps"


def _ensure_base_image(image: str) -> None:
    """Ensure the base ``python`` image is present, pulling it once if genuinely absent.

    ``docker image inspect`` is a cheap presence probe; only a COMPLETED inspect that reports absence
    triggers a ``docker pull`` (which uses the DAEMON's network — legitimate provisioning, distinct
    from the container run that is denied). The pull runs BEFORE the ``--network none`` container, so
    denial never blocks it. An inspect that TIMES OUT means an unresponsive daemon, not a cache miss —
    it fails fast rather than cascading into a full-length pull that would only fail closed minutes
    later. A failed/timed-out pull raises so ``run_baseline`` pauses with a typed non-green record
    rather than executing an unverifiable run.
    """
    inspect = process.run(
        ["docker", "image", "inspect", image],
        cwd=Path(state_root()),
        timeout=_DOCKER_INSPECT_TIMEOUT,
    )
    if inspect.timed_out:
        raise RuntimeError("docker image inspect timed out; daemon is unresponsive")
    if inspect.returncode == 0:
        return
    # Inspect completed and reported absence -> pull once, on a bounded timeout.
    pull = process.run(
        ["docker", "pull", image],
        cwd=Path(state_root()),
        timeout=_DOCKER_PULL_TIMEOUT,
    )
    if pull.returncode != 0 or pull.timed_out:
        raise RuntimeError(f"verify base image pull failed: {pull.stderr.strip()!r}")


def _venv_site_packages(handle: object) -> Path:
    """Locate the authoritative venv's ``site-packages`` under its owned ``env_root``.

    The baseline runs inside the container against the deps PROVISIONED on the host (pytest, the
    report-log reporter, and the target's frozen manifest). Bind-mounting this directory and pointing
    the container's interpreter at it makes those deps importable in the denied run without a second
    install."""
    env_root = getattr(handle, "env_root", None)
    if env_root is None:
        raise RuntimeError("authoritative handle carries no env_root for the denied-network run")
    matches = sorted(Path(env_root).glob("lib/python*/site-packages"))
    if not matches:
        raise RuntimeError(f"authoritative venv has no site-packages under {env_root}")
    return matches[0]


def _base_image_for(site_packages: Path) -> str:
    """The ``python:<major>.<minor>-slim`` image matching the provisioned venv's Python version.

    The version is read from the venv layout (``lib/python3.X/site-packages``) so the container's
    interpreter matches the interpreter the host deps were installed for — a compiled dep's ABI tag
    then agrees between the mounted site-packages and the container's Python."""
    version = site_packages.parent.name.replace("python", "")
    return f"python:{version}-slim"


def _container_command(base_command: list[str]) -> list[str]:
    """Map the committed baseline command onto the container's own interpreter.

    A leading Python flag (``-m``, ``-c``, …) is an argument TO the interpreter, so the container's
    ``python`` runs the command verbatim; otherwise the command names its OWN console script
    (``pytest``). Mirrors ``process.build_launch_argv`` semantics without a host PATH lookup (the
    executable is resolved inside the container)."""
    command = list(base_command)
    if command and str(command[0]).startswith("-"):
        return ["python", *command]
    return command


def _execute_denied_network(
    handle: object,
    base_command: list[str],
    worktree: Path,
    report_file: Path,
    timeout: float,
) -> process.CommandResult:
    """Execute the baseline inside ``docker run --network none``, bind-mounting the worktree, the
    provisioned venv's site-packages, and the out-of-repo report directory, so the run has no
    host/external IP egress at the OS level (loopback remains inside the container) while its deps are
    importable and its report-log is written back to the host.

    The docker argv is a plain list handed to the ``process.run`` seam (which wraps it in the typed
    ``boundary.Command`` before it reaches subprocess) — the same provenance path every engine
    command uses, so the boundary lint's class-2 executable check never sees a raw subprocess literal.
    """
    site_packages = _venv_site_packages(handle)
    image = _base_image_for(site_packages)
    _ensure_base_image(image)
    report_dir = report_file.parent
    report_arg = f"--report-log=/{_CONTAINER_REPORTDIR}/{report_file.name}"
    return process.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{worktree}:/{_CONTAINER_WORKDIR}",
            "-v",
            f"{report_dir}:/{_CONTAINER_REPORTDIR}",
            "-v",
            f"{site_packages}:/{_CONTAINER_SITE_PACKAGES}:ro",
            "-e",
            f"PYTHONPATH=/{_CONTAINER_SITE_PACKAGES}",
            "-w",
            f"/{_CONTAINER_WORKDIR}",
            image,
            *_container_command(base_command),
            report_arg,
        ],
        cwd=worktree,
        timeout=timeout,
    )


_GIT_TIMEOUT = 120.0
_SEAM_DEFAULT_BASELINE = ["-m", "pytest"]


def _committed_baseline(worktree: Path) -> tuple[bool, list[str] | None]:
    """Read the MANDATORY baseline command from the committed HEAD object of ``worktree``.

    Returns ``(enforced, command)``. The baseline is read from the git object at HEAD
    (``git show HEAD:.issueforge.toml``), NEVER the worktree filesystem — a symlinked or
    post-checkout-mutated config must not be able to substitute a command (#58/#2). A real committed
    worktree is ``enforced``: a missing/malformed/empty/non-list committed baseline yields
    ``(True, None)`` so the caller PAUSES with no default and no dispatch. A path that is not a
    committed git tree at all (an injected-seam unit context, where the real workspace was replaced)
    is ``(False, None)`` — the caller may fall back to the legacy default, since there is no
    committed object to enforce against; production always hands in a real isolated worktree.
    """
    wt = Path(worktree)
    # Scrub the location-redirecting GIT_* family (GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/…) so a
    # hostile or leftover ambient redirect cannot swap in a DIFFERENT repo's committed baseline —
    # exactly as the workspace snapshot/fetch git reads already do (#58 hardening / gap #4).
    scrubbed = _workspace_mod._scrubbed_git_env()
    head = process.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", "HEAD"],
        cwd=wt,
        timeout=_GIT_TIMEOUT,
        env=scrubbed,
    )
    if head.returncode != 0:
        # A rev-parse failure on a path that carries a ``.git`` (a real, but corrupt/unreadable,
        # committed worktree) must PAUSE — there is no readable committed baseline, and the run must
        # never fall back to the forbidden default and dispatch AI (#58 hardening / gap #3). Only a
        # path that is not a git tree at all (no ``.git``) is a seam/unit context that may default.
        # ``os.path.lexists`` (not ``Path.exists``) is load-bearing: a PRESENT-but-dangling ``.git``
        # symlink is a corrupt worktree, but ``exists`` FOLLOWS the link and reports False, which
        # would wrongly default and dispatch on unreadable metadata (#58 second-round gap B).
        if os.path.lexists(wt / ".git"):
            return (True, None)
        return (False, None)  # not a committed git worktree — a seam context, not enforceable
    shown = process.run(
        ["git", "-C", str(wt), "show", "HEAD:.issueforge.toml"],
        cwd=wt,
        timeout=_GIT_TIMEOUT,
        env=scrubbed,
    )
    if shown.returncode != 0:
        return (True, None)  # committed tree, but no committed config object -> pause
    try:
        data = tomllib.loads(shown.stdout)
    except tomllib.TOMLDecodeError:
        return (True, None)
    baseline = data.get("baseline")
    if isinstance(baseline, list) and baseline and all(isinstance(item, str) for item in baseline):
        return (True, [str(item) for item in baseline])
    return (True, None)  # empty list, non-list, or wrong element type -> pause


_DEFAULT_RUN_BASELINE = run_baseline


def establish_green_baseline(
    checkout: Path,
    *,
    adapter: object,
    workspace: object = None,
    provisioner: object = None,
    run_baseline: object = None,
    dispatch: object = None,
) -> BaselineOutcome:
    """Fetch -> isolate -> baseline, pausing BEFORE any AI on anything not provably green.

    The baseline command is read from the ISOLATED WORKTREE's committed config (never the normal
    checkout, which may be dirty). ``dispatch`` fires exactly once, and ONLY when the baseline is
    green; a failed fetch, unprovable isolation, or a non-green baseline all pause with no dispatch.
    ``workspace``/``run_baseline``/``dispatch`` are injectable seams.
    """
    ws = workspace if workspace is not None else _workspace_mod
    runner = run_baseline if run_baseline is not None else _DEFAULT_RUN_BASELINE

    fetched = ws.fetch_default_sha(checkout)
    if not getattr(fetched, "ok", False):
        return BaselineOutcome(
            paused=True,
            status=None,
            worktree=None,
            evidence=None,
            pause_reason=f"fetch failed: {getattr(fetched, 'reason', None)}",
        )

    worktree_result = ws.create_isolated_worktree(checkout, fetched.sha)
    if not (getattr(worktree_result, "ok", False) and getattr(worktree_result, "isolated", False)):
        return BaselineOutcome(
            paused=True,
            status=None,
            worktree=None,
            evidence=None,
            pause_reason=f"unprovable isolation: {getattr(worktree_result, 'reason', None)}",
        )

    worktree = worktree_result.path
    # The baseline is MANDATORY and comes from the ISOLATED worktree's committed HEAD object. A
    # missing/malformed/empty/non-list committed baseline PAUSES with no dispatch (#58/#2).
    enforced, command = _committed_baseline(worktree)
    if command is None:
        if enforced:
            return BaselineOutcome(
                paused=True,
                status=None,
                worktree=worktree,
                evidence=None,
                pause_reason="missing or invalid committed baseline in .issueforge.toml",
            )
        command = list(_SEAM_DEFAULT_BASELINE)

    try:
        evidence = runner(worktree, command, adapter=adapter, provisioner=provisioner)
    except Exception:  # noqa: BLE001 — a runner explosion pauses, never crashes the engine (#58/#12).
        return BaselineOutcome(
            paused=True,
            status=BaselineStatus.LAUNCH_FAILED,
            worktree=worktree,
            evidence=None,
            pause_reason="baseline runner failed to produce evidence",
        )

    if evidence.status is BaselineStatus.GREEN:
        if dispatch is not None:
            dispatch(worktree=worktree, evidence=evidence, sha=fetched.sha)
        return BaselineOutcome(
            paused=False,
            status=BaselineStatus.GREEN,
            worktree=worktree,
            evidence=evidence,
            pause_reason=None,
        )
    return BaselineOutcome(
        paused=True,
        status=evidence.status,
        worktree=worktree,
        evidence=evidence,
        pause_reason=f"baseline not green: {evidence.status}",
    )
