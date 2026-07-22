"""Committed PENDING acceptance suite for #13 (S9) — the buildability contract + human approval.

``issueforge.shaper`` and the S9 engine seams do not exist yet, so each test is
``xfail(strict=True)`` (AttributeError/ImportError while pending). Dual-layer docstrings: a
plain-English behavior line first, then the ``technical (contract):`` golden values.

Governing design: assert OBSERVABLE behavior through the REAL seams, never invented internal
APIs or run-status string tokens. The shape artifact is a plain dict persisted on the run record
(``record["shape"]``) plus append-only events; every outcome is read back through
``store.RunStore().read()`` / ``replay_events()``. Refusals and pauses are observable RUN STATE
(``state.State`` values the store already accepts: ``failed`` / ``paused``) plus a reason event,
not new exception classes. The only reused exceptions are ``providers.SharedSessionError`` (a
reviewer session equal to the authoring session) and ``state.IllegalTransition`` (the transition
table refusing authoring).

The S9 interface this suite authors (ATDD):

``issueforge.shaper``:
- ``run_shaping(run_id, issue, *, assessor, reviewer, approver, github, authoring=None)`` — drives
  S9 shaping for a run, persisting all outcomes to ``RunStore``. Seams:
    * ``assessor(issue)`` -> the AI-proposed contract as a dict (validated, never trusted).
    * ``reviewer(proposal)`` -> a fresh secondary-role review exposing ``.result``
      (``providers.AIResult`` for session identity + status), ``.verdict``, ``.justification``,
      ``.duplicate_verdict``; called repeatedly until a fresh secondary session STRUCTURALLY
      confirms the exact observability verdict + justification + duplicate verdict, or attempts
      are exhausted. A bare ``AIResult(status=OK)`` is NOT confirmation.
    * ``approver(shape)`` -> bool, the human gate; buildable only, AFTER confirmation. A True
      approval can NEVER substitute for a missing confirmation.
    * ``github`` -> a read-only issue seam (``search_open_issues``); ANY other attribute access is
      a bug (no GitHub write occurs in this slice).
    * ``authoring`` -> the acceptance-authoring entry point; MUST NOT be called during shaping.
  Persisted on success (buildable, confirmed, approved): ``record["shape"] = {version,
  classification, blocked_reason, duplicate, unresolved_design_decisions, write_scope,
  footprint_known, seams, observability{verdict, justification, events,
  prohibited_sensitive_fields, review{authoring_session_id, reviewer_session_id, confirmed,
  duplicate_confirmed}}}`` ; a ``shape`` event per version; a ``review`` event per reviewer
  attempt; an ``approval`` event per human decision.
  Halt outcomes (observable): PAUSE (run status ``paused`` + a ``shape`` event with
  ``outcome="paused"`` and a ``reason`` token) for the US-3.4 pause conditions and human
  rejection; REFUSAL (run status ``failed`` + a ``shape`` event with ``outcome="refused"``) for an
  invalid contract or an unconfirmed observability verdict; nothing is minted and authoring is
  unreachable. Raises ``providers.SharedSessionError`` on an equal reviewer session.
- ``amend(run_id, issue, *, assessor, reviewer, approver, github, authoring=None)`` — a DISTINCT
  S9 transition: recompute, FRESH non-overridable confirmation, RENEWED human approval; appends
  version N+1; every prior version stays in the append-only event log.

``issueforge.engine``:
- ``enter_authoring(run_id, *, revision_applied)`` — the real authoring-entry path; a documented
  guard over ``record["shape"]``. Raises ``state.IllegalTransition`` unless the run is
  ``buildable`` AND ``revision_applied`` is True, recording NO progression event on the illegal
  path; records one ``authoring`` event on success. The implementation is free to enforce this by
  extending the ``state`` transition table with an authoring edge or by an explicit guard — the
  tests assert only the observable refusal + no-progression, not the internal mechanism.
- ``enforce_write_scope(run_id, diff_text)`` -> list of changed paths in ``diff_text`` OUTSIDE the
  STORED approved ``write_scope`` (``[]`` == within scope). Reads the stored approved scope; it is
  NEVER recomputed from the diff.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from issueforge import store
from issueforge.providers import AIResult, InvocationStatus

_AUTHOR_SESSION = "author-1"
_VERDICT = "required"
_JUST = "changed code crosses the AI boundary via providers.invoke"
_DUP = False

_ISSUE = {
    "slug": "MatthewDruhl/IssueForge",
    "number": 13,
    "body": "S9",
    "contract_paths": ["tests/"],  # the frozen acceptance-contract location (D5)
}


def _ai(session_id: str, role: str = "review", *, ok: bool = True) -> AIResult:
    return AIResult(
        role=role,
        provider="fake",
        session_id=session_id,
        prompt="p",
        stdout="",
        stderr="",
        returncode=0,
        duration_ms=1.0,
        status=InvocationStatus.OK if ok else InvocationStatus.FAILED,
        timed_out=False,
        artifact_path=Path("transcript.txt"),
    )


def _proposal(**over):
    """A complete, valid buildable proposal; override any key to make it invalid."""
    base = {
        "classification": "buildable",
        "blocked_reason": None,
        "duplicate": {"verdict": False, "evidence": []},
        "unresolved_design_decisions": [],
        "write_scope": [
            {"op": "modify", "path": "src/issueforge/shaper.py", "justification": "the shaper"},
            {
                "op": "modify",
                "path": "src/issueforge/engine.py",
                "justification": "authoring guard",
            },
        ],
        "footprint_known": True,
        "testability": "reachable",
        "seams": [],
        "observability": {
            "verdict": _VERDICT,
            "justification": _JUST,
            "events": ["shape.emitted"],
            "prohibited_sensitive_fields": ["issue_body"],
        },
        "authoring_session_id": _AUTHOR_SESSION,
    }
    base.update(over)
    return base


def _assessor(proposal):
    return lambda issue: proposal


def _review(
    session_id,
    *,
    role="review",
    verdict=_VERDICT,
    justification=_JUST,
    duplicate_verdict=_DUP,
    ok=True,
):
    """A structured secondary-role review: session identity plus what it explicitly confirms."""
    return types.SimpleNamespace(
        result=_ai(session_id, role=role, ok=ok),
        verdict=verdict,
        justification=justification,
        duplicate_verdict=duplicate_verdict,
    )


def _reviewer(*reviews):
    """A reviewer seam yielding each review in order, repeating the last; records call count."""
    seq = list(reviews) or [_review("review-1")]
    state = {"n": 0}

    def _r(proposal):
        state["n"] += 1
        return seq[min(state["n"] - 1, len(seq) - 1)]

    _r.state = state
    return _r


def _reviewer_forbidden(proposal):
    raise AssertionError("reviewer called before the contract passed pre-mint validation")


def _approver(value=True):
    calls = []

    def _a(shape):
        calls.append(shape)
        return value

    _a.calls = calls
    return _a


def _approver_forbidden(shape):
    raise AssertionError("approver called before confirmation / before validation")


def _authoring_forbidden(*a, **k):
    raise AssertionError("acceptance-test authoring must not run during shaping")


class _ReadOnlyGitHub:
    """A GitHub seam exposing ONLY the dedup search; any other access is a write-mode bug."""

    def __init__(self, open_issues=(), closed_issues=()):
        self._open = list(open_issues)
        self._closed = list(closed_issues)

    def search_open_issues(self, *a, **k):
        return list(self._open)

    def __getattr__(self, name):
        raise AssertionError(f"github write-mode access during shaping: {name!r}")


# --- observation helpers (read outcomes back through the REAL store) -----------------------------


def _rec(run_id):
    return store.RunStore().read(run_id)


def _status(run_id):
    return _rec(run_id)["status"]


def _shape(run_id):
    return _rec(run_id).get("shape")


def _events(run_id, kind):
    return [e for e in store.RunStore().replay_events(run_id) if e.get("transition") == kind]


def _last_shape_event(run_id):
    evs = _events(run_id, "shape")
    return evs[-1] if evs else None


# ================================================================================================


def test_buildability_contract_emits_in_order_before_any_authoring(isolated_state_home):
    """A buildable run emits a COMPLETE buildability contract — reviewer-confirmed and human-approved, in the order assess -> review -> approve — before any acceptance test is authored.

    technical (contract): with an order log, run_shaping("run-1", assessor, reviewer=<confirms>,
    approver=<True>, authoring=<raises if called>) records assess before review before approve and
    never authors; record["status"] == "running"; record["shape"]["version"] == 1 with every field
    populated (classification "buildable", duplicate, unresolved_design_decisions, write_scope,
    footprint_known True, observability.verdict "required" and observability.review.confirmed True);
    replay_events has a "review", a "shape", and an "approval" event and NO "authoring" event.
    """
    from issueforge import shaper

    order = []

    def assessor(issue):
        order.append("assess")
        return _proposal()

    def reviewer(proposal):
        order.append("review")
        return _review("review-1")

    def approver(shape):
        order.append("approve")
        return True

    def authoring(*a, **k):
        order.append("author")
        raise AssertionError("authoring ran during shaping")

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=assessor,
        reviewer=reviewer,
        approver=approver,
        github=_ReadOnlyGitHub(),
        authoring=authoring,
    )

    assert order == ["assess", "review", "approve"]
    assert _status("run-1") == "running"
    shape = _shape("run-1")
    assert shape["version"] == 1
    assert shape["classification"] == "buildable"
    assert shape["footprint_known"] is True
    assert shape["duplicate"]["verdict"] is False
    assert shape["write_scope"]
    assert shape["observability"]["verdict"] == "required"
    assert shape["observability"]["review"]["confirmed"] is True
    assert _events("run-1", "shape") and _events("run-1", "approval")
    assert _events("run-1", "authoring") == []


@pytest.mark.parametrize(
    "field",
    [
        "classification",
        "duplicate",
        "unresolved_design_decisions",
        "write_scope",
        "footprint_known",
        "observability",
    ],
)
def test_missing_top_level_field_refuses_without_minting(isolated_state_home, field):
    """A buildability contract missing any required top-level field is refused, not defaulted, and nothing is minted.

    technical (contract): run_shaping with a proposal that deletes `field` leaves
    record["status"] == "failed" with a "shape" event outcome "refused"; record has no "shape"
    artifact; engine.enter_authoring(run_id, revision_applied=True) raises state.IllegalTransition.
    """
    from issueforge import engine, shaper, state

    proposal = _proposal()
    del proposal[field]

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(proposal),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None
    with pytest.raises(state.IllegalTransition):
        engine.enter_authoring("run-1", revision_applied=True)


def test_missing_nested_observability_justification_refuses(isolated_state_home):
    """A missing NESTED field — the observability justification — is refused just like a missing top-level field.

    technical (contract): run_shaping with observability = {"verdict": "required", "events": [...],
    "prohibited_sensitive_fields": [...]} (no "justification") -> record["status"] == "failed",
    "shape" event outcome "refused", no minted artifact.
    """
    from issueforge import shaper

    proposal = _proposal(
        observability={
            "verdict": "required",
            "events": ["e"],
            "prohibited_sensitive_fields": ["issue_body"],
        }
    )
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(proposal),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None


@pytest.mark.parametrize(
    "override",
    [
        {"classification": "blocked", "blocked_reason": None},  # blocked WITHOUT a reason
        {
            "classification": "buildable",
            "blocked_reason": "not_testable",
        },  # buildable WITH a reason
    ],
)
def test_classification_and_blocked_reason_must_be_consistent(isolated_state_home, override):
    """The blocked_reason and classification must agree: a blocked outcome requires a reason, and a buildable outcome must not carry one.

    technical (contract): run_shaping with {classification "blocked", blocked_reason None} OR
    {classification "buildable", blocked_reason "not_testable"} -> record["status"] == "failed"
    with a "shape" event outcome "refused"; nothing minted.
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(**override)),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None


def test_missing_observability_verdict_is_a_hard_refusal_not_a_default(isolated_state_home):
    """A missing observability verdict is a hard refusal; the run never leaves S9 with a defaulted verdict.

    technical (contract): run_shaping with observability missing its "verdict" key ->
    record["status"] == "failed", "shape" event outcome "refused", no minted artifact,
    engine.enter_authoring(revision_applied=True) raises state.IllegalTransition.
    """
    from issueforge import engine, shaper, state

    proposal = _proposal(
        observability={"justification": "x", "events": [], "prohibited_sensitive_fields": []}
    )
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(proposal),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None
    with pytest.raises(state.IllegalTransition):
        engine.enter_authoring("run-1", revision_applied=True)


def test_unjustified_not_applicable_refuses_but_justified_is_accepted(isolated_state_home):
    """An observability verdict of `not applicable` with no justification is refused; the same verdict WITH a real justification (and a matching confirmation) is accepted.

    technical (contract): observability {"not applicable", justification ""} -> status "failed";
    observability {"not applicable", justification "pure in-memory refactor, no boundary crossed"}
    with a reviewer confirming that exact verdict+justification -> status "running" and
    record["shape"]["observability"]["verdict"] == "not applicable".
    """
    from issueforge import shaper

    bad = _proposal(
        observability={
            "verdict": "not applicable",
            "justification": "",
            "events": [],
            "prohibited_sensitive_fields": [],
        }
    )
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(bad),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"

    just = "pure in-memory refactor, no boundary crossed"
    good = _proposal(
        observability={
            "verdict": "not applicable",
            "justification": just,
            "events": [],
            "prohibited_sensitive_fields": [],
        }
    )
    shaper.run_shaping(
        "run-2",
        _ISSUE,
        assessor=_assessor(good),
        reviewer=_reviewer(_review("review-1", verdict="not applicable", justification=just)),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-2") == "running"
    assert _shape("run-2")["observability"]["verdict"] == "not applicable"


@pytest.mark.parametrize("verdict", ["required", "existing coverage sufficient", "not applicable"])
def test_each_legal_observability_verdict_is_accepted(isolated_state_home, verdict):
    """Each of the three legal observability verdicts is accepted on its happy path.

    technical (contract): a proposal carrying `verdict` (with events + sensitive fields when
    "required", empty otherwise) and a reviewer confirming that exact verdict -> status "running"
    and record["shape"]["observability"]["verdict"] == verdict.
    """
    from issueforge import shaper

    if verdict == "required":
        obs = {
            "verdict": verdict,
            "justification": _JUST,
            "events": ["shape.emitted"],
            "prohibited_sensitive_fields": ["issue_body"],
        }
    else:
        obs = {
            "verdict": verdict,
            "justification": "no external boundary is crossed by this change",
            "events": [],
            "prohibited_sensitive_fields": [],
        }
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(observability=obs)),
        reviewer=_reviewer(
            _review("review-1", verdict=verdict, justification=obs["justification"])
        ),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "running"
    assert _shape("run-1")["observability"]["verdict"] == verdict


def test_unknown_observability_verdict_is_refused(isolated_state_home):
    """An observability verdict outside the closed set of three is refused.

    technical (contract): observability verdict "optional" -> record["status"] == "failed" with a
    "shape" event outcome "refused"; nothing minted.
    """
    from issueforge import shaper

    proposal = _proposal(
        observability={
            "verdict": "optional",
            "justification": "x",
            "events": [],
            "prohibited_sensitive_fields": [],
        }
    )
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(proposal),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"


@pytest.mark.parametrize("drop", ["events", "prohibited_sensitive_fields"])
def test_required_verdict_needs_events_and_sensitive_fields(isolated_state_home, drop):
    """When observability is `required`, the contract must carry the important events AND the prohibited sensitive fields; either empty is a refusal.

    technical (contract): observability verdict "required" with `drop` set to [] ->
    record["status"] == "failed", "shape" event outcome "refused".
    """
    from issueforge import shaper

    obs = {
        "verdict": "required",
        "justification": _JUST,
        "events": ["shape.emitted"],
        "prohibited_sensitive_fields": ["issue_body"],
    }
    obs[drop] = []
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(observability=obs)),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"


@pytest.mark.parametrize(
    "entry",
    [
        {"op": "add", "path": "src/issueforge/new_mod.py", "justification": "a new module"},
        {"op": "modify", "path": "src/issueforge/engine.py", "justification": "wire the stage"},
        {"op": "delete", "path": "src/issueforge/stale.py", "justification": "remove a stopgap"},
        {
            "op": "rename",
            "source_path": "src/issueforge/old.py",
            "destination_path": "src/issueforge/new.py",
            "justification": "relocate",
        },
    ],
)
def test_all_four_path_operations_are_accepted(isolated_state_home, entry):
    """The write scope speaks in path operations add / modify / delete / rename, and every one is expressible.

    technical (contract): a proposal whose write_scope is [entry] with a valid entry for each op ->
    status "running"; record["shape"]["write_scope"][0]["op"] == entry["op"]; a rename entry
    preserves BOTH ends (source_path and destination_path) in the stored artifact.
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(write_scope=[entry])),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "running"
    stored = _shape("run-1")["write_scope"][0]
    assert stored["op"] == entry["op"]
    if entry["op"] == "rename":
        assert stored["source_path"] == entry["source_path"]
        assert stored["destination_path"] == entry["destination_path"]


def test_unknown_path_operation_is_refused(isolated_state_home):
    """A write-scope entry with an operation outside {add,modify,delete,rename} is refused.

    technical (contract): a write_scope entry {"op": "touch", ...} -> record["status"] == "failed"
    with a "shape" event outcome "refused".
    """
    from issueforge import shaper

    bad = _proposal(write_scope=[{"op": "touch", "path": "src/x.py", "justification": "why"}])
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(bad),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"


def test_rename_requires_both_source_and_destination(isolated_state_home):
    """A rename must name both operands; a rename missing its destination cannot be distinguished from an unapproved delete and is refused.

    technical (contract): a write_scope entry {"op": "rename", "source_path": "a.py",
    "justification": "x"} (no destination_path) -> record["status"] == "failed", "shape" event
    outcome "refused".
    """
    from issueforge import shaper

    bad = _proposal(write_scope=[{"op": "rename", "source_path": "a.py", "justification": "x"}])
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(bad),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"


@pytest.mark.parametrize(
    "scope",
    [
        [{"op": "modify", "path": "", "justification": "empty path"}],
        [{"op": "modify", "path": "src/x.py", "justification": "   "}],  # whitespace justification
        [
            {"op": "add", "path": "src/x.py", "justification": "add it"},
            {"op": "delete", "path": "src/x.py", "justification": "and delete it"},  # contradictory
        ],
    ],
)
def test_scope_entry_validation_rejects_bad_entries(isolated_state_home, scope):
    """A scope entry with an empty path, a whitespace-only justification, or a contradictory add+delete of the same path is refused.

    technical (contract): run_shaping with each malformed scope -> record["status"] == "failed",
    "shape" event outcome "refused".
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(write_scope=scope)),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"


def test_empty_footprint_is_refused(isolated_state_home):
    """A buildable contract with an empty footprint (zero paths) is refused outright — distinct from an UNKNOWN footprint, which pauses.

    technical (contract): write_scope [] -> record["status"] == "failed", "shape" event outcome
    "refused", no minted artifact.
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(write_scope=[])),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None


@pytest.mark.parametrize("bad_path", ["tests/test_shaper.py", "tests/anything.py"])
def test_scope_may_not_include_a_frozen_contract_file(isolated_state_home, bad_path):
    """The implementation write scope may never include an acceptance-contract file (D5: a path is a frozen contract input OR an implementation target, never both).

    technical (contract): with issue contract_paths ["tests/"], a write_scope entry whose path is
    under a contract path (e.g. "tests/test_shaper.py") -> record["status"] == "failed", "shape"
    event outcome "refused", nothing minted (rejected BEFORE approval).
    """
    from issueforge import shaper

    scope = [
        {"op": "modify", "path": "src/issueforge/shaper.py", "justification": "impl"},
        {"op": "modify", "path": bad_path, "justification": "edit the acceptance test"},
    ]
    approver = _approver()
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(write_scope=scope)),
        reviewer=_reviewer(),
        approver=approver,
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None
    assert approver.calls == []  # rejected before the human approval


def test_reviewer_structurally_confirms_the_exact_verdict_and_records_it_session_bound(
    isolated_state_home,
):
    """The observability justification is confirmed only by a fresh secondary-role review that explicitly confirms the EXACT verdict, justification, and duplicate verdict; the confirmation is recorded session-bound.

    technical (contract): authoring session "author-1", a reviewer whose result session is
    "review-1" and whose verdict/justification/duplicate_verdict match the proposal ->
    status "running"; record["shape"]["observability"]["review"] has authoring_session_id
    "author-1", reviewer_session_id "review-1", and confirmed True.
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-1")),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "running"
    review = _shape("run-1")["observability"]["review"]
    assert review["authoring_session_id"] == "author-1"
    assert review["reviewer_session_id"] == "review-1"
    assert review["confirmed"] is True


@pytest.mark.parametrize(
    "review",
    [
        _review("review-1", verdict="not applicable"),  # confirms the WRONG verdict
        _review("review-1", role="author"),  # not a secondary role
        _review("review-1", verdict=None, justification=None),  # says nothing
        _review("review-1", ok=False),  # the review invocation FAILED
    ],
)
def test_a_non_confirming_review_is_not_a_confirmation(isolated_state_home, review):
    """A review that confirms the wrong verdict, runs under the wrong role, says nothing, or fails is NOT a confirmation; the run cannot proceed.

    technical (contract): run_shaping with each non-confirming reviewer (repeated) -> the run does
    not reach "running"; record["status"] == "failed", "shape" event outcome "refused",
    observability never recorded confirmed, nothing minted.
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(review),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") != "running"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _shape("run-1") is None


def test_a_reviewer_session_equal_to_the_authoring_session_is_rejected(isolated_state_home):
    """A review session identical to the authoring session is rejected (independence is required).

    technical (contract): authoring session "author-1", reviewer result session "author-1" ->
    providers.SharedSessionError; the run does not reach "running".
    """
    from issueforge import shaper
    from issueforge.providers import SharedSessionError

    with pytest.raises(SharedSessionError):
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal()),
            reviewer=_reviewer(_review(_AUTHOR_SESSION)),
            approver=_approver(),
            github=_ReadOnlyGitHub(),
        )
    assert _status("run-1") != "running"


def test_reviewer_failure_is_recorded_and_retried_with_a_fresh_session(isolated_state_home):
    """A failed review is recorded and retried with ANOTHER fresh session; a later fresh confirmation lets the run proceed, and both attempts are audited.

    technical (contract): a reviewer sequence [failed session "review-a", confirmed session
    "review-b"] -> status "running"; the "review" events record BOTH sessions "review-a" and
    "review-b" in order; record["shape"]["observability"]["review"]["reviewer_session_id"] ==
    "review-b".
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-a", ok=False), _review("review-b")),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "running"
    sessions = [e.get("session_id") for e in _events("run-1", "review")]
    assert sessions[:2] == ["review-a", "review-b"]
    assert _shape("run-1")["observability"]["review"]["reviewer_session_id"] == "review-b"


def test_confirmation_is_non_overridable_by_human_approval(isolated_state_home):
    """When no fresh secondary review ever confirms, there is no path that proceeds — not even a True human approval overrides the missing confirmation.

    technical (contract): a reviewer that only ever fails (distinct sessions "review-a",
    "review-b") plus approver=<returns True> -> record["status"] == "failed", "shape" event outcome
    "refused"; there is NO "approval" event (the True approval was never used as an override) and
    nothing was minted.
    """
    from issueforge import shaper

    approver = _approver(True)
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-a", ok=False), _review("review-b", ok=False)),
        approver=approver,
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "failed"
    assert _last_shape_event("run-1")["outcome"] == "refused"
    assert _events("run-1", "approval") == []
    assert _shape("run-1") is None


def test_dedup_searches_open_issues_and_only_open_duplicates_pause(isolated_state_home):
    """Dedup runs against OPEN issues before anything is minted; open duplicate work pauses the run, closed matches do not.

    technical (contract): github.search_open_issues returning an open issue -> record["status"]
    == "paused", "shape" event outcome "paused" reason "duplicate", the reviewer and approver are
    never called, and no artifact is minted; a github returning only closed matches (no open ones)
    proceeds to "running".
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer_forbidden,
        approver=_approver_forbidden,
        github=_ReadOnlyGitHub(open_issues=[{"number": 41, "title": "same work"}]),
    )
    assert _status("run-1") == "paused"
    ev = _last_shape_event("run-1")
    assert ev["outcome"] == "paused" and ev["reason"] == "duplicate"
    assert _shape("run-1") is None

    shaper.run_shaping(
        "run-2",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(open_issues=[], closed_issues=[{"number": 7}]),
    )
    assert _status("run-2") == "running"


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"unresolved_design_decisions": ["pick a storage layout"]}, "unresolved_design_decision"),
        ({"footprint_known": False}, "unknown_footprint"),
    ],
)
def test_pause_conditions_halt_before_mint_with_a_named_reason(
    isolated_state_home, override, reason
):
    """An unresolved design decision or an unknown footprint pauses the run BEFORE review or approval and names its reason; nothing is minted.

    technical (contract): run_shaping with the pausing override plus reviewer/approver that raise
    if called -> record["status"] == "paused", "shape" event outcome "paused" with the expected
    reason token, no minted artifact.
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(**override)),
        reviewer=_reviewer_forbidden,
        approver=_approver_forbidden,
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "paused"
    ev = _last_shape_event("run-1")
    assert ev["outcome"] == "paused" and ev["reason"] == reason
    assert _shape("run-1") is None


def test_human_rejection_pauses_without_minting_an_approved_artifact(isolated_state_home):
    """A human who rejects the buildability contract pauses the run; no approved artifact is minted and authoring stays unreachable.

    technical (contract): run_shaping with approver=<returns False> -> record["status"] ==
    "paused", "shape" event outcome "paused" reason "human_rejected"; no "approval" event marks it
    approved; engine.enter_authoring(revision_applied=True) raises state.IllegalTransition.
    """
    from issueforge import engine, shaper, state

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-1")),
        approver=_approver(False),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "paused"
    ev = _last_shape_event("run-1")
    assert ev["outcome"] == "paused" and ev["reason"] == "human_rejected"
    with pytest.raises(state.IllegalTransition):
        engine.enter_authoring("run-1", revision_applied=True)


def test_oversized_stops_at_decomposition_and_cannot_author(isolated_state_home):
    """An oversized issue stops at decomposition and never proceeds to acceptance-test authoring; the halt is enforced by the transition table.

    technical (contract): classification "oversized" -> the run records classification "oversized"
    without calling the reviewer/approver; engine.enter_authoring(run_id, revision_applied=True)
    raises state.IllegalTransition and records no "authoring" event.
    """
    from issueforge import engine, shaper, state

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(classification="oversized")),
        reviewer=_reviewer_forbidden,
        approver=_approver_forbidden,
        github=_ReadOnlyGitHub(),
    )
    assert _shape("run-1")["classification"] == "oversized"
    with pytest.raises(state.IllegalTransition):
        engine.enter_authoring("run-1", revision_applied=True)
    assert _events("run-1", "authoring") == []


def test_not_testable_issue_is_blocked_with_reason_not_testable(isolated_state_home):
    """A pure-refactor / docs / research issue lands blocked with blocked_reason `not_testable`, and cannot author.

    technical (contract): classification "blocked", blocked_reason "not_testable" ->
    record["shape"]["classification"] == "blocked" and ["blocked_reason"] == "not_testable";
    engine.enter_authoring(revision_applied=True) raises state.IllegalTransition.
    """
    from issueforge import engine, shaper, state

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(classification="blocked", blocked_reason="not_testable")),
        reviewer=_reviewer_forbidden,
        approver=_approver_forbidden,
        github=_ReadOnlyGitHub(),
    )
    shape = _shape("run-1")
    assert shape["classification"] == "blocked"
    assert shape["blocked_reason"] == "not_testable"
    with pytest.raises(state.IllegalTransition):
        engine.enter_authoring("run-1", revision_applied=True)


def test_unreachable_code_requires_a_named_seam_in_the_contract(isolated_state_home):
    """When the code under test is not reachable in isolation, the contract must name the seam that makes it testable; an unnamed seam is refused.

    technical (contract): a proposal marked not-reachable (testability "requires_seam") with
    seams ["_run monkeypatch seam"] -> status "running" and record["shape"]["seams"] ==
    ["_run monkeypatch seam"]; the same proposal with seams [] -> record["status"] == "failed",
    "shape" event outcome "refused".
    """
    from issueforge import shaper

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(testability="requires_seam", seams=["_run monkeypatch seam"])),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-1") == "running"
    assert _shape("run-1")["seams"] == ["_run monkeypatch seam"]

    shaper.run_shaping(
        "run-2",
        _ISSUE,
        assessor=_assessor(_proposal(testability="requires_seam", seams=[])),
        reviewer=_reviewer(),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    assert _status("run-2") == "failed"
    assert _last_shape_event("run-2")["outcome"] == "refused"


def test_authoring_is_gated_on_the_applied_revision_via_the_transition_table(isolated_state_home):
    """A buildable run cannot enter acceptance-test authoring until the S20 in-place revision is applied; the transition table enforces it and records no progression on the illegal path.

    technical (contract): after a buildable shape, engine.enter_authoring(run_id,
    revision_applied=False) raises state.IllegalTransition and appends NO "authoring" event;
    engine.enter_authoring(run_id, revision_applied=True) does not raise and appends exactly one
    "authoring" event.
    """
    from issueforge import engine, shaper, state

    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-1")),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    with pytest.raises(state.IllegalTransition):
        engine.enter_authoring("run-1", revision_applied=False)
    assert _events("run-1", "authoring") == []

    engine.enter_authoring("run-1", revision_applied=True)
    assert len(_events("run-1", "authoring")) == 1


def test_readiness_enforces_the_stored_scope_never_recomputing_from_the_diff(isolated_state_home):
    """The scope enforced at readiness is exactly the approved scope stored at shaping, never recomputed from the resulting diff — a change touching an out-of-scope file is rejected.

    technical (contract): after approving a scope of only src/issueforge/shaper.py,
    engine.enforce_write_scope(run_id, <diff touching only shaper.py>) == []; enforce_write_scope(
    run_id, <diff touching src/issueforge/engine.py>) == ["src/issueforge/engine.py"]; and the
    stored write_scope is unchanged by either call (proving it is read, not recomputed).
    """
    from issueforge import engine, shaper

    scope = [{"op": "modify", "path": "src/issueforge/shaper.py", "justification": "the shaper"}]
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal(write_scope=scope)),
        reviewer=_reviewer(_review("review-1")),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    in_scope = (
        "diff --git a/src/issueforge/shaper.py b/src/issueforge/shaper.py\n"
        "--- a/src/issueforge/shaper.py\n+++ b/src/issueforge/shaper.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    out_scope = (
        "diff --git a/src/issueforge/engine.py b/src/issueforge/engine.py\n"
        "--- a/src/issueforge/engine.py\n+++ b/src/issueforge/engine.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    assert engine.enforce_write_scope("run-1", in_scope) == []
    assert engine.enforce_write_scope("run-1", out_scope) == ["src/issueforge/engine.py"]
    assert _shape("run-1")["write_scope"] == scope


def test_amendment_recomputes_a_new_version_with_fresh_confirmation_and_renewed_approval(
    isolated_state_home,
):
    """A buildability amendment recomputes the contract into a new monotonic version with a FRESH non-overridable confirmation and a RENEWED human approval; every prior version and approval stays in the append-only audit trail.

    technical (contract): shape v1 (justification J1, reviewer "review-1", approver P1) then
    amend with a changed observability justification J2, reviewer "review-2", approver P2 ->
    record["shape"]["version"] == 2 and its observability.justification == J2 and review
    reviewer_session_id == "review-2"; replay_events has TWO "shape" events (versions 1 and 2) and
    TWO "approval" events; the v1 "shape" event still carries J1 (unchanged).
    """
    from issueforge import shaper

    j2 = "revised: the boundary crossing was reclassified after review"
    shaper.run_shaping(
        "run-1",
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-1")),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )
    amended = _proposal(
        observability={
            "verdict": "required",
            "justification": j2,
            "events": ["shape.emitted"],
            "prohibited_sensitive_fields": ["issue_body"],
        }
    )
    approver2 = _approver()
    shaper.amend(
        "run-1",
        _ISSUE,
        assessor=_assessor(amended),
        reviewer=_reviewer(_review("review-2", justification=j2)),
        approver=approver2,
        github=_ReadOnlyGitHub(),
    )

    shape = _shape("run-1")
    assert shape["version"] == 2
    assert shape["observability"]["justification"] == j2
    assert shape["observability"]["review"]["reviewer_session_id"] == "review-2"

    shape_versions = [e.get("version") for e in _events("run-1", "shape")]
    assert 1 in shape_versions and 2 in shape_versions
    assert len(_events("run-1", "approval")) == 2
    assert approver2.calls  # a renewed, separate approval for v2

    v1_event = next(e for e in _events("run-1", "shape") if e.get("version") == 1)
    assert v1_event["observability"]["justification"] == _JUST  # prior version unchanged


@pytest.mark.parametrize("branch", ["buildable", "pause", "oversized", "blocked", "amendment"])
def test_no_github_write_occurs_on_any_shaping_branch(isolated_state_home, branch):
    """No GitHub write of any kind happens on ANY shaping branch — buildable, pause, oversized, blocked, or amendment — proven by a read-only seam whose every non-search access fails.

    technical (contract): run_shaping (and amend) with a _ReadOnlyGitHub whose __getattr__ raises
    on any attribute other than search_open_issues completes each branch to its expected observable
    outcome without triggering an unexpected-access error (so no write attribute was touched, even
    before approval).
    """
    from issueforge import shaper

    gh = _ReadOnlyGitHub(open_issues=[{"number": 41}] if branch == "pause" else [])
    if branch == "buildable":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal()),
            reviewer=_reviewer(_review("review-1")),
            approver=_approver(),
            github=gh,
        )
        assert _status("run-1") == "running"
    elif branch == "pause":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal()),
            reviewer=_reviewer(),
            approver=_approver(),
            github=gh,
        )
        assert _status("run-1") == "paused"
    elif branch == "oversized":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal(classification="oversized")),
            reviewer=_reviewer(),
            approver=_approver(),
            github=gh,
        )
        assert _shape("run-1")["classification"] == "oversized"
    elif branch == "blocked":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal(classification="blocked", blocked_reason="not_testable")),
            reviewer=_reviewer(),
            approver=_approver(),
            github=gh,
        )
        assert _shape("run-1")["classification"] == "blocked"
    else:  # amendment
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal()),
            reviewer=_reviewer(_review("review-1")),
            approver=_approver(),
            github=gh,
        )
        shaper.amend(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal()),
            reviewer=_reviewer(_review("review-2")),
            approver=_approver(),
            github=gh,
        )
        assert _shape("run-1")["version"] == 2


# =================================================================================================
# S20 (#14) — propose the in-place revision for a buildable run and perform the FIRST GitHub
# mutation, behind a human gate. propose_revision(run_id, issue, *, reviser) returns a mutation
# plan (no write) and hands the reviser the PERSISTED buildable shape; engine.apply_revision(
# run_id, plan, gateway, *, approver) writes ONLY on human approval, persists completed op-IDs in
# the RunStore so a resumed apply never re-dispatches a completed op (Option A idempotency; the
# ambiguous server-side-success case is a documented v1 limitation), and records a "revision" event
# binding the applied plan's completed op-IDs. (enter_authoring's rewiring to read that evidence is
# #73, out of scope here.)
# =================================================================================================


class _SpyGateway:
    """A spy for the S20 write gateway; records each call, optionally appending to a shared trace
    (to observe approval-before-write ordering) and raising on the Nth call (to model a mid-plan
    failure for the resume test)."""

    def __init__(self, trace: list | None = None, fail_on_call: int | None = None):
        self.calls: list = []
        self._trace = trace
        self._fail_on_call = fail_on_call

    def _record(self, entry):
        self.calls.append(entry)
        if self._trace is not None:
            self._trace.append("write")
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise RuntimeError(f"gateway boom on call {self._fail_on_call}")

    def update_body(self, *, issue, body):
        self._record(("update_body", issue, body))

    def create_issue(self, *, repo, title, body):
        self._record(("create_issue", repo, title))
        return (repo[0], repo[1], 901)

    def add_comment(self, *, issue, body):
        self._record(("add_comment", issue, body))

    def link_child(self, *, parent, child):
        self._record(("link_child", parent, child))


class _ExplodingGateway:
    """A gateway that raises on ANY attribute access — proves that NOT ONE op writes."""

    def __getattr__(self, name):
        raise AssertionError(f"GitHub write attempted before approval: {name!r}")


def _traced_approver(trace: list, value: bool = True):
    def _a(shape):
        trace.append("approve")
        return value

    return _a


def _make_buildable(run_id):
    """Drive a buildable, confirmed, human-approved run so S20 can propose its revision."""
    from issueforge import shaper

    shaper.run_shaping(
        run_id,
        _ISSUE,
        assessor=_assessor(_proposal()),
        reviewer=_reviewer(_review("review-1")),
        approver=_approver(),
        github=_ReadOnlyGitHub(),
    )


def _multi_op_plan():
    """A three-op plan (all repo-qualified) for the persisted-resume test."""
    return [
        {
            "op": "update_body",
            "id": "op-1",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "R",
        },
        {
            "op": "add_comment",
            "id": "op-2",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "c2",
        },
        {
            "op": "add_comment",
            "id": "op-3",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "c3",
        },
    ]


def test_buildable_run_receives_a_proposed_in_place_revision(isolated_state_home):
    """A buildable run receives a proposed in-place revision: an AI-authored new body carried by a repo-qualified update_body op in the mutation plan (US-3.1).

    technical (contract): after a buildable run, propose_revision("run-1", issue, reviser=<returns
    "REVISED BODY">) returns a plan whose update_body op has issue == ("MatthewDruhl","IssueForge",
    13) and body == "REVISED BODY".
    """
    from issueforge import shaper

    _make_buildable("run-1")
    result = shaper.propose_revision("run-1", _ISSUE, reviser=lambda issue, shape: "REVISED BODY")

    update_ops = [op for op in result["plan"] if op["op"] == "update_body"]
    assert update_ops, "the revision plan must carry an update_body op"
    assert update_ops[0]["issue"] == ("MatthewDruhl", "IssueForge", 13)
    assert update_ops[0]["body"] == "REVISED BODY"


def test_reviser_receives_the_exact_persisted_buildable_shape(isolated_state_home):
    """The revision is authored against the run's PERSISTED buildable shape — the reviser is handed exactly the shape recorded at approval, not an empty or reconstructed stand-in, so the revision reflects the real classification and scope.

    technical (contract): the reviser passed to propose_revision("run-1", issue, ...) receives a
    shape argument equal to store.RunStore().read("run-1")["shape"].
    """
    from issueforge import shaper

    _make_buildable("run-1")
    seen = {}

    def reviser(issue, shape):
        seen["shape"] = shape
        return "REVISED BODY"

    shaper.propose_revision("run-1", _ISSUE, reviser=reviser)
    assert seen["shape"] == _shape("run-1")


@pytest.mark.parametrize(
    "setup",
    [
        "missing",  # never shaped
        "blocked",  # classification blocked (not_testable)
        "oversized",  # classification oversized
        "paused",  # a pause condition (unknown footprint)
    ],
)
def test_only_a_buildable_run_gets_the_revision_path(isolated_state_home, setup):
    """Only a buildable, approved run can be revised: a missing, blocked, oversized, or paused run is refused — the in-place revision is not offered for issues S9 did not clear as buildable.

    technical (contract): propose_revision on a run that is missing / blocked / oversized / paused
    raises rather than returning a mutation plan.
    """
    from issueforge import shaper

    if setup == "blocked":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal(classification="blocked", blocked_reason="not_testable")),
            reviewer=_reviewer_forbidden,
            approver=_approver_forbidden,
            github=_ReadOnlyGitHub(),
        )
    elif setup == "oversized":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal(classification="oversized")),
            reviewer=_reviewer_forbidden,
            approver=_approver_forbidden,
            github=_ReadOnlyGitHub(),
        )
    elif setup == "paused":
        shaper.run_shaping(
            "run-1",
            _ISSUE,
            assessor=_assessor(_proposal(footprint_known=False)),
            reviewer=_reviewer_forbidden,
            approver=_approver_forbidden,
            github=_ReadOnlyGitHub(),
        )
    # "missing": never shaped

    with pytest.raises(ValueError):
        shaper.propose_revision("run-1", _ISSUE, reviser=lambda issue, shape: "REVISED")


def test_propose_revision_performs_no_gh_write(isolated_state_home, monkeypatch):
    """Proposing the revision performs NO GitHub call of any kind — it only plans; the write boundary is never touched until apply time.

    technical (contract): with subprocess.run patched to raise on any invocation, propose_revision
    ("run-1", issue, reviser=...) still returns a plan and never triggers a subprocess call.
    """
    import subprocess

    from issueforge import shaper

    _make_buildable("run-1")

    def _no_subprocess(*args, **kwargs):
        raise AssertionError("propose_revision must not shell out to gh")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    result = shaper.propose_revision("run-1", _ISSUE, reviser=lambda issue, shape: "REVISED")
    assert [op for op in result["plan"] if op["op"] == "update_body"]


def test_no_op_of_the_plan_writes_before_approval(isolated_state_home):
    """No op of the mutation plan — not update_body, create_issue, add_comment, or link_child — writes before the human approves; a rejected revision touches the gateway for zero ops and records no revision evidence.

    technical (contract): engine.apply_revision("run-1", <full four-op plan>, <gateway raising on
    ANY attribute access>, approver=<returns False>) does NOT raise (the gateway is never touched)
    and records NO "revision" event.
    """
    from issueforge import engine

    _make_buildable("run-1")
    full_plan = [
        {
            "op": "update_body",
            "id": "op-1",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "R",
        },
        {
            "op": "create_issue",
            "id": "op-2",
            "repo": ("MatthewDruhl", "IssueForge"),
            "title": "T",
            "body": "b",
        },
        {
            "op": "add_comment",
            "id": "op-3",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "c",
        },
        {
            "op": "link_child",
            "id": "op-4",
            "parent": ("MatthewDruhl", "IssueForge", 13),
            "child": ("MatthewDruhl", "IssueForge", 901),
        },
    ]

    engine.apply_revision("run-1", full_plan, _ExplodingGateway(), approver=_approver(False))
    assert _events("run-1", "revision") == []


def test_approved_revision_writes_the_exact_body_after_approval_and_binds_evidence(
    isolated_state_home,
):
    """On approval the revision is applied — the human's approval is recorded BEFORE any write, exactly the approved "REVISED" body is written once, and the persisted revision evidence binds the completed op-IDs (US-3.1; "Human: EVERY GitHub mutation").

    technical (contract): with a shared trace, engine.apply_revision("run-1", <update_body op-1 with
    body "REVISED">, gateway, approver=<True>) records "approve" before the single "write"; the
    gateway sees exactly one update_body call carrying body == "REVISED"; and the persisted
    "revision" event's completed op-IDs == ["op-1"].
    """
    from issueforge import engine

    _make_buildable("run-1")
    plan = [
        {
            "op": "update_body",
            "id": "op-1",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "REVISED",
        }
    ]

    trace: list = []
    gw = _SpyGateway(trace=trace)
    engine.apply_revision("run-1", plan, gw, approver=_traced_approver(trace, True))

    assert trace[0] == "approve"  # approval precedes every write
    assert "write" in trace and trace.index("approve") < trace.index("write")
    update_calls = [c for c in gw.calls if c[0] == "update_body"]
    assert len(update_calls) == 1
    assert update_calls[0] == ("update_body", ("MatthewDruhl", "IssueForge", 13), "REVISED")

    revision_events = _events("run-1", "revision")
    assert revision_events
    assert revision_events[-1]["completed"] == ["op-1"]


def test_apply_revision_resumes_from_persisted_op_ids_without_reissuing(isolated_state_home):
    """A revision apply that fails mid-plan resumes from the RunStore-persisted operation IDs: a completed op is never re-dispatched after the process is reconstructed, so a resumed mutation never duplicates a landed one (Option A idempotency).

    technical (contract): apply_revision("run-1", <three-op plan>, <gateway raising on its 2nd
    call>, approver=True) raises after op-1 lands; a SECOND apply_revision call (a fresh RunStore
    reading the persisted op-IDs) dispatches ONLY op-2 then op-3 — the completed update_body op-1 is
    never re-dispatched.
    """
    from issueforge import engine

    _make_buildable("run-1")
    plan = _multi_op_plan()

    failing = _SpyGateway(fail_on_call=2)
    with pytest.raises(RuntimeError):
        engine.apply_revision("run-1", plan, failing, approver=_approver(True))

    resumed = _SpyGateway()  # a fresh apply_revision call builds its own RunStore from disk
    engine.apply_revision("run-1", plan, resumed, approver=_approver(True))
    assert [c[0] for c in resumed.calls] == ["add_comment", "add_comment"]  # op-2, op-3 only
    assert "update_body" not in [c[0] for c in resumed.calls]  # completed op-1 never re-dispatched


def test_resume_with_a_changed_op_under_a_recorded_id_is_rejected(isolated_state_home):
    """A persisted op-ID binds to the EXACT op that completed: resuming with the same id but a changed target/op/body is rejected, so a mutated plan can never reuse a recorded id to skip — and thereby falsely mark "complete" — a genuinely different mutation.

    technical (contract): after apply_revision lands op-1 (update_body, body "R"), a second
    apply_revision whose op-1 carries a DIFFERENT body ("MUTATED") raises ValueError — the recorded
    op-ID does not match the new op's content, so the resume is refused, never silently skipped.
    """
    from issueforge import engine

    _make_buildable("run-1")
    original = [
        {
            "op": "update_body",
            "id": "op-1",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "R",
        }
    ]
    engine.apply_revision("run-1", original, _SpyGateway(), approver=_approver(True))

    mutated = [
        {
            "op": "update_body",
            "id": "op-1",
            "issue": ("MatthewDruhl", "IssueForge", 13),
            "body": "MUTATED",
        }
    ]
    with pytest.raises(ValueError):
        engine.apply_revision("run-1", mutated, _SpyGateway(), approver=_approver(True))
