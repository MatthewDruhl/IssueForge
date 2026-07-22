"""S9 buildability shaping: derive, confirm, and human-approve a run's build contract (issue #13).

``run_shaping`` drives the S9 stage for one run and persists every outcome through the S4
``store.RunStore`` — the shape artifact is a plain dict on the run record (``record["shape"]``) plus
an append-only event stream, never an invented ``contract.py`` object. Every halt is OBSERVABLE run
state the store already accepts, never a new exception class or status token:

- REFUSAL — an invalid contract, an out-of-set observability verdict, or an unconfirmed verdict:
  run status ``failed`` + a ``shape`` event ``outcome="refused"``; nothing is minted.
- PAUSE — an open duplicate, an unresolved design decision, an unknown footprint, or a human
  rejection: run status ``paused`` + a ``shape`` event ``outcome="paused"`` with a ``reason`` token;
  nothing is minted.
- HALT-WITH-SHAPE — an ``oversized`` (decomposition) or ``blocked`` (not-testable) classification:
  the shape IS recorded with its classification, but the run is left ``paused`` and never proceeds to
  authoring; the reviewer and human gate are not run.

The observability justification is confirmed ONLY by a fresh secondary-role review that structurally
confirms the EXACT verdict, justification, and duplicate verdict; a bare OK is not confirmation and a
True human approval can never substitute for a missing confirmation (the reviewer runs before the
approver, and the approver is unreachable until a confirmation lands). Independence is enforced
through ``providers.validate_review_independence`` (an equal reviewer session raises
``SharedSessionError``). No GitHub write occurs on any branch: the only ``github`` access is the
read-only ``search_open_issues`` dedup seam.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

from issueforge import store
from issueforge.providers import InvocationStatus, validate_review_independence
from issueforge.state import State

VALID_VERDICTS = {"required", "existing coverage sufficient", "not applicable"}
VALID_OPS = {"add", "modify", "delete", "rename"}
VALID_CLASSIFICATIONS = {"buildable", "oversized", "blocked"}

# A non-confirming reviewer is retried once with a fresh session before the run is refused.
_MAX_REVIEW_ATTEMPTS = 2


def run_shaping(
    run_id: str,
    issue: dict,
    *,
    assessor: Callable[[dict], dict],
    reviewer: Callable[[dict], Any],
    approver: Callable[[dict], bool],
    github: Any,
    authoring: Callable[..., Any] | None = None,
) -> dict:
    """Drive S9 shaping for ``run_id``, persisting every outcome to the run store.

    Creates the run record, derives the contract via ``assessor``, and routes it to exactly one
    observable outcome (see the module docstring). ``authoring`` is accepted but MUST NOT be called
    during shaping. Returns the final run record.
    """
    store.RunStore().apply(
        run_id,
        lambda _r: {"run_id": run_id, "status": State.QUEUED.value},
        create=True,
    )
    return _shape(
        run_id,
        issue,
        assessor=assessor,
        reviewer=reviewer,
        approver=approver,
        github=github,
        version=1,
    )


def amend(
    run_id: str,
    issue: dict,
    *,
    assessor: Callable[[dict], dict],
    reviewer: Callable[[dict], Any],
    approver: Callable[[dict], bool],
    github: Any,
    authoring: Callable[..., Any] | None = None,
) -> dict:
    """Recompute the contract into a new monotonic version with fresh confirmation and approval.

    A DISTINCT S9 transition over an existing run: appends version N+1 while every prior version's
    ``shape`` and ``approval`` events remain in the append-only log. The fresh confirmation is
    non-overridable and the human approval is renewed exactly as in :func:`run_shaping`.
    """
    prior = store.RunStore().read(run_id).get("shape") or {}
    next_version = int(prior.get("version", 0)) + 1
    return _shape(
        run_id,
        issue,
        assessor=assessor,
        reviewer=reviewer,
        approver=approver,
        github=github,
        version=next_version,
    )


# --- S20 (#14): propose the in-place revision for a buildable run --------------------------------


def propose_revision(run_id: str, issue: dict, *, reviser: Callable[[dict, dict], str]) -> dict:
    """Propose (never write) the in-place revision for a buildable, approved run (US-3.1).

    Reads the run's PERSISTED buildable ``shape`` and hands it, with the issue, to ``reviser`` — which
    returns the AI-authored new body — then returns ``{"plan": [<update_body op>]}`` carrying a
    repo-qualified ``update_body`` op for the run's issue. NO GitHub write occurs here (planning only).
    Raises ``ValueError`` for a run that is missing, or not cleared ``buildable`` by S9 (blocked,
    oversized, paused): the in-place revision is offered ONLY for a buildable, human-approved run.
    """
    try:
        record = store.RunStore().read(run_id)
    except KeyError as error:
        raise ValueError(f"cannot revise unknown run {run_id!r}") from error

    shape = record.get("shape")
    buildable = (
        record.get("status") == State.RUNNING.value
        and isinstance(shape, dict)
        and shape.get("classification") == "buildable"
    )
    if not buildable:
        raise ValueError(f"cannot revise {run_id!r}: only a buildable, approved run can be revised")

    new_body = reviser(issue, shape)
    owner, _, repo = issue["slug"].partition("/")
    plan = [
        {
            "op": "update_body",
            "id": f"{run_id}-revision-body",
            "issue": (owner, repo, issue["number"]),
            "body": new_body,
        }
    ]
    return {"plan": plan}


# --- the shared S9 pipeline (the record already exists) ------------------------------------------


def _shape(
    run_id: str,
    issue: dict,
    *,
    assessor: Callable[[dict], dict],
    reviewer: Callable[[dict], Any],
    approver: Callable[[dict], bool],
    github: Any,
    version: int,
) -> dict:
    proposal = assessor(issue)

    # 1) Structure — an invalid contract is refused, never defaulted; nothing is minted.
    if _structural_error(proposal, issue) is not None:
        return _refuse(run_id)

    # 2) Dedup — open duplicate work pauses before anything is minted (reviewer/approver unreached).
    if github.search_open_issues(issue):
        return _pause(run_id, "duplicate")

    classification = proposal["classification"]

    # 3) Non-buildable classifications record their shape and stop short of authoring.
    if classification in ("oversized", "blocked"):
        shape = _build_shape(proposal, version, review=None)
        return _halt_with_shape(run_id, shape, version)

    # 4) Pause conditions (US-3.4) halt the buildable path before review or approval.
    if proposal["unresolved_design_decisions"]:
        return _pause(run_id, "unresolved_design_decision")
    if proposal["footprint_known"] is False:
        return _pause(run_id, "unknown_footprint")

    # 5) Confirmation — a fresh secondary review must structurally confirm the exact verdict.
    confirmed = _confirm(run_id, proposal, reviewer)
    if confirmed is None:
        return _refuse(run_id)

    # 6) Human gate — reachable ONLY after confirmation; a rejection pauses, mints nothing.
    shape = _build_shape(proposal, version, review=confirmed)
    if not approver(shape):
        return _pause(run_id, "human_rejected")

    return _mint(run_id, shape, version)


def _confirm(run_id: str, proposal: dict, reviewer: Callable[[dict], Any]):
    """Return the confirming review, or ``None`` if no fresh review confirms within the attempts.

    Each attempt records a ``review`` event bound to its session, verifies independence from the
    authoring session (raising ``SharedSessionError`` on an equal session), and confirms only on an
    exact structural match of verdict + justification + duplicate verdict under a secondary role.
    """
    authoring_ref = types.SimpleNamespace(session_id=proposal.get("authoring_session_id"))
    for _ in range(_MAX_REVIEW_ATTEMPTS):
        review = reviewer(proposal)
        result = review.result
        confirms = _review_confirms(review, proposal)
        store.RunStore().append_event(
            run_id,
            {"transition": "review", "session_id": result.session_id, "confirmed": confirms},
        )
        validate_review_independence(result, authoring_ref)  # raises on an equal session
        if confirms:
            return review
    return None


def _review_confirms(review: Any, proposal: dict) -> bool:
    """True iff a fresh secondary review structurally confirms the exact observability verdict."""
    obs = proposal["observability"]
    result = review.result
    return (
        result.status == InvocationStatus.OK
        and result.role == "review"
        and review.verdict == obs["verdict"]
        and review.justification == obs["justification"]
        and review.duplicate_verdict == proposal["duplicate"]["verdict"]
    )


# --- shape artifact + observable outcomes --------------------------------------------------------


def _build_shape(proposal: dict, version: int, *, review: Any) -> dict:
    """Assemble the complete shape dict persisted on the run record (and passed to the approver)."""
    obs = proposal["observability"]
    if review is not None:
        review_block = {
            "authoring_session_id": proposal.get("authoring_session_id"),
            "reviewer_session_id": review.result.session_id,
            "confirmed": True,
            "duplicate_confirmed": review.duplicate_verdict == proposal["duplicate"]["verdict"],
        }
    else:
        review_block = {
            "authoring_session_id": proposal.get("authoring_session_id"),
            "reviewer_session_id": None,
            "confirmed": False,
            "duplicate_confirmed": False,
        }
    return {
        "version": version,
        "classification": proposal["classification"],
        "blocked_reason": proposal.get("blocked_reason"),
        "duplicate": proposal["duplicate"],
        "unresolved_design_decisions": proposal["unresolved_design_decisions"],
        "write_scope": proposal["write_scope"],
        "footprint_known": proposal["footprint_known"],
        "seams": proposal.get("seams", []),
        "observability": {
            "verdict": obs["verdict"],
            "justification": obs["justification"],
            "events": obs.get("events", []),
            "prohibited_sensitive_fields": obs.get("prohibited_sensitive_fields", []),
            "review": review_block,
        },
    }


def _refuse(run_id: str) -> dict:
    s = store.RunStore()
    record = s.apply(run_id, lambda _r: {"status": State.FAILED.value})
    s.append_event(run_id, {"transition": "shape", "outcome": "refused"})
    return record


def _pause(run_id: str, reason: str) -> dict:
    s = store.RunStore()
    record = s.apply(run_id, lambda _r: {"status": State.PAUSED.value})
    s.append_event(run_id, {"transition": "shape", "outcome": "paused", "reason": reason})
    return record


def _halt_with_shape(run_id: str, shape: dict, version: int) -> dict:
    """Record a non-buildable (oversized/blocked) shape and leave the run paused, unable to author."""
    s = store.RunStore()
    record = s.apply(run_id, lambda _r: {"status": State.PAUSED.value, "shape": shape})
    s.append_event(
        run_id,
        {
            "transition": "shape",
            "outcome": "halted",
            "version": version,
            "classification": shape["classification"],
            "observability": shape["observability"],
        },
    )
    return record


def _mint(run_id: str, shape: dict, version: int) -> dict:
    """Persist a confirmed, human-approved buildable shape and record its shape + approval events."""
    s = store.RunStore()
    record = s.apply(run_id, lambda _r: {"status": State.RUNNING.value, "shape": shape})
    s.append_event(
        run_id,
        {
            "transition": "shape",
            "outcome": "emitted",
            "version": version,
            "classification": shape["classification"],
            "observability": shape["observability"],
        },
    )
    s.append_event(run_id, {"transition": "approval", "version": version, "approved": True})
    return record


# --- structural validation (returns an error token, or None when the contract is well-formed) ----


def _structural_error(proposal: Any, issue: dict) -> str | None:
    if not isinstance(proposal, dict):
        return "not_a_dict"
    for key in (
        "classification",
        "duplicate",
        "unresolved_design_decisions",
        "write_scope",
        "footprint_known",
        "observability",
    ):
        if key not in proposal:
            return f"missing:{key}"

    classification = proposal["classification"]
    if classification not in VALID_CLASSIFICATIONS:
        return "classification_unknown"
    blocked_reason = proposal.get("blocked_reason")
    if classification == "blocked":
        if not blocked_reason:
            return "blocked_reason_missing"
    elif blocked_reason is not None:
        return "blocked_reason_present"

    obs_error = _observability_error(proposal["observability"])
    if obs_error is not None:
        return obs_error

    scope_error = _write_scope_error(proposal["write_scope"], issue.get("contract_paths", []))
    if scope_error is not None:
        return scope_error

    if proposal.get("testability") == "requires_seam" and not proposal.get("seams"):
        return "seams_required"
    return None


def _observability_error(obs: Any) -> str | None:
    if not isinstance(obs, dict):
        return "observability_shape"
    if "verdict" not in obs:
        return "verdict_missing"
    if obs["verdict"] not in VALID_VERDICTS:
        return "verdict_unknown"
    justification = obs.get("justification")
    if not isinstance(justification, str) or not justification.strip():
        return "justification_missing"
    if obs["verdict"] == "required":
        if not obs.get("events"):
            return "events_missing"
        if not obs.get("prohibited_sensitive_fields"):
            return "sensitive_fields_missing"
    return None


def _write_scope_error(scope: Any, contract_paths: list[str]) -> str | None:
    if not isinstance(scope, list) or not scope:
        return "empty_footprint"
    ops_by_path: dict[str, set] = {}
    for entry in scope:
        if not isinstance(entry, dict):
            return "scope_entry_shape"
        op = entry.get("op")
        if op not in VALID_OPS:
            return "unknown_op"
        justification = entry.get("justification")
        if not isinstance(justification, str) or not justification.strip():
            return "entry_justification"
        if op == "rename":
            source, destination = entry.get("source_path"), entry.get("destination_path")
            if not source or not destination:
                return "rename_operands"
            entry_paths = [source, destination]
        else:
            path = entry.get("path")
            if not isinstance(path, str) or not path.strip():
                return "empty_path"
            entry_paths = [path]
            ops_by_path.setdefault(path, set()).add(op)
        for path in entry_paths:
            if _under_contract(path, contract_paths):
                return "frozen_contract_path"
    for ops in ops_by_path.values():
        if "add" in ops and "delete" in ops:
            return "contradictory_ops"
    return None


def _under_contract(path: str, contract_paths: list[str]) -> bool:
    """True when ``path`` falls under any frozen acceptance-contract location (D5)."""
    for contract in contract_paths:
        prefix = contract if contract.endswith("/") else contract + "/"
        if path == contract or path.startswith(prefix):
            return True
    return False
