"""The GitHub read side: is an issue open? (issue #8, S4).

``issue_is_open`` shells out to ``gh`` as an argv array (never ``shell=True``); the ``run`` seam is
injectable so the check runs offline in tests. Open -> True, closed -> False; a lookup failure or an
unparseable/unknown state RAISES (never a silent True/False) so a run never proceeds on a bad read.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


def issue_is_open(slug: str, number: int, *, run: Any = subprocess.run) -> bool:
    """Return whether issue ``number`` of ``owner/repo`` ``slug`` is OPEN, via ``gh issue view``."""
    result = run(
        ["gh", "issue", "view", str(number), "--repo", slug, "--json", "state"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh issue view failed for {slug}#{number} (exit {result.returncode}): {result.stderr}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"unparseable gh output for {slug}#{number}: {result.stdout!r}") from error
    state = data.get("state") if isinstance(data, dict) else None
    if state == "OPEN":
        return True
    if state == "CLOSED":
        return False
    raise ValueError(f"unrecognized issue state {state!r} for {slug}#{number}")


def pr_facts(run_id: str) -> dict:
    """The GitHub-authoritative PR/branch/merge facts for a run (default: none, offline).

    This is the injectable reconcile seam ``engine.continue_run`` reads before resuming; tests pass
    their own. Offline it reports no facts, so reconciliation finds nothing to diverge on.
    """
    return {}


# =================================================================================================
# S20 (#14) — the GitHub WRITE side: apply a mutation plan through an injectable write gateway.
#
# A plan is a list of plain-dict ops; each op names its ``op`` (update_body/create_issue/add_comment/
# link_child), a stable ``id``, and a REPO-QUALIFIED target — issue refs are ``(owner, repo, number)``
# and repo refs are ``(owner, repo)`` — so a mutation can never land in the wrong repository. ``apply``
# validates the WHOLE plan before the first write, then dispatches each op on ``op["op"]`` to the
# matching gateway method, skipping any op whose ``id`` is already in ``ledger`` and adding an op's
# ``id`` to ``ledger`` only AFTER its gateway call returns (so a mid-plan failure resumes without
# re-dispatching a completed op). The engine backs ``ledger`` with the RunStore for restart survival.
# =================================================================================================


def _is_issue_ref(ref: Any) -> bool:
    """True iff ``ref`` is a repo-qualified issue reference ``(owner: str, repo: str, number: int)``.

    Owner and repo must be non-empty and the number a positive int (not a bool), so a mutation can
    never target an empty repository or a zero/negative issue.
    """
    return (
        isinstance(ref, tuple)
        and len(ref) == 3
        and isinstance(ref[0], str)
        and bool(ref[0])
        and isinstance(ref[1], str)
        and bool(ref[1])
        and isinstance(ref[2], int)
        and not isinstance(ref[2], bool)
        and ref[2] > 0
    )


def _is_repo_ref(ref: Any) -> bool:
    """True iff ``ref`` is a repo reference ``(owner: str, repo: str)`` with non-empty parts."""
    return (
        isinstance(ref, tuple)
        and len(ref) == 2
        and isinstance(ref[0], str)
        and bool(ref[0])
        and isinstance(ref[1], str)
        and bool(ref[1])
    )


def _require_str(op: dict, key: str, kind: str) -> None:
    if not isinstance(op.get(key), str):
        raise ValueError(f"{kind} op needs a string {key!r}, got {op.get(key)!r}")


def _validate_op(op: dict) -> None:
    """Reject a malformed op (bad id, unknown kind, non-repo-qualified target, or missing/ill-typed
    payload field) BEFORE any write, so a malformed op later in the plan can never leave earlier ops
    half-applied."""
    op_id = op.get("id")
    if not isinstance(op_id, str) or not op_id:
        raise ValueError(f"op needs a non-empty string id, got {op_id!r}")
    kind = op.get("op")
    if kind in ("update_body", "add_comment"):
        if not _is_issue_ref(op.get("issue")):
            raise ValueError(f"{kind} op needs a repo-qualified issue ref, got {op.get('issue')!r}")
        _require_str(op, "body", kind)
    elif kind == "create_issue":
        if not _is_repo_ref(op.get("repo")):
            raise ValueError(f"create_issue op needs a repo ref, got {op.get('repo')!r}")
        if not isinstance(op.get("title"), str) or not op.get("title"):
            raise ValueError(f"create_issue op needs a non-empty title, got {op.get('title')!r}")
        _require_str(op, "body", "create_issue")
    elif kind == "link_child":
        if not _is_issue_ref(op.get("parent")):
            raise ValueError(
                f"link_child parent must be a repo-qualified issue ref, got {op.get('parent')!r}"
            )
        if not _is_issue_ref(op.get("child")):
            raise ValueError(
                f"link_child child must be a repo-qualified issue ref, got {op.get('child')!r}"
            )
    else:
        raise ValueError(f"unknown mutation op {kind!r}")


def _dispatch(op: dict, gateway: Any) -> None:
    """Drive one op's matching gateway method with its repo-qualified target and payload."""
    kind = op["op"]
    if kind == "update_body":
        gateway.update_body(issue=op["issue"], body=op["body"])
    elif kind == "create_issue":
        gateway.create_issue(repo=op["repo"], title=op["title"], body=op["body"])
    elif kind == "add_comment":
        gateway.add_comment(issue=op["issue"], body=op["body"])
    elif kind == "link_child":
        gateway.link_child(parent=op["parent"], child=op["child"])


def apply(plan: list[dict], gateway: Any, *, ledger: set) -> None:
    """Apply a mutation ``plan`` through ``gateway``, idempotent on the completed-op-ID ``ledger``.

    The WHOLE plan is validated (every op repo-qualified) BEFORE the first write, so one malformed op
    late in the plan never leaves earlier ops half-applied. Each op is then dispatched in order to the
    gateway method named by ``op["op"]``, EXCEPT ops whose ``id`` is already in ``ledger`` (skipped as
    already done). An op's ``id`` is added to ``ledger`` only AFTER its gateway call returns; a raising
    gateway propagates with the ledger recording exactly the ops that completed.
    """
    seen_ids: set = set()
    for op in plan:
        _validate_op(op)
        if op["id"] in seen_ids:
            raise ValueError(f"duplicate op id {op['id']!r} in plan")
        seen_ids.add(op["id"])
    for op in plan:
        if op["id"] in ledger:
            continue
        _dispatch(op, gateway)
        ledger.add(op["id"])


class GhWriteGateway:
    """The real GitHub write gateway: each method shells one ``gh`` write command as an argv array.

    Never ``shell=True``; the ``run`` seam is injectable so :func:`apply` can be driven offline in
    tests. A non-zero exit RAISES (never a silent partial write). ``create_issue`` parses the new
    issue number from ``gh issue create``'s URL and returns the repo-qualified ref.
    """

    def __init__(self, *, run: Any = subprocess.run) -> None:
        self._run = run

    @staticmethod
    def _slug(ref: tuple) -> str:
        return f"{ref[0]}/{ref[1]}"

    def _gh(self, args: list[str]) -> str:
        result = self._run(["gh", *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr}"
            )
        return result.stdout

    def update_body(self, *, issue, body):
        self._gh(["issue", "edit", str(issue[2]), "--repo", self._slug(issue), "--body", body])

    def create_issue(self, *, repo, title, body):
        out = self._gh(
            ["issue", "create", "--repo", self._slug(repo), "--title", title, "--body", body]
        )
        number = int(out.strip().rstrip("/").rsplit("/", 1)[-1])
        return (repo[0], repo[1], number)

    def add_comment(self, *, issue, body):
        self._gh(["issue", "comment", str(issue[2]), "--repo", self._slug(issue), "--body", body])

    def link_child(self, *, parent, child):
        self._gh(
            [
                "issue",
                "comment",
                str(parent[2]),
                "--repo",
                self._slug(parent),
                "--body",
                f"Sub-issue: {self._slug(child)}#{child[2]}",
            ]
        )
