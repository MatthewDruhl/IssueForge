"""The post-implementation repair policy seam (issue #20, S14).

D4 (``docs/prd.md:182``) splits the single recovery budget into TWO independently configurable
budgets that recover from OPPOSITE failures:

- ``review_rounds`` — the independent review raised blocking findings; the implementer fixes them
  IN PLACE (the worktree is preserved).
- ``repair_attempts`` — the implementer failed/died, or the acceptance suite is still red after
  "done"; the attempt is a WRITE-OFF: reset the worktree to the frozen contract commit and
  redispatch a FRESH session with the contract + a compact trace, NEVER the prior transcript.

Both counters are persisted run state incremented INSIDE the store lock (``RunStore.apply``); the
engine gates on them and exhausting EITHER pauses the run with a schema-valid terminal record. This
module owns only the mechanical, assertable parts; the engine (``run_candidate_with_repair``) wires
them into the loop. The ``review_rounds`` -> preserve routing trigger is S15/#21 (deferred to #214);
only the COUNTER lives here.
"""

from __future__ import annotations

from issueforge import store
from issueforge.state import State


def next_action(attempt: int, cap: int = 2) -> str:
    """``"retry"`` while ``attempt`` is within ``cap``, else ``"escalate"``.

    ``attempt == cap`` is the last retry; ``attempt == cap + 1`` is the first escalate. ``cap=0``
    escalates on the very first attempt (no retry at all). Each budget defaults to 2 (US-6.2) and is
    a parameter so the two budgets tune independently.
    """
    return "retry" if attempt <= cap else "escalate"


def build_retry_prompt(trace: str, contract: str, transcript: str | None = None) -> str:
    """The write-off retry dispatch prompt: carries the frozen ``contract`` (the committed acceptance
    tests a retry must still satisfy) and the prior attempt's compact ``trace``, and DELIBERATELY
    omits ``transcript`` — re-seeding a fresh session with the prior session's churn is the exact
    context-rot the write-off loop exists to avoid, so the argument is accepted and dropped.
    """
    return (
        "A prior implementation attempt did not satisfy the frozen acceptance contract; retry from a "
        "freshly reset worktree.\n"
        "FROZEN CONTRACT START\n"
        f"{contract}\n"
        "FROZEN CONTRACT END\n"
        "PRIOR ATTEMPT TRACE START\n"
        f"{trace}\n"
        "PRIOR ATTEMPT TRACE END\n"
    )


def _bump(run_id: str, field: str) -> int:
    """Increment ``field`` by ONE, cumulatively, THROUGH the under-lock ``RunStore.apply``.

    The existing value must be an actual non-negative ``int`` (never ``bool``/``float``/``str``): a
    lax ``int(existing)`` would launder ``True`` -> 2 or ``10.9`` -> 11 into a valid-looking counter.
    Fail loud so a forged counter cannot survive the lock.
    """

    def _transform(record: dict) -> dict:
        current = record.get(field, 0)
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise TypeError(f"{field} must be a non-negative int, got {current!r}")
        return {field: current + 1}

    merged = store.RunStore().apply(run_id, _transform)
    return merged[field]


def record_repair_attempt(run_id: str) -> int:
    """``++repair_attempts`` under the store lock; return the new count."""
    return _bump(run_id, "repair_attempts")


def record_review_round(run_id: str) -> int:
    """``++review_rounds`` under the store lock; return the new count."""
    return _bump(run_id, "review_rounds")


def pause_exhausted(run_id: str, budget: str, notes: str) -> dict:
    """Exhausting ``budget`` PAUSES the run with a schema-valid terminal record: status -> the real
    ``paused`` State, the exhausted budget identity recorded on the record, and the accumulated
    ``notes`` attached (appended to any prior notes). Returns the persisted record dict.
    """

    def _transform(record: dict) -> dict:
        prior = record.get("notes", "") or ""
        combined = f"{prior}\n{notes}" if prior else notes
        return {
            "status": State.PAUSED.value,
            "exhausted_budget": budget,
            "notes": combined,
        }

    return store.RunStore().apply(run_id, _transform)
