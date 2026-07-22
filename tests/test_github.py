"""Committed PENDING acceptance suite for #8 (S4) — the github read side: is the issue open?

``issueforge.github`` does not exist yet, so each test is ``xfail(strict=True)`` (ImportError while
pending). Dual-layer docstrings.

The read-side interface this suite authors (ATDD):
- ``github.issue_is_open(slug, number, *, run=subprocess.run) -> bool`` reports whether issue
  ``number`` of ``owner/repo`` ``slug`` is OPEN. It invokes ``gh`` as an argv array (no shell)
  carrying the slug and number; the ``run`` seam is injectable so the check is offline in tests.
  Open -> True; closed -> False; a lookup failure OR an unparseable/unknown state RAISES (never a
  silent True or False).
"""

from __future__ import annotations

import json
import subprocess

import pytest


def _capturing_run(stdout="", returncode=0):
    calls = []

    def run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return run, calls


def test_issue_is_open_invokes_gh_as_argv_with_slug_and_number_no_shell():
    """The open check shells out to gh as an argv array (no shell) carrying the exact repo slug and issue number.

    technical (contract): github.issue_is_open("Owner/Repo", 148, run=<spy returning
    {"state": "OPEN"}>) is True; the spy's argv is a list starting with "gh", contains "148" and
    "Owner/Repo", and the call did not pass shell=True.
    """
    from issueforge import github

    run, calls = _capturing_run(stdout=json.dumps({"state": "OPEN"}))
    assert github.issue_is_open("Owner/Repo", 148, run=run) is True

    (cmd, kwargs) = calls[0]
    assert isinstance(cmd, list) and cmd[0] == "gh"
    assert "148" in cmd and "Owner/Repo" in cmd
    assert kwargs.get("shell") is not True


def test_issue_is_open_false_for_a_closed_issue():
    """A closed issue reads as not open.

    technical (contract): github.issue_is_open("Owner/Repo", 148, run=<gh returns
    {"state": "CLOSED"}>) is False.
    """
    from issueforge import github

    run, _ = _capturing_run(stdout=json.dumps({"state": "CLOSED"}))
    assert github.issue_is_open("Owner/Repo", 148, run=run) is False


def test_issue_lookup_failure_raises_never_silently_true():
    """A failed gh lookup raises rather than defaulting to open.

    technical (contract): github.issue_is_open("Owner/Repo", 999, run=<gh exits non-zero>)
    raises.
    """
    from issueforge import github

    run, _ = _capturing_run(stdout="", returncode=1)
    with pytest.raises(Exception):
        github.issue_is_open("Owner/Repo", 999, run=run)


def test_unparseable_or_unknown_state_raises_not_a_silent_false():
    """Malformed JSON or an unrecognized state raises, so a run never proceeds on a bad read.

    technical (contract): github.issue_is_open("Owner/Repo", 1, run=<gh returns "not json">)
    raises; github.issue_is_open("Owner/Repo", 1, run=<gh returns {"state": "WEIRD"}>) raises.
    """
    from issueforge import github

    bad_json, _ = _capturing_run(stdout="not json at all")
    with pytest.raises(Exception):
        github.issue_is_open("Owner/Repo", 1, run=bad_json)

    weird, _ = _capturing_run(stdout=json.dumps({"state": "WEIRD"}))
    with pytest.raises(Exception):
        github.issue_is_open("Owner/Repo", 1, run=weird)


# =================================================================================================
# S20 (#14) — the GitHub WRITE side: a mutation plan (list of dicts) applied via github.apply, a
# dict-dispatch over four ops. Every op is repository-qualified (owner, repo, number); idempotency
# is keyed on persisted mutation-operation IDs (never titles); a partial write resumes with no
# duplicated completed op. (The dict-dispatch "no op classes" shape is a design note, not an
# asserted behavior — a black-box suite cannot prove an internal wrapping was avoided.)
#
# apply(plan, gateway, *, ledger): validate the WHOLE plan (every op repo-qualified) before any
# write; then dispatch each op on ``op["op"]`` to the matching gateway method, skipping any op whose
# ``id`` is already in ``ledger`` (a set of completed op-IDs) and adding an op's ``id`` to ``ledger``
# only AFTER its gateway call returns. The engine (see test_shaper.py) backs ``ledger`` with the
# RunStore so completed IDs survive a restart; the ambiguous case (gateway succeeds server-side but
# the process dies before recording the id) is a documented v1 limitation, out of scope here.
# =================================================================================================


class _SpyGateway:
    """A spy for the injectable write gateway; records each call, optionally raising on the Nth.

    The real gateway shells ``gh`` write commands; here every method just records (method, kwargs).
    ``create_issue`` returns a repo-qualified ref for the freshly created issue.
    """

    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._fail_on_call = fail_on_call
        self._created = 900

    def _record(self, method: str, **kwargs):
        self.calls.append((method, kwargs))
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise RuntimeError(f"gateway boom on call {self._fail_on_call}")

    def update_body(self, *, issue, body):
        self._record("update_body", issue=issue, body=body)

    def create_issue(self, *, repo, title, body):
        self._record("create_issue", repo=repo, title=title, body=body)
        self._created += 1
        return (repo[0], repo[1], self._created)

    def add_comment(self, *, issue, body):
        self._record("add_comment", issue=issue, body=body)

    def link_child(self, *, parent, child):
        self._record("link_child", parent=parent, child=child)


def _full_plan():
    return [
        {"op": "update_body", "id": "op-1", "issue": ("Owner", "Repo", 148), "body": "revised"},
        {
            "op": "create_issue",
            "id": "op-2",
            "repo": ("Owner", "Repo"),
            "title": "Child",
            "body": "c",
        },
        {"op": "add_comment", "id": "op-3", "issue": ("Owner", "Repo", 148), "body": "note"},
        {
            "op": "link_child",
            "id": "op-4",
            "parent": ("Owner", "Repo", 148),
            "child": ("Owner", "Repo", 901),
        },
    ]


def test_apply_dispatches_each_op_to_the_matching_gateway_method():
    """A mutation plan applies each op to its matching gateway method, in order, carrying the repo-qualified target and payload.

    technical (contract): github.apply(<plan of update_body/create_issue/add_comment/link_child>,
    gateway, ledger=set()) drives gateway.update_body/create_issue/add_comment/link_child in that
    order; the update_body call carries issue == ("Owner","Repo",148) and body == "revised"; the
    create_issue call carries repo == ("Owner","Repo"); each op's id lands in the ledger.
    """
    from issueforge import github

    gw = _SpyGateway()
    ledger: set = set()
    github.apply(_full_plan(), gw, ledger=ledger)

    assert [m for m, _ in gw.calls] == ["update_body", "create_issue", "add_comment", "link_child"]
    assert gw.calls[0][1]["issue"] == ("Owner", "Repo", 148)
    assert gw.calls[0][1]["body"] == "revised"
    assert gw.calls[1][1]["repo"] == ("Owner", "Repo")
    assert ledger == {"op-1", "op-2", "op-3", "op-4"}


@pytest.mark.parametrize(
    "bad_op",
    [
        {"op": "update_body", "id": "x", "issue": 148, "body": "b"},
        {"op": "create_issue", "id": "x", "repo": "Owner/Repo", "title": "t", "body": "b"},
        {"op": "add_comment", "id": "x", "issue": 148, "body": "b"},
        {"op": "link_child", "id": "x", "parent": 148, "child": ("Owner", "Repo", 2)},
        {"op": "link_child", "id": "x", "parent": ("Owner", "Repo", 1), "child": 2},
    ],
)
def test_every_op_target_must_be_repo_qualified(bad_op):
    """Every op names its target as (owner, repo, number) — for ALL four ops and BOTH ends of a link — so a mutation can never land in the wrong repository; a bare/unqualified target is rejected before any write.

    technical (contract): github.apply([<one op with a bare-int or string target in any target
    field>], gateway, ledger=set()) raises (ValueError/TypeError); the gateway records NO call and
    the ledger stays empty.
    """
    from issueforge import github

    gw = _SpyGateway()
    ledger: set = set()
    with pytest.raises((ValueError, TypeError)):
        github.apply([bad_op], gw, ledger=ledger)
    assert gw.calls == []
    assert ledger == set()


def test_whole_plan_is_validated_before_any_write():
    """The entire plan is validated before the first mutation, so one malformed op late in the plan does not leave earlier ops half-applied.

    technical (contract): github.apply([<valid update_body op-1>, <update_body with a bare-int
    issue>], gateway, ledger=set()) raises; the gateway records ZERO calls (op-1 never dispatched)
    and the ledger stays empty.
    """
    from issueforge import github

    gw = _SpyGateway()
    ledger: set = set()
    plan = [
        {"op": "update_body", "id": "op-1", "issue": ("Owner", "Repo", 148), "body": "ok"},
        {
            "op": "update_body",
            "id": "op-2",
            "issue": 999,
            "body": "bad",
        },  # invalid, after a valid op
    ]
    with pytest.raises((ValueError, TypeError)):
        github.apply(plan, gw, ledger=ledger)
    assert gw.calls == []
    assert ledger == set()


def test_idempotency_is_keyed_on_op_ids_not_titles():
    """Idempotency is keyed on persisted operation IDs, never on content: re-applying a plan skips the ops already recorded done, while two create-issue ops that share a title but differ by id BOTH execute.

    technical (contract): applying [update_body id op-1] twice against a shared ledger calls the
    gateway exactly once; applying [create_issue id "a" title "Same", create_issue id "b" title
    "Same"] calls gateway.create_issue twice (title collision does not dedupe).
    """
    from issueforge import github

    gw = _SpyGateway()
    ledger: set = set()
    plan = [{"op": "update_body", "id": "op-1", "issue": ("Owner", "Repo", 148), "body": "b"}]
    github.apply(plan, gw, ledger=ledger)
    github.apply(plan, gw, ledger=ledger)  # op-1 already done -> skipped
    assert sum(1 for m, _ in gw.calls if m == "update_body") == 1

    gw2 = _SpyGateway()
    same_title = [
        {"op": "create_issue", "id": "a", "repo": ("Owner", "Repo"), "title": "Same", "body": "1"},
        {"op": "create_issue", "id": "b", "repo": ("Owner", "Repo"), "title": "Same", "body": "2"},
    ]
    github.apply(same_title, gw2, ledger=set())
    assert sum(1 for m, _ in gw2.calls if m == "create_issue") == 2


def test_a_completed_op_is_never_re_dispatched_on_resume():
    """After a mid-plan failure the completed ops are recorded and, on resume, are NEVER dispatched to the gateway again — the failed op and the rest run exactly once, so a resumed write never duplicates a landed one.

    technical (contract): applying [op-1, op-2, op-3] with a gateway that raises on its 2nd call
    leaves ledger == {"op-1"}; re-applying the SAME plan with a fresh gateway dispatches ONLY op-2
    then op-3 (op-1 never reappears in the resumed gateway's calls), and ledger becomes
    {"op-1","op-2","op-3"}.
    """
    from issueforge import github

    plan = [
        {"op": "update_body", "id": "op-1", "issue": ("Owner", "Repo", 148), "body": "1"},
        {"op": "add_comment", "id": "op-2", "issue": ("Owner", "Repo", 148), "body": "2"},
        {"op": "add_comment", "id": "op-3", "issue": ("Owner", "Repo", 148), "body": "3"},
    ]
    ledger: set = set()
    failing = _SpyGateway(fail_on_call=2)
    with pytest.raises(RuntimeError):
        github.apply(plan, failing, ledger=ledger)
    assert ledger == {"op-1"}

    resumed = _SpyGateway()
    github.apply(plan, resumed, ledger=ledger)
    assert [m for m, _ in resumed.calls] == ["add_comment", "add_comment"]  # op-2, op-3 only
    assert "update_body" not in [m for m, _ in resumed.calls]  # completed op-1 never re-dispatched
    assert ledger == {"op-1", "op-2", "op-3"}
