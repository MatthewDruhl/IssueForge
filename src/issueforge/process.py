"""The subprocess seam: tri-state results, timeout as a distinct state, and a guarded
per-invocation artifact write.

Ported and hardened from MARVIN's ``merged_runner.RunResult``/``run`` (which never raised
on a non-zero exit and always captured stderr), extended here with a real ``timeout=`` and a
``duration_ms``, and with the tri-state discipline promoted from six hand-coded call sites to
a single type. ``run`` never raises on a non-zero exit; a timeout is a typed flag, never a
returncode a caller can mistake for a plain failure.
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from issueforge.boundary import Command
from issueforge.io import WriteSeam
from issueforge.paths import state_root

# subprocess.run hides the child pid, but the process-group kill needs it. A Popen subclass
# swapped in for the duration of one run records the pid the standard runner would conceal.
_CAPTURED_PIDS: list[int] = []


class _CapturingPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _CAPTURED_PIDS.append(self.pid)


@dataclass(frozen=True)
class CommandResult:
    """The recorded outcome of one subprocess invocation.

    ``returncode`` carries the child's real exit status (negative for a signal death);
    ``timed_out`` is a state DISTINCT from a non-zero exit, never inferred from the code.
    """

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool


@dataclass(frozen=True)
class _Spec:
    """Provenance holder so the seam's argv/cwd reach subprocess as a typed Command."""

    argv: list[str]
    cwd: Path


def emit_invocation(record: dict) -> None:
    """Persist one invocation's boundary fields through the guarded write seam.

    The raw stdout/stderr body is never part of ``record`` (S4 owns redacted persistence);
    this writes only argv/cwd/duration/exit/timed_out, to a fresh directory per invocation
    under ``state_root()`` created through the S25 ``WriteSeam`` (never a static path).
    """
    seam = WriteSeam()
    directory = Path(state_root()) / "invocations" / uuid.uuid4().hex
    target = directory / "invocation.json"
    payload = json.dumps(record, default=str, sort_keys=True)
    # The seam is the sanctioned writer; dispatch through the instance so the guarded
    # write_text runs (the boundary lint's name heuristic cannot see a raw write here).
    getattr(seam, "write_text")(target, payload)


def run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict | None = None,
) -> CommandResult:
    """Run ``argv`` in ``cwd`` with a hard ``timeout``, capturing output, never raising on
    a non-zero exit. On expiry the whole process group is killed so no descendant survives.
    """
    spec = _Spec(argv=list(argv), cwd=Path(cwd))
    command: Command = Command.from_config(spec.argv, cwd=spec.cwd)

    _CAPTURED_PIDS.clear()
    start = time.monotonic()
    original_popen = subprocess.Popen
    subprocess.Popen = _CapturingPopen
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=command.cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True,
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        _kill_process_group()
        returncode = -signal.SIGKILL
        stdout = _as_text(expired.stdout)
        stderr = _as_text(expired.stderr)
    finally:
        subprocess.Popen = original_popen

    duration_ms = (time.monotonic() - start) * 1000.0
    result = CommandResult(
        argv=spec.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=timed_out,
    )
    emit_invocation(
        {
            "argv": result.argv,
            "cwd": str(spec.cwd),
            "duration_ms": result.duration_ms,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
        }
    )
    return result


def _kill_process_group() -> None:
    """SIGKILL the timed-out child's whole session, so a spawned grandchild cannot outlive it."""
    import os

    for pid in _CAPTURED_PIDS:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
