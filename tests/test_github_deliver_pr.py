"""Committed PENDING acceptance suite for #113 (PoC-C) — deliver a ready candidate as one PR.

``issueforge.github`` has a read side and a mutation-plan write side, but no *delivery* callable
yet, so every test here is ``@pytest.mark.xfail(strict=True, reason="PENDING (#113)")`` until the
build lands. Dual-layer docstrings: a plain-English behavior line, then a ``technical (contract):``
block pinning the golden values.

The delivery interface this suite authors (ATDD), all inside ``issueforge.github``:

- ``github.deliver_pr(record, *, gateway, store) -> dict`` takes ONE persisted "ready record" (the
  fixed input of #113) and delivers it as exactly one open PR. It NEVER merges. It reads everything
  it needs from ``record`` (repo, default_branch, repo-qualified issue, run_id, base_sha,
  contract_commit, candidate_sha, candidate branch, readiness, ready_sha, and the summarized
  acceptance / baseline / scope / contract-integrity evidence).

- ``gateway`` is the injectable git/GitHub write+read seam (offline in tests). Its surface:
    * ``default_branch(*, repo) -> str`` — the *registered* default branch (authoritative source).
    * ``push(*, repo, branch) -> None`` — push the candidate branch to origin.
    * ``origin_sha(*, repo, branch) -> str`` — fetch+read ``origin/<branch>``'s sha (post-push
      verification); a failed read RAISES.
    * ``open_pr(*, repo, base, head, issue, title, body) -> {"number", "url"}`` — open one PR.
  The real gateway (``github.GhWriteGateway``) grows these by shelling ``git push`` / ``git fetch``
  / ``git rev-parse`` / ``gh pr create`` as argv arrays; it exposes NO merge/approve method, and a
  non-zero exit RAISES.

- ``store`` is the persistence seam with the RunStore-shaped
  ``apply(run_id, transform, *, validate=None, create=False)`` primitive. Delivery persists the PR
  facts as the nested field ``record["pr"] = {"number", "url", "status": "waiting-for-merge"}``,
  by applying ONE ``store.apply(run_id, ..., create=False)`` over the already-persisted record — it
  never conjures a phantom record, and it changes no field but the new nested ``pr``.

Ordering the contract requires:
  readiness checks (ready? ready_sha == candidate_sha? record's default branch == registered?)
  -> push candidate branch -> read origin/<candidate branch> and require exact sha equality
  -> open PR against the registered default branch -> persist PR facts as waiting-for-merge.
A refusal or any failed step happens BEFORE the next side effect and is NEVER recorded as success.
"""

from __future__ import annotations

import pytest

# --- golden fixture values (a single "ready" record, #113's fixed input) -------------------------

_CANDIDATE_SHA = "cand1da7e0000000000000000000000000000cand"
_BASE_SHA = "ba5e00000000000000000000000000000000000e"
_CONTRACT_COMMIT = "c0117rac700000000000000000000000000000c0"

# A remote SHA that differs from the candidate by exactly its FINAL character, so a mismatch check
# that only compares a prefix (or the first N chars) would wrongly accept it.
_ONE_CHAR_OFF_SHA = _CANDIDATE_SHA[:-1] + ("e" if _CANDIDATE_SHA[-1] != "e" else "d")

_DEFAULT_PR = {"number": 4242, "url": "https://github.com/Owner/Repo/pull/4242"}


def _ready_record(**overrides):
    """A persisted candidate with ``readiness == 'ready'`` and ``ready_sha == candidate_sha``.

    Overrides let a test flip exactly one field (e.g. ``readiness='blocked'``) to exercise a refusal.
    """
    record = {
        "run_id": "run-113",
        "repo": ("Owner", "Repo"),
        "registered_checkout": "/checkouts/Owner/Repo",
        "default_branch": "main",
        "issue": ("Owner", "Repo", 113),
        "base_sha": _BASE_SHA,
        "contract_commit": _CONTRACT_COMMIT,
        "candidate_sha": _CANDIDATE_SHA,
        "candidate_branch": "issueforge/run-113-poc-c",
        "readiness": "ready",
        "ready_sha": _CANDIDATE_SHA,
        "acceptance": "acceptance: 42 passed, 0 failed",
        "baseline": "baseline: 42 passed (pre-change tree green)",
        "scope": "scope: only src/issueforge/github.py touched",
        "contract_integrity": "contract-integrity: acceptance tests unmodified",
    }
    record.update(overrides)
    return record


class _FakeGateway:
    """An injectable stand-in for the real git/GitHub gateway; records every call in order.

    ``origin_sha`` returns ``candidate_sha`` by default (post-push verification passes). Set
    ``origin_sha_value`` to a different sha to force a mismatch, or to an Exception instance to
    simulate a failed read. ``raise_on`` maps a method name to an exception to simulate a failed
    push / failed PR creation. ``pr`` sets the {"number","url"} the fake's open_pr returns, so a
    test can prove the REAL returned facts (not a hardcoded default) are what gets persisted. The
    real gateway shells ``git``/``gh``; here nothing touches the network.
    """

    def __init__(
        self, *, default_branch="main", origin_sha_value=_CANDIDATE_SHA, pr=None, raise_on=None
    ):
        self.calls: list[tuple[str, dict]] = []
        self._default_branch = default_branch
        self._origin_sha_value = origin_sha_value
        self._pr = dict(pr) if pr is not None else dict(_DEFAULT_PR)
        self._raise_on = dict(raise_on or {})

    @property
    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def _kwargs(self, name: str) -> dict:
        return next(kw for n, kw in self.calls if n == name)

    def _record(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        if name in self._raise_on:
            raise self._raise_on[name]

    def default_branch(self, *, repo):
        self._record("default_branch", repo=repo)
        return self._default_branch

    def push(self, *, repo, branch):
        self._record("push", repo=repo, branch=branch)

    def origin_sha(self, *, repo, branch):
        self._record("origin_sha", repo=repo, branch=branch)
        if isinstance(self._origin_sha_value, Exception):
            raise self._origin_sha_value
        return self._origin_sha_value

    def open_pr(self, *, repo, base, head, issue, title, body):
        self._record(
            "open_pr", repo=repo, base=base, head=head, issue=issue, title=title, body=body
        )
        return dict(self._pr)


class _FakeStore:
    """A RunStore-shaped persistence seam: ``apply`` merges the transform's returned fields.

    Holds one record per run_id (seeded via ``seed``); ``apply`` runs ``transform`` on the current
    record and merges the returned dict, recording each call's ``(run_id, create)`` so a test can
    assert delivery persists with ``create=False`` over the ALREADY-seeded record. No lock, no disk.
    """

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.applies: list[dict] = []

    def seed(self, run_id: str, record: dict):
        self.records[run_id] = dict(record)

    def apply(self, run_id, transform, *, validate=None, create=False):
        self.applies.append({"run_id": run_id, "create": create})
        current = self.records.get(run_id, {})
        merged = {**current, **transform(dict(current))}
        self.records[run_id] = merged
        return merged


def _persisted_pr(store: _FakeStore, run_id="run-113"):
    return store.records.get(run_id, {}).get("pr")


def _seeded(record):
    """Return ``(store, snapshot)`` with ``record`` already persisted and a deep-ish copy snapshot.

    Delivery MUST operate over an already-persisted record; seeding lets the refusal/failure tests
    prove the stored record is byte-for-byte unchanged, and the success tests prove exactly one
    ``create=False`` apply that adds ONLY the nested ``pr``.
    """
    store = _FakeStore()
    store.seed(record["run_id"], record)
    return store, dict(record)


def _assert_store_untouched(store, snapshot, run_id="run-113"):
    assert store.applies == [], f"failure must persist nothing, saw applies={store.applies!r}"
    assert store.records[run_id] == snapshot, "the seeded record must be unchanged after a failure"
    assert _persisted_pr(store, run_id) is None


def _assert_only_pr_added(store, snapshot, run_id="run-113"):
    """Exactly one ``create=False`` apply added ONLY a nested ``pr``; every other field is intact."""
    assert store.applies == [{"run_id": run_id, "create": False}], (
        f"delivery must persist via exactly one create=False apply, saw {store.applies!r}"
    )
    final = store.records[run_id]
    assert set(final) == set(snapshot) | {"pr"}, "delivery must add only the nested 'pr' field"
    for key, value in snapshot.items():
        assert final[key] == value, f"delivery changed pre-existing field {key!r}"


def _expect_delivery_failure(record, *, gateway, store):
    """Assert ``deliver_pr`` raises a REAL refusal/failure (not a missing-symbol AttributeError).

    Guards the xfail-masks-setup-errors trap: a bare ``pytest.raises(Exception)`` would swallow the
    ``AttributeError`` from an unimplemented ``deliver_pr`` and let the no-side-effect assertions pass
    (a false green). Requiring the raised type not be ``AttributeError`` keeps the suite RED until the
    callable exists, while leaving the build free to pick the refusal/failure exception type.
    """
    from issueforge import github

    with pytest.raises(Exception) as excinfo:
        github.deliver_pr(record, gateway=gateway, store=store)
    assert not isinstance(excinfo.value, AttributeError), (
        "deliver_pr must exist and raise a real refusal/failure, not a missing-symbol AttributeError"
    )


def _expect_raises_real(func):
    """Assert ``func()`` raises a REAL failure, not a missing-symbol AttributeError.

    The real-gateway "non-zero exit raises" tests must stay RED until the gateway method exists: a
    bare ``pytest.raises(Exception)`` would swallow the AttributeError from the not-yet-built method
    and XPASS (a false green under strict xfail). Rejecting AttributeError keeps them failing for the
    right reason while leaving the build free to pick the failure exception type.
    """
    with pytest.raises(Exception) as excinfo:
        func()
    assert not isinstance(excinfo.value, AttributeError), (
        "gateway method must exist and raise a real failure, not a missing-symbol AttributeError"
    )


def _spy(dispatch):
    """A subprocess.run spy: records ``(argv, kwargs)`` and returns ``dispatch(argv)``.

    ``dispatch`` maps an argv list to a ``subprocess.CompletedProcess`` (or raises). The recorded
    calls let a test pin the EXACT argv the real gateway shells, offline.
    """
    import subprocess

    calls: list[tuple[list, dict]] = []

    def run(cmd, *args, **kwargs):
        calls.append((list(cmd), kwargs))
        result = dispatch(list(cmd))
        if isinstance(result, subprocess.CompletedProcess):
            return result
        return subprocess.CompletedProcess(cmd, result[0], stdout=result[1], stderr=result[2])

    run.calls = calls
    return run


def _flag_value(argv, flag):
    """Return the value that immediately follows ``flag`` in an argv list (KeyError if absent)."""
    idx = argv.index(flag)
    return argv[idx + 1]


# =================================================================================================
# Criterion 1 — delivery REFUSES a record that is not ready / ready_sha != candidate_sha / base is
# not the registered default branch; and the branch is pushed ONLY after those checks pass. Every
# refusal leaves the SEEDED store byte-for-byte unchanged (no apply at all).
# =================================================================================================


def test_refuses_a_record_that_is_not_ready_and_never_pushes():
    """A record whose readiness is not "ready" is refused and nothing is pushed, opened, or stored.

    technical (contract): github.deliver_pr(_ready_record(readiness="blocked"), gateway, store)
    raises; the gateway records NO "push"/"open_pr"; the seeded store is unchanged (no apply).
    """
    gateway = _FakeGateway()
    record = _ready_record(readiness="blocked")
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    assert "push" not in gateway.methods
    assert "open_pr" not in gateway.methods
    _assert_store_untouched(store, snapshot)


def test_refuses_when_ready_sha_differs_from_candidate_sha_and_never_pushes():
    """If the readiness signature (ready_sha) does not match the candidate SHA, delivery refuses.

    technical (contract): github.deliver_pr(_ready_record(ready_sha="deadbeef" + "0" * 34),
    gateway, store) raises; gateway records NO "push"/"open_pr"; the seeded store is unchanged.
    """
    gateway = _FakeGateway()
    record = _ready_record(ready_sha="deadbeef" + "0" * 34)
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    assert "push" not in gateway.methods
    assert "open_pr" not in gateway.methods
    _assert_store_untouched(store, snapshot)


def test_refuses_when_record_base_is_not_the_registered_default_branch_and_never_pushes():
    """If the record's declared default branch disagrees with the gateway's registered default branch, delivery refuses.

    technical (contract): with a gateway whose registered default_branch is "main" but a record whose
    default_branch is "release", github.deliver_pr(record, gateway, store) raises; gateway records
    NO "push"/"open_pr"; the seeded store is unchanged.
    """
    gateway = _FakeGateway(default_branch="main")
    record = _ready_record(default_branch="release")
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    assert "push" not in gateway.methods
    assert "open_pr" not in gateway.methods
    _assert_store_untouched(store, snapshot)


def test_push_happens_only_after_readiness_checks_and_before_pr_open():
    """On a ready record the gateway is driven default_branch -> push -> origin_sha -> open_pr, in that order, and the push carries the candidate branch and repo.

    technical (contract): github.deliver_pr(_ready_record(), gateway, store) drives the gateway so
    that the call order is default_branch < push < origin_sha < open_pr, and the push carries
    branch == "issueforge/run-113-poc-c" and repo == ("Owner","Repo").
    """
    from issueforge import github

    gateway = _FakeGateway()
    record = _ready_record()
    store, _ = _seeded(record)
    github.deliver_pr(record, gateway=gateway, store=store)

    order = gateway.methods
    for name in ("default_branch", "push", "origin_sha", "open_pr"):
        assert name in order, f"gateway.{name} was never called"
    assert (
        order.index("default_branch")
        < order.index("push")
        < order.index("origin_sha")
        < order.index("open_pr")
    )
    push_kwargs = gateway._kwargs("push")
    assert push_kwargs["branch"] == "issueforge/run-113-poc-c"
    assert push_kwargs["repo"] == ("Owner", "Repo")


# =================================================================================================
# Criterion 2/3 — after push, verify origin/<branch> EXACTLY equals candidate_sha; a failed read or
# a mismatch HALTS before PR creation. The real gateway proves it actually fetches+reads
# origin/<branch> and raises on a bad read.
# =================================================================================================


def test_origin_sha_mismatch_after_push_halts_before_pr():
    """If the SHA at origin/<branch> after push is not exactly the candidate SHA, delivery halts before opening a PR.

    technical (contract): with a gateway whose origin_sha returns a value differing from the
    candidate by only its FINAL character, github.deliver_pr(_ready_record(), gateway, store)
    raises; the gateway records "push" then "origin_sha" (queried for repo + candidate branch) but
    NO "open_pr"; the seeded store is unchanged.
    """
    gateway = _FakeGateway(origin_sha_value=_ONE_CHAR_OFF_SHA)
    record = _ready_record()
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    order = gateway.methods
    assert "push" in order and "origin_sha" in order
    assert order.index("push") < order.index("origin_sha")
    assert "open_pr" not in order
    origin_kwargs = gateway._kwargs("origin_sha")
    assert origin_kwargs["repo"] == ("Owner", "Repo")
    assert origin_kwargs["branch"] == "issueforge/run-113-poc-c"
    _assert_store_untouched(store, snapshot)


def test_failed_origin_read_after_push_halts_before_pr():
    """If reading origin/<branch> fails, delivery halts before opening a PR rather than proceeding on a bad read.

    technical (contract): with a gateway whose origin_sha RAISES, github.deliver_pr(_ready_record(),
    gateway, store) raises; the gateway records "push" then "origin_sha" but NO "open_pr"; the
    seeded store is unchanged.
    """
    gateway = _FakeGateway(origin_sha_value=RuntimeError("git fetch failed"))
    record = _ready_record()
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    order = gateway.methods
    assert "push" in order and "origin_sha" in order
    assert order.index("push") < order.index("origin_sha")
    assert "open_pr" not in order
    _assert_store_untouched(store, snapshot)


def test_real_gateway_origin_sha_fetches_then_reads_origin_branch():
    """The real gateway's origin_sha shells `git fetch` then reads `origin/<branch>` and returns that SHA, offline, parsed from the read's stdout.

    technical (contract): github.GhWriteGateway(run=<spy>).origin_sha(repo=("Owner","Repo"),
    branch="issueforge/run-113-poc-c") invokes a "git" argv containing "fetch" and a "git" argv
    containing the literal "origin/issueforge/run-113-poc-c"; the returned value is the stripped
    stdout of that read; no argv passes shell=True.
    """
    from issueforge import github

    branch = "issueforge/run-113-poc-c"
    ref = f"origin/{branch}"

    def dispatch(argv):
        if ref in argv:
            return (0, _CANDIDATE_SHA + "\n", "")
        return (0, "", "")

    spy = _spy(dispatch)
    gw = github.GhWriteGateway(run=spy)
    got = gw.origin_sha(repo=("Owner", "Repo"), branch=branch)

    assert got == _CANDIDATE_SHA
    argvs = [cmd for cmd, _ in spy.calls]
    fetch_idx = next((i for i, cmd in enumerate(argvs) if cmd[:2] == ["git", "fetch"]), None)
    read_idx = next(
        (i for i, cmd in enumerate(argvs) if cmd[:2] == ["git", "rev-parse"] and ref in cmd),
        None,
    )
    assert fetch_idx is not None, f"no `git fetch` argv in {argvs!r}"
    assert read_idx is not None, f"no `git rev-parse {ref}` argv in {argvs!r}"
    # The fetch of origin MUST precede the read of origin/<branch>: a reversed order (or a read
    # without a prior fetch) would verify a stale ref.
    assert fetch_idx < read_idx, f"git fetch must precede the rev-parse read: {argvs!r}"
    for cmd, kwargs in spy.calls:
        assert kwargs.get("shell") is not True


def test_real_gateway_origin_sha_raises_on_failed_read():
    """The real gateway's origin_sha RAISES when the read of origin/<branch> exits non-zero, never returning a bad/empty sha.

    technical (contract): with a spy that returns a non-zero exit for the read of
    "origin/issueforge/run-113-poc-c", github.GhWriteGateway(run=<spy>).origin_sha(...) raises
    (never returns).
    """
    from issueforge import github

    branch = "issueforge/run-113-poc-c"
    ref = f"origin/{branch}"

    def dispatch(argv):
        if ref in argv:
            return (1, "", "fatal: ambiguous argument")
        return (0, "", "")

    gw = github.GhWriteGateway(run=_spy(dispatch))
    _expect_raises_real(lambda: gw.origin_sha(repo=("Owner", "Repo"), branch=branch))


# =================================================================================================
# Criterion 4/5 — the PR is opened against the registered default branch, links the exact
# repo-qualified run issue, and its body is assembled deterministically from the persisted evidence.
# =================================================================================================


def test_pr_opened_against_default_branch_and_links_repo_qualified_issue():
    """The PR targets the registered default branch, is pushed from the candidate branch, and links the exact repository-qualified run issue.

    technical (contract): github.deliver_pr(_ready_record(), gateway, store) calls
    gateway.open_pr with base == "main", head == "issueforge/run-113-poc-c", and
    issue == ("Owner","Repo",113); the assembled body contains the repo-qualified reference
    "Owner/Repo#113".
    """
    from issueforge import github

    gateway = _FakeGateway()
    record = _ready_record()
    store, _ = _seeded(record)
    github.deliver_pr(record, gateway=gateway, store=store)

    open_kwargs = gateway._kwargs("open_pr")
    assert open_kwargs["base"] == "main"
    assert open_kwargs["head"] == "issueforge/run-113-poc-c"
    assert open_kwargs["issue"] == ("Owner", "Repo", 113)
    assert "Owner/Repo#113" in open_kwargs["body"]


def test_pr_body_assembled_deterministically_from_persisted_evidence():
    """The PR body is built deterministically from THIS record's persisted evidence: a different record yields a body carrying only its own values, and equal input yields a byte-identical body.

    technical (contract): the body from delivering _ready_record() contains "Owner/Repo#113", the
    contract_commit, candidate_sha, and the four summary strings, and is byte-identical across two
    equal deliveries; delivering a SECOND record with different issue/commit/sha/evidence yields a
    body that carries the second values, carries NONE of the first record's unique values, and
    differs from the first body (defeating a hardcoded golden body).
    """
    from issueforge import github

    def _body(record):
        gw, store = _FakeGateway(), _FakeStore()
        store.seed(record["run_id"], record)
        github.deliver_pr(record, gateway=gw, store=store)
        return gw._kwargs("open_pr")["body"]

    body1 = _body(_ready_record())
    for token in (
        "Owner/Repo#113",
        _CONTRACT_COMMIT,
        _CANDIDATE_SHA,
        "acceptance: 42 passed, 0 failed",
        "baseline: 42 passed (pre-change tree green)",
        "scope: only src/issueforge/github.py touched",
        "contract-integrity: acceptance tests unmodified",
    ):
        assert token in body1, f"body missing evidence: {token!r}"

    # Determinism: an equal record produces a byte-identical body.
    assert body1 == _body(_ready_record())

    # A genuinely different record must produce a different body carrying only ITS OWN values.
    other_sha = "0ther5ha0000000000000000000000000000face"
    other_commit = "0therc0mm1700000000000000000000000000face"
    other = _ready_record(
        run_id="run-999",
        repo=("Acme", "Widget"),
        issue=("Acme", "Widget", 999),
        candidate_sha=other_sha,
        ready_sha=other_sha,
        candidate_branch="issueforge/run-999-poc-c",
        contract_commit=other_commit,
        acceptance="acceptance: 7 passed, 0 failed",
        baseline="baseline: 7 passed (pre-change tree green)",
        scope="scope: only src/issueforge/widget.py touched",
        contract_integrity="contract-integrity: acceptance tests verified untouched",
    )
    body2 = _body(other)

    assert body2 != body1
    # ALL of the second record's own values must appear — including every one of the four evidence
    # summaries — so a body that hardcodes any first-record evidence string is caught.
    for token in (
        "Acme/Widget#999",
        other_commit,
        other_sha,
        "acceptance: 7 passed, 0 failed",
        "baseline: 7 passed (pre-change tree green)",
        "scope: only src/issueforge/widget.py touched",
        "contract-integrity: acceptance tests verified untouched",
    ):
        assert token in body2, f"second body missing its own evidence: {token!r}"
    # And NONE of the first record's values — including all four of its evidence summaries — may
    # leak into the second body.
    for stale in (
        "Owner/Repo#113",
        _CONTRACT_COMMIT,
        _CANDIDATE_SHA,
        "acceptance: 42 passed, 0 failed",
        "baseline: 42 passed (pre-change tree green)",
        "scope: only src/issueforge/github.py touched",
        "contract-integrity: acceptance tests unmodified",
    ):
        assert stale not in body2, f"second body leaked the first record's value: {stale!r}"


# =================================================================================================
# Criterion 6/7 — the returned PR number+url are persisted with status "waiting-for-merge", and a
# re-run after a successful open neither pushes nor opens a duplicate.
# =================================================================================================


def test_pr_number_and_url_persisted_with_waiting_for_merge_status():
    """The REAL PR number and URL the gateway returned are persisted as record["pr"] with status "waiting-for-merge", via exactly one create=False apply that changes nothing else.

    technical (contract): with a gateway whose open_pr returns a UNIQUE non-default {"number": 9001,
    "url": ".../pull/9001"}, after github.deliver_pr(_ready_record(), gateway, store) the store's
    run-113 record has pr == {"number": 9001, "url": ".../pull/9001", "status":
    "waiting-for-merge"}; store.apply ran exactly once with create=False; no other field changed.
    """
    from issueforge import github

    pr = {"number": 9001, "url": "https://github.com/Owner/Repo/pull/9001"}
    gateway = _FakeGateway(pr=pr)
    record = _ready_record()
    store, snapshot = _seeded(record)
    github.deliver_pr(record, gateway=gateway, store=store)

    assert _persisted_pr(store) == {
        "number": 9001,
        "url": "https://github.com/Owner/Repo/pull/9001",
        "status": "waiting-for-merge",
    }
    _assert_only_pr_added(store, snapshot)


def test_rerun_after_successful_open_does_not_push_or_open_a_duplicate():
    """Delivering twice — a real first delivery, then a re-run over the resulting persisted record — pushes, verifies, opens, and persists exactly once total; the re-run is a no-op.

    technical (contract): github.deliver_pr(_ready_record(), gateway, store) succeeds and persists
    pr; feeding the resulting persisted record back through github.deliver_pr(record, gateway,
    store) adds NO second "push"/"origin_sha"/"open_pr" call and NO second apply — across both runs
    exactly one of each occurred.
    """
    from issueforge import github

    gateway = _FakeGateway()
    record = _ready_record()
    store, _ = _seeded(record)

    github.deliver_pr(record, gateway=gateway, store=store)
    persisted_record = dict(store.records["run-113"])
    assert persisted_record.get("pr", {}).get("status") == "waiting-for-merge"

    github.deliver_pr(persisted_record, gateway=gateway, store=store)

    assert gateway.methods.count("push") == 1
    assert gateway.methods.count("origin_sha") == 1
    assert gateway.methods.count("open_pr") == 1
    assert len(store.applies) == 1


# =================================================================================================
# Criterion 8/9 — IssueForge must NEVER merge/approve a PR; a failed push or failed PR creation is a
# failure and is NEVER recorded as success.
# =================================================================================================


def test_delivery_never_invokes_and_gateway_never_exposes_a_merge_or_approve_operation():
    """Delivery only pushes, verifies, and opens a PR — never a merge/approve — and NO public callable on the real gateway has a merge/approve-flavoured name.

    technical (contract): across a full github.deliver_pr(_ready_record(), ...) the gateway's
    invoked method names are a subset of {"default_branch","push","origin_sha","open_pr"} (none
    case-folding to contain "merge"/"approve"); github.GhWriteGateway exposes callables "push" and
    "open_pr" and NO public callable whose case-folded name contains "merge" or "approve".
    """
    from issueforge import github

    gateway = _FakeGateway()
    record = _ready_record()
    store, _ = _seeded(record)
    github.deliver_pr(record, gateway=gateway, store=store)

    allowed = {"default_branch", "push", "origin_sha", "open_pr"}
    assert set(gateway.methods) <= allowed
    assert not any("merge" in n.casefold() or "approve" in n.casefold() for n in gateway.methods)

    assert callable(getattr(github.GhWriteGateway, "push", None))
    assert callable(getattr(github.GhWriteGateway, "open_pr", None))

    # Default-deny: enumerate EVERY public callable and reject any merge/approve-flavoured name,
    # so an impl cannot smuggle in e.g. merge_pull_request / approvePr / squash_merge.
    for name in dir(github.GhWriteGateway):
        if name.startswith("_"):
            continue
        if not callable(getattr(github.GhWriteGateway, name)):
            continue
        folded = name.casefold()
        assert "merge" not in folded, f"gateway exposes a merge-flavoured method: {name!r}"
        assert "approve" not in folded, f"gateway exposes an approve-flavoured method: {name!r}"


def test_real_gateway_shells_gh_pr_create_and_git_push_offline_with_no_merge_tokens():
    """The real gateway opens a PR by shelling `gh pr create` and pushes by shelling `git push`, as argv arrays (no shell), with the exact base/head/repo/title/body flag-values; it parses the PR number+url from the command's output; and NO write argv carries a merge/approve/admin/auto verb.

    technical (contract): github.GhWriteGateway(run=<spy>).open_pr(repo=("Owner","Repo"),
    base="main", head="issueforge/run-113-poc-c", issue=("Owner","Repo",113), title="t", body="b")
    shells an argv whose first three tokens are exactly ["gh","pr","create"] with
    --repo Owner/Repo, --base main, --head issueforge/run-113-poc-c, --title t, --body b, and
    RETURNS {"number": 7, "url": ".../pull/7"} parsed from the command's stdout; .push(...) shells
    an argv whose first two tokens are exactly ["git","push"]; no argv passes shell=True or contains
    any token case-folding to include "merge","approve","admin","auto","review", or "--merge".
    """
    from issueforge import github

    def dispatch(argv):
        # gh pr create prints the new PR URL to stdout.
        if argv[:2] == ["gh", "pr"]:
            return (0, "https://github.com/Owner/Repo/pull/7\n", "")
        return (0, "", "")

    spy = _spy(dispatch)
    gw = github.GhWriteGateway(run=spy)
    result = gw.open_pr(
        repo=("Owner", "Repo"),
        base="main",
        head="issueforge/run-113-poc-c",
        issue=("Owner", "Repo", 113),
        title="t",
        body="b",
    )
    gw.push(repo=("Owner", "Repo"), branch="issueforge/run-113-poc-c")

    open_cmd = spy.calls[0][0]
    push_cmd = spy.calls[1][0]

    # Positive: exact command prefixes (not just a token appearing somewhere in the argv).
    assert open_cmd[:3] == ["gh", "pr", "create"], f"open argv was {open_cmd!r}"
    assert push_cmd[:2] == ["git", "push"], f"push argv was {push_cmd!r}"

    # The push MUST target origin AND the record's candidate branch — not `main`, not a bare push.
    assert "origin" in push_cmd, f"push argv must target origin: {push_cmd!r}"
    assert "issueforge/run-113-poc-c" in push_cmd, (
        f"push argv must name the candidate branch: {push_cmd!r}"
    )

    # Positive: exact flag/value pairs on the open-PR argv.
    assert _flag_value(open_cmd, "--repo") == "Owner/Repo"
    assert _flag_value(open_cmd, "--base") == "main"
    assert _flag_value(open_cmd, "--head") == "issueforge/run-113-poc-c"
    assert _flag_value(open_cmd, "--title") == "t"
    assert _flag_value(open_cmd, "--body") == "b"

    # The URL + number are PARSED from the command output, not invented.
    assert result["url"] == "https://github.com/Owner/Repo/pull/7"
    assert result["number"] == 7

    # Default-deny: reject a forbidden verb appearing as any SUBSTRING of any argv token, catching
    # --admin=true / --auto=true / --approve=true / pr-merge / `gh pr review` and case variants.
    forbidden = ("merge", "approve", "admin", "auto", "review")
    for cmd, kwargs in spy.calls:
        assert kwargs.get("shell") is not True
        for token in cmd:
            folded = str(token).casefold()
            for verb in forbidden:
                assert verb not in folded, f"forbidden verb {verb!r} in argv token {token!r}"


def test_real_gateway_push_nonzero_exit_raises():
    """The real gateway's push RAISES when `git push` exits non-zero, never silently succeeding.

    technical (contract): with a spy that returns a non-zero exit for the "git push" argv,
    github.GhWriteGateway(run=<spy>).push(repo=("Owner","Repo"), branch="issueforge/run-113-poc-c")
    raises.
    """
    from issueforge import github

    def dispatch(argv):
        if argv[:2] == ["git", "push"]:
            return (1, "", "error: failed to push some refs")
        return (0, "", "")

    gw = github.GhWriteGateway(run=_spy(dispatch))
    _expect_raises_real(lambda: gw.push(repo=("Owner", "Repo"), branch="issueforge/run-113-poc-c"))


def test_real_gateway_open_pr_nonzero_exit_raises():
    """The real gateway's open_pr RAISES when `gh pr create` exits non-zero, never returning a fake success.

    technical (contract): with a spy that returns a non-zero exit for the "gh pr create" argv,
    github.GhWriteGateway(run=<spy>).open_pr(...) raises.
    """
    from issueforge import github

    def dispatch(argv):
        if argv[:2] == ["gh", "pr"]:
            return (1, "", "GraphQL: pull request create failed")
        return (0, "", "")

    gw = github.GhWriteGateway(run=_spy(dispatch))
    _expect_raises_real(
        lambda: gw.open_pr(
            repo=("Owner", "Repo"),
            base="main",
            head="issueforge/run-113-poc-c",
            issue=("Owner", "Repo", 113),
            title="t",
            body="b",
        )
    )


def test_failed_push_is_reported_as_failure_never_recorded_as_success():
    """A failed push is surfaced as a failure and is never recorded as a delivered PR.

    technical (contract): with a gateway that raises on "push", github.deliver_pr(_ready_record(),
    gateway, store) raises; the gateway records NO "open_pr"; the seeded store is byte-for-byte
    unchanged and never persisted a "pr".
    """
    gateway = _FakeGateway(raise_on={"push": RuntimeError("git push rejected")})
    record = _ready_record()
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    assert "open_pr" not in gateway.methods
    _assert_store_untouched(store, snapshot)


def test_failed_pr_creation_is_reported_as_failure_never_recorded_as_success():
    """A failed PR creation is surfaced as a failure and is never recorded as a delivered PR.

    technical (contract): with a gateway that raises on "open_pr", github.deliver_pr(_ready_record(),
    gateway, store) raises; the seeded store is byte-for-byte unchanged and never persisted a "pr".
    """
    gateway = _FakeGateway(raise_on={"open_pr": RuntimeError("gh pr create failed")})
    record = _ready_record()
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    _assert_store_untouched(store, snapshot)


def test_delivery_through_real_gateway_failed_push_leaves_store_unchanged():
    """Wiring the PRODUCTION gateway (not the fake) into delivery, a non-zero `git push` surfaces as a failure and leaves the seeded store unchanged — the failure path is real, not a fake-gateway artifact.

    technical (contract): with github.GhWriteGateway(run=<spy>) whose "git push" argv exits
    non-zero (default-branch query returns "main"), github.deliver_pr(_ready_record(), gateway,
    store) raises; the gateway never opens a PR; the seeded store is byte-for-byte unchanged.
    """
    from issueforge import github

    def dispatch(argv):
        if argv[:2] == ["git", "push"]:
            return (1, "", "error: failed to push some refs")
        # Any read the delivery makes before the push (e.g. the registered default branch) resolves
        # to "main"; push fails before origin verification is reached.
        return (0, "main\n", "")

    gateway = github.GhWriteGateway(run=_spy(dispatch))
    record = _ready_record()
    store, snapshot = _seeded(record)

    _expect_delivery_failure(record, gateway=gateway, store=store)

    _assert_store_untouched(store, snapshot)
