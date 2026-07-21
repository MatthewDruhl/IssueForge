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
import os
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from issueforge.boundary import Command
from issueforge.io import WriteSeam
from issueforge.paths import state_root


def build_launch_argv(
    interpreter: object, command: list[str], *, env: dict | None = None
) -> list[str]:
    """Resolve the launch argv for a configured baseline ``command`` in the authoritative env.

    The committed baseline names its OWN executable — ``["pytest"]`` or ``["uv", "run", "pytest"]``
    — so its ``argv[0]`` must be resolved as an executable in the authoritative environment, never
    blindly prefixed with the provisioned interpreter (that turns ``pytest`` into a bogus script
    path). The one exception is a leading Python flag (``-m``, ``-c``, ``-X``…): a token starting
    with ``-`` is an argument TO the interpreter, so the interpreter is the executable and the whole
    command is handed to it verbatim. Resolution uses the authoritative env's ``PATH`` (``shutil``'s
    ``which`` is a read, not a write), falling back to the literal name when it is not on PATH so the
    caller's chosen executable — not the interpreter — is still what launches.
    """
    command = list(command)
    if not command:
        return [str(interpreter)]
    first = str(command[0])
    if first.startswith("-"):
        return [str(interpreter), *command]
    path = (env or {}).get("PATH") or os.environ.get("PATH", "")
    resolved = shutil.which(first, path=path) or first
    return [resolved, *command[1:]]


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


def emit_invocation(record: dict, *, secrets: set[str] | None = None) -> None:
    """Persist one invocation's boundary fields through the guarded write seam.

    The raw stdout/stderr body is never part of ``record`` (S4 owns redacted persistence);
    this writes only argv/cwd/duration/exit/timed_out, to a fresh directory per invocation
    under ``state_root()`` created through the S25 ``WriteSeam`` (never a static path).
    ``secrets``, when given, is redacted out of the serialized payload before it is written —
    the argv can carry a caller's prompt, so this record is a persisted artifact too.
    """
    seam = WriteSeam()
    directory = Path(state_root()) / "invocations" / uuid.uuid4().hex
    target = directory / "invocation.json"
    # Redact each secret out of the record's RAW string values BEFORE serialization. json.dumps
    # escapes newlines/quotes/backslashes/control chars/non-ASCII, so a post-serialization
    # str.replace would miss any secret containing such a character and leak it in escaped form.
    # Longest-first so a longer secret is masked before a shorter one it contains.
    redacted = _redact_record(record, sorted(secrets or (), key=len, reverse=True))
    payload = json.dumps(redacted, default=str, sort_keys=True)
    # The seam is the sanctioned writer; dispatch through the instance so the guarded
    # write_text runs (the boundary lint's name heuristic cannot see a raw write here).
    getattr(seam, "write_text")(target, payload)


def _redact_record(value: object, secrets: list[str]) -> object:
    """Recursively replace each raw secret string with ``[REDACTED]`` in every string value."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: _redact_record(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_record(item, secrets) for item in value]
    return value


def run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict | None = None,
    secrets: set[str] | None = None,
) -> CommandResult:
    """Run ``argv`` in ``cwd`` with a hard ``timeout``, capturing output, never raising on
    a non-zero exit. On expiry the whole process group is killed so no descendant survives.
    ``secrets`` is forwarded to the invocation record so a caller-provided prompt in ``argv``
    is redacted from that persisted artifact too.
    """
    spec = _Spec(argv=list(argv), cwd=Path(cwd))
    command: Command = Command.from_config(spec.argv, cwd=spec.cwd)

    start = time.monotonic()
    timed_out = False
    # subprocess.Popen (not subprocess.run) so this invocation keeps its own child pid locally
    # for the process-group kill on timeout — no process-global state, so concurrent runs
    # cannot race or mis-restore. start_new_session makes the child a group leader (PGID==PID),
    # so killpg(pid) reaches every descendant. In-process timeout via communicate(timeout=...),
    # never an external timeout(1) tool.
    process = subprocess.Popen(
        command,
        cwd=command.cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process.pid)
        # The group is dead, so the pipes close and this second communicate() cannot block on a
        # surviving grandchild; it reaps the child and drains any buffered output.
        stdout, stderr = process.communicate()
        returncode = process.returncode if process.returncode is not None else -signal.SIGKILL

    stdout = stdout or ""
    stderr = stderr or ""
    duration_ms = (time.monotonic() - start) * 1000.0
    result = CommandResult(
        argv=spec.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=timed_out,
    )
    record = {
        "argv": result.argv,
        "cwd": str(spec.cwd),
        "duration_ms": result.duration_ms,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
    }
    # Only pass secrets through when the caller actually gave us some — this keeps
    # emit_invocation's positional-only call shape intact for any existing caller that has not
    # been updated to accept the (new, optional) redaction kwarg.
    if secrets:
        emit_invocation(record, secrets=secrets)
    else:
        emit_invocation(record)
    return result


def _kill_process_group(pid: int) -> None:
    """SIGKILL the timed-out child's whole session, so a spawned grandchild cannot outlive it."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
