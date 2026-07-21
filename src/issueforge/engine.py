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


def _advance_unlocked(s: store.RunStore, run_id: str) -> str | None:
    """Caller HOLDS the lock. Release ``run_id``'s slot and promote the FIFO head, atomically.

    Only acts when ``run_id`` actually owns the slot (``queue.active == run_id``); otherwise the
    queue is left untouched and ``None`` is returned (no spurious advance). When a waiter exists it
    is popped to the active slot and its manifest is set ``running`` under this same lock; the
    caller then dispatches it OUTSIDE the lock via :func:`_dispatch`. Returns the promoted run id.
    """
    queue = s.read_queue()
    if queue.get("active") != run_id:
        return None
    queue["active"] = None
    next_id: str | None = None
    if queue["queue"]:
        next_id = queue["queue"].pop(0)
        queue["active"] = next_id
    s.write_queue_unlocked(queue)
    if next_id is not None:
        record = s._read_unlocked(next_id)
        s.write_record_unlocked(next_id, {**record, "status": RUNNING})
    return next_id


def _drain_stranded_waiters(s: store.RunStore) -> None:
    """Dispatch any waiter left with an empty active slot after a crash-recovery, in FIFO order.

    Normal operation never leaves ``active is None`` while the FIFO is non-empty (a released slot
    immediately promotes the head). Only :meth:`store.RunStore.reconcile`, having dropped an orphan
    or stuck active, can strand a valid waiter behind the freed slot. This promotes the head waiter
    under the lock and dispatches it OUTSIDE the lock; its own release then advances the rest, so the
    pre-crash queue drains head-first BEFORE the caller admits a new run (recovery preserves order).
    """
    while True:
        with s.locked():
            queue = s.read_queue()
            if queue.get("active") is not None or not queue["queue"]:
                return
            next_id = queue["queue"].pop(0)
            queue["active"] = next_id
            s.write_queue_unlocked(queue)
            record = s._read_unlocked(next_id)
            s.write_record_unlocked(next_id, {**record, "status": RUNNING})
        _dispatch(s, next_id)


def _dispatch(s: store.RunStore, next_id: str) -> None:
    """OUTSIDE the lock: emit the promoted run's ``running`` event, run the default stage, finalize.

    Draining is iterative-by-recursion: the dispatched head finalizes, and its own release advances
    the next waiter, so a run of waiters drains head-first in FIFO order.
    """
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

    A raising stage must not brick the engine: persist captured output, then release the slot AND
    advance the FIFO head atomically under one lock (else ``queue.active`` stays set forever,
    blocking ALL future runs), then re-raise. The manifest status is NOT flipped to ``failed`` on an
    untyped raise (crash-status recovery is deferred to #48); only the slot fairness is restored.
    """
    out, err = StringIO(), StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = stage(record)
    except BaseException:
        _persist_captured(s, run_id, out, err, secrets)
        with s.locked():
            next_id = _advance_unlocked(s, run_id)
        if next_id is not None:
            _dispatch(s, next_id)
        raise
    _persist_captured(s, run_id, out, err, secrets)
    return result


def _finalize(s: store.RunStore, run_id: str, result: Any, secrets: set[str] | None = None) -> None:
    """Land the run after its stage returns, HONORING any non-running status the stage set.

    Manifest transition + slot release/advance happen in ONE locked transaction (state re-read under
    the lock; if it changed, the guard fails rather than forcing the write). A typed
    ``StageResult(status=FAILED, failure=<StageFailure>)`` lands ``failed`` (recording the failure
    type) and advances. A MALFORMED result (FAILED without a ``StageFailure``, or a non-FAILED
    ``StageResult`` — outside the S5 contract) fails SAFELY: it releases + advances rather than
    leaving the worker occupied, without a manifest transition. A still-``running`` run completes and
    advances. A stage that paused its run keeps the slot (no advance); a parked/cancelled stage
    already released and advanced.
    """
    event: str | None = None
    next_id: str | None = None
    with s.locked():
        record = s._read_unlocked(run_id)
        status = record["status"]
        if isinstance(result, StageResult):
            if result.status == State.FAILED and isinstance(result.failure, StageFailure):
                transition(State(status), State.FAILED)
                s.write_record_unlocked(
                    run_id,
                    {
                        **record,
                        "status": State.FAILED.value,
                        "failure": {"type": result.failure.type},
                    },
                )
                event = State.FAILED.value
            # else: malformed StageResult -> fail safe (release + advance), no manifest transition.
            next_id = _advance_unlocked(s, run_id)
        elif status == RUNNING:
            transition(State.RUNNING, State.COMPLETED)
            s.write_record_unlocked(run_id, {**record, "status": COMPLETED})
            event = COMPLETED
            next_id = _advance_unlocked(s, run_id)
        # else: paused (keep slot, no advance) or parked (already advanced by park): do nothing.
    if event is not None:
        s.append_event(run_id, {"transition": event})
    if next_id is not None:
        _dispatch(s, next_id)


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

    # Startup reconcile (#48): before deciding the active slot, recover the queue from a torn
    # admission/completion — drop an orphan active or clear a slot stuck on a terminal run — so a
    # crash cannot wedge admission behind a phantom. This runs ONLY here, at mutating admission
    # (never in read-only verbs). A crash-recovery can then leave the slot empty with a valid waiter
    # stranded behind the dropped orphan: drain those pre-existing waiters (FIFO) before admitting
    # the new run, so a freshly admitted run never jumps a waiter that was already pending.
    s.reconcile()
    _drain_stranded_waiters(s)

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
    """``running -> paused``; keep the single worker slot (blocks the worker until resumed).

    State is re-read and guarded UNDER the lock, and the manifest transition is written in that same
    transaction; if the status changed after the caller's view, the guard fails rather than forcing
    the write. Pause keeps the slot, so the queue is untouched.
    """
    s = store.RunStore()
    with s.locked():
        record = s._read_unlocked(run_id)
        transition(State(record["status"]), State.PAUSED)
        s.write_record_unlocked(run_id, {**record, "status": State.PAUSED.value})
    s.append_event(run_id, {"transition": State.PAUSED.value})
    return s.read(run_id)


def park(run_id: str) -> dict:
    """``running|paused -> parked``; preserve every other field, release the slot, advance the FIFO.

    The guarded manifest transition and the slot release/advance are ONE locked transaction (state
    re-read under the lock); the promoted waiter is dispatched OUTSIDE the lock.
    """
    s = store.RunStore()
    with s.locked():
        record = s._read_unlocked(run_id)
        transition(State(record["status"]), State.PARKED)
        s.write_record_unlocked(run_id, {**record, "status": State.PARKED.value})
        next_id = _advance_unlocked(s, run_id)
    s.append_event(run_id, {"transition": State.PARKED.value})
    if next_id is not None:
        _dispatch(s, next_id)
    return s.read(run_id)


def cancel(run_id: str) -> dict:
    """``queued -> cancelled`` (never held the slot) or ``paused -> cancelled`` (release + advance).

    Refuses ``running``/``parked``/terminal via the transition guard; on refusal the record, queue,
    and event stream are unchanged (the guard raises before any write). All state validation and
    mutation happen in ONE locked transaction, re-reading status + queue under the lock, so a
    concurrent auto-advance that promotes a queued run to ``running`` between check and cancel cannot
    strand the slot.
    """
    s = store.RunStore()
    record = s.read(run_id)
    transition(State(record["status"]), State.CANCELLED)  # early refusal: running/parked/terminal

    next_id: str | None = None
    with s.locked():
        record = s._read_unlocked(run_id)
        current = State(record["status"])
        queue = s.read_queue()
        if current == State.QUEUED:
            # Re-validate BOTH the queued status and FIFO membership under the lock: if it was
            # promoted to running/active meanwhile, refuse rather than cancel-and-strand the slot.
            if run_id not in queue["queue"]:
                raise IllegalTransition(f"{run_id!r} is not a cancellable queued run")
            transition(current, State.CANCELLED)
            queue["queue"].remove(run_id)
            s.write_queue_unlocked(queue)
            s.write_record_unlocked(run_id, {**record, "status": State.CANCELLED.value})
        elif current == State.PAUSED:
            transition(current, State.CANCELLED)
            s.write_record_unlocked(run_id, {**record, "status": State.CANCELLED.value})
            next_id = _advance_unlocked(s, run_id)
        else:
            # Status changed out from under the caller (e.g. promoted then started): refuse.
            transition(current, State.CANCELLED)  # raises IllegalTransition
    s.append_event(run_id, {"transition": State.CANCELLED.value})
    if next_id is not None:
        _dispatch(s, next_id)
    return s.read(run_id)


def reorder(run_id: str, index: int) -> list[str]:
    """Move a QUEUED run to 0-based ``index`` in the FIFO; return the new order.

    Refuses a non-queued run (``IllegalTransition``); raises ``ValueError`` on a negative,
    out-of-range, or non-int index (``bool`` and ``float`` are not ints). Queue unchanged on refusal.
    Status and FIFO membership are re-read and checked in ONE locked transaction before the write.
    """
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError(f"index must be an int, got {index!r}")
    s = store.RunStore()
    with s.locked():
        record = s._read_unlocked(run_id)
        if State(record["status"]) != State.QUEUED:
            raise IllegalTransition(f"cannot reorder {run_id!r} in state {record['status']!r}")
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
        return list(order)


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

    # Claim/verify the worker slot AND perform the guarded manifest transition in ONE locked
    # transaction, re-reading state under the lock. A crash-orphaned running run must own the slot
    # before its stage runs; a parked run may only resume onto a free slot.
    emit_running = False
    with s.locked():
        record = s._read_unlocked(run_id)
        current = State(record["status"])
        queue = s.read_queue()
        active = queue.get("active")
        if current == State.RUNNING:
            # Crash-orphan: accept the slot it already owns, claim a free one, else refuse.
            if active == run_id:
                pass
            elif active is None:
                queue["active"] = run_id
                s.write_queue_unlocked(queue)
            else:
                raise WorkerBusyError(f"cannot continue {run_id!r}: worker slot held by {active!r}")
            # Already "running": no manifest transition, no duplicate running event.
        elif current == State.PAUSED:
            # A paused run holds its own slot; keep it, just re-mark running.
            transition(State.PAUSED, State.RUNNING)
            s.write_record_unlocked(run_id, {**record, "status": RUNNING})
            emit_running = True
        elif current == State.PARKED:
            transition(State.PARKED, State.RUNNING)
            if active is not None:
                raise WorkerBusyError(f"cannot resume {run_id!r}: worker slot held by {active!r}")
            queue["active"] = run_id
            s.write_queue_unlocked(queue)
            s.write_record_unlocked(run_id, {**record, "status": RUNNING})
            emit_running = True
        else:
            raise IllegalTransition(f"cannot continue {run_id!r} in state {record['status']!r}")
    if emit_running:
        s.append_event(run_id, {"transition": RUNNING})

    record = s.read(run_id)
    result = _execute_stage(s, run_id, stage, record)
    _finalize(s, run_id, result)
    return s.read(run_id)
