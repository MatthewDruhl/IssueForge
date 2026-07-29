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
import subprocess
import tomllib
import uuid
from collections.abc import Callable
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from functools import partial
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from issueforge import contract, github, providers, store
from issueforge import verify as _verify
from issueforge.adapters.base import BaselineStatus
from issueforge.adapters.base import registry as _adapter_registry
from issueforge.paths import state_root
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


class ScopeRejected(StageFailure):
    """The composed stage's failure type when the pre-authoring scope gate REJECTS (#142).

    A run rejected at the pre-authoring scope gate produced no candidate and is not resumable, so it
    is terminal (``failed``) rather than a resumable ``paused`` — freeing the worker slot and
    auto-advancing the queue. ``.type == "ScopeRejected"``.
    """


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

    Each promoted run emits its ``running`` event, runs the COMPOSED default stage (#142 decision 2,
    gate ruling 2026-07-27: a promoted never-started queued run goes through the SAME composed PoC
    default stage a fresh ``engine.run`` dispatch uses — no admission-time discriminator exists, so
    the drain has ONE behavior), and finalizes; finalize RETURNS the next promoted run rather than
    dispatching it, so this drains a queue of any length in one flat loop — no per-waiter recursion
    frame, so a long persistent FIFO cannot blow the stack. The composed stage is read as a MODULE
    GLOBAL at dispatch time, so a queue-mechanics / crash-recovery UNIT test that wants the stub on
    the drain path pins it by monkeypatching ``engine._poc_composed_stage`` to ``_default_stage`` (or
    a recording stub) — the seam this section's module docstring already documents.
    """
    while next_id is not None:
        s.append_event(next_id, {"transition": RUNNING})
        record = s.read(next_id)
        result = _execute_stage(s, next_id, _poc_composed_stage, record)
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
        elif status == State.WAITING_FOR_MERGE.value:
            # A composed PoC-D (#115) stage that delivered a PR already performed the guarded
            # running -> waiting-for-merge transition and persisted the terminal status. That state is
            # TERMINAL (like completed), so release the slot and advance the FIFO — but do NOT
            # re-transition (the run is no longer running) or overwrite the record.
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
    approved_scope: list[str] | None = None,
    auto_approve_contract: bool = False,
) -> dict:
    """Run ``alias#n`` to completion (or land it queued behind the active run).

    ``approved_scope``/``auto_approve_contract`` (#140) are the HEADLESS answers to the composed
    stage's two human gates, so any caller (the CLI's ``--scope``/``--yes``, a worker, a future
    daemon) can drive a run with no keyboard. Left at their defaults the run stays interactive.
    """
    # Both-or-neither headless answers (#164): either BOTH gates are answered up front (headless)
    # or NEITHER (interactive). Exactly one leaves a gate unanswered, which used to author and then
    # silently park. Refuse loudly HERE at the shared seam so every caller (CLI, worker, daemon, any
    # injected stage) is guarded, before any registry lookup, issue-open check, or side effect.
    scope_given = approved_scope is not None
    if scope_given != auto_approve_contract:
        raise ValueError(
            "engine.run needs BOTH approved_scope and auto_approve_contract to run "
            "headless, or NEITHER to run interactively; got only "
            + ("approved_scope" if scope_given else "auto_approve_contract")
        )
    if issue_open is None:
        issue_open = github.issue_is_open
    if stage is None:
        # PoC-D (#115): the composed end-to-end stage is the engine's DEFAULT. The bare CLI path
        # (``cli.run`` -> ``engine.run(spec)``) therefore drives candidate -> readiness -> delivery.
        # The stub ``_default_stage`` is retained for the queue auto-advance/drain path and is pinned
        # explicitly by the queue/admission/crash-tx UNIT tests that exercise queue mechanics.
        stage = _poc_composed_stage
        if approved_scope is not None or auto_approve_contract:
            # Bind the headless answers ONLY when they were actually supplied, so the interactive
            # path still calls the module global with ``record`` alone (a monkeypatched stand-in
            # stage takes no keyword parameters).
            stage = partial(
                stage,
                approved_scope=approved_scope,
                auto_approve_contract=auto_approve_contract,
            )
    if new_run_id is None:
        new_run_id = _default_run_id

    alias, _, raw_number = spec.partition("#")
    number = int(raw_number)

    entry = Registry.load().get(alias)  # RegistryError before any run when unregistered
    if not issue_open(entry.slug, number):
        raise ValueError(f"issue {entry.slug}#{number} is not open; refusing to run")

    # Resolved issue context the composed stage reads from the record (the stub ignores it): the
    # persisted registry slug, the issue number, the registered default branch, the normal checkout,
    # and the alias (``run_candidate`` re-resolves the entry from ``record["repo"]``).
    slug_owner, _, slug_repo = entry.slug.partition("/")
    issue_context = {
        "repo": alias,
        "slug": entry.slug,
        "issue_number": number,
        "issue_ref": (slug_owner, slug_repo, number),
        "default_branch": entry.default_branch,
        "registered_checkout": str(entry.path),
    }

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
        active_id = queue.get("active")
        if active_id is None:
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
        s.write_record_unlocked(
            run_id, {"run_id": run_id, "status": status, **issue_context}, create=True
        )

    s.append_event(run_id, {"transition": QUEUED})
    if not admitted:
        # #142 decision 3: engine-side feedback when a run lands behind the active worker — name the
        # active run it waits behind. An immediately-admitted run (above) is silent. cli.py is
        # untouched, keeping #142 file-disjoint from #152.
        print(f"queued behind active run {active_id}")
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


# ---------------------------------------------------------------------------
# PoC-A (#114): the engine-owned local candidate stage
# ---------------------------------------------------------------------------
#
# An engine-internal stage (plugged into ``run(stage=...)``, never reached through cli.py) that turns
# one already-shaped pytest issue into ONE immutable local candidate commit WITHOUT pushing. It
# reuses the existing seams — ``providers.invoke`` (authoring, then implementation),
# ``contract.prove_red`` (the machine-checked red proof), and ``verify.run_baseline`` (the
# AUTHORITATIVE per-command verdict) — and resolves the normal checkout AND the pytest adapter from
# the REGISTERED repository entry (the ``repo`` alias on the record), never from a mandated
# ``base_checkout`` record key. The engine (never the provider) creates the contract commit AFTER approval;
# implementation runs only after approval; the authoritative acceptance + baseline verdicts (never the
# provider's self-report) decide landing; a green result lands as a SEPARATE implementation commit.

_CANDIDATE_TIMEOUT = 600.0


@dataclass
class CandidateResult:
    """The outcome of the candidate stage: a pause verdict plus the local artifacts it produced."""

    paused: bool
    pause_reason: str | None
    contract_commit: str | None
    candidate_sha: str | None
    evidence: dict | None


def _candidate_git(worktree: Path, *args: str, check: bool = True) -> str:
    """A plain ``git -C <worktree>`` read/write (never a push/PR/merge; the caller composes only
    local verbs). Returns stripped stdout; raises on a non-zero exit when ``check``."""
    result = subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, check=check
    )
    return result.stdout.strip()


def _candidate_head(worktree: Path) -> str:
    return _candidate_git(worktree, "rev-parse", "HEAD")


def _collect_ids(adapter: object, worktree: Path) -> tuple[str, ...]:
    """The adapter's authoritative ``--collect-only`` node-id set for ``worktree`` (the same real seam
    ``contract._collect`` uses): provision the environment, then ``canonical_collect`` under the
    target's COMMITTED baseline command (``contract._committed_command``), so a subdir layout scopes
    past a root-broken module and an enforced-invalid committed baseline raises rather than falling
    back to bare-root."""
    handle = adapter.provision_environment(Path(worktree), None)
    invocation = SimpleNamespace(
        worktree=Path(worktree),
        interpreter=handle.interpreter,
        command=contract._committed_command(Path(worktree)),
        env=getattr(handle, "env", None),
    )
    collection = adapter.canonical_collect(invocation)
    return tuple(getattr(collection, "ids", ()) or ())


@contextmanager
def _base_worktree(candidate_worktree: Path, base_sha: str) -> Any:
    """A throwaway detached git worktree of the candidate's own repo pinned at ``base_sha`` — the
    clean BEFORE state for authoritative collection. Git itself creates and removes the worktree
    directory (no raw filesystem write here), placed under IssueForge's own state root, never
    touching any registered checkout (criterion 1)."""
    path = Path(state_root()).resolve() / "candidate-base" / uuid.uuid4().hex
    _candidate_git(candidate_worktree, "worktree", "add", "-q", "--detach", str(path), base_sha)
    try:
        yield path
    finally:
        _candidate_git(candidate_worktree, "worktree", "remove", "--force", str(path), check=False)


def _added_node_ids(candidate_worktree: Path, base_sha: str, adapter: object) -> tuple[str, ...]:
    """The REAL added pytest ids: authoritative candidate collection MINUS the base collection at
    ``base_sha`` (``adapter.select_baseline``), so parametrized-case suffixes are included and any
    pre-existing test in an EDITED contract file is excluded — exactly the ids the real
    ``prove_red`` recomputes as ``added``. Source-AST reconstruction (which mishandled both) is not
    used."""
    with _base_worktree(candidate_worktree, base_sha) as before:
        base_ids = _collect_ids(adapter, before)
    candidate_ids = _collect_ids(adapter, candidate_worktree)
    return tuple(adapter.select_baseline(base_ids, candidate_ids).added)


def _capture_head_ref(worktree: Path) -> str:
    """The worktree's current HEAD attachment: its short branch name when HEAD is symbolic, else the
    exact commit sha (a detached HEAD)."""
    branch = _candidate_git(worktree, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    return branch or _candidate_head(worktree)


def _restore_head_ref(worktree: Path, ref: str) -> None:
    """Re-attach the worktree to ``ref`` (branch name or sha), undoing a transient detach."""
    _candidate_git(worktree, "checkout", "-q", ref, check=False)


# The repo's test-directory convention, used to direct authoring when the issue declares no paths.
# No config key exposes a test directory at the authoring point (``.issueforge.toml`` carries only
# ``baseline`` / ``framework``); the convention lives in ``audit._test_path`` (``tests/test_*``).
_DEFAULT_CONTRACT_DIR = "tests"


def _added_paths(candidate_worktree: Path) -> list[str]:
    """The files authoring ADDED in the candidate worktree, the real source of the frozen contract on
    a live run (``github.read_issue_body`` cannot know them; the tests do not exist when the issue
    body is read). The candidate's HEAD is ``base_sha``, so the untracked (not-yet-committed) files —
    ``git ls-files --others --exclude-standard`` — ARE the paths added since base: a file the author
    merely edited or deleted stays tracked and is excluded, and every added file is listed
    individually (never a bare parent directory)."""
    out = _candidate_git(candidate_worktree, "ls-files", "--others", "--exclude-standard")
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def _authoring_prompt(
    issue_body: str, contract_paths: list[str], baseline_command: list[str]
) -> str:
    """The authoring instruction: the full issue body, the allowed test paths, the baseline command,
    and an explicit may-edit-but-no-git instruction whose prohibitions bind to their OWN operation
    (each in its own sentence/clause), never a bare keyword list and never a permitted git verb.

    On a live run ``github.read_issue_body`` cannot know the authored-test paths (the tests do not
    exist when the body is read), so ``contract_paths`` arrives empty. Rendering an empty list would
    produce ``...only in these paths: .`` — naming NOWHERE, so a provider that obeys the instruction
    writes nothing. When no paths are declared, direct the provider at the repo's test directory
    (``_DEFAULT_CONTRACT_DIR``, the ``tests/`` convention ``audit._test_path`` enforces) so an
    obedient provider produces files the worktree-derivation step below can then find."""
    locations = contract_paths or [_DEFAULT_CONTRACT_DIR]
    return (
        "You are authoring pytest acceptance tests for the issue below.\n"
        "ISSUE START\n"
        f"{issue_body}\n"
        "ISSUE END\n"
        "Every test you author must fail when run now, before any implementation, "
        "for the behavioral reason in the issue.\n"
        "Do not include tests that pass against the current code; guard or control tests are "
        "added during implementation, never in this contract.\n"
        f"Author the failing tests only in these paths: {', '.join(locations)}.\n"
        f"After writing them the baseline command is: {' '.join(baseline_command)}.\n"
        "You may edit and modify files in the worktree.\n"
        "You may not run git in any form.\n"
        "You must not push to any remote.\n"
        "You may not open a pull request or a PR.\n"
        "You must not merge anything.\n"
    )


def _implementation_prompt(
    issue_body: str,
    write_scope: list[str],
    frozen_contract: str,
    acceptance_command: list[str],
    baseline_command: list[str],
) -> str:
    """The implementation instruction, after the contract is frozen: the issue, the approved write
    scope, the FROZEN authored test blob verbatim (one contiguous block), and EVERY token of both
    verification commands as ordered serializations."""
    return (
        "Implement the behavior so the frozen acceptance contract passes.\n"
        "ISSUE START\n"
        f"{issue_body}\n"
        "ISSUE END\n"
        f"Approved write scope: {', '.join(write_scope)}.\n"
        "FROZEN CONTRACT START\n"
        f"{frozen_contract}\n"
        "FROZEN CONTRACT END\n"
        f"The acceptance command is: {' '.join(acceptance_command)}.\n"
        f"The full baseline command is: {' '.join(baseline_command)}.\n"
        "You may edit and modify files in the worktree.\n"
        "You may not run git in any form.\n"
        "You must not push to any remote.\n"
        "You may not open a pull request or a PR.\n"
        "You must not merge anything.\n"
    )


def _evidence_entry(command: list[str], evidence: object) -> dict:
    """Summarize one authoritative ``verify.run_baseline`` result into a JSON-safe, per-command entry
    (the golden status/collected/executed/exit_code the downstream integration issues read)."""
    status = getattr(evidence, "status", None)
    return {
        "command": list(command),
        "status": getattr(status, "value", status),
        "collected": getattr(evidence, "collected", None),
        "executed": getattr(evidence, "executed", None),
        "exit_code": getattr(evidence, "exit_code", None),
    }


def _pause_candidate(
    st: store.RunStore, run_id: str, reason: str, *, contract_commit: str | None = None
) -> CandidateResult:
    """Persist ``paused`` and return a paused, no-candidate result (no push/PR/merge/cleanup)."""
    st.apply(run_id, lambda _r: {"status": State.PAUSED.value})
    return CandidateResult(
        paused=True,
        pause_reason=reason,
        contract_commit=contract_commit,
        candidate_sha=None,
        evidence=None,
    )


def run_candidate(
    record: dict,
    *,
    profile: object,
    approver: Callable[[object], bool],
    invoke: Callable[..., Any] = providers.invoke,
    prove_red: Callable[..., Any] = contract.prove_red,
    run_baseline: Callable[..., Any] = _verify.run_baseline,
) -> CandidateResult:
    """Produce ONE engine-owned local candidate SHA from one already-shaped pytest issue (see the
    section doc). Reuses the real provider/red-proof/verify seams; resolves the normal checkout and
    the pytest adapter from the REGISTERED repository entry; the engine (never the provider) freezes the
    contract after approval; the AUTHORITATIVE acceptance + baseline verdicts decide landing."""
    run_id = record["run_id"]
    issue_body = record["issue"]
    contract_paths = list(record["contract_paths"])
    write_scope = list(record["write_scope"])
    acceptance_command = list(record["acceptance_command"])
    baseline_command = list(record["baseline_command"])
    candidate_worktree = Path(record["candidate_worktree"])
    base_sha = record["base_sha"]

    # Resolve the normal checkout AND the verification adapter from the REGISTERED repository entry
    # (the ``repo`` alias), NOT from any mandated ``base_checkout`` record key.
    entry = Registry.load().get(record["repo"])
    base_checkout = Path(entry.path)
    adapter = _adapter_registry.resolve(framework=entry.framework, reporter=entry.reporter)

    st = store.RunStore()

    # 1) Authoring: ONE provider invocation in the candidate worktree; it MAY edit files but MAY NOT
    #    run git/push/PR/merge. The provider edits the worktree; it never commits.
    invoke(
        profile,
        _authoring_prompt(issue_body, contract_paths, baseline_command),
        cwd=candidate_worktree,
        run_id=run_id,
        role="primary",
        timeout=_CANDIDATE_TIMEOUT,
    )

    # 1b) Derive the frozen contract from what authoring ACTUALLY added in the worktree. The record's
    #     declared ``contract_paths`` is empty on a live run (``github.read_issue_body`` hardcodes
    #     ``[]``); the added files are the only real source. An empty derived set means the author
    #     produced no files: pause cleanly with ``no_contract_paths`` BEFORE any staging, approval, or
    #     commit, instead of staging an empty pathspec and crashing at an empty-index commit.
    contract_paths = _added_paths(candidate_worktree)
    if not contract_paths:
        return _pause_candidate(st, run_id, "no_contract_paths")

    # 2) Red proof (reused) BEFORE approval: the authored tests must collect and FAIL for the named
    #    behavior while the baseline stays green. A REFUSED proof pauses cold — no approval, no commit.
    try:
        targeted_ids = _added_node_ids(candidate_worktree, base_sha, adapter)
    except ValueError:
        return _pause_candidate(st, run_id, "baseline_command_missing")
    # ``prove_red``'s real seam ``_checkout_detached``-es the registered base checkout to run the base
    # suite at the bound sha, changing its HEAD attachment. Capture the checkout's exact attachment
    # and restore it in a ``finally`` so the registered normal checkout is left byte-for-byte
    # untouched (criterion 1) even on the REAL seam — never only under the fake proof.
    base_ref = _capture_head_ref(base_checkout)
    try:
        proof = prove_red(
            run_id,
            targeted_ids=targeted_ids,
            base_checkout=base_checkout,
            candidate_worktree=candidate_worktree,
            base_sha=base_sha,
            adapter=adapter,
        )
    finally:
        _restore_head_ref(base_checkout, base_ref)
    if not getattr(proof, "accepted", False):
        return _pause_candidate(st, run_id, "red_proof_rejected")

    # 3) Approval: the human sees the EXACT authored test diff (vs base) and the machine-checked red
    #    evidence. Stage the authored contract paths (index only — HEAD stays at base_sha) so the
    #    diff is bound to base; nothing is committed before approval.
    _candidate_git(candidate_worktree, "add", "--", *contract_paths)
    diff = _candidate_git(candidate_worktree, "diff", "--cached")
    review = SimpleNamespace(diff=diff, red_evidence=proof)
    if not approver(review):
        return _pause_candidate(st, run_id, "rejected_by_approver")

    # 4) Contract commit: the ENGINE (never the provider) freezes the approved tests as a commit whose
    #    parent is base_sha.
    _candidate_git(candidate_worktree, "commit", "-m", "Freeze authored acceptance contract (#114)")
    contract_commit = _candidate_head(candidate_worktree)

    # 5) Implementation: a SECOND provider invocation, on top of the frozen contract, carrying the
    #    issue, the frozen authored test blob, the approved write scope, and both verification commands.
    frozen_contract = "\n".join(
        (candidate_worktree / rel).read_text(encoding="utf-8")
        for rel in contract_paths
        if (candidate_worktree / rel).exists()
    )
    invoke(
        profile,
        _implementation_prompt(
            issue_body, write_scope, frozen_contract, acceptance_command, baseline_command
        ),
        cwd=candidate_worktree,
        run_id=run_id,
        role="primary",
        timeout=_CANDIDATE_TIMEOUT,
    )

    # 6) Authoritative verification (reused): run the acceptance AND full baseline commands, each once
    #    (no retry). The green/red verdict comes from IT, per command, never the provider's self-report.
    acceptance_ev = run_baseline(candidate_worktree, acceptance_command, adapter=adapter)
    baseline_ev = run_baseline(candidate_worktree, baseline_command, adapter=adapter)
    evidence = {
        "acceptance": _evidence_entry(acceptance_command, acceptance_ev),
        "baseline": _evidence_entry(baseline_command, baseline_ev),
    }

    # Either command non-green PAUSES with the candidate HEAD left at the contract commit (no impl
    # commit lands): the self-report never overrides the authoritative verdict.
    if not (
        acceptance_ev.status is BaselineStatus.GREEN and baseline_ev.status is BaselineStatus.GREEN
    ):
        return _pause_candidate(
            st, run_id, "verification_not_green", contract_commit=contract_commit
        )

    # 7) Green landing: the ENGINE commits a SEPARATE implementation commit (child of the contract
    #    commit); the worktree is clean at the resulting candidate SHA.
    _candidate_git(candidate_worktree, "add", "-A")
    _candidate_git(candidate_worktree, "commit", "-m", "Land candidate implementation (#114)")
    candidate_sha = _candidate_head(candidate_worktree)

    # 8) Persist the candidate run-record fields as FLAT keys plus summarized evidence. The status is
    #    left ``running`` here ON PURPOSE: when this stage is driven through ``engine.run(stage=...)``,
    #    ``_finalize`` performs the running->completed transition AND releases/advances the queue slot
    #    (it only acts on a still-``running`` run). Writing ``completed`` here would wedge the queue —
    #    ``_finalize`` would see a non-running status and skip the slot release.
    st.apply(
        run_id,
        lambda _r: {
            "contract_commit": contract_commit,
            "candidate_sha": candidate_sha,
            "candidate_worktree": str(candidate_worktree),
            "base_sha": base_sha,
            "write_scope": write_scope,
            "contract_paths": contract_paths,
            "acceptance_command": acceptance_command,
            "baseline_command": baseline_command,
            "evidence": evidence,
        },
    )

    return CandidateResult(
        paused=False,
        pause_reason=None,
        contract_commit=contract_commit,
        candidate_sha=candidate_sha,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# PoC-D (#115): the composed default stage — candidate -> readiness -> delivery
# ---------------------------------------------------------------------------
#
# ``engine.run(spec)`` (the bare CLI path) drives this stage by default: it resolves the DandD alias,
# reads the issue through the seams, fetches the FRESH default-branch tip, opens an isolated detached
# worktree, proves the committed baseline green, then COMPOSES the three already-built Wave-1 seams —
# ``run_candidate`` (#114) -> ``verify.issue_readiness`` (#112) -> ``github.deliver_pr`` (#113) — to
# author + implement one candidate, gate it, and deliver EXACTLY one PR, landing ``waiting-for-merge``
# without ever merging. Contract-integrity is SCOPED OUT for M1 (#126): readiness runs with a
# pass-through integrity verdict, so the gate is acceptance + baseline + write-scope only.


def _poc_approver(review: Any) -> bool:
    """The human contract-approval gate the composed stage consults BEFORE freezing the contract.

    Called with ``SimpleNamespace(diff=<authored test diff>, red_evidence=<machine-checked proof>)``.
    The default is INTERACTIVE (prints the authored test diff + the red evidence, then reads a y/n
    from stdin); the acceptance suite monkeypatches this module-level seam. Any answer other than an
    explicit yes rejects, and a closed stdin (EOF) is a rejection — the gate never auto-approves.
    """
    print("=== IssueForge: approve the authored acceptance contract? ===")
    print(getattr(review, "diff", ""))
    print(f"red evidence: {getattr(review, 'red_evidence', None)!r}")
    try:
        answer = input("Freeze this contract and implement it? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _poc_scope_approver(stated_files: Any) -> list | None:
    """Pre-authoring human scope gate (#129): show the issue's stated files, read the approved file
    list from stdin (space-separated). An empty line or a closed stdin (EOF) REJECTS (returns None).
    Never auto-approves."""
    print("=== IssueForge: approve the write scope BEFORE authoring? ===")
    print(f"stated files: {list(stated_files)}")
    try:
        answer = input("Approved files (space-separated), empty to reject: ")
    except EOFError:
        return None
    files = answer.split()
    return files or None


def _preapproved_contract(_review: Any) -> bool:
    """The contract-gate stand-in for a headless run (#140): the operator already answered ``--yes``
    on the command line, so there is nothing to ask. It is a SEPARATE callable from ``_poc_approver``
    so a headless run provably never reaches the interactive gate."""
    return True


def _poc_pause(st: store.RunStore, run_id: str, reason: str) -> None:
    """Persist ``paused`` with a pause reason and no delivery (the composed stage's pause path)."""
    st.apply(run_id, lambda _r: {"status": State.PAUSED.value, "pause_reason": reason})


def _persist_baseline_diagnostics(st: store.RunStore, run_id: str, evidence: object) -> None:
    """Persist a non-green baseline's real stdout/stderr + exit code so the pause is diagnosable (#141).

    Writes ``baseline-stdout.log`` / ``baseline-stderr.log`` through the redacting artifact writer —
    distinct names from ``_persist_captured``'s ``stdout.log`` / ``stderr.log`` (the STAGE's redirected
    streams), so the subprocess capture and the stage capture stay separate layers. The subprocess
    exit code is surfaced on the record under ``baseline_exit_code``.
    """
    store.write_artifact(run_id, "baseline-stdout.log", getattr(evidence, "stdout", "") or "")
    store.write_artifact(run_id, "baseline-stderr.log", getattr(evidence, "stderr", "") or "")
    st.apply(run_id, lambda _r: {"baseline_exit_code": getattr(evidence, "exit_code", None)})


def _integrity_scoped_out(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    """Pass-through contract-integrity verdict — integrity is SCOPED OUT for M1 (#126)."""
    return SimpleNamespace(ok=True, violations=())


def _poc_composed_stage(
    record: dict,
    *,
    approved_scope: list[str] | None = None,
    auto_approve_contract: bool = False,
) -> StageResult | None:
    """Compose candidate -> readiness -> delivery for the bare ``engine.run(spec)`` path (#115).

    Both headless answers (#140) are KEYWORD-ONLY and DEFAULTED, so every existing ``stage(record)``
    call site keeps binding. ``approved_scope`` stands in for the pre-authoring scope gate and
    ``auto_approve_contract`` for the contract gate; when a headless answer is supplied the matching
    human gate is never consulted at all (not answered on the human's behalf, simply not reached).
    """
    from issueforge import config as _config
    from issueforge import workspace

    run_id = record["run_id"]
    st = store.RunStore()

    slug = record["slug"]
    number = record["issue_number"]
    checkout = Path(record["registered_checkout"])
    default_branch = record["default_branch"]
    issue_ref = tuple(record["issue_ref"])

    entry = Registry.load().get(record["repo"])
    adapter = _adapter_registry.resolve(framework=entry.framework, reporter=entry.reporter)

    # 1) Read the issue through the seam: body + approved write scope + committed contract paths.
    #    (``run()`` already recorded the ``issue_is_open`` check with the resolved slug.)
    body_info = github.read_issue_body(slug, number)
    issue_body = body_info["body"]
    stated_files = list(body_info["files"])
    contract_paths = list(body_info["contract_paths"])

    # 1b) Pre-authoring human scope gate (#129): the human approves the write scope BEFORE any AI edit,
    #     fetch, or worktree is created. A rejection (None) pauses with NO side effects. Read as a bare
    #     module-global so the acceptance suite's monkeypatched seam is honored. A headless
    #     ``approved_scope`` (#140) IS the operator's answer, so the gate is skipped, not auto-passed.
    if approved_scope is None:
        approved_scope = _poc_scope_approver(stated_files)
    if approved_scope is None:
        # #142 decision 1: a scope-gate rejection produced no candidate and is not resumable, so it
        # is TERMINAL (``failed``), NOT a resumable ``paused``. Record the scope reason for
        # diagnosis, then hand ``_finalize`` a typed FAILED StageResult so it performs the guarded
        # running -> failed transition AND releases + auto-advances the worker slot (a bare
        # ``_poc_pause`` here is exactly the wedge #142 removes). Every OTHER pause below stays
        # resumable and keeps the slot.
        reason = "scope_rejected (pre-authoring scope gate)"
        st.apply(run_id, lambda _r: {"pause_reason": reason})
        return StageResult(status=State.FAILED, failure=ScopeRejected(reason))
    write_scope = list(approved_scope)

    # 1c) Resolve the primary provider profile from the OPERATOR-level providers config (#135), not the
    #     target repo's committed .issueforge.toml. Provider/role config is operator/environment state,
    #     so the repo contract stays minimal (baseline/acceptance/framework); only ROLES resolve here.
    #     Resolved BEFORE any fetch/worktree side effect so a missing/invalid operator config pauses with
    #     NO orphaned worktree (a real run cannot launch without a configured primary role; the
    #     acceptance path always provides one, so the happy path resolves).
    from issueforge import paths as _paths

    _providers_path = _paths.providers_config()
    try:
        primary_profile = _config.load_roles(tomllib.loads(_providers_path.read_text())).primary
    except FileNotFoundError:
        _poc_pause(st, run_id, f"missing provider config: {_providers_path}")
        return
    except _config.ConfigError as exc:
        _poc_pause(st, run_id, f"provider config: {exc}")
        return

    # 2) Fetch the FRESH default-branch tip. A failed read is never negative evidence — it PAUSES.
    fetch = workspace.fetch_default_sha(checkout)
    if not fetch.ok:
        _poc_pause(st, run_id, f"fetch_default_sha: {fetch.reason}")
        return
    base_sha = fetch.sha

    # 3) Isolated detached worktree at the fresh base (proven isolated from the normal checkout).
    wt = workspace.create_isolated_worktree(checkout, base_sha)
    if not (wt.ok and wt.isolated):
        _poc_pause(st, run_id, f"create_isolated_worktree: {wt.reason}")
        return
    candidate_worktree = wt.path

    # The acceptance + baseline commands come from the committed .issueforge.toml of the FETCHED
    # base — read from the candidate worktree (created at the freshly fetched ``base_sha``), NOT the
    # registered checkout's possibly-stale HEAD. If the fetched default branch changed the config, the
    # stage must run the fetched commands, never obsolete ones off a stale checkout (unearned green).
    cfg = _config.load_config(candidate_worktree)
    baseline_command = list(cfg.baseline)
    acceptance_command = list(cfg.acceptance or cfg.baseline)

    # 4) Prove the committed baseline GREEN before any AI edit (red/failed -> pause).
    baseline_ev = _verify.run_baseline(candidate_worktree, baseline_command, adapter=adapter)
    if baseline_ev.status is not BaselineStatus.GREEN:
        # Persist the baseline command's ACTUAL stdout/stderr + exit code (#141): a USAGE_ERROR
        # pause otherwise surfaces only the classified enum, so the subprocess's real complaint (a
        # self-provisioned env missing pytest-reportlog, say) never reaches the run record.
        _persist_baseline_diagnostics(st, run_id, baseline_ev)
        _poc_pause(
            st, run_id, f"baseline_not_green: {baseline_ev.status} (exit {baseline_ev.exit_code})"
        )
        return

    # Seed the run_candidate contract into the record (#114 reads the issue body under "issue").
    st.apply(
        run_id,
        lambda _r: {
            "issue": issue_body,
            "write_scope": write_scope,
            "contract_paths": contract_paths,
            "acceptance_command": acceptance_command,
            "baseline_command": baseline_command,
            "candidate_worktree": str(candidate_worktree),
            "base_sha": base_sha,
        },
    )
    record = st.read(run_id)

    # 5) Candidate: author -> red proof -> human approval -> contract commit -> implement -> verify.
    #    ``invoke`` / ``_poc_approver`` are read from their module attributes at call time so the
    #    acceptance suite's monkeypatched seams are honored; prove_red/run_baseline stay REAL.
    #    A headless ``auto_approve_contract`` (#140) supplies the operator's answer up front, so the
    #    human gate is never consulted; without it the interactive seam runs unchanged.
    approver = _preapproved_contract if auto_approve_contract else _poc_approver
    candidate = run_candidate(
        record,
        profile=primary_profile,
        approver=approver,
        invoke=providers.invoke,
    )
    if candidate.paused:
        return  # run_candidate already persisted status=paused; no readiness, no delivery.

    # 6) Readiness = acceptance + baseline + write-scope. Integrity is SCOPED OUT for M1 (#126): a
    #    pass-through verdict is injected so readiness never runs the deferred integrity gate.
    verdict = _verify.issue_readiness(
        run_id, adapter=adapter, verify_integrity=_integrity_scoped_out
    )
    if verdict.get("readiness") != "ready":
        _poc_pause(st, run_id, f"not_ready: {verdict.get('predicate')}")
        return

    # 7) Flatten the readiness verdict to the flat top-level fields #113 delivery reads, name a branch
    #    at the candidate sha (the worktree HEAD is detached), and re-target the record for delivery.
    record = st.read(run_id)
    candidate_sha = record["candidate_sha"]
    branch = f"issueforge/{run_id}"
    subprocess.run(
        ["git", "-C", str(candidate_worktree), "branch", "-f", branch, candidate_sha],
        check=True,
        capture_output=True,
        text=True,
    )
    st.apply(
        run_id,
        lambda _r: {
            "readiness": "ready",
            "ready_sha": verdict["ready_sha"],
            "acceptance": verdict["acceptance"],
            "baseline": verdict["baseline"],
            "scope": verdict["scope"],
            "contract_integrity": verdict["contract_integrity"],
            "candidate_branch": branch,
            "registered_checkout": str(candidate_worktree),
            # deliver_pr (#113) needs record["issue"] as the (owner, repo, number) tuple, but that
            # overwrites the exact issue BODY seeded for run_candidate (#114). Preserve the body under
            # a distinct key so the delivered waiting-for-merge record still carries "the exact issue
            # body ... persisted" (issue #115 acceptance criterion).
            "issue_body": issue_body,
            "issue": issue_ref,
            "default_branch": default_branch,
        },
    )
    record = st.read(run_id)

    # 8) Deliver EXACTLY one PR (default-branch read -> push -> origin verify -> open PR), never
    #    merging. The gateway class is read from the module attribute so the fake is honored.
    github.deliver_pr(record, gateway=github.GhWriteGateway(), store=st)
    record = st.read(run_id)

    # 9) Terminal: guarded running -> waiting-for-merge + the flat pr_url. ``_finalize`` sees the
    #    terminal status and releases the worker slot (no re-transition, no overwrite).
    with st.locked():
        current = st._read_unlocked(run_id)
        transition(State(current["status"]), State.WAITING_FOR_MERGE)
        st.write_record_unlocked(
            run_id,
            {
                **current,
                "status": State.WAITING_FOR_MERGE.value,
                "pr_url": record["pr"]["url"],
            },
        )
    st.append_event(run_id, {"transition": State.WAITING_FOR_MERGE.value})
