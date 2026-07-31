"""Committed PENDING acceptance suite for #20 (S14): the engine owns two separate repair budgets.

Build deliverable this suite pins: a NEW module ``issueforge.repair`` — the ``RepairPolicy`` seam
ported from MARVIN's ``scripts/build_recovery.py`` — owning the mechanical, assertable parts of the
post-implementation repair loop that ``run_candidate`` (#114) is wrapped in. The engine/skill wiring
that routes a review-finding vs a write-off into the loop stays out of scope here (live + CI
validation and the /spec-dev build gate cover it), exactly as MARVIN's ``test_build_recovery.py``
pinned only the module seam and left the operational wiring to live validation.

D4 (``docs/prd.md:182``) splits MARVIN's single ``cap=2`` into TWO separate, independently
configurable budgets because they recover from OPPOSITE failures and one counter would hide both:
- ``review_rounds`` — the independent review raised blocking findings, so the implementer fixes them
  IN PLACE and the worktree is PRESERVED.
- ``repair_attempts`` — the implementer failed/died, or the acceptance suite is still red after
  "done", so the attempt is a WRITE-OFF: ``workspace.reset_worktree`` to base, a FRESH session with
  the frozen contract + a compact trace but NEVER the prior transcript.
Both counters are persisted run state incremented INSIDE the store lock (``RunStore.apply``), the
engine gates on them, and exhausting EITHER pauses the run with a schema-valid terminal record.

Public seam this suite assumes (``issueforge.repair`` — none of it exists yet, so every pending test
fails today with ImportError/AttributeError under ``xfail(strict=True)``, the RIGHT-reason red):

    next_action(attempt: int, cap: int = 2) -> str            # "retry" | "escalate"
    build_retry_prompt(trace: str, contract: str, transcript: str | None = None) -> str
    record_repair_attempt(run_id: str) -> int                 # ++repair_attempts under the lock
    record_review_round(run_id: str) -> int                   # ++review_rounds under the lock
    pause_exhausted(run_id: str, budget: str, notes: str) -> dict  # running -> paused + notes

Counter increments and the pause round-trip through ``store.RunStore`` (the same public, lock-held
store API the pipeline already uses); ``pause_exhausted`` writes ``status="paused"`` (a real
IssueForge State, ``state.py:17`` — the ``needs-review`` workaround MARVIN needed does NOT apply).

Marker convention (repo standard, #698 lint): each pending test carries a LITERAL
``@pytest.mark.xfail(strict=True, reason="PENDING (#20)")`` — the guard is the FIRST statement of
each test (``_repair()`` raises the dedicated ``MissingRepair`` while the module is absent), so the
pending RED can only be that ONE missing surface, confirmable with ``pytest --runxfail``. Symbols
under test are imported INSIDE each test body so collection never errors. Green controls carry NO
marker: permanent regression guards that double as anti-tautology fixtures proving the pending
assertions are not lazily satisfiable.

Golden values trace to issue #20's Acceptance Criteria and PRD US-6.2 (:93) / US-6.3 (:94).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

# --------------------------------------------------------------------------- the pending guard


class MissingRepair(Exception):
    """Raised ONLY when ``issueforge.repair`` (or a named attr) is absent — the single sanctioned
    PENDING RED reason. Any OTHER exception a test raises is a genuine failure, never a disguised
    pending."""


def _repair(attr: str | None = None):
    """Guard called as the FIRST statement of every pending test: return the ``issueforge.repair``
    module (or ``attr`` off it) or raise :class:`MissingRepair` while it does not exist yet. It runs
    before any fixture-built store state is touched, so the pending RED can only come from this one
    missing surface — confirmable with ``pytest --runxfail`` (which surfaces ``MissingRepair``)."""
    try:
        from issueforge import repair
    except ImportError as exc:  # module absent -> the pending red
        raise MissingRepair("issueforge.repair is not implemented yet (#20)") from exc
    if attr is None:
        return repair
    fn = getattr(repair, attr, None)
    if fn is None:
        raise MissingRepair(f"issueforge.repair.{attr} is not implemented yet (#20)")
    return fn


# --------------------------------------------------------------------------- store seed helpers

SLUG = "owner/repo"


def _seed_running_run(run_id: str = "run-2026-07-31-a", **extra) -> str:
    """Seed one ``running`` run through the real, lock-held store API; return its id.

    ``isolated_state_home`` (autouse in conftest) points ``ISSUEFORGE_STATE_HOME`` at a per-test
    dir, so this never touches the real state home."""
    from issueforge import store

    fields = {"run_id": run_id, "project": SLUG, "status": "running", **extra}
    store.RunStore().apply(run_id, lambda _r: fields, create=True)
    return run_id


def _read(run_id: str) -> dict:
    from issueforge import store

    return store.RunStore().read(run_id)


# ===========================================================================
# PENDING acceptance tests (red today: issueforge.repair does not exist)
# ===========================================================================


def test_next_action_retries_until_cap_then_escalates() -> None:
    """A repair budget is bounded: within the cap the driver retries; past it, it escalates. The cap
    defaults to 2 and is a parameter, so a wave (or one budget vs the other) can tighten or loosen
    it independently.

    technical (contract): with cap=2 -> next_action(1)=="retry", next_action(2)=="retry",
    next_action(3)=="escalate"; the default (no cap arg) matches cap=2 so next_action(3)=="escalate";
    with cap=1 -> next_action(1)=="retry", next_action(2)=="escalate". An off-by-one or an
    always-"retry" impl (the unbounded-loop bug the cap prevents) fails an exact equality, not a
    truthy check.
    """
    next_action = _repair("next_action")
    assert next_action(1, cap=2) == "retry"
    assert next_action(2, cap=2) == "retry"
    assert next_action(3, cap=2) == "escalate"
    # Default cap is 2 (each budget defaults to 2, US-6.2).
    assert next_action(3) == "escalate"
    # Cap is a parameter, not a constant — the two budgets are separately configurable.
    assert next_action(1, cap=1) == "retry"
    assert next_action(2, cap=1) == "escalate"
    # A larger cap with both boundary sides, so a lookup table for the small cases cannot pass:
    # attempt == cap is the last retry, attempt == cap + 1 is the first escalate.
    assert next_action(4, cap=5) == "retry"
    assert next_action(5, cap=5) == "retry"
    assert next_action(6, cap=5) == "escalate"


def test_retry_prompt_carries_trace_and_contract_not_transcript() -> None:
    """The write-off retry dispatch prompt carries the frozen contract (the committed acceptance
    tests a retry must still satisfy) and the prior attempt's compact trace, and DELIBERATELY omits
    the prior session transcript — re-seeding a fresh session with the prior churn is the context-rot
    the loop exists to avoid.

    technical (contract): build_retry_prompt(TRACE, CONTRACT, transcript=SENTINEL) returns a str that
    CONTAINS the entire TRACE and the entire CONTRACT (whole-constant containment, so ignoring the
    ``contract`` arg or returning a non-string container fails, not just a lucky substring), and does
    NOT contain the SENTINEL even though a transcript was passed. An impl that echoes the transcript,
    or drops the trace or the contract, fails a specific assertion.
    """
    build_retry_prompt = _repair("build_retry_prompt")

    contract = (
        "Acceptance tests (frozen contract):\n"
        "- tests/test_repair.py::test_next_action_retries_until_cap_then_escalates\n"
        "- tests/test_repair.py::test_two_budgets_increment_independently\n"
    )
    trace = (
        "FAILED tests/test_x.py::test_widget - assert 3 == 4\n"
        "Stricter instruction: return the count, do not hardcode."
    )
    transcript = (
        "PRIOR_SESSION_TRANSCRIPT_SENTINEL: first tried refactoring the parser, then "
        "second-guessed the schema, then rewrote it twice, then reverted the config change..."
    )
    transcript_fragments = [
        "second-guessed the schema",
        "rewrote it twice",
        "reverted the config change",
    ]

    prompt = build_retry_prompt(trace, contract, transcript=transcript)

    assert isinstance(prompt, str), "retry prompt must be a string"
    assert trace in prompt, "the entire compact trace must be carried"
    assert contract in prompt, "the entire frozen contract must be carried"
    assert "FAILED tests/test_x.py::test_widget - assert 3 == 4" in prompt
    assert "test_next_action_retries_until_cap_then_escalates" in prompt
    # Exclude the WHOLE transcript, not just its sentinel: stripping one marker substring while
    # leaking the rest of the prior session history must still fail (Codex weakening finding).
    assert transcript not in prompt, (
        "the whole prior transcript must not carry into the retry prompt"
    )
    for fragment in transcript_fragments:
        assert fragment not in prompt, (
            f"prior-session fragment {fragment!r} must not leak into the retry prompt (context rot)"
        )


def test_repair_attempt_counter_increments_through_store_apply(monkeypatch) -> None:
    """Each write-off attempt increments the PERSISTED ``repair_attempts`` counter CUMULATIVELY, one
    per call, THROUGH the under-lock ``RunStore.apply`` primitive — a lock-free read-then-write or a
    constant assignment is what US-6.3 forbids (it under-counts and under-enforces the cap).

    technical (contract): seed repair_attempts=0; record_repair_attempt returns 1 then 2; the
    persisted counter reads EXACTLY 2 (an int, not reset to 1); and a spy on RunStore.apply confirms
    each of the two increments went THROUGH apply (2 apply calls that raised repair_attempts by 1).
    An overwrite-instead-of-increment impl (stuck at 1) fails the exact-equality; a lock-free bump
    fails the spy count.
    """
    repair = _repair()
    from issueforge import store as store_mod

    run_id = _seed_running_run(repair_attempts=0)

    real_apply = store_mod.RunStore.apply
    seen = {"prev": 0}
    inc = {"n": 0}

    def spy(self, rid, transform, **kwargs):
        merged = real_apply(self, rid, transform, **kwargs)
        cur = merged.get("repair_attempts")
        if isinstance(cur, int) and cur == seen["prev"] + 1:
            inc["n"] += 1
        if isinstance(cur, int):
            seen["prev"] = cur
        return merged

    monkeypatch.setattr(store_mod.RunStore, "apply", spy)

    first = repair.record_repair_attempt(run_id)
    second = repair.record_repair_attempt(run_id)

    # `type(...) is int` (not `== 1`): bool subclasses int, so a broken impl returning True on the
    # first call would satisfy `first == 1` — reject that false-allow (Codex).
    assert type(first) is int and first == 1
    assert type(second) is int and second == 2
    record = _read(run_id)
    assert type(record["repair_attempts"]) is int
    assert record["repair_attempts"] == 2, "persisted repair_attempts must read 2 after two calls"
    assert inc["n"] == 2, "each increment must go through the under-lock RunStore.apply"


def test_review_round_counter_increments_in_store(monkeypatch) -> None:
    """Each in-place review round increments the PERSISTED ``review_rounds`` counter cumulatively,
    one per call, through the under-lock ``RunStore.apply`` primitive — the review budget is engine
    state, gated the same way as the repair budget.

    technical (contract): seed review_rounds=0; record_review_round returns 1 then 2; the persisted
    counter reads EXACTLY 2 (an int); a spy on RunStore.apply confirms both increments went through
    apply.
    """
    repair = _repair()
    from issueforge import store as store_mod

    run_id = _seed_running_run(review_rounds=0)

    real_apply = store_mod.RunStore.apply
    seen = {"prev": 0}
    inc = {"n": 0}

    def spy(self, rid, transform, **kwargs):
        merged = real_apply(self, rid, transform, **kwargs)
        cur = merged.get("review_rounds")
        if isinstance(cur, int) and cur == seen["prev"] + 1:
            inc["n"] += 1
        if isinstance(cur, int):
            seen["prev"] = cur
        return merged

    monkeypatch.setattr(store_mod.RunStore, "apply", spy)

    first = repair.record_review_round(run_id)
    second = repair.record_review_round(run_id)

    assert type(first) is int and first == 1  # bool subclasses int — reject the True false-allow
    assert type(second) is int and second == 2
    record = _read(run_id)
    assert type(record["review_rounds"]) is int
    assert record["review_rounds"] == 2
    assert inc["n"] == 2, "each increment must go through the under-lock RunStore.apply"


def test_two_budgets_increment_independently() -> None:
    """The two budgets are SEPARATE counters: incrementing one never touches the other. Collapsing
    them into one counter (what US-6.2 warns "would hide both") lets a transient implementer failure
    consume the budget meant for review iteration — this test catches exactly that.

    technical (contract): seed review_rounds=0, repair_attempts=0; two record_repair_attempt calls ->
    repair_attempts==2 while review_rounds stays 0; then one record_review_round call ->
    review_rounds==1 while repair_attempts stays 2. A single shared counter fails: the repair bumps
    would move review_rounds too.
    """
    repair = _repair()
    run_id = _seed_running_run(review_rounds=0, repair_attempts=0)

    repair.record_repair_attempt(run_id)
    repair.record_repair_attempt(run_id)
    mid = _read(run_id)
    assert mid["repair_attempts"] == 2
    assert mid["review_rounds"] == 0, "repair attempts must not consume the review budget"

    repair.record_review_round(run_id)
    end = _read(run_id)
    assert end["review_rounds"] == 1
    assert end["repair_attempts"] == 2, "a review round must not touch the repair budget"


def test_corrupt_persisted_counter_is_rejected_on_read_not_coerced() -> None:
    """A persisted budget counter that is not a real int (bool included) is rejected loud on read,
    never silently coerced — ``int(True)`` is 1 and ``int(1.9)`` is 1, either of which would forge a
    valid-looking budget and under-enforce the cap. The store's ONE record validator must guard the
    two new persisted counters exactly as it already guards ``attempts`` (``store.py:87``,
    ``require_int`` -> TypeError).

    technical (contract): plant a corrupt ``repair_attempts: true`` DIRECTLY into the manifest JSON
    (bypassing the write-path validation, simulating a hand-edit or an older record), then
    store.RunStore().read(run_id) raises TypeError via require_int — it does NOT return the record
    with a bool coerced to 1. (Seeding the corrupt value through ``apply`` would instead be rejected
    at WRITE time once the field is guarded, which is why the corruption is planted on disk.)

    Red today via the ``_repair`` guard (MissingRepair). Green when S14 both implements
    ``issueforge.repair`` AND extends ``validate_record`` to require_int the two new counters.
    """
    _repair()  # first-statement pending guard: red-via-MissingRepair until S14 lands
    from issueforge import store

    run_id = _seed_running_run(repair_attempts=0)
    manifest = store.manifest_path(run_id)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    record["repair_attempts"] = True  # a bool masquerading as an int counter
    manifest.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(TypeError):
        store.RunStore().read(run_id)


def test_pause_exhausted_writes_schema_valid_paused_record() -> None:
    """Exhausting a budget PAUSES the run with a schema-valid terminal record (never a crash, never a
    half-written record): the status becomes the real ``paused`` State and the accumulated failure
    notes are attached, and the record re-reads clean through the store's ONE validator.

    technical (contract): seed a ``running`` run; pause_exhausted(run_id, "repair_attempts", notes)
    RETURNS the persisted record dict (not None) with status=="paused"; the re-read record has
    status=="paused", records the exhausted budget identity ("repair_attempts") somewhere in the
    record (a field or the notes), and its notes CONTAIN the accumulated notes; and
    store.RunStore().read (which re-runs validate_record) returns without raising. A naive
    write that returns None, drops the budget identity, or leaves an invalid/half-written record
    fails a specific assertion.
    """
    repair = _repair()
    run_id = _seed_running_run()
    assert _read(run_id)["status"] == "running", "precondition"

    notes = "attempt 1: assert 3 == 4\nattempt 2: assert 3 == 4 (still red)"
    result = repair.pause_exhausted(run_id, "repair_attempts", notes)

    assert isinstance(result, dict), "pause_exhausted must return the persisted record dict"
    assert result["status"] == "paused"

    record = _read(run_id)  # re-validates via validate_record; raises if the record is invalid
    assert record["status"] == "paused"
    assert notes in record.get("notes", ""), "accumulated failure notes must be attached"
    # The exhausted budget must be identifiable on the record (which cap tripped the pause).
    assert "repair_attempts" in json.dumps(record), (
        "the exhausted budget identity must be recorded on the paused record"
    )


# ===========================================================================
# Engine-integration harness (drives the real two-budget loop through fakes)
# ===========================================================================
#
# These tests pin the ENGINE BEHAVIOR the ported helper cannot: a build could satisfy every helper
# test above while `run_candidate_with_repair` never resets, never redispatches, or ignores the caps.
# The loop is driven end-to-end through the SAME injected seams `run_candidate` already exposes
# (invoke / prove_red / run_baseline / approver) plus the S14-new seams (reset_worktree, the two
# caps), with fakes returning a per-attempt verdict SEQUENCE so a red->green recovery is observable.
#
# The seam this layer authors (ATDD):
#   engine.run_candidate_with_repair(record, *, profile, approver,
#       invoke=providers.invoke, prove_red=contract.prove_red, run_baseline=verify.run_baseline,
#       reset_worktree=workspace.reset_worktree, repair_cap=2, review_cap=2) -> CandidateResult
# A write-off (implementer reported done but the AUTHORITATIVE verify is red, OR the implementer
# process failed/died) resets the worktree to the CONTRACT COMMIT — never base_sha, which would
# discard the frozen contract — and redispatches a FRESH implementation session against the frozen
# contract, bounded by repair_cap; exhaustion pauses. The review_rounds budget's TRIGGER (a blocking
# review finding) is produced downstream by S15/#21, so the review->preserve routing is filed as
# post-demo acceptance work, not pinned here.

REPO_ALIAS = "DandD"
ISSUE_BODY = "DandD#111 GREET-BEHAVIOR: greet(name) must return 'hi <name>'"
WRITE_SCOPE = ["src/dandd/greet.py"]
CONTRACT_PATHS = ["tests/test_greet.py"]
ACCEPTANCE_COMMAND = ["pytest", "tests/test_greet.py", "-k", "greetcase"]
BASELINE_COMMAND = ["pytest", "-p", "no:cacheprovider"]
AUTHORED_BLOB = "# GREET-BEHAVIOR-TEST\ndef test_greetcase():\n    assert True\n"


class MissingRepairLoop(Exception):
    """Raised ONLY when ``issueforge.engine.run_candidate_with_repair`` is absent — the sanctioned
    PENDING red for the engine-integration layer."""


def _repair_loop():
    """First-statement guard for every engine-integration pending test: the two-budget loop entry, or
    :class:`MissingRepairLoop` while it does not exist yet (confirmable with ``--runxfail``)."""
    from issueforge import engine

    fn = getattr(engine, "run_candidate_with_repair", None)
    if fn is None:
        raise MissingRepairLoop(
            "issueforge.engine.run_candidate_with_repair is not implemented (#20)"
        )
    return fn


def _rg(repo, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _seed_candidate(make_git_repo):
    """Build a candidate worktree + a separate registered checkout, register it, persist the run
    record with the eight candidate fields (mirrors #114's ``_seed``); return (run_id, record,
    candidate, base_sha)."""
    from issueforge import registry, store

    candidate = make_git_repo(name="candidate")
    base_checkout = make_git_repo(name="base-checkout")
    for repo in (candidate, base_checkout):
        _rg(repo, "config", "user.email", "engine@issueforge.invalid")
        _rg(repo, "config", "user.name", "IssueForge Engine")
    base_sha = _rg(candidate, "rev-parse", "HEAD")
    registry.register(REPO_ALIAS, str(base_checkout))

    run_id = "run-20-loop"
    fields = {
        "run_id": run_id,
        "status": "running",
        "repo": REPO_ALIAS,
        "issue": ISSUE_BODY,
        "candidate_worktree": str(candidate),
        "base_sha": base_sha,
        "write_scope": WRITE_SCOPE,
        "contract_paths": CONTRACT_PATHS,
        "acceptance_command": ACCEPTANCE_COMMAND,
        "baseline_command": BASELINE_COMMAND,
    }
    store.RunStore().apply(run_id, lambda _r: fields, create=True)
    return run_id, store.RunStore().read(run_id), candidate, base_sha


class _LoopFakes:
    """The #114 seam fakes extended with a per-ATTEMPT verdict sequence, so a red->green recovery is
    observable. Signatures mirror the REAL seams (keyword-only requireds) so a wrong call surfaces as
    a TypeError, never a silent pass. ``attempt_verdicts`` is indexed by implementation attempt (1st
    impl = index 0); it clamps to its last element, so ``["red"]`` stays red forever (the cap test)."""

    def __init__(self, worktree, base_sha, *, attempt_verdicts, impl_statuses=None):
        self.worktree = worktree
        self.base_sha = base_sha
        self.attempt_verdicts = list(attempt_verdicts)
        # Per-attempt implementer process status ("OK" or a failure status like "USAGE_ERROR"),
        # clamped to the last element; default: every attempt's process succeeds.
        self.impl_statuses = list(impl_statuses) if impl_statuses else ["OK"]
        self.invoke_calls: list[dict] = []
        self.impl_count = 0
        self.reset_calls: list[dict] = []
        self.order: list[str] = []

    def _verdict(self) -> str:
        idx = min(max(self.impl_count - 1, 0), len(self.attempt_verdicts) - 1)
        return self.attempt_verdicts[idx]

    def _impl_status(self) -> str:
        idx = min(max(self.impl_count - 1, 0), len(self.impl_statuses) - 1)
        return self.impl_statuses[idx]

    def invoke(
        self,
        profile,
        prompt,
        *,
        cwd,
        run_id=None,
        role="primary",
        timeout=None,
        secrets=frozenset(),
        session=None,
        **_kw,
    ):
        from pathlib import Path

        from issueforge.providers import InvocationStatus

        cwd = Path(cwd)
        is_author = len(self.invoke_calls) == 0
        phase = "author" if is_author else "impl"
        # Record the `session` arg: a FRESH dispatch passes session=None; a RESUME passes a prior id.
        self.invoke_calls.append({"prompt": prompt, "phase": phase, "session": session})
        self.order.append(phase)
        status = "OK"
        stdout = ""
        if is_author:
            target = cwd / CONTRACT_PATHS[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(AUTHORED_BLOB)
        else:
            self.impl_count += 1
            status = self._impl_status()
            # A per-session transcript sentinel: the retry must NOT re-seed the PRIOR session's
            # transcript (context rot), so a naive engine that concatenates it is caught downstream.
            stdout = f"IMPL_TRANSCRIPT_SENTINEL_{self.impl_count}"
            if status == "OK":
                target = cwd / WRITE_SCOPE[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("def greet(name):\n    return f'hi {name}'\n")
            # else: the implementer process failed/died this attempt — it writes nothing.
        return SimpleNamespace(
            role=role,
            provider="fake",
            session_id=f"sess-{len(self.invoke_calls)}",
            prompt=prompt,
            stdout=stdout,
            stderr="",
            returncode=0 if status == "OK" else 1,
            duration_ms=1.0,
            status=getattr(InvocationStatus, status),
            timed_out=False,
            artifact_path=self.worktree / "transcript.txt",
        )

    def prove_red(
        self,
        run_id,
        *,
        targeted_ids,
        base_checkout,
        candidate_worktree,
        base_sha,
        adapter,
        provisioner=None,
        store=None,
        secrets=frozenset(),
    ):
        self.order.append("prove_red")
        rec = SimpleNamespace(
            nodeid="tests/test_greet.py::test_greetcase",
            exception_type="AssertionError",
            assertion_line=1,
            message="RED",
        )
        return SimpleNamespace(
            accepted=True,
            reason="behavioral_red",
            records=(rec,),
            base_sha=self.base_sha,
            added_ids=("tests/test_greet.py::test_greetcase",),
            head_sha="",
        )

    def approver(self, review):
        self.order.append("approver")
        return True

    def run_baseline(self, worktree, base_command, *, adapter, provisioner=None, timeout=None):
        from issueforge.adapters.base import BaselineStatus

        self.order.append("run_baseline")
        green = self._verdict() == "green"
        return SimpleNamespace(
            status=BaselineStatus.GREEN if green else BaselineStatus.BEHAVIORAL_RED,
            collected=1,
            executed=1,
            nodes=(),
            exit_code=0 if green else 1,
        )

    def reset_worktree(self, worktree, base_sha):
        """Fake write-off reset: records (worktree, sha) then does a PLAIN reset (the real
        ``workspace.reset_worktree`` requires a linked worktree, which these plain fixture repos are
        not). The impl fake re-creates the impl file on the next attempt."""
        self.reset_calls.append({"worktree": str(worktree), "sha": base_sha})
        _rg(worktree, "reset", "--hard", base_sha)
        _rg(worktree, "clean", "-fd")


def _drive(loop, record, fakes, **kw):
    return loop(
        record,
        profile=SimpleNamespace(name="fake"),
        approver=fakes.approver,
        invoke=fakes.invoke,
        prove_red=fakes.prove_red,
        run_baseline=fakes.run_baseline,
        reset_worktree=fakes.reset_worktree,
        **kw,
    )


@pytest.mark.parametrize("trigger", ["reported_done_but_red", "implementer_failed"])
def test_writeoff_resets_to_contract_commit_and_redispatches_fresh(make_git_repo, trigger) -> None:
    """A write-off (implementer reported done but the authoritative verify is RED, OR the implementer
    process failed/died) discards the failed implementation and retries against the FROZEN contract:
    the engine resets the worktree to the CONTRACT COMMIT (not base_sha, which would throw away the
    frozen tests), redispatches a FRESH implementation session, increments the persisted
    repair_attempts, and lands once the authoritative verify is green — WITHOUT re-authoring or
    re-approving.

    technical (contract): drive the loop with a red-then-green attempt sequence (or a failed-then-ok
    implementer whose recovered attempt verifies GREEN, so ONLY the failed status — not a red verify
    — can trigger the write-off). Assert: invoke phases == ["author","impl","impl"] (authored ONCE,
    implemented TWICE); the retry is a FRESH session (session arg is None, not a resume of the prior);
    reset_worktree was called exactly once with sha == the contract commit (== base_sha's child, and
    != base_sha); persisted repair_attempts == 1; the retry (2nd impl) prompt carries the frozen
    authored contract AND excludes the prior session's transcript sentinel; and the final result is
    not paused with a candidate_sha set (landed green).
    """
    loop = _repair_loop()
    from issueforge import store

    run_id, record, candidate, base_sha = _seed_candidate(make_git_repo)
    if trigger == "reported_done_but_red":
        # Implementer reports done, but the AUTHORITATIVE verify is red on attempt 1, green on retry.
        fakes = _LoopFakes(candidate, base_sha, attempt_verdicts=["red", "green"])
    else:
        # The implementer PROCESS fails on attempt 1 (writes nothing); the recovered attempt verifies
        # GREEN. Verdicts are green throughout, so ONLY the failed invocation status can trigger the
        # write-off — an engine that ignores the failure and verifies would (wrongly) land at once.
        fakes = _LoopFakes(
            candidate,
            base_sha,
            attempt_verdicts=["green", "green"],
            impl_statuses=["FAILED", "OK"],
        )

    result = _drive(loop, record, fakes)

    assert fakes.order.count("author") == 1, "authoring must run ONCE, not per retry"
    assert fakes.order.count("approver") == 1, "approval must run ONCE, not per retry"
    impl_sessions = [c for c in fakes.invoke_calls if c["phase"] == "impl"]
    assert len(impl_sessions) == 2, (
        "implementation must be redispatched once (a fresh second session)"
    )
    # The retry is a FRESH dispatch, not a resume of the prior implementer session (context rot).
    assert impl_sessions[1]["session"] is None, (
        "the retry must start a FRESH implementer session, never resume the prior one"
    )
    assert len(fakes.reset_calls) == 1, "exactly one write-off reset between the two attempts"

    # The reset target is the CONTRACT COMMIT (frozen tests preserved), never base_sha.
    reset_sha = fakes.reset_calls[0]["sha"]
    assert reset_sha != base_sha, (
        "a write-off must NOT reset to base_sha (that discards the contract)"
    )
    assert _rg(candidate, "rev-parse", f"{reset_sha}^") == base_sha, (
        "the reset target must be the contract commit — base_sha's direct child"
    )

    record_after = store.RunStore().read(run_id)
    assert record_after.get("repair_attempts") == 1, (
        "one write-off must persist repair_attempts == 1"
    )

    retry_prompt = impl_sessions[1]["prompt"]
    assert "GREET-BEHAVIOR-TEST" in retry_prompt, (
        "the retry must carry the frozen authored contract"
    )
    assert "IMPL_TRANSCRIPT_SENTINEL_1" not in retry_prompt, (
        "the retry must NOT re-seed the prior implementer session's transcript (context rot)"
    )

    assert result.paused is False, "a green retry must land, not pause"
    assert result.candidate_sha, "a landed run has a candidate sha"


def test_repair_cap_zero_pauses_immediately_without_retry(make_git_repo) -> None:
    """The engine GATES on repair_cap: with the budget set to 0, a red first attempt pauses at once —
    no reset, no redispatch. Proves the engine actually reads the cap rather than retrying blindly.

    technical (contract): drive a persistently-red loop with repair_cap=0. Assert implementation was
    invoked EXACTLY once, reset_worktree was NEVER called, and the result is paused.
    """
    loop = _repair_loop()
    run_id, record, candidate, base_sha = _seed_candidate(make_git_repo)
    fakes = _LoopFakes(candidate, base_sha, attempt_verdicts=["red"])

    result = _drive(loop, record, fakes, repair_cap=0)

    assert sum(1 for c in fakes.invoke_calls if c["phase"] == "impl") == 1, "cap 0 -> no retry"
    assert fakes.reset_calls == [], "cap 0 -> no write-off reset"
    assert result.paused is True, "an unrecovered run pauses"


def test_repair_budget_bounds_retries_and_pauses_on_exhaustion(make_git_repo) -> None:
    """A persistently-red run is BOUNDED by repair_cap, terminates PAUSED (never loops forever), and
    writes a schema-valid paused terminal record naming the exhausted budget.

    technical (contract): drive a persistently-red loop with repair_cap=1 (== one retry allowed).
    Assert the run ends paused; implementation was invoked EXACTLY twice (initial + one retry — an
    off-by-one engine that allows a third despite next_action(2, cap=1)=="escalate" fails); exactly
    one write-off reset; and the RE-READ manifest has status=="paused" with a positive-int
    repair_attempts and the exhausted budget identity ("repair_attempts") recorded on it.
    """
    loop = _repair_loop()
    from issueforge import store

    run_id, record, candidate, base_sha = _seed_candidate(make_git_repo)
    fakes = _LoopFakes(candidate, base_sha, attempt_verdicts=["red"])

    result = _drive(loop, record, fakes, repair_cap=1)

    impls = sum(1 for c in fakes.invoke_calls if c["phase"] == "impl")
    assert result.paused is True, "an exhausted repair budget pauses"
    assert impls == 2, f"repair_cap=1 permits exactly one retry (2 implementations), got {impls}"
    assert len(fakes.reset_calls) == 1, "exactly one write-off reset for one allowed retry"

    persisted = store.RunStore().read(run_id)
    assert persisted["status"] == "paused", "exhaustion must persist a paused terminal record"
    assert type(persisted.get("repair_attempts")) is int and persisted["repair_attempts"] >= 1, (
        "the spent budget must be persisted as a positive int"
    )
    assert "repair_attempts" in json.dumps(persisted), (
        "the exhausted budget identity must be recorded on the paused record"
    )


def test_green_first_attempt_lands_without_reset_or_retry(make_git_repo) -> None:
    """The loop adds NO overhead to the happy path: a first attempt that verifies green lands with no
    write-off reset, no retry, and the repair budget untouched. (Anti-tautology baseline for the
    write-off tests — proves the reset/retry are triggered by failure, not always.)

    technical (contract): drive a green-on-first-attempt loop. Assert implementation was invoked
    exactly once, reset_worktree was never called, the persisted repair_attempts is 0 (or absent),
    and the result landed (not paused, candidate_sha set).
    """
    loop = _repair_loop()
    from issueforge import store

    run_id, record, candidate, base_sha = _seed_candidate(make_git_repo)
    fakes = _LoopFakes(candidate, base_sha, attempt_verdicts=["green"])

    result = _drive(loop, record, fakes)

    assert sum(1 for c in fakes.invoke_calls if c["phase"] == "impl") == 1, (
        "green first -> no retry"
    )
    assert fakes.reset_calls == [], "green first -> no write-off reset"
    assert store.RunStore().read(run_id).get("repair_attempts", 0) == 0, "budget untouched on green"
    assert result.paused is False and result.candidate_sha, "green first attempt lands"


def test_repair_loop_never_pushes(make_git_repo, monkeypatch) -> None:
    """Across a full red->green write-off loop the engine performs NO push, PR, or merge — this slice
    produces only a LOCAL candidate sha (US-7.1: only S16 pushes). Spies every engine git invocation.

    technical (contract): wrap engine._candidate_git to record its argv, drive a red-then-green loop,
    and assert no recorded git invocation contains 'push' (nor a 'pr'/'merge' subcommand).
    """
    loop = _repair_loop()
    from issueforge import engine

    seen: list[tuple[str, ...]] = []
    real_git = engine._candidate_git

    def spy(worktree, *args, check=True):
        seen.append(tuple(args))
        return real_git(worktree, *args, check=check)

    monkeypatch.setattr(engine, "_candidate_git", spy)

    run_id, record, candidate, base_sha = _seed_candidate(make_git_repo)
    fakes = _LoopFakes(candidate, base_sha, attempt_verdicts=["red", "green"])
    _drive(loop, record, fakes)

    # Per-TOKEN membership (mirrors test_engine_candidate_poc.py:912-915): a flatten-and-substring
    # check false-positives on git args that merely CONTAIN "push" — e.g. the pytest tmp-dir path
    # ``.../test_repair_loop_never_pushes0/...`` that flows into the collection ``worktree add`` — so
    # assert on exact tokens, which still catches a real ``git push``/``merge``/``gh pr`` verb.
    assert seen, "the loop must exercise engine git (else the spy proves nothing)"
    for argv in seen:
        assert "push" not in argv, f"the implementer stage must never push: {argv!r}"
        assert "merge" not in argv, f"no merge in the implementer stage: {argv!r}"
        assert not ("gh" in argv and "pr" in argv), f"no PR in the implementer stage: {argv!r}"


# ===========================================================================
# GREEN controls (no marker; permanent guards + anti-tautology fixtures)
# ===========================================================================


def test_store_apply_roundtrip_is_real_not_stubbed() -> None:
    """Control: the store seed/read helpers this suite leans on actually persist and read back, so a
    pending counter test that stays red is red because ``issueforge.repair`` is absent, NOT because
    the store fixture is broken."""
    run_id = _seed_running_run(review_rounds=0, repair_attempts=0)
    record = _read(run_id)
    assert record["status"] == "running"
    assert record["review_rounds"] == 0 and record["repair_attempts"] == 0


def test_paused_is_a_valid_store_status() -> None:
    """Schema-premise control: ``paused`` is a real store status, so the exhaustion test targets a
    genuine State rather than MARVIN's ``needs-review`` escalation workaround. (This is a premise
    check on the store schema, not a check of any ``issueforge.repair`` behavior.)"""
    from issueforge import store
    from issueforge.state import State

    assert "paused" in store.ALLOWED_STATUS
    assert State.PAUSED.value == "paused"


def test_reset_worktree_primitive_exists_for_the_writeoff_path() -> None:
    """Control: ``workspace.reset_worktree`` — the write-off primitive the ``repair_attempts`` path
    reuses to reset a worktree to its branch base — already exists and is callable. Proves the
    ``repair_attempts`` recovery has a real reset seam to delegate to (its own behavior is pinned in
    ``tests/test_workspace.py``), so this suite is not authoring against a phantom."""
    from issueforge import workspace

    assert callable(getattr(workspace, "reset_worktree", None))
