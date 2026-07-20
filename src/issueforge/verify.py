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


def _parse_report_log(text: str) -> list[dict]:
    """Extract per-phase TestReport records from a pytest report-log JSONL body."""
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("$report_type") == "TestReport":
            records.append(
                {
                    "nodeid": data.get("nodeid"),
                    "when": data.get("when"),
                    "outcome": data.get("outcome"),
                    "longrepr": data.get("longrepr"),
                }
            )
    return records


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
    handle = adapter.provision_environment(worktree, None, provisioner=provisioner)
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

    exit_code: int | None = None
    timed_out = False
    if not launch_failed:
        argv = [str(interpreter), *base_command, f"--report-log={report_file}"]
        try:
            result = process.run(argv, cwd=worktree, timeout=timeout, env=env)
            exit_code = result.returncode
            timed_out = result.timed_out
        except _LAUNCH_ERRORS:
            launch_failed = True

    records: list[dict] = []
    report_present = False
    if not launch_failed and not timed_out and report_file.exists():
        text = report_file.read_text()
        report_present = bool(text.strip())
        records = _parse_report_log(text)

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


def _read_baseline_command(worktree: Path) -> list[str]:
    """Read the committed baseline command from the worktree's ``.issueforge.toml``."""
    config = Path(worktree) / ".issueforge.toml"
    if not config.exists():
        return ["-m", "pytest"]
    try:
        data = tomllib.loads(config.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return ["-m", "pytest"]
    baseline = data.get("baseline")
    if isinstance(baseline, list) and baseline:
        return [str(item) for item in baseline]
    return ["-m", "pytest"]


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
    command = _read_baseline_command(worktree)
    evidence = runner(worktree, command, adapter=adapter, provisioner=provisioner)

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
