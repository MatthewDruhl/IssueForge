"""The run state machine: a hand-rolled State enum + a table of legal edges (issue #9, S5).

No state-machine library. The whole machine is three module constants and one guard:

- ``State`` — the seven run states, each mapping to its persisted status string.
- ``TRANSITIONS`` — ``dict[State, set[State]]`` naming exactly the legal next states.
- ``TERMINAL`` — the states with no outgoing edge.
- ``transition(current, target)`` — returns ``target`` iff the table allows the edge, else raises
  ``IllegalTransition``.
"""

from __future__ import annotations

import enum


class State(enum.Enum):
    """The run lifecycle states; ``.value`` is the persisted status string."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    PARKED = "parked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


TRANSITIONS: dict[State, set[State]] = {
    State.QUEUED: {State.RUNNING, State.CANCELLED},
    State.RUNNING: {State.PAUSED, State.PARKED, State.COMPLETED, State.FAILED},
    State.PAUSED: {State.RUNNING, State.PARKED, State.CANCELLED},
    State.PARKED: {State.RUNNING},
    State.COMPLETED: set(),
    State.CANCELLED: set(),
    State.FAILED: set(),
}

TERMINAL: set[State] = {State.COMPLETED, State.CANCELLED, State.FAILED}


class IllegalTransition(Exception):
    """Raised when a transition is not one of the edges declared in ``TRANSITIONS``."""


def transition(current: State, target: State) -> State:
    """Return ``target`` if ``current -> target`` is a declared edge, else raise IllegalTransition."""
    if target in TRANSITIONS[current]:
        return target
    raise IllegalTransition(f"illegal transition {current.value!r} -> {target.value!r}")
