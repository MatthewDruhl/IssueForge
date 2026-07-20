"""Committed PENDING acceptance suite for #9 (S5) — queue control: FIFO, pause, park, cancel, resume.

The S5 surfaces this suite authors do not exist yet (``issueforge.state`` is a new module; the
queue-control verbs ``engine.pause/park/cancel/continue_run/reorder``, the typed ``StageResult`` /
``StageFailure`` / ``StubStageFailure``, and ``engine.DivergenceError`` / ``engine.WorkerBusyError``
are new attributes on the existing engine), so each test FAILS while pending — an ``ImportError``
for the missing module or an ``AttributeError`` for a missing attribute — and is therefore
``@pytest.mark.xfail(strict=True, reason="PENDING (#9)")``. Imports of the pending surfaces live
INSIDE each test so a module-top import cannot turn the whole file into a collection ERROR.
Dual-layer docstrings: plain behavior first, then the ``technical (contract):`` golden values.

The S5 interface this suite authors (ATDD):

- ``issueforge.state`` — the table-driven state machine, NO state-machine library:
  - ``State`` is an ``enum.Enum`` whose ``__members__`` are exactly ``QUEUED="queued"``,
    ``RUNNING="running"``, ``PAUSED="paused"``, ``PARKED="parked"``, ``CANCELLED="cancelled"``,
    ``COMPLETED="completed"``, ``FAILED="failed"`` (no aliases).
  - ``TRANSITIONS: dict[State, set[State]]`` declares exactly the legal edges:
    ``QUEUED -> {RUNNING, CANCELLED}``; ``RUNNING -> {PAUSED, PARKED, COMPLETED, FAILED}``;
    ``PAUSED -> {RUNNING, PARKED, CANCELLED}``; ``PARKED -> {RUNNING}``;
    ``COMPLETED/CANCELLED/FAILED -> set()``.
  - ``TERMINAL == {COMPLETED, CANCELLED, FAILED}`` (exactly the states with no outgoing edge).
  - ``transition(current, target) -> State`` returns ``target`` iff ``target in
    TRANSITIONS[current]``, else raises ``IllegalTransition``.

- ``issueforge.engine`` gains the human-initiated queue-control verbs (guarded transitions, each
  persisting one transition event). PAUSE keeps the single worker slot; PARK, CANCEL-of-paused, a
  FAILED stage, and natural COMPLETION all release the slot and auto-advance the FIFO head. The
  engine's post-stage finalization HONORS a non-running status the stage set (a stage that
  paused/parked/failed its run is NOT overwritten to completed). Auto-advance dispatches the FIFO
  head through ``engine._default_stage`` (the same default ``engine.run`` uses), so a monkeypatched
  default observes dispatch order. Each verb RETURNS the exact final persisted record (``reorder``
  returns the new queue order list).
  - ``engine.pause(run_id)``: ``running -> paused``; keeps ``queue.active == run_id``.
  - ``engine.park(run_id)``: ``running|paused -> parked``; preserves every other record field;
    releases the slot; advances the FIFO.
  - ``engine.cancel(run_id)``: ``queued -> cancelled`` (never held the slot; removed from the FIFO)
    or ``paused -> cancelled`` (releases the slot, advances). Refuses ``running``/``parked``/terminal
    (``IllegalTransition``); the record, queue, and event stream are unchanged on refusal.
  - ``engine.continue_run(run_id, *, github_facts=None, stage=None)``: the ONE resume verb (G7) for
    a ``paused``, ``parked``, or crash-orphaned ``running`` run (``stage`` is the same injectable
    stub-stage seam ``engine.run`` exposes, defaulting to ``_default_stage``). RECONCILES first:
    ``github_facts``
    (default ``github.pr_facts``) is authoritative for the PR/branch/merge keys
    ``{"pr","branch","merged"}``; the run record is authoritative for the gate-artifact keys
    ``{"approvals","verdicts","attempts"}``. A key present in BOTH the record and github_facts with
    unequal values raises ``engine.DivergenceError`` (naming the diverging key), leaves the record
    byte-untouched, and does NOT resume. github_facts NEVER writes a gate artifact. Resuming a
    parked run requires a free slot; onto a busy worker it raises ``engine.WorkerBusyError``.
  - ``engine.reorder(run_id, index)``: move a QUEUED, not-yet-started run to 0-based ``index`` in
    the FIFO; returns the new order. Refuses a non-queued run (``IllegalTransition``); raises
    ``ValueError`` on a negative, out-of-range, or non-int index; leaves the queue unchanged on any
    refusal.

- Typed stage failure (not a copied string catalogue): ``engine.StageResult(status, failure=None)``;
  each stage declares its OWN ``StageFailure`` subclass; the stub's is ``StubStageFailure`` with
  ``.type == "StubStageFailure"``. A stage returning ``StageResult(status=State.FAILED,
  failure=StubStageFailure(...))`` lands the run in ``failed`` (terminal), records
  ``record["failure"]["type"] == "StubStageFailure"`` derived from the instance, and advances.

- CLI parity: ``issueforge queue|pause|park|cancel|continue|reorder`` invoke these engine verbs.

Out of scope for S5 (stub stages only): resume that replays a real stage's INTERNAL checkpoint and
skips already-done work — that arrives with real stages (S6+). S5 proves the persisted run
record + event stream survive a crash and ``continue`` re-derives state without duplicating
recorded transitions.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

_STATES = {"queued", "running", "paused", "parked", "cancelled", "completed", "failed"}
_EDGES = {
    "queued": {"running", "cancelled"},
    "running": {"paused", "parked", "completed", "failed"},
    "paused": {"running", "parked", "cancelled"},
    "parked": {"running"},
    "completed": set(),
    "cancelled": set(),
    "failed": set(),
}


# --- helpers ---------------------------------------------------------------


def _register(make_git_repo, alias="DandD"):
    from typer.testing import CliRunner

    from issueforge.cli import app

    result = CliRunner().invoke(app, ["repo", "add", f"{alias}:{make_git_repo()}"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)


def _queue():
    from issueforge import store

    path = store.queue_path()
    if not path.exists():
        return {"active": None, "queue": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _status(run_id):
    from issueforge import store

    return store.RunStore().read(run_id)["status"]


def _transitions(run_id):
    from issueforge import store

    return [e["transition"] for e in store.RunStore().replay_events(run_id)]


def _admit_paused(make_git_repo, run_id, number=148, alias="DandD"):
    """Admit an issue and leave its run PAUSED in the worker slot (holds the slot, no advance)."""
    from issueforge import engine

    return engine.run(
        f"{alias}#{number}",
        issue_open=lambda s, n: True,
        new_run_id=lambda: run_id,
        stage=lambda record: engine.pause(record["run_id"]),
    )


def _enqueue(run_id, number, alias="DandD"):
    """Admit an issue while the slot is held; it LANDS queued (its stub stage does not run)."""
    from issueforge import engine

    return engine.run(f"{alias}#{number}", issue_open=lambda s, n: True, new_run_id=lambda: run_id)


def _child(state_home, body):
    """Run `body` in a fresh interpreter sharing this test's ISSUEFORGE_STATE_HOME."""
    code = (
        "from issueforge import engine, store\n"
        "from issueforge.state import State\n" + textwrap.dedent(body)
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "ISSUEFORGE_STATE_HOME": str(state_home)},
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# State machine: table-driven, no FSM library
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_state_enum_members_are_exactly_the_seven_declared_states(isolated_state_home):
    """The State enum declares exactly seven members (no aliases), each mapping to its status string.

    technical (contract): issueforge.state.State is an enum.Enum subclass; {name: m.value for name,
    m in State.__members__.items()} == {"QUEUED":"queued","RUNNING":"running","PAUSED":"paused",
    "PARKED":"parked","CANCELLED":"cancelled","COMPLETED":"completed","FAILED":"failed"} (exactly
    seven names, so no hidden alias inflates the value set).
    """
    import enum

    from issueforge.state import State

    assert issubclass(State, enum.Enum)
    assert {name: m.value for name, m in State.__members__.items()} == {
        "QUEUED": "queued",
        "RUNNING": "running",
        "PAUSED": "paused",
        "PARKED": "parked",
        "CANCELLED": "cancelled",
        "COMPLETED": "completed",
        "FAILED": "failed",
    }


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_transitions_table_declares_exactly_the_expected_edges(isolated_state_home):
    """The transition table maps each state to exactly its set of allowed next states.

    technical (contract): TRANSITIONS is a dict[State, set[State]]; {k.value: {v.value for v in vs}
    for k, vs in TRANSITIONS.items()} == _EDGES (queued->{running,cancelled};
    running->{paused,parked,completed,failed}; paused->{running,parked,cancelled}; parked->{running};
    completed/cancelled/failed -> set()); every value is a set.
    """
    from issueforge.state import TRANSITIONS

    assert isinstance(TRANSITIONS, dict)
    assert all(isinstance(vs, set) for vs in TRANSITIONS.values())
    assert {k.value: {v.value for v in vs} for k, vs in TRANSITIONS.items()} == _EDGES


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_terminal_set_is_exactly_the_three_states_with_no_outgoing_edge(isolated_state_home):
    """TERMINAL is exactly {completed, cancelled, failed} — the states with no outgoing edge — and no others.

    technical (contract): TERMINAL == {State.COMPLETED, State.CANCELLED, State.FAILED}; and for every
    State s, (TRANSITIONS[s] == set()) iff (s in TERMINAL).
    """
    from issueforge.state import TERMINAL, TRANSITIONS, State

    assert TERMINAL == {State.COMPLETED, State.CANCELLED, State.FAILED}
    for s in State:
        assert (TRANSITIONS[s] == set()) is (s in TERMINAL)


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_transition_guard_accepts_exactly_the_declared_edges_over_the_full_matrix(
    isolated_state_home,
):
    """Over all 49 ordered state pairs, the guard accepts a transition exactly when the table declares it, and raises IllegalTransition otherwise.

    technical (contract): for every (current, target) in State x State (49 pairs): if target in
    TRANSITIONS[current], transition(current, target) == target; else transition(current, target)
    raises issueforge.state.IllegalTransition. This pins the guard to the table exactly — no extra
    allowed edge, no missing rejection.
    """
    from issueforge.state import IllegalTransition, TRANSITIONS, State, transition

    for current in State:
        for target in State:
            if target in TRANSITIONS[current]:
                assert transition(current, target) == target
            else:
                with pytest.raises(IllegalTransition):
                    transition(current, target)


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_no_state_machine_library_is_imported_or_declared(isolated_state_home):
    """The state machine is hand-rolled: neither its source nor the project's declared dependencies pull in a state-machine library.

    technical (contract): the AST of issueforge.state and issueforge.engine contains no import whose
    top-level name is in {transitions, automat, statemachine, python_statemachine, xstate, sismic};
    and the project's pyproject.toml [project].dependencies list names none of them.
    """
    import ast
    import tomllib
    from pathlib import Path

    from issueforge import engine, state

    banned = {"transitions", "automat", "statemachine", "python_statemachine", "xstate", "sismic"}
    for module in (state, engine):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            assert not (names & banned), f"{module.__name__} imports {names & banned}"

    pyproject = Path(engine.__file__).resolve().parents[2] / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    joined = " ".join(deps).lower()
    assert not any(name in joined for name in banned)


# ---------------------------------------------------------------------------
# FIFO queue + reorder
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_additional_runs_enter_a_persistent_fifo_queue_readable_by_a_fresh_process(
    make_git_repo, isolated_state_home
):
    """Runs admitted behind the active run wait in a persistent FIFO queue that a brand-new process reads back in order.

    technical (contract): with run-1 admitted+paused (slot held), enqueue run-2 then run-3;
    store.RunStore().read_queue() == {"active":"run-1","queue":["run-2","run-3"]}; a FRESH
    interpreter (subprocess) reading store.read_queue() prints active=run-1 and
    queue=["run-2","run-3"] (persistence proven across a clean process, not just an in-process read).
    """
    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    _enqueue("run-3", 150)
    assert _queue() == {"active": "run-1", "queue": ["run-2", "run-3"]}

    proc = _child(
        os.environ["ISSUEFORGE_STATE_HOME"],
        """
        q = store.RunStore().read_queue()
        print(q["active"], q["queue"])
        """,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "run-1 ['run-2', 'run-3']"


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_reorder_moves_a_queued_run_to_an_absolute_index(make_git_repo, isolated_state_home):
    """A queued run is reordered to an explicit 0-based position (absolute indexing, distinguishable from move-to-front).

    technical (contract): with active run-1 and queue ["run-2","run-3","run-4","run-5"],
    engine.reorder("run-4", 1) returns ["run-2","run-4","run-3","run-5"] and
    store.read_queue()["queue"] equals it; the active slot (run-1) is untouched.
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    for i, rid in enumerate(["run-2", "run-3", "run-4", "run-5"]):
        _enqueue(rid, 149 + i)

    assert engine.reorder("run-4", 1) == ["run-2", "run-4", "run-3", "run-5"]
    assert _queue()["queue"] == ["run-2", "run-4", "run-3", "run-5"]
    assert _queue()["active"] == "run-1"


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
@pytest.mark.parametrize("bad_index", [-1, 2, True, "0", 1.0])
def test_reorder_rejects_an_out_of_range_or_non_int_index(
    bad_index, make_git_repo, isolated_state_home
):
    """Reorder rejects a negative, out-of-range, or non-integer index and leaves the queue unchanged.

    technical (contract): with queue ["run-2","run-3"] (len 2), engine.reorder("run-3", bad_index)
    for bad_index in {-1, 2, True, "0", 1.0} raises ValueError and store.read_queue()["queue"] is
    still ["run-2","run-3"]. (bool and float are not accepted as ints; index must be 0 <= i < len.)
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    _enqueue("run-3", 150)

    before = _queue()
    with pytest.raises(ValueError):
        engine.reorder("run-3", bad_index)
    assert _queue() == before


def _drive_to(engine, state_name, run_id="run-x", number=148, make_git_repo=None):
    """Drive a fresh run to a non-queued terminal/started state and return its id (never queued)."""
    from issueforge.state import State

    if state_name == "paused":
        _admit_paused(make_git_repo, run_id, number=number)
    elif state_name == "parked":
        _admit_paused(make_git_repo, run_id, number=number)
        engine.park(run_id)
    elif state_name == "completed":
        engine.run(f"DandD#{number}", issue_open=lambda s, n: True, new_run_id=lambda: run_id)
    elif state_name == "cancelled":
        _admit_paused(make_git_repo, run_id, number=number)
        engine.cancel(run_id)
    elif state_name == "failed":
        engine.run(
            f"DandD#{number}",
            issue_open=lambda s, n: True,
            new_run_id=lambda: run_id,
            stage=lambda rec: engine.StageResult(
                status=State.FAILED, failure=engine.StubStageFailure("x")
            ),
        )
    return run_id


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
@pytest.mark.parametrize("state_name", ["paused", "parked", "completed", "cancelled", "failed"])
def test_reorder_refuses_a_run_that_is_not_queued(state_name, make_git_repo, isolated_state_home):
    """Reorder applies only to queued runs; a run that has started (paused/parked) or is terminal (completed/cancelled/failed) is refused and the queue is unchanged.

    technical (contract): a run driven to each of {paused, parked, completed, cancelled, failed}
    (never `queued`) reordered via engine.reorder("run-x", 0) raises
    issueforge.state.IllegalTransition and store.read_queue()["queue"] is unchanged.
    """
    from issueforge import engine
    from issueforge.state import IllegalTransition

    _register(make_git_repo)
    _drive_to(engine, state_name, make_git_repo=make_git_repo)

    assert "run-x" not in _queue()["queue"]
    before = _queue()
    with pytest.raises(IllegalTransition):
        engine.reorder("run-x", 0)
    assert _queue() == before


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_reorder_and_cancel_refuse_the_active_running_run(make_git_repo, isolated_state_home):
    """While a run is actually running (inside its stage), it is neither reorderable nor cancellable — both verbs refuse the active running run.

    technical (contract): inside engine.run's stage for run-1 (status "running", holding the slot,
    not in the queue), engine.reorder("run-1", 0) and engine.cancel("run-1") each raise
    issueforge.state.IllegalTransition; each refused call leaves the run record, the queue, and the
    run's transition stream byte-for-byte unchanged from the snapshot taken immediately before it
    (no "cancelled" event appended, no mutation); the stage records both raised, and run-1 still
    completes normally to "completed".
    """
    from issueforge import engine, store
    from issueforge.state import IllegalTransition

    _register(make_git_repo)
    seen = {}

    def stage(record):
        rid = record["run_id"]
        assert _status(rid) == "running"
        for verb, call in (
            ("reorder", lambda: engine.reorder(rid, 0)),
            ("cancel", lambda: engine.cancel(rid)),
        ):
            before = (store.RunStore().read(rid), _queue(), _transitions(rid))
            try:
                call()
                seen[verb] = "no-raise"
            except IllegalTransition:
                seen[verb] = "raised"
            assert (store.RunStore().read(rid), _queue(), _transitions(rid)) == before

    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1", stage=stage)
    assert seen == {"reorder": "raised", "cancel": "raised"}
    assert _status("run-1") == "completed"


# ---------------------------------------------------------------------------
# Cancel: two paths (F3), refusals, and events
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_cancel_a_queued_run_writes_a_terminal_record_and_never_held_the_slot(
    make_git_repo, isolated_state_home
):
    """Cancelling a queued run removes it from the FIFO, writes a terminal cancelled record with one cancelled event, and never touched the worker slot.

    technical (contract): with active run-1 (paused) and queue ["run-2"], engine.cancel("run-2")
    returns the run-2 record with status "cancelled"; queue == {"active":"run-1","queue":[]}; the
    active slot was "run-1" before AND after; run-2's event stream contains exactly one "cancelled"
    transition and never a "running" one (the queued run never started).
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    assert _queue()["active"] == "run-1"

    result = engine.cancel("run-2")
    assert result["status"] == "cancelled"
    assert _status("run-2") == "cancelled"
    assert _queue() == {"active": "run-1", "queue": []}
    seq = _transitions("run-2")
    assert seq.count("cancelled") == 1 and "running" not in seq


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_cancel_the_paused_run_releases_the_slot_and_advances_the_fifo(
    make_git_repo, isolated_state_home
):
    """Cancelling the current paused run releases the worker to the next queued run and writes a terminal record with one cancelled event.

    technical (contract): with active run-1 (paused) and queue ["run-2"], engine.cancel("run-1")
    -> run-1 status "cancelled" with exactly one "cancelled" event; the slot is released and run-2
    auto-advances (runs its stub to "completed"); queue ends {"active":None,"queue":[]}.
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)

    engine.cancel("run-1")
    assert _status("run-1") == "cancelled"
    assert _transitions("run-1").count("cancelled") == 1
    assert _status("run-2") == "completed"
    assert _queue() == {"active": None, "queue": []}


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
@pytest.mark.parametrize("state_name", ["parked", "completed", "cancelled", "failed"])
def test_cancel_refuses_a_non_queued_non_paused_run(state_name, make_git_repo, isolated_state_home):
    """Cancel is only for a queued run or the current paused run; a parked or terminal (completed/cancelled/failed) run is refused with the record, queue, and events unchanged.

    technical (contract): a run driven to {parked, completed, cancelled, failed} cancelled via
    engine.cancel(<run>) raises issueforge.state.IllegalTransition; the run's status, the queue, and
    the run's event stream are all unchanged (no additional "cancelled" event appended). (The active
    running run is covered separately.)
    """
    from issueforge import engine
    from issueforge.state import IllegalTransition

    _register(make_git_repo)
    _drive_to(engine, state_name, make_git_repo=make_git_repo)

    before_status, before_queue, before_events = _status("run-x"), _queue(), _transitions("run-x")
    with pytest.raises(IllegalTransition):
        engine.cancel("run-x")
    assert _status("run-x") == before_status
    assert _queue() == before_queue
    assert _transitions("run-x") == before_events


# ---------------------------------------------------------------------------
# Pause / park: the two exits from the worker-slot state
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_pause_blocks_the_worker_and_keeps_the_slot(make_git_repo, isolated_state_home):
    """A paused run keeps the worker slot, so a later run enqueues rather than starting; pause returns the paused record.

    technical (contract): _admit_paused("run-1") returns a record with status "paused";
    queue["active"] == "run-1"; the transition stream is exactly ["queued","running","paused"] (one
    paused event, no more); a second issue then LANDS queued (status "queued", queue["queue"] ==
    ["run-2"], transitions ["queued"] only) because the slot is held.
    """
    from issueforge import store

    _register(make_git_repo)
    result = _admit_paused(make_git_repo, "run-1")

    assert result["status"] == "paused"
    assert store.RunStore().read("run-1")["status"] == "paused"
    assert _queue()["active"] == "run-1"
    assert _transitions("run-1") == ["queued", "running", "paused"]

    _enqueue("run-2", 149)
    assert _status("run-2") == "queued"
    assert _queue()["queue"] == ["run-2"]
    assert _transitions("run-2") == ["queued"]


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_park_from_paused_preserves_every_other_field_and_releases_the_worker(
    make_git_repo, isolated_state_home
):
    """Parking a paused run preserves every record field except the status, and releases the worker to the next queued run.

    technical (contract): seed run-1 (paused) with representative fields
    {"approvals":["matt"],"verdicts":{"codex":"ship"},"attempts":2,"pr":{"number":7}}; snapshot the
    record minus "status"; engine.park("run-1") returns status "parked" with every other field
    byte-equal to the snapshot; the slot is released and a queued run-2 auto-advances to "completed";
    queue ends {"active":None,"queue":[]}.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    store.RunStore().apply(
        "run-1",
        lambda r: {"approvals": ["matt"], "verdicts": {"codex": "ship"}, "attempts": 2,
                   "pr": {"number": 7}},
    )
    before = store.RunStore().read("run-1")
    before_no_status = {k: v for k, v in before.items() if k != "status"}

    result = engine.park("run-1")
    assert result["status"] == "parked"
    after = store.RunStore().read("run-1")
    assert after["status"] == "parked"
    assert {k: v for k, v in after.items() if k != "status"} == before_no_status
    assert _transitions("run-1") == ["queued", "running", "paused", "parked"]
    assert _status("run-2") == "completed"
    assert _queue() == {"active": None, "queue": []}


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_a_stage_that_parks_its_running_run_is_honored_not_overwritten(
    make_git_repo, isolated_state_home
):
    """When the active stage parks its own still-running run, the engine honors parked (running -> parked) and does not overwrite it with completed; the FIFO advances.

    technical (contract): admit run-1 paused with run-2 queued behind it, then
    engine.continue_run("run-1", stage=lambda rec: engine.park(rec["run_id"])) exercises
    running->park while a waiter exists -> run-1 ends "parked" (NOT overwritten to "completed") with
    transition stream exactly ["queued","running","paused","running","parked"]; run-2 auto-advances
    to "completed"; queue ends {"active":None,"queue":[]}.
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    engine.continue_run("run-1", stage=lambda rec: engine.park(rec["run_id"]))

    assert _status("run-1") == "parked"
    assert _transitions("run-1") == ["queued", "running", "paused", "running", "parked"]
    assert _status("run-2") == "completed"
    assert _queue() == {"active": None, "queue": []}


# ---------------------------------------------------------------------------
# Auto-advance dispatches the FIFO head-first
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_releasing_the_slot_dispatches_the_fifo_head_first(
    make_git_repo, isolated_state_home, monkeypatch
):
    """When the worker frees with several runs waiting, the queued runs are dispatched strictly in FIFO order, head first.

    technical (contract): with active run-1 (paused) and queue ["run-2","run-3"], monkeypatch
    engine._default_stage to append record["run_id"] to a list then complete; engine.cancel("run-1")
    drains the queue and the recorded dispatch order is exactly ["run-2","run-3"] (head before tail),
    queue ends empty.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    _enqueue("run-3", 150)

    dispatched = []

    def recording_stage(record):
        dispatched.append(record["run_id"])
        store.RunStore().append_event(record["run_id"], {"transition": "stage"})

    monkeypatch.setattr(engine, "_default_stage", recording_stage)
    engine.cancel("run-1")

    assert dispatched == ["run-2", "run-3"]
    assert _queue() == {"active": None, "queue": []}


# ---------------------------------------------------------------------------
# continue: one verb (G7), reconcile-never-heal, single-worker
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_continue_resumes_a_paused_run_recording_a_running_event_after_the_pause(
    make_git_repo, isolated_state_home
):
    """`continue` resumes a paused run: it records a running transition AFTER the paused one and drives the run to completion.

    technical (contract): _admit_paused("run-1") then engine.continue_run("run-1") -> the transition
    stream is exactly ["queued","running","paused","running","completed"] and status is "completed";
    the resume "running" event follows the "paused" event.
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    engine.continue_run("run-1")

    assert _transitions("run-1") == ["queued", "running", "paused", "running", "completed"]
    assert _status("run-1") == "completed"


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_continue_resumes_a_parked_run_with_a_free_worker(make_git_repo, isolated_state_home):
    """The same `continue` verb resumes a parked run once the worker is free, recording a running transition after the parked one.

    technical (contract): _admit_paused("run-1") then engine.park("run-1") (slot released), then
    engine.continue_run("run-1") -> transition stream is
    ["queued","running","paused","parked","running","completed"] and status "completed".
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    engine.park("run-1")
    assert _status("run-1") == "parked"

    engine.continue_run("run-1")
    assert _transitions("run-1") == [
        "queued", "running", "paused", "parked", "running", "completed",
    ]
    assert _status("run-1") == "completed"


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_continue_a_parked_run_onto_a_busy_worker_is_refused(make_git_repo, isolated_state_home):
    """A parked run cannot resume while another run holds the single worker slot; it is refused and left parked.

    technical (contract): park run-1 (slot released), then admit run-2 paused (slot held by run-2);
    engine.continue_run("run-1") raises engine.WorkerBusyError; run-1 status is still "parked" and
    queue["active"] is still "run-2".
    """
    from issueforge import engine

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    engine.park("run-1")
    _admit_paused(make_git_repo, "run-2", number=149)
    assert _queue()["active"] == "run-2"

    with pytest.raises(engine.WorkerBusyError):
        engine.continue_run("run-1")
    assert _status("run-1") == "parked"
    assert _queue()["active"] == "run-2"


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
@pytest.mark.parametrize(
    "key,record_value,github_value",
    [
        ("merged", False, True),
        ("pr", {"number": 7}, {"number": 9}),
        ("branch", "feat/a", "feat/b"),
    ],
)
def test_continue_halts_and_surfaces_a_pr_fact_divergence_without_touching_the_record(
    key, record_value, github_value, make_git_repo, isolated_state_home
):
    """When any GitHub-authoritative PR fact disagrees with the run record, `continue` halts and surfaces the diverging key instead of healing it — the run stays parked and the manifest bytes are unchanged.

    technical (contract): a parked run-1 with record[key] == record_value; engine.continue_run(
    "run-1", github_facts=lambda rid: {key: github_value}, stage=<spy>) raises engine.DivergenceError
    whose message names `key` (for key in {"merged","pr","branch"}); the reconcile happens BEFORE any
    resume so the stage spy is NEVER called; run-1 status is STILL "parked", its transition stream is
    still exactly ["queued","running","paused","parked"] (nothing appended), and
    store.manifest_path("run-1").read_bytes() is unchanged.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    store.RunStore().apply("run-1", lambda r: {key: record_value})
    engine.park("run-1")

    before_bytes = store.manifest_path("run-1").read_bytes()
    called = []

    def stage_spy(record):
        called.append(record["run_id"])

    with pytest.raises(engine.DivergenceError) as excinfo:
        engine.continue_run(
            "run-1", github_facts=lambda rid: {key: github_value}, stage=stage_spy
        )

    assert key in str(excinfo.value)
    assert called == []  # reconcile halted before the stage ever ran
    assert _status("run-1") == "parked"
    assert _transitions("run-1") == ["queued", "running", "paused", "parked"]
    assert store.manifest_path("run-1").read_bytes() == before_bytes


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_continue_never_lets_github_facts_overwrite_any_gate_artifact_and_still_resumes(
    make_git_repo, isolated_state_home
):
    """The run record stays authoritative for every gate artifact: a clean `continue` resumes and completes, and none of approvals/verdicts/attempts is overwritten by a conflicting github value.

    technical (contract): a parked run-1 with approvals=["matt"], verdicts={"codex":"ship"},
    attempts=3 and merged=None; engine.continue_run("run-1", github_facts=lambda rid: {"approvals":
    [], "verdicts": {}, "attempts": 0, "merged": None}) resumes cleanly (merged agrees) to
    "completed"; the resumed record still has approvals==["matt"], verdicts=={"codex":"ship"},
    attempts==3 (the github gate-artifact values were ignored, not written).
    """
    from issueforge import engine, store

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    store.RunStore().apply(
        "run-1",
        lambda r: {"approvals": ["matt"], "verdicts": {"codex": "ship"}, "attempts": 3,
                   "merged": None},
    )
    engine.park("run-1")

    engine.continue_run(
        "run-1",
        github_facts=lambda rid: {"approvals": [], "verdicts": {}, "attempts": 0, "merged": None},
    )

    record = store.RunStore().read("run-1")
    assert record["status"] == "completed"
    assert record["approvals"] == ["matt"]
    assert record["verdicts"] == {"codex": "ship"}
    assert record["attempts"] == 3


# ---------------------------------------------------------------------------
# US-9.3 crash recovery (real cross-process kill + continue)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_kill9_mid_run_then_continue_in_a_fresh_process_resumes_from_persisted_state(
    make_git_repo, isolated_state_home, tmp_path
):
    """Killing the worker process (kill -9) mid-run loses nothing: a separate process `continue`s the run from its persisted state, the event stream replays intact, and recorded transitions are not duplicated.

    technical (contract): a child process starts run-crash with a stage that writes a "ready" marker
    then blocks forever; the parent SIGKILLs it once the marker appears. On disk run-crash has a
    manifest and an events stream whose transitions begin ["queued","running"]. A SECOND, fresh
    process then runs engine.continue_run("run-crash"); afterward store.read("run-crash")["status"]
    == "completed", replay_events parses every physical line without error, and the transition stream
    is exactly ["queued","running","completed"] — the pre-crash queued/running events are NOT
    duplicated (continue re-derived state, it did not restart from scratch).
    """
    from issueforge import store

    _register(make_git_repo)
    ready = tmp_path / "ready.marker"
    blocking_child = textwrap.dedent(
        f"""
        import signal
        def stage(record):
            open({str(ready)!r}, "w").write("ready")
            signal.pause()
        engine.run("DandD#148", issue_open=lambda s, n: True,
                   new_run_id=lambda: "run-crash", stage=stage)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from issueforge import engine, store\n" + blocking_child],
        env={**os.environ, "ISSUEFORGE_STATE_HOME": os.environ["ISSUEFORGE_STATE_HOME"]},
    )
    try:
        deadline = time.time() + 30
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "child never reached the blocking stage"
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

        assert _transitions("run-crash")[:2] == ["queued", "running"]

        resumed = _child(
            os.environ["ISSUEFORGE_STATE_HOME"],
            """
            engine.continue_run("run-crash")
            print(store.RunStore().read("run-crash")["status"])
            """,
        )
        assert resumed.returncode == 0, resumed.stderr
        assert resumed.stdout.strip() == "completed"
        assert _status("run-crash") == "completed"
        # A clean replay proves no torn/malformed middle line; the sequence proves no duplicates.
        assert store.RunStore().replay_events("run-crash")
        assert _transitions("run-crash") == ["queued", "running", "completed"]
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# Typed stage failure -> failed terminal state
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_a_typed_stage_failure_lands_the_run_in_failed_records_the_type_and_advances(
    make_git_repo, isolated_state_home
):
    """A stage failure is a typed result carrying the stage's own failure type; it lands the run in the terminal `failed` state (recording that type, not a shared string), and the worker advances to the next queued run.

    technical (contract): engine.StageResult and engine.StageFailure exist with
    issubclass(engine.StubStageFailure, engine.StageFailure); a stage returning
    engine.StageResult(status=State.FAILED, failure=engine.StubStageFailure("boom")) for run-f (with
    run-2 queued behind it) -> run-f status "failed" with a "failed" transition event and
    record["failure"]["type"] == "StubStageFailure" (derived from the instance's own type); run-2
    auto-advances to "completed"; queue ends {"active":None,"queue":[]}.
    """
    from issueforge import engine, store
    from issueforge.state import State

    _register(make_git_repo)
    assert issubclass(engine.StubStageFailure, engine.StageFailure)

    _admit_paused(make_git_repo, "run-f")
    _enqueue("run-2", 149)

    engine.continue_run(
        "run-f",
        stage=lambda rec: engine.StageResult(
            status=State.FAILED, failure=engine.StubStageFailure("boom")
        ),
    )

    record = store.RunStore().read("run-f")
    assert record["status"] == "failed"
    assert record["failure"]["type"] == "StubStageFailure"
    assert _transitions("run-f") == ["queued", "running", "paused", "running", "failed"]
    assert _status("run-2") == "completed"
    assert _queue() == {"active": None, "queue": []}


# ---------------------------------------------------------------------------
# CLI parity: the named verbs are wired to the engine
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_cli_queue_lists_the_active_run_then_the_fifo_in_order(make_git_repo, isolated_state_home):
    """The `issueforge queue` command lists the active run and then the queued runs in FIFO order.

    technical (contract): with active run-1 (paused) and queue ["run-2","run-3"], CliRunner invoking
    ["queue"] exits 0 and its stdout names run-1, run-2, run-3 with run-1 before run-2 before run-3.
    """
    from typer.testing import CliRunner

    from issueforge.cli import app

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    _enqueue("run-3", 150)

    result = CliRunner().invoke(app, ["queue"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)
    out = result.stdout
    assert out.index("run-1") < out.index("run-2") < out.index("run-3")


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
@pytest.mark.parametrize(
    "verb,expected_status",
    [("park", "parked"), ("cancel", "cancelled")],
)
def test_cli_verbs_forward_to_the_engine_and_change_persisted_state(
    verb, expected_status, make_git_repo, isolated_state_home
):
    """The `issueforge park` and `issueforge cancel` commands forward to the engine and change the run's persisted state.

    technical (contract): with run-1 paused (holding the slot), CliRunner invoking [verb, "run-1"]
    for verb in {"park","cancel"} exits 0 and store.read("run-1")["status"] becomes the matching
    terminal/parked status ("parked" / "cancelled").
    """
    from typer.testing import CliRunner

    from issueforge.cli import app

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")

    result = CliRunner().invoke(app, [verb, "run-1"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)
    assert _status("run-1") == expected_status


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_cli_continue_resumes_a_paused_run(make_git_repo, isolated_state_home):
    """The `issueforge continue` command resumes a paused run to completion.

    technical (contract): with run-1 paused, CliRunner invoking ["continue", "run-1"] exits 0 and
    store.read("run-1")["status"] == "completed", with a resume "running" event after the "paused".
    """
    from typer.testing import CliRunner

    from issueforge.cli import app

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")

    result = CliRunner().invoke(app, ["continue", "run-1"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)
    assert _status("run-1") == "completed"
    assert _transitions("run-1") == ["queued", "running", "paused", "running", "completed"]


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_cli_pause_pauses_the_active_running_run_and_keeps_the_slot(
    make_git_repo, isolated_state_home
):
    """The `issueforge pause` command pauses the active running run: its status becomes paused, a paused event is recorded, and the worker slot is retained.

    technical (contract): with run-1 admitted via a stage that invokes CliRunner(["pause","run-1"])
    while the run is running, the command exits 0, store.read("run-1")["status"] == "paused", the
    transition stream is exactly ["queued","running","paused"], and queue["active"] is still "run-1".
    """
    from typer.testing import CliRunner

    from issueforge import engine
    from issueforge.cli import app

    _register(make_git_repo)
    results = {}

    def stage(record):
        # Pause the active RUNNING run through the CLI verb, mid-stage.
        results["exit"] = CliRunner().invoke(app, ["pause", record["run_id"]]).exit_code

    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1", stage=stage)

    assert results["exit"] == 0
    assert _status("run-1") == "paused"
    assert _transitions("run-1") == ["queued", "running", "paused"]
    assert _queue()["active"] == "run-1"


@pytest.mark.xfail(strict=True, reason="PENDING (#9)")
def test_cli_reorder_moves_a_queued_run(make_git_repo, isolated_state_home):
    """The `issueforge reorder` command moves a queued run to the given index.

    technical (contract): with active run-1 and queue ["run-2","run-3"], CliRunner invoking
    ["reorder", "run-3", "0"] exits 0 and store.read_queue()["queue"] == ["run-3","run-2"].
    """
    from typer.testing import CliRunner

    from issueforge.cli import app

    _register(make_git_repo)
    _admit_paused(make_git_repo, "run-1")
    _enqueue("run-2", 149)
    _enqueue("run-3", 150)

    result = CliRunner().invoke(app, ["reorder", "run-3", "0"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)
    assert _queue()["queue"] == ["run-3", "run-2"]
