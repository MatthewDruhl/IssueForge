"""The deterministic red proof (issue #16, S10): the load-bearing integrity control.

A run produces AI-authored acceptance tests PLUS machine-checked evidence that they collected,
executed, and FAILED in the CALL phase on a healthy baseline at a bound sha — and REFUSES to
proceed otherwise. ``prove_red`` is an ORDERED proof: every rejection PAUSES the run with a
SPECIFIC reason token and records a ``red_proof`` event, so a red is never the else branch and a
non-red third state (an XPASS, an all-skipped, an empty parametrize, a setup/teardown error, an
import error at any phase) can never be laundered into a behavioral red.

The proof reuses the real S6 seams rather than re-implementing pytest orchestration: an injected
host provisioner, ``PytestAdapter.canonical_collect`` for the frozen id sets, ``select_baseline``
for the protected-baseline reconciliation, and ``verify.run_baseline`` for the report-log execution
that yields per-phase ``NodeRecord``s. The injected provisioner disables plugin autoload, so the
report-log reporter is force-loaded with ``-p pytest_reportlog`` on the baseline command. Every
verdict, record, pause, and redaction is persisted through the S4 ``RunStore`` (secrets redacted).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from issueforge import engine, process
from issueforge import verify as _verify
from issueforge import workspace as _workspace_mod
from issueforge.adapters.base import BaselineStatus, Outcome
from issueforge.state import State, transition
from issueforge.store import REDACTED, RunStore

_GIT_TIMEOUT = 120.0
_COLLECT_TIMEOUT = 120.0
# The report-log reporter is force-loaded because the injected provisioner disables plugin
# autoload (so a developer's installed plugins cannot alter collection/execution).
_BASELINE = ["-m", "pytest", "-p", "pytest_reportlog"]
_VALID_DISPOSITIONS = frozenset({"keep", "revise", "supersede"})
_IMPORT_TYPES = frozenset({"ImportError", "ModuleNotFoundError"})


@dataclass(frozen=True)
class RedRecord:
    """One canonical, redacted red-evidence record for a targeted unit."""

    nodeid: str
    exception_type: str
    assertion_line: int | None
    message: str


@dataclass(frozen=True)
class RedProof:
    """The whole red-proof verdict: a boolean, its reason token, the canonical records, the bound
    base sha, and the computed added ids."""

    accepted: bool
    reason: str
    records: tuple[RedRecord, ...]
    base_sha: str
    added_ids: tuple[str, ...]


# --------------------------------------------------------------------------- redaction (S4 writer)


def _redact(text: str, secrets: frozenset) -> str:
    """Longest-first ``[REDACTED]`` substitution, matching the S4 store writer's algorithm so the
    canonical record's message is scrubbed identically to every persisted artifact."""
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, REDACTED)
    return text


# --------------------------------------------------------------------------- git seam reads


def _git(repo: Path, *args: str) -> process.CommandResult:
    """A scrubbed-env git read/checkout through the sanctioned subprocess seam."""
    return process.run(
        ["git", "-C", str(repo), *args],
        cwd=Path(repo),
        timeout=_GIT_TIMEOUT,
        env=_workspace_mod._scrubbed_git_env(),
    )


def _origin_default_sha(base_checkout: Path) -> str | None:
    """Resolve the base checkout's committed origin-default sha (``refs/remotes/origin/HEAD``).

    This is REAL git state, so a ``base_sha`` argument is VERIFIED against it, never echoed.
    """
    result = _git(base_checkout, "rev-parse", "refs/remotes/origin/HEAD")
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _checkout_detached(repo: Path, sha: str) -> None:
    """Bind the base checkout to the verified origin-default sha, so the base baseline runs at the
    BOUND sha and not the checkout's (possibly reddened) local HEAD."""
    _git(repo, "checkout", "-q", sha)


# --------------------------------------------------------------------------- pytest seam reads


def _provision(adapter: object, worktree: Path, provisioner: object) -> object:
    return adapter.provision_environment(worktree, None, provisioner=provisioner)


def _invocation(worktree: Path, handle: object) -> SimpleNamespace:
    return SimpleNamespace(
        worktree=Path(worktree),
        interpreter=handle.interpreter,
        command=["-m", "pytest"],
        env=getattr(handle, "env", None),
    )


def _collect(adapter: object, worktree: Path, provisioner: object) -> object:
    """Canonical id set + collection evidence for ``worktree`` via the real adapter seam."""
    handle = _provision(adapter, worktree, provisioner)
    return adapter.canonical_collect(_invocation(worktree, handle))


def _raw_collect_output(adapter: object, worktree: Path, provisioner: object) -> str:
    """The raw ``--collect-only`` stdout+stderr, so a hard collection error can be SPLIT into an
    import error vs a syntax/config error (a distinction the frozen id set alone cannot carry)."""
    handle = _provision(adapter, worktree, provisioner)
    argv = [
        *process.build_launch_argv(handle.interpreter, ["-m", "pytest"], env=handle.env),
        "--collect-only",
        "-q",
    ]
    result = process.run(
        argv, cwd=Path(worktree), timeout=_COLLECT_TIMEOUT, env=getattr(handle, "env", None)
    )
    return result.stdout + "\n" + result.stderr


def _run_suite(adapter: object, worktree: Path, provisioner: object) -> object:
    """Provision, run the whole suite with a report-log, and classify — returning the real
    ``Evidence`` (its per-phase ``NodeRecord``s drive base-green + call-phase discrimination)."""
    return _verify.run_baseline(worktree, list(_BASELINE), adapter=adapter, provisioner=provisioner)


# --------------------------------------------------------------------------- node inspection


def _by_node(nodes: object) -> dict:
    grouped: dict[str, list] = {}
    for node in nodes:
        grouped.setdefault(node.nodeid, []).append(node)
    return grouped


def _phase(records: list, phase: str) -> list:
    return [r for r in records if r.phase == phase]


def _failed(record: object) -> bool:
    return record.outcome in (Outcome.FAILED, Outcome.BROKEN)


def _node_passed(nodeid: str, by_node: dict) -> bool:
    """A base id is still green iff it produced a call record, every phase passed, and none failed."""
    records = by_node.get(nodeid)
    if not records:
        return False
    if any(_failed(r) for r in records):
        return False
    call = _phase(records, "call")
    return bool(call) and all(r.outcome is Outcome.PASSED for r in call)


def _is_empty_parametrize(notset_id: str, by_node: dict) -> bool:
    """The empty-``parametrize`` third state: pytest collects a single ``[NOTSET]`` node SKIPPED at
    setup with an ``empty parameter set`` reason (never zero collection)."""
    for record in by_node.get(notset_id, ()):
        if (
            record.phase == "setup"
            and record.outcome is Outcome.SKIPPED
            and "empty parameter set" in str(record.longrepr)
        ):
            return True
    return False


def _exception_type(longrepr: object) -> str:
    """The ACTUAL raised exception type — read from the innermost traceback frame's
    ``reprfileloc.message`` (which carries the type even for a pytest-rewritten bare assert whose
    reprcrash message is only ``assert x == y``), never coerced to AssertionError."""
    if isinstance(longrepr, dict):
        entries = (longrepr.get("reprtraceback") or {}).get("reprentries") or []
        if entries:
            message = ((entries[-1].get("data") or {}).get("reprfileloc") or {}).get("message")
            if message:
                return str(message)
        crash = longrepr.get("reprcrash") or {}
        first = str(crash.get("message", "")).split("\n", 1)[0]
        if ":" in first:
            return first.split(":", 1)[0].strip()
        return first.strip() or "Exception"
    return "Exception"


def _red_record(nodeid: str, call: object, secrets: frozenset) -> RedRecord:
    """Build the canonical, redacted red-evidence record from a call-phase failure's longrepr."""
    longrepr = call.longrepr if isinstance(call.longrepr, dict) else {}
    crash = longrepr.get("reprcrash") or {}
    lineno = crash.get("lineno")
    message = _redact(str(crash.get("message", "")), secrets)
    return RedRecord(
        nodeid=nodeid,
        exception_type=_exception_type(call.longrepr),
        assertion_line=lineno if isinstance(lineno, int) else None,
        message=message,
    )


def _classify_targeted(
    nodeid: str, by_node: dict, secrets: frozenset
) -> tuple[str, RedRecord | None]:
    """Classify ONE targeted id's phase-outcome. Returns ``("behavioral_red", record)`` for a valid
    call-phase behavioral failure, else ``(reason, record_or_None)`` for its specific rejection.

    Discrimination is PHASE-based and TYPE-based, never whitelisted to AssertionError: a call-phase
    ImportError is invalid (imports are invalid at every phase); a passing call with ``wasxfail`` is
    an XPASS; a setup/teardown failure is infra breakage, not a behavioral red.
    """
    records = by_node.get(nodeid)
    if not records:
        return ("missing_targeted_id", None)
    if any(_failed(r) for r in _phase(records, "setup")):
        return ("setup_error", None)
    call = _phase(records, "call")
    if not call:
        return ("setup_error", None)
    call_record = call[0]
    if call_record.outcome is Outcome.SKIPPED:
        return ("all_skipped", None)
    if call_record.outcome is Outcome.PASSED:
        if any(_failed(r) for r in _phase(records, "teardown")):
            return ("teardown_error", None)
        if call_record.wasxfail is not None:
            return ("xpass", None)
        return ("not_red", None)
    # Call FAILED/BROKEN: a genuine behavioral red unless the raised type is an import error.
    record = _red_record(nodeid, call_record, secrets)
    if record.exception_type in _IMPORT_TYPES:
        return ("import_error", record)
    return ("behavioral_red", record)


# --------------------------------------------------------------------------- persistence


def _proof_dict(proof: RedProof) -> dict:
    return {
        "accepted": proof.accepted,
        "reason": proof.reason,
        "base_sha": proof.base_sha,
        "added_ids": list(proof.added_ids),
        "records": [
            {
                "nodeid": r.nodeid,
                "exception_type": r.exception_type,
                "assertion_line": r.assertion_line,
                "message": r.message,
            }
            for r in proof.records
        ],
    }


def _persist(store: RunStore, run_id: str, proof: RedProof, outcome: str) -> None:
    """Persist the canonical verdict manifest artifact PLUS the append-only ``red_proof`` event.

    Only the canonical record lands in the permanent manifest — never a raw pytest dump. The store
    redacts the event; the record messages are already redacted in place.
    """
    store.apply(run_id, lambda _record: {"red_proof": _proof_dict(proof)})
    event = {"transition": "red_proof", "outcome": outcome}
    if proof.reason:
        event["reason"] = proof.reason
    store.append_event(run_id, event)


def _pause(store: RunStore, run_id: str) -> None:
    """Transition the run to ``paused`` through the guarded state machine."""

    def _transform(record: dict) -> dict:
        transition(State(record["status"]), State.PAUSED)
        return {"status": State.PAUSED.value}

    store.apply(run_id, _transform)


def _reject(
    store: RunStore, run_id: str, reason: str, base_sha: str, records: tuple[RedRecord, ...]
) -> RedProof:
    proof = RedProof(
        accepted=False, reason=reason, records=records, base_sha=base_sha or "", added_ids=()
    )
    _pause(store, run_id)
    _persist(store, run_id, proof, "rejected")
    return proof


def _accept(
    store: RunStore,
    run_id: str,
    base_sha: str,
    records: tuple[RedRecord, ...],
    added: tuple[str, ...],
) -> RedProof:
    proof = RedProof(
        accepted=True,
        reason="behavioral_red",
        records=records,
        base_sha=base_sha,
        added_ids=added,
    )
    _persist(store, run_id, proof, "accepted")
    return proof


# --------------------------------------------------------------------------- the deterministic proof


def prove_red(
    run_id: str,
    *,
    targeted_ids: object,
    base_checkout: object,
    candidate_worktree: object,
    base_sha: str,
    adapter: object,
    provisioner: object = None,
    store: object = None,
    secrets: frozenset = frozenset(),
) -> RedProof:
    """The deterministic red proof — an ordered, self-refusing integrity control (see module doc)."""
    secrets = frozenset(secrets)
    st = store if store is not None else RunStore(secrets=secrets)
    targeted = tuple(targeted_ids)
    base_checkout = Path(base_checkout)
    candidate_worktree = Path(candidate_worktree)

    # (1) The bound sha must be the base checkout's VERIFIED committed origin-default HEAD.
    origin_sha = _origin_default_sha(base_checkout)
    if origin_sha is None or origin_sha != base_sha:
        return _reject(st, run_id, "sha_mismatch", origin_sha or base_sha, ())

    # (2) The base suite must be GREEN at the bound sha — run it at the checked-out origin sha,
    # never the base checkout's local HEAD.
    _checkout_detached(base_checkout, base_sha)
    base_collection = _collect(adapter, base_checkout, provisioner)
    base_ids = tuple(getattr(base_collection, "ids", ()) or ())
    base_evidence = _run_suite(adapter, base_checkout, provisioner)
    if base_evidence.status is not BaselineStatus.GREEN:
        return _reject(st, run_id, "baseline_not_green", base_sha, ())

    # (3) Collect the candidate. A hard collection error is split by type: an import error vs a
    # syntax/config collection error (both exit 2), or nothing collected.
    candidate_collection = _collect(adapter, candidate_worktree, provisioner)
    candidate_ids = tuple(getattr(candidate_collection, "ids", ()) or ())
    if not getattr(candidate_collection, "ok", True):
        output = _raw_collect_output(adapter, candidate_worktree, provisioner)
        if "SyntaxError" in output:
            reason = "collection_error"
        elif any(token in output for token in ("ModuleNotFoundError", "ImportError")):
            reason = "import_error"
        else:
            reason = "collection_error"
        return _reject(st, run_id, reason, base_sha, ())

    # (4) A preexisting base id that DISAPPEARED at the candidate is a hard failure.
    selection = adapter.select_baseline(base_ids, candidate_ids)
    if not selection.ok:
        return _reject(st, run_id, "base_id_disappeared", base_sha, ())

    # (5) A targeted id that REUSES a preexisting base id is a hard failure — never subtracted away.
    base_set = set(base_ids)
    if any(t in base_set for t in targeted):
        return _reject(st, run_id, "reused_base_id", base_sha, ())

    added = tuple(selection.added)

    # Run the candidate suite once: its per-phase records drive both base-green and the call-phase
    # discrimination below.
    candidate_evidence = _run_suite(adapter, candidate_worktree, provisioner)
    by_node = _by_node(candidate_evidence.nodes)

    # (6) EVERY base id must still be green when run at the candidate.
    if any(not _node_passed(bid, by_node) for bid in base_ids):
        return _reject(st, run_id, "baseline_not_green", base_sha, ())

    # An empty-``parametrize`` targeted unit collects only a ``[NOTSET]`` node — detected before the
    # identity check, whose exact-id comparison would otherwise misreport it as a missing id.
    candidate_set = set(candidate_ids)
    for t in targeted:
        notset = f"{t}[NOTSET]"
        if notset in candidate_set and _is_empty_parametrize(notset, by_node):
            return _reject(st, run_id, "empty_parametrize", base_sha, ())

    # (7) Collection IDENTITY: the computed ADDED set must equal the targeted set (SET equality, not
    # a count). An empty ADDED means the candidate authored no new test at all.
    if not added:
        return _reject(st, run_id, "no_tests_collected", base_sha, ())
    if set(added) != set(targeted):
        return _reject(st, run_id, "missing_targeted_id", base_sha, ())

    # (8) Call-phase discrimination over EVERY targeted id — all must be valid behavioral reds.
    records: list[RedRecord] = []
    for t in targeted:
        reason, record = _classify_targeted(t, by_node, secrets)
        if reason != "behavioral_red":
            return _reject(st, run_id, reason, base_sha, (record,) if record else ())
        records.append(record)

    return _accept(st, run_id, base_sha, tuple(records), added)


# --------------------------------------------------------------------------- authoring gate


def author_tests(
    run_id: str,
    *,
    author: object,
    existing_ids: object,
    dispositions: object,
    store: object = None,
) -> object:
    """The authoring entry: a buildable + revision-applied gate behind a discover-before-authoring
    disposition check.

    Every preexisting contract id must carry a keep/revise/supersede disposition BEFORE authoring
    (else the run pauses and ``author`` is never invoked); the buildable + revision-applied gate is
    the real ``engine.enter_authoring`` seam (which raises ``state.IllegalTransition`` and records
    no event when the run is ineligible, and records the single ``authoring`` event on the legal
    path). Only a fully legal run invokes ``author`` — exactly once.
    """
    st = store if store is not None else RunStore()
    record = st.read(run_id)
    revision_applied = "revision_ledger" in record

    # Discover-before-authoring: refuse (pause, author uninvoked, no authoring event) if any
    # existing id lacks a VALID disposition value. Checked before the event-recording gate so a
    # buildable+revision run with undisposed tests records no authoring event.
    for existing in existing_ids:
        if dispositions.get(existing) not in _VALID_DISPOSITIONS:
            _pause(st, run_id)
            raise ValueError(
                f"cannot author for {run_id!r}: existing id {existing!r} has no valid disposition"
            )

    # The buildable + revision-applied gate (records the single authoring event on success).
    engine.enter_authoring(run_id, revision_applied=revision_applied)
    return author(run_id=run_id, dispositions=dict(dispositions))


# --------------------------------------------------------------------------- suite-level disciplines


def _asserts(source: str) -> list[ast.Assert]:
    return [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Assert)]


def _asserts_nonzero_exit(asserts: list[ast.Assert]) -> bool:
    """True iff some assertion checks a ``.returncode`` is NON-zero (``!= 0`` or ``> 0``)."""
    for node in asserts:
        for cmp_node in ast.walk(node.test):
            if not isinstance(cmp_node, ast.Compare):
                continue
            operands = [cmp_node.left, *cmp_node.comparators]
            touches_returncode = any(
                isinstance(op, ast.Attribute) and op.attr == "returncode" for op in operands
            )
            has_zero = any(isinstance(op, ast.Constant) and op.value == 0 for op in operands)
            nonzero_op = any(isinstance(op, (ast.NotEq, ast.Gt)) for op in cmp_node.ops)
            if touches_returncode and has_zero and nonzero_op:
                return True
    return False


def _in_asserted_strings(asserts: list[ast.Assert]) -> list[str]:
    """The string literals X in an asserted ``X in <expr>`` (a comment or unasserted string never
    counts)."""
    found: list[str] = []
    for node in asserts:
        for cmp_node in ast.walk(node.test):
            if (
                isinstance(cmp_node, ast.Compare)
                and any(isinstance(op, ast.In) for op in cmp_node.ops)
                and isinstance(cmp_node.left, ast.Constant)
                and isinstance(cmp_node.left.value, str)
            ):
                found.append(cmp_node.left.value)
    return found


def reject_false_green(test_source: str) -> str | None:
    """The suite-level anti-false-green discipline: ``None`` ONLY when a blocked test asserts a
    NON-zero exit AND a keyword AND the offending test name; a reason string for every weaker shape
    (only-failed, exit-0, missing-name, comment-only).

    The keyword is a non-test-name asserted-``in`` string; the offending test name is an
    asserted-``in`` string that names a test (``test``-prefixed). Both must be ASSERTED, not merely
    present in a comment.
    """
    try:
        asserts = _asserts(test_source)
    except SyntaxError:
        return "unparseable test source"
    if not _asserts_nonzero_exit(asserts):
        return "does not assert a non-zero exit code"
    in_strings = _in_asserted_strings(asserts)
    has_name = any(s.startswith("test") for s in in_strings)
    has_keyword = any(not s.startswith("test") for s in in_strings)
    if not has_keyword:
        return "does not assert a diagnostic keyword"
    if not has_name:
        return "does not assert the offending test name"
    return None


def example_reproduced_verbatim(issue_example: str, fixtures: object) -> bool:
    """True iff EXACTLY ONE fixture reproduces ``issue_example`` byte-for-byte."""
    return sum(1 for value in fixtures.values() if value == issue_example) == 1
