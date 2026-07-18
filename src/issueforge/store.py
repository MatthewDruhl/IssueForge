"""The run store: one locked write path for every run's persisted state (issue #8, S4).

Layout under ``paths.state_root()``:
- ``runs/<run-id>/manifest.json`` — the run record (one JSON object).
- ``runs/<run-id>/events.jsonl`` — the append-only transition/event stream.
- ``queue.json`` — the single-slot admission queue ``{"active": <id|None>, "queue": [...]}``.
- ``store.lock`` — ONE store-level advisory lock file.

``RunStore.apply`` is the ONE mutation primitive: an ``fcntl.flock``-guarded
read -> transform -> validate -> atomic-write, with existence decided INSIDE the lock. Every
persisted byte routes through the sanctioned ``io.WriteSeam`` (never a store-local raw write).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from issueforge.io import WriteSeam
from issueforge.paths import state_root

ALLOWED_STATUS = {"queued", "running", "completed"}

REDACTED = "[REDACTED]"


def _root() -> Path:
    return Path(state_root()).resolve()


def run_dir(run_id: str) -> Path:
    """The directory holding one run's manifest and event stream."""
    return _root() / "runs" / run_id


def manifest_path(run_id: str) -> Path:
    """The run's manifest.json path."""
    return run_dir(run_id) / "manifest.json"


def events_path(run_id: str) -> Path:
    """The run's append-only events.jsonl path."""
    return run_dir(run_id) / "events.jsonl"


def queue_path() -> Path:
    """The single admission queue file, directly under the state root."""
    return _root() / "queue.json"


def lock_path() -> Path:
    """The ONE store-level advisory lock file, directly under the state root."""
    return _root() / "store.lock"


def require_int(name: str, value: Any) -> int:
    """Return ``value`` if it is a real int (not bool), else raise TypeError.

    A persisted int field must never be minted from ``bool``/``float``/``str``: ``int(True)`` is
    1 and ``int(1.9)`` is 1, either of which would forge a valid-looking record. Fail loud here.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {value!r}")
    return value


def validate_record(record: dict) -> dict:
    """The ONE record validator, called on BOTH write and read.

    Rejects an unknown ``status`` (allowed: queued/running/completed) and any non-int ``attempts``.
    """
    if not isinstance(record, dict):
        raise TypeError(f"run record must be a dict, got {record!r}")
    status = record.get("status")
    if status is not None and status not in ALLOWED_STATUS:
        raise ValueError(f"invalid status {status!r} (allowed: {sorted(ALLOWED_STATUS)})")
    if "attempts" in record:
        require_int("attempts", record["attempts"])
    return record


def write_artifact(run_id: str, name: str, text: str, *, secrets: set[str] | None = None) -> Path:
    """Module-level artifact writer: the ONLY sanctioned way to persist a run artifact.

    Redacts each secret to ``[REDACTED]`` then writes under the run dir through the seam.
    """
    return RunStore().write_artifact(run_id, name, text, secrets=secrets)


class RunStore:
    """The persisted set of runs, mutated only through the single locked write path."""

    def __init__(self, secrets: set[str] | None = None) -> None:
        self._secrets = set(secrets or ())
        self._seam = WriteSeam()

    # --- the store-level lock ------------------------------------------------
    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        fd = self._seam.open_lock(lock_path())
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def locked(self):
        """Acquire the store-level lock for a caller-composed read-modify-write (e.g. admission)."""
        return self._lock()

    # --- the ONE mutation primitive -----------------------------------------
    def apply(
        self,
        run_id: str,
        transform: Callable[[dict], dict[str, Any]],
        *,
        validate: Callable[[dict], Any] | None = None,
        create: bool = False,
    ) -> dict:
        """Locked read -> transform -> validate -> atomic-write; existence decided under the lock.

        ``transform`` receives the current on-disk record (``{}`` for a fresh ``create``) and MUST
        return a dict of fields to MERGE (it must not mutate in place). ``validate`` (default
        ``validate_record``) runs UNDER the lock on the merged record; if it raises, nothing is
        written. ``create=False`` on a missing run raises before any write (no phantom dir).
        """
        with self._lock():
            exists = manifest_path(run_id).exists()
            if not exists and not create:
                raise KeyError(f"run {run_id!r} does not exist")
            record = self._read_unlocked(run_id) if exists else {}
            fields = transform(record)
            if not isinstance(fields, dict):
                raise TypeError(f"transform must return a dict of fields, got {fields!r}")
            merged = {**record, **fields}
            validator = validate if validate is not None else validate_record
            validator(merged)
            self._seam.write_text_atomic(manifest_path(run_id), self._dump(merged))
            return merged

    def read(self, run_id: str) -> dict:
        """Read a run's manifest and RE-VALIDATE it through the same ``validate_record``."""
        if not manifest_path(run_id).exists():
            raise KeyError(f"run {run_id!r} does not exist")
        record = self._read_unlocked(run_id)
        validate_record(record)
        return record

    # --- unlocked internals (caller holds the lock when composing) ----------
    def write_record_unlocked(self, run_id: str, record: dict, *, create: bool = False) -> dict:
        """Validate and atomically persist a run record; caller MUST hold the store lock."""
        exists = manifest_path(run_id).exists()
        if not exists and not create:
            raise KeyError(f"run {run_id!r} does not exist")
        validate_record(record)
        self._seam.write_text_atomic(manifest_path(run_id), self._dump(record))
        return record

    def read_queue(self) -> dict:
        """The admission queue (default ``{"active": None, "queue": []}`` when absent)."""
        path = queue_path()
        if not path.exists():
            return {"active": None, "queue": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def write_queue_unlocked(self, queue: dict) -> None:
        """Atomically persist the admission queue; caller MUST hold the store lock."""
        self._seam.write_text_atomic(queue_path(), self._dump(queue))

    def _read_unlocked(self, run_id: str) -> dict:
        return json.loads(manifest_path(run_id).read_text(encoding="utf-8"))

    @staticmethod
    def _dump(payload: dict) -> str:
        return json.dumps(payload, indent=2) + "\n"

    # --- append-only event stream -------------------------------------------
    def append_event(self, run_id: str, event: dict) -> Path:
        """Append one JSON line to events.jsonl, redacting instance secrets first (append-only)."""
        line = self._redact(json.dumps(event)) + "\n"
        return self._seam.append_text(events_path(run_id), line)

    def replay_events(self, run_id: str) -> list[dict]:
        """Parse events.jsonl in order, discarding ONLY an unparseable/torn FINAL line.

        A valid final line without a trailing newline still replays; a malformed MIDDLE line raises.
        """
        path = events_path(run_id)
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        if text == "":
            return []
        lines = text.split("\n")
        last = len(lines) - 1
        events: list[dict] = []
        for index, line in enumerate(lines):
            if line == "":
                if index == last:
                    continue  # trailing newline
                raise ValueError(f"empty event line {index} in {path}")
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if index == last:
                    continue  # torn/unterminated final line
                raise
        return events

    # --- the ONLY artifact writer -------------------------------------------
    def write_artifact(
        self, run_id: str, name: str, text: str, *, secrets: set[str] | None = None
    ) -> Path:
        """Redact each secret (instance + call) to ``[REDACTED]`` then persist under the run dir."""
        combined = self._secrets | set(secrets or ())
        scrubbed = self._redact(text, combined)
        return self._seam.write_text_atomic(run_dir(run_id) / name, scrubbed)

    def _redact(self, text: str, secrets: set[str] | None = None) -> str:
        for secret in self._secrets if secrets is None else secrets:
            if secret:
                text = text.replace(secret, REDACTED)
        return text
