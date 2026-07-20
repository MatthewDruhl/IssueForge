"""The engine: run one issue end-to-end and control its queue (issues #8 S4, #9 S5).

``run(spec)`` resolves ``alias#n`` via the S3 registry, refuses a non-open issue (via the injectable
``issue_open`` seam) before minting anything, decides admission INSIDE the store lock against the
single active slot (else enqueues), runs the stub ``stage`` OUTSIDE the lock while capturing its
stdout/stderr through the redacting artifact writer, records transitions as events, and finalizes.

S5 adds the human-initiated queue-control verbs (``pause``/``park``/``cancel``/``reorder`` and the
one resume verb ``continue_run``). Every state change is guarded by the table-driven state machine in
``issueforge.state`` (no state-machine library) and persisted as one transition event. PAUSE keeps
the single worker slot; PARK, CANCEL-of-paused, a FAILED stage, and natural COMPLETION all release
the slot and auto-advance the FIFO head, dispatched through ``_default_stage`` so a monkeypatched
default observes dispatch order. Finalization HONORS a non-running status a stage set (a stage that
paused/parked/failed its run is not overwritten to completed).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from typing import Any

from issueforge import github, store
from issueforge.registry import Registry
from issueforge.state import IllegalTransition, State, transition

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"

# github_facts is authoritative for these keys; the run record is authoritative for the gate
# artifacts {"approvals","verdicts","attempts"} (never overwritten by github_facts).
_GITHUB_KEYS = ("pr", "branch", "merged")


class DivergenceError(Exception):
    """A GitHub-authoritative PR fact disagrees with the persisted record; surfaced, never healed."""


class WorkerBusyError(Exception):
    """A parked run cannot resume while another run holds the single worker slot."""


class StageFailure:
    """Base class for a stage's own typed failure; ``.type`` is the concrete subclass name."""

    def __init__(self, message: str = "") -> None:
        self.message = message

    @property
    def type(self) -> str:
        return type(self).__name__


class StubStageFailure(StageFailure):
    """The stub stage's failure type (``.type == "StubStageFailure"``)."""


@dataclass
class StageResult:
    """A typed stage outcome: a target :class:`State` plus an optional typed failure."""

    status: State
    failure: StageFailure | None = None


def _default_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:12]


def _default_stage(record: dict) -> None:
    """The stub stage for a fresh dispatch: mark progress with an observable event, then complete."""
    store.RunStore().append_event(record["run_id"], {"transition": "stage"})


def _resume_stage(record: dict) -> None:
    """The default resume stage: a no-op; finalization completes the resumed run."""
    return None


def _persist_captured(
    s: store.RunStore, run_id: str, out: StringIO, err: StringIO, secrets: set[str] | None
) -> None:
    """Persist whatever stdout/stderr the stage produced, through the redacting artifact writer."""
    if out.getvalue():
        s.write_artifact(run_id, "stdout.log", out.getvalue(), secrets=secrets)
    if err.getvalue():
        s.write_artifact(run_id, "stderr.log", err.getvalue(), secrets=secrets)


def _release_active(s: store.RunStore, run_id: str) -> None:
    """Clear the single active slot iff it still holds ``run_id`` (under the store lock)."""
    with s.locked():
        queue = s.read_queue()
        if queue.get("active") == run_id:
            queue["active"] = None
            s.write_queue_unlocked(queue)


def _release_and_advance(s: store.RunStore, run_id: str) -> None:
    """Release ``run_id``'s slot and dispatch the FIFO head (if any) through ``_default_stage``.

    Draining is iterative-by-recursion: the dispatched head runs its stage and finalizes, and its
    own completion releases the slot again, so a run of waiters drains head-first in FIFO order.
    """
    with s.locked():
        queue = s.read_queue()
        if queue.get("active") == run_id:
            queue["active"] = None
        next_id = None
        if queue["queue"]:
            next_id = queue["queue"].pop(0)
            queue["active"] = next_id
        s.write_queue_unlocked(queue)
        if next_id is not None:
            record = json.loads(store.manifest_path(next_id).read_text(encoding="utf-8"))
            s.write_record_unlocked(next_id, {**record, "status": RUNNING})
    if next_id is not None:
        s.append_event(next_id, {"transition": RUNNING})
        record = s.read(next_id)
        result = _execute_stage(s, next_id, _default_stage, record)
        _finalize(s, next_id, result)


def _execute_stage(
    s: store.RunStore,
    run_id: str,
    stage: Callable[[dict], Any],
    record: dict,
    secrets: set[str] | None = None,
) -> Any:
    """Run ``stage`` OUTSIDE the lock, capturing stdout/stderr through the redacting writer.

    A raising stage must not brick the engine: persist captured output and release the active slot
    (else ``queue.active`` stays set forever, blocking ALL future runs), then re-raise.
    """
    out, err = StringIO(), StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = stage(record)
    except BaseException:
        _persist_captured(s, run_id, out, err, secrets)
        _release_active(s, run_id)
        raise
    _persist_captured(s, run_id, out, err, secrets)
    return result


def _finalize(s: store.RunStore, run_id: str, result: Any, secrets: set[str] | None = None) -> None:
    """Land the run after its stage returns, HONORING any non-running status the stage set.

    A typed ``StageResult(status=FAILED, ...)`` lands ``failed`` (recording the failure type) and
    advances. A still-``running`` run completes and advances. A stage that paused its run keeps the
    slot (no advance); a stage that parked/cancelled its run already released and advanced.
    """
    record = s.read(run_id)
    status = record["status"]
    if isinstance(result, StageResult) and result.status == State.FAILED:
        transition(State(status), State.FAILED)
        failure_type = result.failure.type if result.failure is not None else None
        s.apply(run_id, lambda r: {"status": State.FAILED.value, "failure": {"type": failure_type}})
        s.append_event(run_id, {"transition": State.FAILED.value})
        _release_and_advance(s, run_id)
        return
    if status == RUNNING:
        transition(State.RUNNING, State.COMPLETED)
        s.apply(run_id, lambda r: {"status": COMPLETED})
        s.append_event(run_id, {"transition": COMPLETED})
        _release_and_advance(s, run_id)


def run(
    spec: str,
    *,
    issue_open: Callable[[str, int], bool] | None = None,
    stage: Callable[[dict], Any] | None = None,
    new_run_id: Callable[[], str] | None = None,
    secrets: set[str] | None = None,
) -> dict:
    """Run ``alias#n`` to completion (or land it queued behind the active run)."""
    if issue_open is None:
        issue_open = github.issue_is_open
    if stage is None:
        stage = _default_stage
    if new_run_id is None:
        new_run_id = _default_run_id

    alias, _, raw_number = spec.partition("#")
    number = int(raw_number)

    entry = Registry.load().get(alias)  # RegistryError before any run when unregistered
    if not issue_open(entry.slug, number):
        raise ValueError(f"issue {entry.slug}#{number} is not open; refusing to run")

    run_id = new_run_id()
    s = store.RunStore(secrets=secrets)

    # Admission is decided INSIDE the store lock: a lock-free check-then-start would admit two.
    with s.locked():
        queue = s.read_queue()
        if queue.get("active") is None:
            admitted = True
            queue["active"] = run_id
            status = RUNNING
        else:
            admitted = False
            queue["queue"].append(run_id)
            status = QUEUED
        # Fail-safe order: write the QUEUE (active=run_id) BEFORE the manifest(status=running). A
        # crash between the two then leaves the slot OCCUPIED (a later run enqueues) rather than
        # empty (which would wrongly admit a second run = double-active). Full multi-file
        # transactionality / startup reconcile is deferred to #48.
        s.write_queue_unlocked(queue)
        s.write_record_unlocked(run_id, {"run_id": run_id, "status": status}, create=True)

    s.append_event(run_id, {"transition": QUEUED})
    if not admitted:
        return s.read(run_id)

    s.append_event(run_id, {"transition": RUNNING})
    record = s.read(run_id)
    result = _execute_stage(s, run_id, stage, record, secrets)
    _finalize(s, run_id, result, secrets)
    return s.read(run_id)


# ---------------------------------------------------------------------------
# S5 queue-control verbs
# ---------------------------------------------------------------------------


def pause(run_id: str) -> dict:
    """``running -> paused``; keep the single worker slot (blocks the worker until resumed)."""
    s = store.RunStore()
    record = s.read(run_id)
    transition(State(record["status"]), State.PAUSED)
    s.apply(run_id, lambda r: {"status": State.PAUSED.value})
    s.append_event(run_id, {"transition": State.PAUSED.value})
    return s.read(run_id)


def park(run_id: str) -> dict:
    """``running|paused -> parked``; preserve every other field, release the slot, advance the FIFO."""
    s = store.RunStore()
    record = s.read(run_id)
    transition(State(record["status"]), State.PARKED)
    s.apply(run_id, lambda r: {"status": State.PARKED.value})
    s.append_event(run_id, {"transition": State.PARKED.value})
    _release_and_advance(s, run_id)
    return s.read(run_id)


def cancel(run_id: str) -> dict:
    """``queued -> cancelled`` (never held the slot) or ``paused -> cancelled`` (release + advance).

    Refuses ``running``/``parked``/terminal via the transition guard; on refusal the record, queue,
    and event stream are unchanged (the guard raises before any write).
    """
    s = store.RunStore()
    record = s.read(run_id)
    current = State(record["status"])
    transition(current, State.CANCELLED)  # IllegalTransition for running/parked/terminal

    if current == State.QUEUED:
        # A queued run never held the slot: drop it from the FIFO, leave the active slot untouched.
        with s.locked():
            queue = s.read_queue()
            if run_id in queue["queue"]:
                queue["queue"].remove(run_id)
            s.write_queue_unlocked(queue)
        s.apply(run_id, lambda r: {"status": State.CANCELLED.value})
        s.append_event(run_id, {"transition": State.CANCELLED.value})
    else:  # PAUSED: it holds the slot; cancelling releases it and advances the FIFO.
        s.apply(run_id, lambda r: {"status": State.CANCELLED.value})
        s.append_event(run_id, {"transition": State.CANCELLED.value})
        _release_and_advance(s, run_id)
    return s.read(run_id)


def reorder(run_id: str, index: int) -> list[str]:
    """Move a QUEUED run to 0-based ``index`` in the FIFO; return the new order.

    Refuses a non-queued run (``IllegalTransition``); raises ``ValueError`` on a negative,
    out-of-range, or non-int index (``bool`` and ``float`` are not ints). Queue unchanged on refusal.
    """
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError(f"index must be an int, got {index!r}")
    s = store.RunStore()
    record = s.read(run_id)
    if State(record["status"]) != State.QUEUED:
        raise IllegalTransition(f"cannot reorder {run_id!r} in state {record['status']!r}")
    with s.locked():
        queue = s.read_queue()
        order = queue["queue"]
        if run_id not in order:
            raise IllegalTransition(f"{run_id!r} is not in the FIFO queue")
        if index < 0 or index >= len(order):
            raise ValueError(f"index {index} out of range [0, {len(order)})")
        order.remove(run_id)
        order.insert(index, run_id)
        queue["queue"] = order
        s.write_queue_unlocked(queue)
    return s.read_queue()["queue"]


def _reconcile(record: dict, github_facts: Callable[[str], dict]) -> None:
    """Reconcile the record against GitHub-authoritative PR facts; raise on divergence, never heal.

    github is authoritative for ``{pr, branch, merged}``: a key present in BOTH the record and the
    facts with unequal values raises :class:`DivergenceError` naming it. The record stays
    authoritative for the gate artifacts ``{approvals, verdicts, attempts}`` (github values ignored).
    Nothing is written; github_facts NEVER writes a gate artifact.
    """
    facts = github_facts(record["run_id"])
    for key in _GITHUB_KEYS:
        if key in facts and key in record and record[key] != facts[key]:
            raise DivergenceError(
                f"divergence on {key!r}: record={record[key]!r} github={facts[key]!r}"
            )


def continue_run(
    run_id: str,
    *,
    github_facts: Callable[[str], dict] | None = None,
    stage: Callable[[dict], Any] | None = None,
) -> dict:
    """The ONE resume verb: reconcile-then-resume a paused, parked, or crash-orphaned running run."""
    if github_facts is None:
        github_facts = github.pr_facts
    if stage is None:
        stage = _resume_stage

    s = store.RunStore()
    record = s.read(run_id)
    current = State(record["status"])

    # Reconcile BEFORE any resume: divergence halts here, leaving the record byte-untouched and
    # never running the stage.
    _reconcile(record, github_facts)

    if current == State.RUNNING:
        # Crash-orphaned: it still holds its slot and is already "running"; do not re-emit running.
        pass
    elif current == State.PAUSED:
        transition(State.PAUSED, State.RUNNING)
        s.apply(run_id, lambda r: {"status": RUNNING})
        s.append_event(run_id, {"transition": RUNNING})
    elif current == State.PARKED:
        transition(State.PARKED, State.RUNNING)
        with s.locked():
            queue = s.read_queue()
            if queue.get("active") is not None:
                raise WorkerBusyError(
                    f"cannot resume {run_id!r}: worker slot held by {queue['active']!r}"
                )
            queue["active"] = run_id
            s.write_queue_unlocked(queue)
            s.write_record_unlocked(run_id, {**record, "status": RUNNING})
        s.append_event(run_id, {"transition": RUNNING})
    else:
        raise IllegalTransition(f"cannot continue {run_id!r} in state {record['status']!r}")

    record = s.read(run_id)
    result = _execute_stage(s, run_id, stage, record)
    _finalize(s, run_id, result)
    return s.read(run_id)
