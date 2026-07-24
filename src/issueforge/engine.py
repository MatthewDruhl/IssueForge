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


def _advance_unlocked(s: store.RunStore, run_id: str) -> str | None:
    """Caller HOLDS the lock. Release ``run_id``'s slot and promote the FIFO head, atomically.

    Only acts when ``run_id`` actually owns the slot (``queue.active == run_id``); otherwise the
    queue is left untouched and ``None`` is returned (no spurious advance). When a waiter exists it
    is popped to the active slot and its manifest is set ``running`` under this same lock; the
    caller's :func:`_drain` loop then dispatches it OUTSIDE the lock. Returns the promoted run id.
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
    under the lock and drains it OUTSIDE the lock; its own release then advances the rest, so the
    pre-crash queue drains head-first BEFORE the caller admits a new run (recovery preserves order).

    ``reconcile`` has already dropped any waiter without a valid ``queued`` manifest, but the head's
    manifest is still READ before ``queue.json`` is mutated: an unreadable head aborts the promotion
    without persisting a new orphan into the active slot.
    """
    while True:
        with s.locked():
            queue = s.read_queue()
            if queue.get("active") is not None or not queue["queue"]:
                return
            next_id = queue["queue"][0]
            record = s._read_unlocked(next_id)  # read BEFORE persisting active
            queue["queue"].pop(0)
            queue["active"] = next_id
            s.write_queue_unlocked(queue)
            s.write_record_unlocked(next_id, {**record, "status": RUNNING})
        _drain(s, next_id)


def _drain(s: store.RunStore, next_id: str | None) -> None:
    """ITERATIVELY dispatch ``next_id`` and each waiter its release promotes, head-first FIFO.

    Each promoted run emits its ``running`` event, runs the default stage, and finalizes; finalize
    RETURNS the next promoted run rather than dispatching it, so this drains a queue of any length in
    one flat loop — no per-waiter recursion frame, so a long persistent FIFO cannot blow the stack.
    """
    while next_id is not None:
        s.append_event(next_id, {"transition": RUNNING})
        record = s.read(next_id)
        result = _execute_stage(s, next_id, _default_stage, record)
        next_id = _finalize(s, next_id, result)


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
        _drain(s, next_id)
        raise
    _persist_captured(s, run_id, out, err, secrets)
    _persist_candidate_worktree(s, run_id, result)
    return result


def _persist_candidate_worktree(s: store.RunStore, run_id: str, result: Any) -> None:
    """Persist the isolated worktree a baseline run-stage established onto the run record — the REAL
    production seam the S13 integrity gate (#19) reads. When a stage returns a
    ``verify.BaselineOutcome`` carrying a worktree, that worktree IS the run's candidate; recording
    it here (from inside the run-stage lifecycle, never threaded through a caller-supplied context)
    is what makes the gate active in production. Any other stage result is left untouched."""
    from issueforge import verify

    if isinstance(result, verify.BaselineOutcome) and result.worktree is not None:
        s.apply(run_id, lambda _r: {"candidate_worktree": str(result.worktree)})


def _finalize(
    s: store.RunStore, run_id: str, result: Any, secrets: set[str] | None = None
) -> str | None:
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
    return next_id  # the caller's :func:`_drain` loop dispatches the promoted waiter (no recursion)


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
    next_id = _finalize(s, run_id, result, secrets)
    _drain(s, next_id)
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
    _drain(s, next_id)
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
    _drain(s, next_id)
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


# ---------------------------------------------------------------------------
# S9 authoring gate + write-scope readiness enforcement (issue #13)
# ---------------------------------------------------------------------------


def enter_authoring(run_id: str, *, revision_applied: bool) -> dict:
    """The real authoring-entry path: a documented guard over the run's approved ``shape``.

    Raises :class:`state.IllegalTransition` unless the run is BUILDABLE (a ``running`` record whose
    ``shape`` classifies ``buildable``) AND ``revision_applied`` is True, recording NO progression
    event on the illegal path. On the legal path it records exactly one ``authoring`` event. The
    guard is explicit (the shape carries the classification the ``state`` table does not), so the
    transition table is left untouched.
    """
    s = store.RunStore()
    record = s.read(run_id)
    shape = record.get("shape")
    buildable = (
        record.get("status") == State.RUNNING.value
        and isinstance(shape, dict)
        and shape.get("classification") == "buildable"
    )
    if not (buildable and revision_applied is True):
        raise IllegalTransition(
            f"cannot enter authoring for {run_id!r}: buildable+revision_applied required"
        )
    s.append_event(run_id, {"transition": "authoring"})
    return s.read(run_id)


def enforce_write_scope(run_id: str, diff_text: str) -> list[str]:
    """Return the paths changed in ``diff_text`` that fall OUTSIDE the stored approved write scope.

    The enforced scope is exactly the approved ``write_scope`` persisted at shaping — READ from the
    record, NEVER recomputed from the diff. ``[]`` means every changed path is within scope.
    """
    record = store.RunStore().read(run_id)
    shape = record.get("shape") or {}
    allowed: set[str] = set()
    for entry in shape.get("write_scope") or []:
        if entry.get("op") == "rename":
            for key in ("source_path", "destination_path"):
                if entry.get(key):
                    allowed.add(entry[key])
        elif entry.get("path"):
            allowed.add(entry["path"])
    return [path for path in _diff_paths(diff_text) if path not in allowed]


def _diff_paths(diff_text: str) -> list[str]:
    """The distinct changed file paths named by a unified diff, in first-seen order."""
    paths: list[str] = []
    seen: set[str] = set()
    for line in diff_text.splitlines():
        for prefix in ("+++ b/", "--- a/"):
            if line.startswith(prefix):
                path = line[len(prefix) :].strip()
                if path and path != "/dev/null" and path not in seen:
                    seen.add(path)
                    paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# S20 (#14): apply the in-place revision behind a human gate (the FIRST GitHub mutation)
# ---------------------------------------------------------------------------


def _op_fingerprint(op: dict) -> str:
    """A stable content fingerprint for a mutation op, binding a recorded op-ID to its exact content.

    Option A idempotency: a recorded op-ID must resume ONLY the identical op; a changed target/op/body
    under the same id is refused, never silently skipped. Tuple targets serialize deterministically.
    """
    return json.dumps(op, sort_keys=True)


def _gate_provisioner(frozen_pins: dict | None = None) -> Callable[..., Any]:
    """The provisioner the integrity gate resolves its collection under: the TARGET AUTHORITATIVE
    environment (a real, separate venv under IssueForge's owned ``state_root()``), NOT the host
    interpreter — a host that happens to match a frozen pin would mask a real authoritative drift
    (#19, S13 finding #8). Delegates to ``PytestAdapter._provision_default`` with the frozen external
    pins installed so the contract's imports resolve in the authoritative env and the external-pin
    re-resolution runs under a state-root interpreter, never ``sys.executable``."""
    from issueforge.adapters.pytest_adapter import _provision_default

    pins = dict(frozen_pins or {})

    def _provision(worktree: object, frozen_deps: object = None) -> object:
        merged = dict(pins)
        if isinstance(frozen_deps, dict):
            merged.update(frozen_deps)
        handle = _provision_default(worktree, merged or None)
        # The gate must provision under the SAME plugin-autoload policy as freeze (#105 finding #1). The
        # earlier wrapper DISABLED autoload here on the (mistaken) premise that freeze also disables it;
        # freeze in fact runs with autoload ON via ``_provision_default`` and intentionally pins entry-
        # point plugins as external deps (see test_freeze_pins_externally_autoloaded_plugin_...). With
        # autoload OFF here, a plugin freeze discovered+pinned read as a MISSING pin at verify, spuriously
        # failing a clean candidate. Provisioning identically to freeze keeps the two symmetric.
        return handle

    return _provision


def _integrity_gate(run_id: str, record: dict) -> None:
    """The mandatory S13 integrity check (#19). Reads the run's OWN persisted state: the frozen
    manifest artifact and the recorded ``candidate_worktree``. When a frozen manifest exists, it
    verifies the candidate through :func:`contract.verify_contract_integrity` (adapter resolved via
    the ``registry.resolve(framework="pytest", reporter="pytest")`` seam) and raises
    :class:`state.IllegalTransition` naming every violated predicate on a violation. A run with no
    frozen manifest (pre-S13) is untouched; a run WITH a frozen manifest but no recorded
    ``candidate_worktree`` is FAIL-CLOSED — missing gate context refuses, never falls through to a
    mutation (#19, S13 finding #7)."""
    manifest_path = store.run_dir(run_id) / "contract-manifest.json"
    if not manifest_path.exists():
        return
    candidate = record.get("candidate_worktree")
    if not candidate:
        raise IllegalTransition(
            f"apply_revision refused: run {run_id!r} has a frozen contract manifest but no persisted "
            "candidate_worktree; the integrity gate cannot verify and fails closed"
        )
    from issueforge import contract
    from issueforge.adapters.base import registry

    adapter = registry.resolve(framework="pytest", reporter="pytest")
    base_sha = (record.get("red_proof") or {}).get("base_sha")
    # Derive the provisioner's frozen external pins from the ACTIVE manifest — the latest APPROVED
    # amendment when one exists — via the SAME loader ``verify_contract_integrity`` uses
    # (``contract._load_frozen_manifest``), NOT the original ``contract-manifest.json`` on disk (#105
    # finding #3). Provisioning M0's pins while verify checks against an amended M1 false-blocks a
    # legitimately amended candidate; both sides must read the same active manifest.
    active_manifest = contract._load_frozen_manifest(run_id)
    frozen_pins = {
        str(pin[0]): str(pin[1])
        for pin in (active_manifest.get("external_pins") or [])
        if len(pin) >= 2
    }
    report = contract.verify_contract_integrity(
        run_id,
        candidate_worktree=candidate,
        base_sha=base_sha,
        adapter=adapter,
        provisioner=_gate_provisioner(frozen_pins),
    )
    if not report.ok:
        predicates = sorted({v.predicate for v in report.violations})
        raise IllegalTransition(
            f"apply_revision refused: contract integrity violated ({', '.join(predicates)})"
        )


def apply_revision(
    run_id: str, plan: list, gateway: Any, *, approver: Callable[[Any], bool]
) -> dict:
    """Apply an approved in-place revision — the FIRST GitHub mutation — behind the human gate (US-3.1).

    The human ``approver`` is consulted BEFORE any write: on rejection the gateway is touched for ZERO
    ops and no ``revision`` event is recorded. On approval the plan is applied via :func:`github.apply`
    against a completed-op-ID ledger PERSISTED in the RunStore (``record["revision_ledger"]`` maps each
    completed op-ID to its content fingerprint), so a resumed apply never re-dispatches a completed op
    (Option A idempotency) and a changed op under a recorded id raises ``ValueError``. On success a
    ``revision`` event records the completed op-IDs. The completed ledger is persisted even on a
    mid-plan gateway failure, so the RuntimeError propagates with the landed op recorded.
    """
    s = store.RunStore()
    record = s.read(run_id)

    # Workflow eligibility: only a buildable, approved (running) run may have its revision applied — the
    # same rule propose_revision enforces. A paused/blocked/oversized/failed run is refused BEFORE any
    # write (the plan-level pre-approval gate alone does not enforce this workflow rule).
    shape = record.get("shape")
    if not (
        record.get("status") == State.RUNNING.value
        and isinstance(shape, dict)
        and shape.get("classification") == "buildable"
    ):
        raise ValueError(
            f"apply_revision requires a buildable, approved run; {run_id!r} is not eligible"
        )

    # S13 (#19) mandatory contract-integrity gate — runs on EVERY first-mutation path, deriving its
    # context ENTIRELY from PERSISTED RUN STATE (the frozen manifest + the run's recorded
    # candidate_worktree), never from a caller kwarg (apply_revision has none). It refuses BEFORE any
    # gateway mutation or human approval when the candidate violates a predicate, naming it; it runs
    # ONLY when a frozen manifest exists, so a pre-S13 run proceeds exactly as before.
    _integrity_gate(run_id, record)

    # Resume integrity (Option A): a recorded op-ID binds to the EXACT op that completed. A changed op
    # under a recorded id is refused; a matching one is treated as already done (seeded into the set).
    persisted: dict = record.get("revision_ledger") or {}
    completed: set = set()
    for op in plan:
        op_id = op["id"]
        if op_id in persisted:
            if persisted[op_id] != _op_fingerprint(op):
                raise ValueError(
                    f"op id {op_id!r} was recorded for different content; refusing to resume"
                )
            completed.add(op_id)

    # Human gate: record approval BEFORE any write; a rejection touches nothing.
    if not approver(plan):
        return s.read(run_id)
    s.append_event(run_id, {"transition": "approval", "revision": True, "approved": True})

    fingerprints = dict(persisted)
    try:
        github.apply(plan, gateway, ledger=completed)
    finally:
        # Persist every completed op-ID bound to its content, even after a mid-plan gateway failure.
        for op in plan:
            if op["id"] in completed:
                fingerprints[op["id"]] = _op_fingerprint(op)
        s.apply(run_id, lambda _r: {"revision_ledger": fingerprints})

    ordered = [op["id"] for op in plan if op["id"] in completed]
    s.append_event(run_id, {"transition": "revision", "completed": ordered})
    return s.read(run_id)


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
    next_id = _finalize(s, run_id, result)
    _drain(s, next_id)
    return s.read(run_id)
