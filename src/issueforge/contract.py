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
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from issueforge import engine, process, providers
from issueforge import verify as _verify
from issueforge import workspace as _workspace_mod
from issueforge.adapters.base import BaselineStatus, Outcome
from issueforge.io import WriteSeam
from issueforge.state import State, transition
from issueforge.store import REDACTED, RunStore, write_artifact

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
    head_sha: str = ""


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


# A traceback line naming a raised exception: a dotted class whose leaf ends in a Python-exception
# suffix, optionally followed by ``: <message>``. Matches ``ImportError: ...``,
# ``pkg.NotImplementedError``, ``SystemExit`` — never a rewritten-assert body line (``assert x == y``).
_EXC_HEADER = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt))\b\s*:?\s*(.*)$"
)
_LINE_RE = re.compile(r"\bline (\d+)\b")


def _strip_e(line: str) -> str:
    """Strip pytest's leading ``E`` traceback marker so the exception header can be matched."""
    line = line.rstrip()
    if line[:1] == "E":
        return line[1:].strip()
    return line.strip()


def _exc_from_text(text: str) -> tuple[str | None, str | None]:
    """Extract ``(exception_type, header_line)`` from a STRING ``longrepr``'s traceback text.

    The LAST exception-header line wins (the innermost raised type). This is why a call-phase
    ``ImportError`` whose longrepr is a plain string (not the default structured dict) is still
    recognized as an import error — invalid at every phase — rather than accepted as a red.
    """
    exc_type: str | None = None
    message: str | None = None
    for raw in text.splitlines():
        match = _EXC_HEADER.match(_strip_e(raw))
        if match:
            exc_type = match.group(1).split(".")[-1]
            message = _strip_e(raw)
    return exc_type, message


def _line_from_text(text: str) -> int | None:
    """The last ``line N`` reference in a string ``longrepr`` (best-effort source line)."""
    found: int | None = None
    for match in _LINE_RE.finditer(text):
        found = int(match.group(1))
    return found


def _exception_type(longrepr: object) -> str:
    """The ACTUAL raised exception type — from the innermost traceback frame's ``reprfileloc.message``
    for the structured dict form (which carries the type even for a pytest-rewritten bare assert whose
    reprcrash message is only ``assert x == y``), or parsed from the traceback text for the STRING
    form. Never coerced to AssertionError, and never a benign ``"Exception"`` when a real type is
    recoverable (so a string-form call-phase ImportError cannot slip through as a red)."""
    if isinstance(longrepr, dict):
        entries = (longrepr.get("reprtraceback") or {}).get("reprentries") or []
        if entries:
            message = ((entries[-1].get("data") or {}).get("reprfileloc") or {}).get("message")
            if message:
                return str(message)
        exc_type, _ = _exc_from_text(str((longrepr.get("reprcrash") or {}).get("message", "")))
        return exc_type or "Exception"
    if isinstance(longrepr, str):
        exc_type, _ = _exc_from_text(longrepr)
        return exc_type or "Exception"
    return "Exception"


def _red_record(nodeid: str, call: object, secrets: frozenset) -> RedRecord:
    """Build the canonical, redacted red-evidence record from a call-phase failure's ``longrepr``,
    handling BOTH the structured dict form and the plain-string form (so a string-form failure still
    yields an accurate type and a redacted message, never an empty record)."""
    longrepr = call.longrepr
    if isinstance(longrepr, dict):
        crash = longrepr.get("reprcrash") or {}
        lineno = crash.get("lineno")
        raw_message = str(crash.get("message", ""))
    elif isinstance(longrepr, str):
        lineno = _line_from_text(longrepr)
        _, header = _exc_from_text(longrepr)
        raw_message = header if header is not None else longrepr
    else:
        lineno = None
        raw_message = ""
    return RedRecord(
        nodeid=nodeid,
        exception_type=_exception_type(longrepr),
        assertion_line=lineno if isinstance(lineno, int) else None,
        message=_redact(str(raw_message), secrets),
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
    setup = _phase(records, "setup")
    teardown = _phase(records, "teardown")
    call = _phase(records, "call")
    # A setup-phase FAILED/BROKEN is infra breakage: the body never ran.
    if any(_failed(r) for r in setup):
        return ("setup_error", None)
    # A SKIPPED setup with no call (e.g. ``@pytest.mark.skip``) is a skip, NOT a setup error.
    if not call and any(r.outcome is Outcome.SKIPPED for r in setup):
        return ("all_skipped", None)
    if not call:
        return ("setup_error", None)
    # A teardown FAILED/BROKEN forbids a clean red EVEN when the call itself failed — the seam's
    # rule is that NO setup/teardown FAILED/BROKEN may exist anywhere for a trustworthy red, so an
    # infra-contaminated call failure is rejected as a teardown error, never accepted.
    if any(_failed(r) for r in teardown):
        return ("teardown_error", None)
    call_record = call[0]
    if call_record.outcome is Outcome.SKIPPED:
        return ("all_skipped", None)
    if call_record.outcome is Outcome.PASSED:
        if call_record.wasxfail is not None:
            return ("xpass", None)
        return ("not_red", None)
    # ONLY a genuine call-phase FAILED is a behavioral red (unless the raised type is an import
    # error, invalid at every phase). A call-phase BROKEN — an unknown/alternate outcome such as a
    # rerun — is infrastructure noise, not a clean red, and is rejected.
    if call_record.outcome is Outcome.FAILED:
        record = _red_record(nodeid, call_record, secrets)
        if record.exception_type in _IMPORT_TYPES:
            return ("import_error", record)
        return ("behavioral_red", record)
    return ("not_red", None)


# --------------------------------------------------------------------------- persistence


def _proof_dict(proof: RedProof) -> dict:
    return {
        "accepted": proof.accepted,
        "reason": proof.reason,
        "base_sha": proof.base_sha,
        "head_sha": proof.head_sha,
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


def _redact_strings(value: object, secrets: frozenset) -> object:
    """Recursively ``[REDACTED]``-scrub every string in a payload (longest-first per field)."""
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, dict):
        return {key: _redact_strings(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_strings(item, secrets) for item in value]
    return value


def _persist(store: RunStore, run_id: str, proof: RedProof, outcome: str) -> None:
    """Persist the canonical verdict manifest artifact PLUS the append-only ``red_proof`` event.

    Only the canonical record lands in the permanent manifest — never a raw pytest dump. The store
    does NOT redact manifest writes (only ``append_event``/``write_artifact`` redact), so EVERY string
    field of the payload — nodeid (a parametrized id can embed a secret), exception_type, message,
    reason, base_sha, added_ids — is scrubbed here BEFORE ``apply`` writes it, on both the accept and
    reject paths. The event is redacted by the store on append.
    """
    secrets = frozenset(getattr(store, "_secrets", set()))
    payload = _redact_strings(_proof_dict(proof), secrets)
    store.apply(run_id, lambda _record: {"red_proof": payload})
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
    head_sha: str,
) -> RedProof:
    proof = RedProof(
        accepted=True,
        reason="behavioral_red",
        records=records,
        base_sha=base_sha,
        added_ids=added,
        head_sha=head_sha,
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

    return _accept(st, run_id, base_sha, tuple(records), added, _resolve_head(candidate_worktree))


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


# =============================================================== S11 (#17): independent red review
#
# S10 proves the tests executed and FAILED at a bound sha. It CANNOT prove the red corresponds to the
# NAMED missing behavior. ``review_red_contract`` adds that irreducibly semantic gate: an independent,
# FRESH session reviews the REAL candidate worktree with the LITERAL proof command, is fail-loud on
# empty/non-zero/timeout, binds its verdict to the REAL candidate HEAD, runs a bounded batched-round
# protocol on its OWN under-lock counter, records an explicit override, and retains a fully-redacted
# structured audit packet.

_DEFAULT_MAX_ROUNDS = 2
_EXHAUSTIVE_INSTRUCTION = (
    "Independently review the observed red against the expected behavioral reason. This is the first "
    "pass: enumerate ALL blocking findings (weak golden, wrong-reason red, coverage gap); do not stop "
    "at the first."
)
_CONFIRMATION_INSTRUCTION = (
    "Confirmation round: verify the reproved red now corresponds to the expected behavioral reason. "
    "Reopen ONLY if you find a NEW blocking finding."
)
_BLOCKING_RE = re.compile(r"^blocking:([0-9]+)$")
_MISSING_CORRESPONDENCE = "missing-correspondence"


@dataclass(frozen=True)
class ContractReview:
    """The whole contract-review verdict: the verdict string, its acceptance, the bound reviewed head,
    the real reviewer session, the round count, the findings, and the retained audit-packet path."""

    verdict: str
    accepted: bool
    head_sha: str
    reviewer_session_id: str
    rounds: int
    findings: tuple[str, ...]
    packet_path: Path


# --------------------------------------------------------------------------- verdict vocabulary


def valid_contract_verdict(verdict: object) -> bool:
    """The verdict vocabulary: ``done`` | ``blocking:<positive int>`` | ``skipped:<one-line reason>``.

    A zero/negative/junk count, a bare keyword, a whitespace-only or multi-line skip reason, or an
    unknown token is rejected (ported from MARVIN's ``_validate_cross_review`` enforcement rule).
    """
    if not isinstance(verdict, str):
        return False
    if verdict == "done":
        return True
    match = _BLOCKING_RE.match(verdict)
    if match:
        return int(match.group(1)) > 0
    if verdict.startswith("skipped:"):
        reason = verdict[len("skipped:") :]
        # One NON-EMPTY line only: ``splitlines`` catches every line separator a downstream consumer
        # would honor (\n, \r, \r\n, and the Unicode line/paragraph separators), so ``\r``-embedded
        # or otherwise multi-line reasons are rejected, not just ``\n``.
        return bool(reason.strip()) and len(reason.splitlines()) == 1
    return False


# --------------------------------------------------------------------------- currency predicates


def red_evidence_current(manifest: dict, head: str) -> bool:
    """True iff the manifest's ``contract_review`` is EFFECTIVELY ``done`` at the current head.

    Currency reads the EFFECTIVE outcome: ``outcome`` when present (so a recorded override to ``done``
    recovers currency even though the reviewer ``verdict`` stays ``blocking:<n>`` for provenance),
    falling back to ``verdict`` when no outcome was recorded.
    """
    block = manifest.get("contract_review")
    if not isinstance(block, dict):
        return False
    effective = block.get("outcome") if "outcome" in block else block.get("verdict")
    return effective == "done" and block.get("head_sha") == head


def require_current_evidence(manifest: dict, head: str) -> None:
    """The consumer guard S12's freeze calls: refuse (``ValueError``) stale or non-current evidence."""
    if not red_evidence_current(manifest, head):
        raise ValueError(
            "contract-review evidence is stale or non-current: no ``done`` verdict bound to the "
            f"current head {head!r}"
        )


# --------------------------------------------------------------------------- git head resolution


def _resolve_head(worktree: Path) -> str:
    """The REAL current HEAD commit of ``worktree`` (never a caller-supplied sha).

    Uses ``rev-parse --verify HEAD^{commit}`` and checks the return code, so a non-repository, an
    unborn branch, or a non-commit ref fails loudly rather than yielding an empty/garbage ``"HEAD"``.
    """
    result = _git(worktree, "rev-parse", "--verify", "HEAD^{commit}")
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise ValueError(f"cannot resolve a committed HEAD in {worktree}")
    return sha


# --------------------------------------------------------------------------- input materialization


def _materialize_review_inputs(
    dest: Path, review_inputs: dict, proof_command: str, head_sha: str
) -> dict[str, Path]:
    """Write every review input to ``dest`` with EXACT BYTES before the review runs (S7 posture).

    ``dest`` is caller-supplied transient scratch (the reviewer's working material), deliberately
    OUTSIDE the run store, so it is registered on the guarded write seam as an ephemeral scratch
    root; every field is written UTF-8 byte-for-byte (no strip, no re-quote, no CRLF drift).
    """
    dest = Path(dest)
    seam = WriteSeam()
    seam.allow_scratch(dest)
    fields = {
        "diff": review_inputs["diff"],
        "contract": review_inputs["contract"],
        "manifest": review_inputs["manifest"],
        "red_evidence": review_inputs["red_evidence"],
        "proof_command": proof_command,
        "head_sha": head_sha,
    }
    out: dict[str, Path] = {}
    for name, value in fields.items():
        target = dest / f"{name}.txt"
        seam.write_text(target, value)
        out[name] = target
    return out


# --------------------------------------------------------------------------- audit packet


def _fail_cause(result: providers.AIResult) -> str:
    """The SPECIFIC fail-loud cause: timeout, then non-zero exit, then empty output."""
    if result.timed_out:
        return "timeout"
    if result.returncode != 0:
        return "nonzero_exit"
    return "empty_output"


def _write_packet(
    run_id: str,
    *,
    round_no: int,
    head_sha: str,
    proof_command: str,
    result: providers.AIResult,
    verdict: str,
    inputs: dict[str, Path],
    secrets: frozenset,
) -> Path:
    """Persist a STRUCTURED, redacted audit packet under the run dir (success AND failure paths).

    The text carries the reviewed head, the literal proof command, the reviewer provider + session,
    the invocation status, the reviewer stdout AND stderr, the verdict, and every materialized input
    (name + echoed content). The store's redacting writer scrubs every seeded secret to ``[REDACTED]``.
    """
    lines = [
        f"contract-review packet (round {round_no})",
        f"head_sha: {head_sha}",
        f"proof_command: {proof_command}",
        f"provider: {result.provider}",
        f"reviewer_session_id: {result.session_id}",
        f"status: {result.status.value}",
        f"verdict: {verdict}",
        "--- inputs ---",
    ]
    for name, path in inputs.items():
        try:
            content = Path(path).read_text(errors="replace")
        except OSError:
            content = ""
        lines.append(f"{name}: {content}")
    lines += ["--- stdout ---", result.stdout, "--- stderr ---", result.stderr]
    name = f"contract-review-packet-round-{round_no}-{result.session_id}.txt"
    return write_artifact(run_id, name, "\n".join(lines), secrets=secrets)


# --------------------------------------------------------------------------- persistence helpers


def _bump_rounds(st: RunStore, run_id: str) -> None:
    """Increment ``contract_review_rounds`` by ONE, cumulatively, THROUGH the under-lock apply.

    The existing value must be an actual non-negative ``int`` (never ``bool``/``float``/``str``): a
    lax ``int(existing)`` would launder ``True`` -> 2 or ``10.9`` -> 11 into a valid-looking counter.
    Fail loud on anything else so a forged counter cannot survive the lock.
    """

    def _transform(record: dict) -> dict:
        current = record.get("contract_review_rounds", 0)
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise TypeError(f"contract_review_rounds must be a non-negative int, got {current!r}")
        return {"contract_review_rounds": current + 1}

    st.apply(run_id, _transform)


def _persist_block(
    st: RunStore,
    run_id: str,
    *,
    verdict: str,
    head_sha: str,
    reviewer_session_id: str,
    authoring_session_id: str,
    provider: str,
    findings: tuple[str, ...],
    outcome: str,
    secrets: frozenset,
) -> None:
    """Persist the manifest ``contract_review`` block, REDACTED before it lands.

    The manifest write path does NOT redact (only ``append_event``/``write_artifact`` do), and a
    reviewer finding can echo a secret, so every string in the block is scrubbed here BEFORE
    ``apply`` — mirroring ``prove_red``'s ``_redact_strings`` — so a canary can never reach
    ``manifest.json`` raw on either the success or the failure path.
    """
    block = {
        "verdict": verdict,
        "head_sha": head_sha,
        "reviewer_session_id": reviewer_session_id,
        "authoring_session_id": authoring_session_id,
        "provider": provider,
        "findings": list(findings),
        "outcome": outcome,
    }
    redacted = _redact_strings(block, secrets)
    st.apply(run_id, lambda _r: {"contract_review": redacted})


def _compose_verdict(outcome: object) -> tuple[str, tuple[str, ...], bool]:
    """``done`` iff correspondence AND no findings; else ``blocking:<n>`` (synthesizing the
    missing-correspondence finding when correspondence is absent and no explicit finding was named)."""
    findings = tuple(outcome.findings)
    if outcome.correspondence and not findings:
        return "done", (), True
    if not findings:
        findings = (_MISSING_CORRESPONDENCE,)
    return f"blocking:{len(findings)}", findings, False


# --------------------------------------------------------------------------- the review gate


def _require_bound_proof(record: dict, base_sha: str, current_head: str) -> dict:
    """Refuse to review unless the run carries an ACCEPTED S10 proof bound to THIS candidate.

    Requires: accepted status, the ``behavioral_red`` reason, the SAME ``base_sha`` the review runs
    against, and a persisted proof ``head_sha`` equal to the current verified candidate HEAD — so a
    C1 proof plus a C2 test change (a HEAD the proof never covered) is rejected, not silently reviewed.
    """
    proof = record.get("red_proof")
    if not isinstance(proof, dict):
        raise ValueError("cannot review: no S10 red proof on the run (proof precedes review)")
    if proof.get("accepted") is not True:
        raise ValueError("cannot review: the S10 red proof is not accepted")
    if proof.get("reason") != "behavioral_red":
        raise ValueError(f"cannot review: red proof reason {proof.get('reason')!r} is not a red")
    if proof.get("base_sha") != base_sha:
        raise ValueError("cannot review: red proof base_sha does not match the review base_sha")
    proof_head = proof.get("head_sha")
    if not proof_head or proof_head != current_head:
        raise ValueError(
            "cannot review: red proof is not bound to the current candidate HEAD "
            f"(proof {proof_head!r} != head {current_head!r}); re-prove the changed contract"
        )
    return proof


def _fail_and_pause(
    st: RunStore,
    run_id: str,
    *,
    round_no: int,
    result: providers.AIResult,
    head_sha: str,
    proof_command: str,
    inputs: dict[str, Path],
    secrets: frozenset,
    authoring_session_id: str,
    cause: str,
    record_authoring: bool = False,
) -> Path:
    """Record a FAILED review (event + retained packet + a redacted ``blocking:1`` block) and PAUSE.

    Persisting a blocking block INVALIDATES any prior ``done`` at this head BEFORE the run pauses, so
    a failed shared-session / role / fail-loud attempt can never leave stale ``done`` evidence current.
    """
    event = {
        "transition": "review",
        "session_id": result.session_id,
        "round": round_no,
        "confirmed": False,
        "failed": True,
        "cause": cause,
    }
    if record_authoring:
        event["authoring_session_id"] = authoring_session_id
    st.append_event(run_id, event)
    packet_path = _write_packet(
        run_id,
        round_no=round_no,
        head_sha=head_sha,
        proof_command=proof_command,
        result=result,
        verdict="blocking:1",
        inputs=inputs,
        secrets=secrets,
    )
    _persist_block(
        st,
        run_id,
        verdict="blocking:1",
        head_sha=head_sha,
        reviewer_session_id=result.session_id,
        authoring_session_id=authoring_session_id,
        provider=result.provider,
        findings=(),
        outcome="blocking:1",
        secrets=secrets,
    )
    _pause(st, run_id)
    return packet_path


def review_red_contract(
    run_id: str,
    *,
    candidate_worktree: object,
    base_sha: str,
    proof_command: str,
    reviewer: object,
    authoring_session_id: str,
    materialize_dest: object,
    review_inputs: dict,
    provisioner: object,
    secrets: frozenset = frozenset(),
    max_rounds: int | None = None,
    reprove: object = None,
    fixer: object = None,
) -> ContractReview:
    """The independent semantic review of the red contract (see the S11 section doc).

    Consumes an ACCEPTED S10 red proof BOUND to the current candidate HEAD, binds the verdict to the
    REAL verified HEAD (re-checked after the reviewer completes), materializes every input before each
    review, runs the bounded batched-round protocol (one exhaustive pass -> fix -> RE-PROVE at a
    CHANGED head -> confirmation, reopening only on a finding never seen before), is fail-loud on any
    non-OK invocation, rejects a reviewer session that is not a fresh independent ``review`` session,
    increments its OWN under-lock counter, and retains a redacted structured audit packet every round.
    """
    st = RunStore()
    secrets = frozenset(secrets)
    worktree = Path(candidate_worktree)
    review_inputs = dict(review_inputs)
    bound = _DEFAULT_MAX_ROUNDS if max_rounds is None else max_rounds

    # Precondition: an accepted S10 proof bound to the current verified candidate HEAD (fix 1).
    record = st.read(run_id)
    current_head = _resolve_head(worktree)
    _require_bound_proof(record, base_sha, current_head)

    # Provision the real candidate worktree: the handle carries the network-off marker the reviewer
    # runs under (the raw provisioner is a callable; ``.network`` lives on the provisioned handle).
    handle = provisioner(worktree)

    authoring_ref = SimpleNamespace(session_id=authoring_session_id)
    seen_findings: set[str] = set()
    prior_sessions: set[str] = set()
    round_no = 0
    prev_head = current_head

    while True:
        round_no += 1
        _bump_rounds(st, run_id)

        head_before = _resolve_head(worktree)  # verified committed HEAD
        inputs = _materialize_review_inputs(
            materialize_dest, review_inputs, proof_command, head_before
        )
        packet = {
            "worktree": worktree,
            "proof_command": proof_command,
            "head_sha": head_before,
            "provisioner": handle,
            "inputs": inputs,
            "instruction": (
                _EXHAUSTIVE_INSTRUCTION if round_no == 1 else _CONFIRMATION_INSTRUCTION
            ),
            "prior_findings": sorted(seen_findings),
        }
        outcome = reviewer(packet)
        result = outcome.result

        # Session independence + role (US-9.5, fix 6): the reviewer must run a fresh, independent
        # ``review`` session — never the authoring session, never a reused earlier-round session, and
        # never a non-review role. Each failure persists a blocking failed review + pauses BEFORE
        # raising (so no prior ``done`` stays current), then raises.
        if result.role != "review":
            _fail_and_pause(
                st,
                run_id,
                round_no=round_no,
                result=result,
                head_sha=head_before,
                proof_command=proof_command,
                inputs=inputs,
                secrets=secrets,
                authoring_session_id=authoring_session_id,
                cause="role_mismatch",
            )
            raise ValueError(f"reviewer AIResult role must be 'review', got {result.role!r}")
        if result.session_id == authoring_session_id:
            _fail_and_pause(
                st,
                run_id,
                round_no=round_no,
                result=result,
                head_sha=head_before,
                proof_command=proof_command,
                inputs=inputs,
                secrets=secrets,
                authoring_session_id=authoring_session_id,
                cause="shared_session",
                record_authoring=True,
            )
            providers.validate_review_independence(result, authoring_ref)  # raises
        if result.session_id in prior_sessions:
            _fail_and_pause(
                st,
                run_id,
                round_no=round_no,
                result=result,
                head_sha=head_before,
                proof_command=proof_command,
                inputs=inputs,
                secrets=secrets,
                authoring_session_id=authoring_session_id,
                cause="reused_session",
            )
            raise providers.SharedSessionError(
                f"reviewer session {result.session_id!r} reused across review rounds"
            )
        prior_sessions.add(result.session_id)

        # Fail-loud (from S7): empty output / non-zero exit / timeout = FAILED, never a pass.
        if result.status is not providers.InvocationStatus.OK:
            packet_path = _fail_and_pause(
                st,
                run_id,
                round_no=round_no,
                result=result,
                head_sha=head_before,
                proof_command=proof_command,
                inputs=inputs,
                secrets=secrets,
                authoring_session_id=authoring_session_id,
                cause=_fail_cause(result),
            )
            return ContractReview(
                verdict="blocking:1",
                accepted=False,
                head_sha=head_before,
                reviewer_session_id=result.session_id,
                rounds=round_no,
                findings=(),
                packet_path=packet_path,
            )

        # TOCTOU guard (fix 3): the HEAD must not have moved WHILE the reviewer ran, else the reviewer
        # saw content that no longer matches the sha the verdict would bind to — fail closed.
        head_after = _resolve_head(worktree)
        if head_after != head_before:
            packet_path = _fail_and_pause(
                st,
                run_id,
                round_no=round_no,
                result=result,
                head_sha=head_before,
                proof_command=proof_command,
                inputs=inputs,
                secrets=secrets,
                authoring_session_id=authoring_session_id,
                cause="head_moved_during_review",
            )
            return ContractReview(
                verdict="blocking:1",
                accepted=False,
                head_sha=head_before,
                reviewer_session_id=result.session_id,
                rounds=round_no,
                findings=(),
                packet_path=packet_path,
            )

        # Healthy invocation: compose the semantic verdict, bound to the verified head.
        verdict, findings, done = _compose_verdict(outcome)
        packet_path = _write_packet(
            run_id,
            round_no=round_no,
            head_sha=head_before,
            proof_command=proof_command,
            result=result,
            verdict=verdict,
            inputs=inputs,
            secrets=secrets,
        )
        st.append_event(
            run_id,
            {
                "transition": "review",
                "session_id": result.session_id,
                "round": round_no,
                "confirmed": done,
                "verdict": verdict,
            },
        )
        _persist_block(
            st,
            run_id,
            verdict=verdict,
            head_sha=head_before,
            reviewer_session_id=result.session_id,
            authoring_session_id=authoring_session_id,
            provider=result.provider,
            findings=findings,
            outcome=verdict,
            secrets=secrets,
        )

        if done:
            return ContractReview(
                verdict=verdict,
                accepted=True,
                head_sha=head_before,
                reviewer_session_id=result.session_id,
                rounds=round_no,
                findings=findings,
                packet_path=packet_path,
            )

        # Blocking: reopen another bounded round ONLY for a finding never seen in ANY prior round
        # (fix 5, cumulative — an a->b->a sequence does not reopen on the repeated ``a``).
        is_new = any(f not in seen_findings for f in findings)
        seen_findings |= set(findings)
        should_continue = round_no < bound and fixer is not None and reprove is not None and is_new
        if not should_continue:
            _pause(st, run_id)
            return ContractReview(
                verdict=verdict,
                accepted=False,
                head_sha=head_before,
                reviewer_session_id=result.session_id,
                rounds=round_no,
                findings=findings,
                packet_path=packet_path,
            )

        # A test change RE-RUNS the full S10 predicate set and mints NEW sha-bound red evidence: fix,
        # require the HEAD to have genuinely ADVANCED (fix 2 — a no-op fixer that leaves the head
        # unchanged cannot reach ``done`` at the original head), then reprove that changed head.
        fixer()
        new_head = _resolve_head(worktree)
        if new_head == prev_head or new_head == head_before:
            _pause(st, run_id)
            return ContractReview(
                verdict=verdict,
                accepted=False,
                head_sha=head_before,
                reviewer_session_id=result.session_id,
                rounds=round_no,
                findings=findings,
                packet_path=packet_path,
            )
        prev_head = new_head
        review_inputs["red_evidence"] = reprove(new_head)


# --------------------------------------------------------------------------- override + finalize


def override_contract_review(
    run_id: str, *, by: str, reason: str, verdict: str, method: str
) -> dict:
    """Explicitly override a FAILED review, recording full provenance (US-5.4) — never a silent retry.

    Records an ``override`` event carrying who/why/when (a parseable ISO 8601 timestamp), the NEW
    verdict, the verdict it OVERRODE, the reviewed head, and the reviewer session/provider/method, then
    sets the manifest ``contract_review`` OUTCOME (the field currency reads) while preserving the
    original reviewer ``verdict`` for provenance. Refused (``ValueError``) unless there is a
    schema-complete FAILED review to override (a valid failed verdict with a real head/session/
    provider), the caller supplies non-empty ``by``/``reason``/``method``, and the replacement
    ``verdict`` is in the contract vocabulary. The reviewer is NEVER re-run.
    """
    if not (isinstance(by, str) and by.strip()):
        raise ValueError("override requires a non-empty 'by'")
    if not (isinstance(reason, str) and reason.strip()):
        raise ValueError("override requires a non-empty 'reason'")
    if not (isinstance(method, str) and method.strip()):
        raise ValueError("override requires a non-empty 'method'")
    if not valid_contract_verdict(verdict):
        raise ValueError(f"override verdict {verdict!r} is not a valid contract verdict")

    st = RunStore()
    record = st.read(run_id)
    block = record.get("contract_review")
    if not isinstance(block, dict):
        raise ValueError(f"cannot override {run_id!r}: no contract-review to override")
    prior = block.get("verdict")
    effective = block.get("outcome") if "outcome" in block else prior
    # Only a schema-complete FAILED review (a valid ``blocking:``/``skipped:`` verdict that is NOT
    # already effectively done, with real head/session/provider provenance) may be overridden.
    if not (isinstance(prior, str) and valid_contract_verdict(prior)) or prior == "done":
        raise ValueError(f"cannot override {run_id!r}: no valid failed review verdict to override")
    if effective == "done":
        raise ValueError(f"cannot override {run_id!r}: the review already succeeded")
    head_sha = block.get("head_sha")
    session_id = block.get("reviewer_session_id")
    provider = block.get("provider")
    if not (head_sha and session_id and provider):
        raise ValueError(
            f"cannot override {run_id!r}: the failed review lacks head/session/provider provenance"
        )

    event = {
        "transition": "override",
        "by": by,
        "reason": reason,
        "verdict": verdict,
        "overrode": prior,
        "head_sha": head_sha,
        "reviewer_session_id": session_id,
        "provider": provider,
        "method": method,
        "when": datetime.now(timezone.utc).isoformat(),
    }
    st.append_event(run_id, event)
    st.apply(
        run_id,
        lambda r: {"contract_review": {**r["contract_review"], "outcome": verdict}},
    )
    return event


def finalize_review(run_id: str, verdict: str) -> None:
    """Enforce the verdict vocabulary on the TERMINAL write, ATOMICALLY.

    Raises (leaving the run non-terminal, writing nothing) on a verdict outside the vocabulary. On a
    valid verdict, the terminal transition and the recorded verdict are written in ONE ``RunStore.apply``
    transaction: the transform validates the ``running -> completed`` edge FIRST (raising
    ``IllegalTransition`` before returning, so a paused run can never be left with current-looking
    ``done`` evidence and there is no verdict-written-then-transition-failed crash window).
    """
    if not valid_contract_verdict(verdict):
        raise ValueError(f"invalid terminal contract verdict {verdict!r}")

    def _transform(record: dict) -> dict:
        transition(State(record["status"]), State.COMPLETED)  # raises before any field is returned
        existing = record.get("contract_review", {})
        # The terminal ``outcome`` is what the currency check reads; ``verdict`` is the reviewer's
        # own finding and is PRESERVED when one is already recorded (an override sets outcome="done"
        # while keeping verdict="blocking:n"). Only mint ``verdict`` when the block has none yet, so
        # finalizing an overridden review never erases the reviewer's verdict/provenance.
        block = {**existing, "outcome": verdict}
        if "verdict" not in existing:
            block["verdict"] = verdict
        return {"status": State.COMPLETED.value, "contract_review": block}

    RunStore().apply(run_id, _transform)
