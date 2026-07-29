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


def read_issue_body(slug: str, number: int, *, run: Any = subprocess.run) -> dict:
    """Read issue ``number`` of ``slug`` and return its build context (PoC-D #115).

    Shells ``gh issue view`` as an argv array (never ``shell=True``); the ``run`` seam is injectable
    so the read runs offline in tests. Returns ``{"body", "files", "contract_paths"}``: the issue
    body text, the stated implementation files (the approved write scope), and the committed
    acceptance-test paths (the contract). A lookup or parse failure RAISES (never a silent empty
    read), so the composed stage never proceeds on a bad read. ``files`` / ``contract_paths`` default
    to empty lists when the issue body carries no explicit machine-readable scope block.
    """
    result = run(
        ["gh", "issue", "view", str(number), "--repo", slug, "--json", "body"],
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
    body = data.get("body", "") if isinstance(data, dict) else ""
    return {"body": body, "files": [], "contract_paths": []}


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
    elif kind == "open_pr":
        if not _is_repo_ref(op.get("repo")):
            raise ValueError(f"open_pr op needs a repo ref, got {op.get('repo')!r}")
        if not _is_issue_ref(op.get("issue")):
            raise ValueError(
                f"open_pr op needs a repo-qualified issue ref, got {op.get('issue')!r}"
            )
        for key in ("base", "head", "title", "body"):
            _require_str(op, key, "open_pr")
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
    elif kind == "open_pr":
        gateway.open_pr(
            repo=op["repo"],
            base=op["base"],
            head=op["head"],
            issue=op["issue"],
            title=op["title"],
            body=op["body"],
        )


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


# =================================================================================================
# PoC-C (#113) — deliver ONE persisted "ready" record as exactly one open PR. NEVER merges.
#
# ``deliver_pr`` reads everything from the record, runs the readiness gate (ready? ready_sha ==
# candidate_sha? record's default branch == the gateway's REGISTERED default branch?), pushes the
# candidate branch, verifies origin/<branch> is EXACTLY the candidate sha, opens a PR against the
# registered default branch, and persists the returned PR facts as the nested ``record["pr"]``
# with ``status = "waiting-for-merge"`` via one ``store.apply(..., create=False)``. A refusal or any
# failed step happens BEFORE the next side effect and is NEVER recorded as success. A record that
# already carries a delivered PR is a no-op (no push / open / apply).
# =================================================================================================

_PR_WAITING = "waiting-for-merge"


def _issue_ref(issue: tuple) -> str:
    """Repo-qualified issue reference ``owner/repo#number`` (e.g. ``Owner/Repo#113``)."""
    return f"{issue[0]}/{issue[1]}#{issue[2]}"


def _pr_body(record: dict) -> str:
    """Assemble the PR body deterministically from THIS record's persisted evidence.

    A pure function of the record's fields, so equal records yield a byte-identical body and no value
    from another record can leak in. Includes the repo-qualified issue reference, the contract commit,
    the candidate sha, and the four evidence summaries (acceptance / baseline / scope / integrity).
    """
    return (
        "## IssueForge delivery\n\n"
        f"Issue: {_issue_ref(record['issue'])}\n"
        f"Contract commit: {record['contract_commit']}\n"
        f"Candidate SHA: {record['candidate_sha']}\n\n"
        "## Evidence\n\n"
        f"- {record['acceptance']}\n"
        f"- {record['baseline']}\n"
        f"- {record['scope']}\n"
        f"- {record['contract_integrity']}\n"
    )


def deliver_pr(record: dict, *, gateway: Any, store: Any) -> dict:
    """Deliver ONE persisted ready ``record`` as exactly one open PR; never merges.

    Ordering (each step gated on the prior): readiness checks -> push candidate branch -> read
    origin/<branch> and require exact sha equality -> open PR against the registered default branch
    -> persist ``record["pr"] = {number, url, status: "waiting-for-merge"}`` via one create=False
    apply. Any refusal or failed step raises before the next side effect and persists nothing. A
    record that already carries a delivered PR is a no-op.
    """
    existing = record.get("pr")
    if isinstance(existing, dict) and existing.get("status") == _PR_WAITING:
        return existing

    # The gh gateway needs an (owner, name) pair (its _slug reads repo[0]/repo[1]); take it from the
    # issue_ref, NOT record["repo"] — that field stays the registry alias string for the run's lifetime
    # (#207). issue_ref is (owner, name, number); _slug ignores the trailing number.
    repo = record["issue"][:2]
    branch = record["candidate_branch"]
    candidate_sha = record["candidate_sha"]
    checkout = record["registered_checkout"]

    # --- readiness gate (from the record + the AUTHORITATIVE registered default branch) ----------
    if record.get("readiness") != "ready":
        raise ValueError(
            f"refusing delivery: readiness is {record.get('readiness')!r}, not 'ready'"
        )
    if record.get("ready_sha") != candidate_sha:
        raise ValueError(
            "refusing delivery: ready_sha does not match candidate_sha "
            f"({record.get('ready_sha')!r} != {candidate_sha!r})"
        )
    registered = gateway.default_branch(repo=repo)
    if record.get("default_branch") != registered:
        raise ValueError(
            "refusing delivery: record base "
            f"{record.get('default_branch')!r} is not the registered default branch {registered!r}"
        )

    # --- push, then verify origin/<branch> is EXACTLY the candidate sha before opening a PR -------
    # The git operations are bound to the record's REGISTERED candidate checkout, so a real delivery
    # pushes/verifies THAT repository's origin, never IssueForge's own working directory.
    gateway.push(repo=repo, branch=branch, checkout=checkout)
    origin = gateway.origin_sha(repo=repo, branch=branch, checkout=checkout)
    if origin != candidate_sha:
        raise ValueError(
            f"refusing delivery: origin/{branch} is {origin!r}, not the candidate sha "
            f"{candidate_sha!r}"
        )

    # --- open exactly one PR against the registered default branch, linking the run issue ---------
    body = _pr_body(record)
    title = f"IssueForge delivery: {_issue_ref(record['issue'])}"
    pr = gateway.open_pr(
        repo=repo,
        base=registered,
        head=branch,
        issue=record["issue"],
        title=title,
        body=body,
    )

    # --- persist the REAL returned facts as the nested pr field (only after everything succeeded) -
    pr_facts = {"number": pr["number"], "url": pr["url"], "status": _PR_WAITING}
    store.apply(record["run_id"], lambda current: {"pr": pr_facts}, create=False)
    return pr_facts


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

    def _exec(self, argv: list[str], *, cwd: str | None = None) -> str:
        """Shell one argv array through the seam; a non-zero exit RAISES (never a silent write).

        ``cwd`` binds the command to a specific directory (the registered candidate checkout for the
        git operations), so a delivery can never push/verify IssueForge's own working directory.
        """
        result = self._run(argv, capture_output=True, text=True, cwd=cwd)
        if result.returncode != 0:
            raise RuntimeError(
                f"{' '.join(argv)} failed (exit {result.returncode}): {result.stderr}"
            )
        return result.stdout

    def _gh(self, args: list[str]) -> str:
        return self._exec(["gh", *args])

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

    # --- PoC-C (#113) delivery surface: default-branch read, push, origin verify, open PR ---------
    # NO merge/approve/admin method exists here; delivery can push and open a PR, never merge it.

    def default_branch(self, *, repo) -> str:
        """The registered default branch of ``repo``, read via ``gh repo view`` (stripped stdout)."""
        out = self._gh(
            [
                "repo",
                "view",
                self._slug(repo),
                "--json",
                "defaultBranchRef",
                "--jq",
                ".defaultBranchRef.name",
            ]
        )
        return out.strip()

    def push(self, *, repo, branch, checkout: str | None = None) -> None:
        """Push the candidate ``branch`` to origin via ``git push`` IN ``checkout``; non-zero RAISES.

        ``checkout`` is the registered candidate checkout of ``repo``; the push runs there (``cwd``),
        so IssueForge pushes THAT repository's origin, never its own working directory.
        """
        self._exec(["git", "push", "origin", branch], cwd=checkout)

    def origin_sha(self, *, repo, branch, checkout: str | None = None) -> str:
        """Fetch origin, then read ``origin/<branch>``'s sha via ``git rev-parse``; a bad read RAISES.

        Both git commands run in ``checkout`` (the registered candidate checkout of ``repo``), so the
        verified ref belongs to THAT repository. The fetch MUST precede the read so a stale local ref
        is never verified.
        """
        self._exec(["git", "fetch", "origin", branch], cwd=checkout)
        out = self._exec(["git", "rev-parse", f"origin/{branch}"], cwd=checkout)
        return out.strip()

    def open_pr(self, *, repo, base, head, issue, title, body) -> dict:
        """Open one PR via ``gh pr create``; parse the number+url from stdout. A non-zero exit RAISES.

        Never approves or merges: the argv is exactly ``gh pr create`` with base/head/repo/title/body.
        """
        out = self._gh(
            [
                "pr",
                "create",
                "--repo",
                self._slug(repo),
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body",
                body,
            ]
        )
        url = out.strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        return {"number": number, "url": url}
