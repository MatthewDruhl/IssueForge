"""Committed PENDING acceptance suite for #114 (PoC-A): the engine produces ONE engine-owned
local candidate SHA from one already-shaped pytest issue, with Claude, WITHOUT pushing.

``issueforge.engine.run_candidate`` does not exist yet. Each test's FIRST statement calls the
``_run_candidate`` guard, which raises the dedicated ``MissingRunCandidate`` exception while the
symbol is absent; every test therefore carries
``@pytest.mark.xfail(strict=True, reason="PENDING (#114)")`` and reports XFAIL for that ONE reason
during the pending phase. (The canonical marker admits only ``strict`` + ``reason`` under the
#698 authoring lint, so the RIGHT-reason guarantee is enforced by the guard-first structure and
confirmed with ``--runxfail`` — which surfaces ``MissingRunCandidate`` as the concrete failure —
rather than by an ``xfail(raises=...)`` kwarg the lint forbids.) Symbols under test are imported
INSIDE each test body so collection never errors. Dual-layer docstrings: a plain-English behavior
summary, then the ``technical (contract):`` golden values.

The interface this suite authors (ATDD) — the candidate stage, engine-internal, invoked at the
engine level (never through cli.py), pluggable as ``engine.run(stage=...)``:

``engine.run_candidate(record, *, profile, approver, invoke=providers.invoke,
prove_red=contract.prove_red, run_baseline=verify.run_baseline) -> result``

``record`` is the persisted run record carrying the already-shaped inputs (issue body, the
registered repository ``repo`` alias, ``candidate_worktree``, ``base_sha``, ``write_scope``,
``contract_paths``, ``acceptance_command``, ``baseline_command``). The engine resolves the normal
checkout AND the verification adapter from the REGISTERED repository entry (issue "Fixed input:
registered repository entry"); ``base_checkout`` is NOT a persisted record key — it is the internal
keyword the engine hands to ``prove_red``. The returned object exposes ``.paused`` (bool),
``.pause_reason``, ``.contract_commit``, ``.candidate_sha``, and ``.evidence``.

Seam call contracts the engine honors:
- authoring: ONE ``invoke(profile, prompt, cwd=candidate_worktree, ...)`` whose prompt carries the
  full issue body, the allowed test paths, the baseline command, and an explicit instruction that
  the provider MAY edit/modify files but may NOT run git, push, open a pull request, or merge — the
  prohibitions expressed as negative language next to each operation, not a bare keyword list. The
  provider edits the worktree but never commits.
- red proof: ``prove_red`` (default ``contract.prove_red``) proves the authored tests collect and
  FAIL for the named behavior while the baseline stays green; the engine passes it the real seam's
  required arguments (``targeted_ids``, ``base_checkout``, ``candidate_worktree``, ``base_sha``,
  ``adapter``) and its evidence feeds the approver.
- approval: ``approver(review)`` sees ``review.diff`` (the exact authored test diff vs base) and
  ``review.red_evidence`` (the serialized proof: accepted status, base sha, node id, classification,
  marker). Rejection pauses with NO implementation invocation and NO contract commit.
- contract commit: the ENGINE commits the approved tests AFTER approval (Claude does not); the
  approver observes the candidate HEAD still at ``base_sha``.
- implementation: a SECOND ``invoke`` AFTER the contract commit, carrying the issue, the frozen
  authored test blob, the approved write scope, and every token of both verification commands.
- authoritative verification: ``run_baseline`` (default ``verify.run_baseline``) runs the acceptance
  AND baseline commands (one call each, no retry); the green/red verdict comes from IT, per command,
  never Claude's self-report. Either command non-green PAUSES.
- green landing: the ENGINE commits a SEPARATE implementation commit (child of the contract commit).
- persistence: the run record persists the eight candidate fields plus a summarized ``evidence``.
- forbidden ops: this slice does NO push, PR, merge, closeout, cleanup, override, or retry budget.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

PENDING = "PENDING (#114)"

REPO_ALIAS = "DandD"
ISSUE_BODY = "DandD#111 GREET-BEHAVIOR: greet(name) must return 'hi <name>'"
WRITE_SCOPE = ["src/dandd/greet.py"]
CONTRACT_PATHS = ["tests/test_greet.py"]
ACCEPTANCE_COMMAND = ["pytest", "tests/test_greet.py", "-k", "greetcase"]
BASELINE_COMMAND = ["pytest", "-p", "no:cacheprovider"]

RED_EVIDENCE_MARKER = "RED-EVIDENCE-MARKER"
AUTHORED_TEST_MARKER = "GREET-BEHAVIOR-TEST"
AUTHORED_NODE_ID = "tests/test_greet.py::test_greetcase"
# The exact bytes the authoring provider writes into the candidate worktree.
AUTHORED_BLOB = f"# {AUTHORED_TEST_MARKER}\ndef test_greetcase():\n    assert True\n"

# Negative / positive vocabulary the authoring instruction must place NEAR each operation.
_NEG = r"do not|don't|must not|may not|cannot|can't|will not|won't|never|not allowed|prohibited|forbidden"
_POS = r"\bmay\b|\bcan\b|\ballowed\b|\bpermitted\b"


# --------------------------------------------------------------------------- the pending guard


class MissingRunCandidate(Exception):
    """Raised ONLY when ``issueforge.engine.run_candidate`` is absent — the single sanctioned
    PENDING RED reason. Any OTHER exception raised by a test is a genuine failure, never a
    disguised pending."""


def _run_candidate():
    """Guard called as the FIRST statement of every test: return ``engine.run_candidate`` or raise
    :class:`MissingRunCandidate` when it does not exist yet. Because it runs before any fixture-built
    git state or store write is touched in the body, the pending RED can only come from this one
    missing symbol — confirmable with ``pytest --runxfail`` (which shows ``MissingRunCandidate``)."""
    from issueforge import engine

    fn = getattr(engine, "run_candidate", None)
    if fn is None:
        raise MissingRunCandidate("issueforge.engine.run_candidate is not implemented yet (#114)")
    return fn


# --------------------------------------------------------------------------- git helpers


def _run_git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _head(repo: Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD")


def _porcelain(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    ).stdout


def _sha_exists(repo: Path, sha: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _parent(repo: Path, sha: str) -> str:
    return _run_git(repo, "rev-parse", f"{sha}^")


def _all_commit_shas(repo: Path) -> set[str]:
    """Every commit reachable from any ref OR the reflog — so a commit-then-reset leaves a trace."""
    out = _run_git(repo, "rev-list", "--all", "--reflog")
    return set(out.split()) if out else set()


def _remote_refs(repo: Path) -> str:
    return _run_git(repo, "for-each-ref", "refs/remotes")


# --------------------------------------------------------------------------- text helpers


def _flatten(obj: object) -> str:
    """Best-effort searchable text for any object/dict/namespace the engine may pass or persist."""
    if isinstance(obj, str):
        return obj
    parts = [repr(obj)]
    try:
        parts.append(json.dumps(obj, default=str))
    except TypeError:
        pass
    data = getattr(obj, "__dict__", None)
    if data:
        parts.append(json.dumps(data, default=str))
    return " ".join(parts)


def _clauses(text: str) -> list[str]:
    """Split into clauses on sentence/list punctuation so a prohibition must bind to its OWN clause,
    not merely sit within N characters of an operation it does not govern."""
    return [c for c in re.split(r"[.;\n,:]", text) if c.strip()]


def _op_prohibited(text: str, op: str) -> bool:
    """True iff ``op`` is genuinely prohibited: it appears in at least one clause carrying negative
    language, and NO clause grants it permission (a positive verb without a negation). This rejects
    'Git is allowed. Do not push...' — the git clause permits git, so git is not prohibited."""
    mentions = [c for c in _clauses(text) if re.search(op, c, re.I)]
    if not mentions:
        return False
    prohibited = any(re.search(_NEG, c, re.I) for c in mentions)
    permitted = any(re.search(_POS, c, re.I) and not re.search(_NEG, c, re.I) for c in mentions)
    return prohibited and not permitted


def _op_permitted(text: str, op: str) -> bool:
    """True iff some clause explicitly permits ``op`` (positive verb, no negation)."""
    return any(
        re.search(op, c, re.I) and re.search(_POS, c, re.I) and not re.search(_NEG, c, re.I)
        for c in _clauses(text)
    )


# --------------------------------------------------------------------------- fake collaborators


def _green_evidence():
    from issueforge.adapters.base import BaselineStatus

    return SimpleNamespace(
        status=BaselineStatus.GREEN, collected=1, executed=1, nodes=(), exit_code=0
    )


def _red_evidence():
    from issueforge.adapters.base import BaselineStatus

    return SimpleNamespace(
        status=BaselineStatus.BEHAVIORAL_RED, collected=1, executed=1, nodes=(), exit_code=1
    )


def _accepted_proof(base_sha: str):
    record = SimpleNamespace(
        nodeid=AUTHORED_NODE_ID,
        exception_type="AssertionError",
        assertion_line=1,
        message=RED_EVIDENCE_MARKER,
    )
    return SimpleNamespace(
        accepted=True,
        reason="behavioral_red",
        records=(record,),
        base_sha=base_sha,
        added_ids=(AUTHORED_NODE_ID,),
        head_sha="",
    )


def _rejected_proof(base_sha: str):
    """A red proof that REFUSES: the authored tests did not establish meaningful red (e.g. they
    passed at base, or collected nothing). An engine that ignores ``accepted`` and presses on is
    caught by the rejected-proof test."""
    return SimpleNamespace(
        accepted=False,
        reason="not_red",
        records=(),
        base_sha=base_sha,
        added_ids=(),
        head_sha="",
    )


class _Fakes:
    """Records every seam call so ordering, arguments, per-command verdicts, and self-report
    independence are assertable. ``prove_red`` and ``run_baseline`` mirror the REAL seam signatures
    (keyword-only requireds, not ``**kwargs``) so a missing argument surfaces as a TypeError."""

    def __init__(
        self,
        worktree: Path,
        base_sha: str,
        *,
        acceptance_verdict="green",
        baseline_verdict="green",
        red_accepted=True,
        impl_status="OK",
        impl_stdout="",
        author_writes=True,
        impl_writes=True,
    ):
        self.worktree = worktree
        self.base_sha = base_sha
        self.red_accepted = red_accepted
        self.acceptance_verdict = acceptance_verdict
        self.baseline_verdict = baseline_verdict
        self.impl_status = impl_status
        self.impl_stdout = impl_stdout
        self.author_writes = author_writes
        self.impl_writes = impl_writes
        self.invoke_calls: list[dict] = []
        self.head_at_author: str | None = None
        self.head_at_impl: str | None = None
        self.head_at_approval: str | None = None
        self.prove_red_calls = 0
        self.prove_red_before_approver = False
        self.prove_red_kwargs: dict | None = None
        self.approver_reviews: list[object] = []
        self.baseline_calls: list[list[str]] = []
        self.run_baseline_kwargs: list[dict] = []
        self.order: list[str] = []

    # -- providers.invoke stand-in (authoring, then implementation) --
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
        cwd = Path(cwd)
        is_author = len(self.invoke_calls) == 0
        self.invoke_calls.append(
            {"prompt": prompt, "cwd": cwd, "role": role, "phase": "author" if is_author else "impl"}
        )
        if is_author:
            self.order.append("author")
            self.head_at_author = _head(cwd)
            if self.author_writes:
                target = cwd / CONTRACT_PATHS[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(AUTHORED_BLOB)
            status, stdout = "OK", "authored"
        else:
            self.order.append("impl")
            self.head_at_impl = _head(cwd)
            if self.impl_writes:
                target = cwd / WRITE_SCOPE[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("def greet(name):\n    return f'hi {name}'\n")
            status, stdout = self.impl_status, self.impl_stdout
        from issueforge.providers import InvocationStatus

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

    # -- contract.prove_red stand-in (mirrors the real keyword-only signature) --
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
        self.prove_red_calls += 1
        self.prove_red_before_approver = not self.approver_reviews
        self.prove_red_kwargs = {
            "targeted_ids": tuple(targeted_ids),
            "base_checkout": Path(base_checkout),
            "candidate_worktree": Path(candidate_worktree),
            "base_sha": base_sha,
            "adapter": adapter,
        }
        self.order.append("prove_red")
        return (
            _accepted_proof(self.base_sha) if self.red_accepted else _rejected_proof(self.base_sha)
        )

    # -- the human approver --
    def approver(self, review):
        self.approver_reviews.append(review)
        self.head_at_approval = _head(self.worktree)
        self.order.append("approver")
        return True

    def rejecting_approver(self, review):
        self.approver_reviews.append(review)
        self.head_at_approval = _head(self.worktree)
        self.order.append("approver")
        return False

    # -- verify.run_baseline stand-in (mirrors the real signature; per-command verdict) --
    def run_baseline(self, worktree, base_command, *, adapter, provisioner=None, timeout=None):
        cmd = list(base_command)
        self.baseline_calls.append(cmd)
        self.run_baseline_kwargs.append(
            {"worktree": Path(worktree), "command": cmd, "adapter": adapter}
        )
        self.order.append("run_baseline")
        verdict = self.acceptance_verdict if cmd == ACCEPTANCE_COMMAND else self.baseline_verdict
        return _green_evidence() if verdict == "green" else _red_evidence()


# --------------------------------------------------------------------------- shared setup


def _seed(make_git_repo):
    """Build a candidate worktree + a SEPARATE registered normal checkout, register the checkout
    under ``REPO_ALIAS`` (the issue's "registered repository entry" input), persist the input record
    WITHOUT a ``base_checkout`` key, and return (run_id, record, candidate, base_checkout, base_sha).
    """
    from issueforge import registry, store

    candidate = make_git_repo(name="candidate")
    base_checkout = make_git_repo(name="base-checkout")
    for repo in (candidate, base_checkout):
        _run_git(repo, "config", "user.email", "engine@issueforge.invalid")
        _run_git(repo, "config", "user.name", "IssueForge Engine")
    base_sha = _head(candidate)
    registry.register(REPO_ALIAS, str(base_checkout))

    run_id = "run-114"
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
    record = store.RunStore().read(run_id)
    return run_id, record, candidate, base_checkout, base_sha


def _call(record, fakes, approver=None):
    from issueforge import engine

    return engine.run_candidate(
        record,
        profile=SimpleNamespace(name="fake"),
        approver=approver if approver is not None else fakes.approver,
        invoke=fakes.invoke,
        prove_red=fakes.prove_red,
        run_baseline=fakes.run_baseline,
    )


# =========================================================================== the suite


def test_default_seams_reuse_the_real_provider_redproof_and_verify_code(isolated_state_home):
    """The candidate stage reuses the existing provider, red-proof, and baseline code: its seam
    defaults ARE providers.invoke, contract.prove_red, and verify.run_baseline.

    technical (contract): inspect.signature(engine.run_candidate).parameters -> the "invoke"
    default IS providers.invoke, the "prove_red" default IS contract.prove_red, and the
    "run_baseline" default IS verify.run_baseline (identity, not a re-implementation).
    """
    _run_candidate()
    from issueforge import contract, engine, providers, verify

    params = inspect.signature(engine.run_candidate).parameters
    assert params["invoke"].default is providers.invoke
    assert params["prove_red"].default is contract.prove_red
    assert params["run_baseline"].default is verify.run_baseline


def test_stage_runs_only_in_candidate_worktree_leaving_normal_checkout_untouched(
    make_git_repo, tmp_path, isolated_state_home
):
    """The stage writes only inside the isolated candidate worktree; the registered normal checkout's
    HEAD and working tree are byte-for-byte unchanged.

    technical (contract): after a successful run_candidate, the registered checkout's HEAD sha and
    `git status --porcelain` are identical to before the run, while the candidate worktree advanced
    to a new commit (its HEAD != base_sha).
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    before_head = _head(base_checkout)
    before_status = _porcelain(base_checkout)
    fakes = _Fakes(candidate, base_sha)

    _call(record, fakes)

    assert _head(base_checkout) == before_head
    assert _porcelain(base_checkout) == before_status
    assert _head(candidate) != base_sha


def test_authoring_invokes_provider_with_paths_baseline_and_a_no_git_instruction(
    make_git_repo, tmp_path, isolated_state_home
):
    """Test authoring goes through the provider invoke seam, in the candidate worktree, handing it
    the full issue body, the allowed test paths, the baseline command, and an explicit
    may-edit-but-no-git instruction expressed as prohibitions ON each operation.

    technical (contract): the FIRST invoke has cwd == candidate_worktree and a prompt containing the
    FULL ISSUE_BODY, every contract path, and every baseline token; each of git / push /
    pull-request|PR / merge is prohibited IN ITS OWN CLAUSE (a negation binds to that operation and
    no clause permits it), and edit|modify is explicitly permitted. A prompt that lists the keywords,
    or that permits an operation ("Git is allowed. Do not push..."), fails.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    _call(record, fakes)

    author = fakes.invoke_calls[0]
    assert author["cwd"] == candidate
    prompt = author["prompt"]
    assert ISSUE_BODY in prompt
    for path in CONTRACT_PATHS:
        assert path in prompt, f"authoring prompt missing contract path {path!r}"
    for token in BASELINE_COMMAND:
        assert token in prompt, f"authoring prompt missing baseline token {token!r}"
    assert _op_prohibited(prompt, r"\bgit\b"), "'git' is not prohibited in its own clause"
    assert _op_prohibited(prompt, r"\bpush"), "'push' is not prohibited in its own clause"
    assert _op_prohibited(prompt, r"pull request|\bPR\b"), "pull-request/PR not prohibited"
    assert _op_prohibited(prompt, r"\bmerg"), "'merge' is not prohibited in its own clause"
    assert _op_permitted(prompt, r"\bedit|\bmodif"), "no explicit edit/modify permission"


def test_authored_tests_are_red_proved_before_approval_reusing_prove_red(
    make_git_repo, tmp_path, isolated_state_home
):
    """The engine reuses the red proof to establish the authored tests fail for the named behavior
    (baseline still green) BEFORE it asks the human — the proof precedes approval.

    technical (contract): prove_red is invoked exactly once and BEFORE the approver is ever called
    (fakes.prove_red_before_approver is True); the run proceeds to approval on the accepted proof.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    _call(record, fakes)

    assert fakes.prove_red_calls == 1
    assert fakes.prove_red_before_approver is True
    assert fakes.order.index("prove_red") < fakes.order.index("approver")


def test_a_rejected_red_proof_pauses_before_approval_with_no_commit_or_implementation(
    make_git_repo, tmp_path, isolated_state_home
):
    """A red proof that REFUSES (accepted=False: the authored tests never established meaningful red)
    stops the run cold — the engine honors ``proof.accepted`` rather than pressing on to freeze tests
    that prove nothing.

    technical (contract): with prove_red returning accepted=False, run_candidate returns .paused True,
    .candidate_sha None, and .contract_commit None; the approver is NEVER called and NO implementation
    invoke occurs (invoke ran exactly once, authoring only); the candidate HEAD stays at base_sha with
    no new commit/reflog entry; the persisted status is "paused".
    """
    _run_candidate()
    from issueforge import store

    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha, red_accepted=False)
    before_commits = _all_commit_shas(candidate)

    result = _call(record, fakes)

    assert result.paused is True
    assert result.candidate_sha is None
    assert result.contract_commit is None
    assert fakes.prove_red_calls == 1
    assert fakes.approver_reviews == [], "a rejected red proof must NOT reach the human approver"
    assert len(fakes.invoke_calls) == 1
    assert fakes.invoke_calls[0]["phase"] == "author"
    assert _head(candidate) == base_sha
    assert _all_commit_shas(candidate) == before_commits
    assert store.RunStore().read(run_id)["status"] == "paused"


def test_approver_sees_the_exact_test_diff_and_the_red_evidence(
    make_git_repo, tmp_path, isolated_state_home
):
    """The human approver is shown the EXACT authored test diff against base and the complete
    machine-checked red evidence — not a fabricated or partial summary.

    technical (contract): review.diff is a real unified diff bound to base (`--- /dev/null`,
    `+++ b/tests/test_greet.py`) that changes EXACTLY ONE file (the contract path) with its added
    lines EXACTLY equal to the authored blob and NO removed lines and NO fabricated extra hunks.
    review.red_evidence, flattened, carries accepted=True plus the proof's node id, base_sha, failure
    classification, and the RED-EVIDENCE-MARKER.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    _call(record, fakes)

    review = fakes.approver_reviews[0]
    diff = review.diff
    # bound to base (the file is NEW at base_sha) and names ONLY the contract path
    assert "--- /dev/null" in diff
    assert f"+++ b/{CONTRACT_PATHS[0]}" in diff
    assert diff.count("diff --git ") == 1, "exactly one file may change; no fabricated extra hunks"
    assert f"diff --git a/{CONTRACT_PATHS[0]} b/{CONTRACT_PATHS[0]}" in diff
    assert WRITE_SCOPE[0] not in diff  # no impl leakage
    # the added content is EXACTLY the authored blob (rejects fabricated/extra added lines)
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert added == AUTHORED_BLOB.splitlines()
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed == [], "a NEW file must have no removed lines"

    ev = _flatten(review.red_evidence)
    assert re.search(r"accepted['\"]?\s*[:=]\s*true", ev, re.I), (
        "red_evidence must carry accepted=True"
    )
    assert RED_EVIDENCE_MARKER in ev
    assert AUTHORED_NODE_ID in ev
    assert base_sha in ev
    assert "behavioral_red" in ev or "AssertionError" in ev


def test_rejection_pauses_with_no_implementation_and_no_contract_commit(
    make_git_repo, tmp_path, isolated_state_home
):
    """A rejected review pauses the run: the implementation provider is never invoked and NO contract
    commit is ever created — not even created-then-reset.

    technical (contract): with a rejecting approver, run_candidate returns .paused True,
    .candidate_sha None, and .contract_commit None; invoke ran exactly once (authoring only); the
    approver observed HEAD == base_sha; the set of commits reachable by any ref OR reflog is
    unchanged (a commit-then-reset would leave a reflog trace); no persisted contract_commit; the
    persisted status is "paused".
    """
    _run_candidate()
    from issueforge import store

    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)
    before_commits = _all_commit_shas(candidate)

    result = _call(record, fakes, approver=fakes.rejecting_approver)

    assert result.paused is True
    assert result.candidate_sha is None
    assert result.contract_commit is None
    assert fakes.head_at_approval == base_sha
    assert len(fakes.invoke_calls) == 1
    assert fakes.invoke_calls[0]["phase"] == "author"
    assert _head(candidate) == base_sha
    assert _all_commit_shas(candidate) == before_commits
    saved = store.RunStore().read(run_id)
    assert saved["status"] == "paused"
    assert saved.get("contract_commit") is None


def test_engine_creates_the_contract_commit_after_approval_not_claude(
    make_git_repo, tmp_path, isolated_state_home
):
    """The engine — not Claude — creates the contract commit, and only AFTER approval: the provider
    AND the approver both saw an uncommitted worktree at base_sha, and the frozen contract commit is
    a real child of the base commit made once approval landed.

    technical (contract): the authoring invoke observed HEAD == base_sha AND the approver observed
    HEAD == base_sha (nothing was committed before approval); result.contract_commit != base_sha,
    `git cat-file -e` resolves it, its parent is base_sha; the impl invoke observed HEAD ==
    result.contract_commit; order: approver precedes impl.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    result = _call(record, fakes)

    assert fakes.head_at_author == base_sha
    assert fakes.head_at_approval == base_sha
    assert result.contract_commit != base_sha
    assert _sha_exists(candidate, result.contract_commit)
    assert _parent(candidate, result.contract_commit) == base_sha
    assert fakes.head_at_impl == result.contract_commit
    assert fakes.order.index("approver") < fakes.order.index("impl")


def test_implementation_invoked_after_the_contract_commit_with_scope_and_verification(
    make_git_repo, tmp_path, isolated_state_home
):
    """Implementation invokes the provider only after approval, on top of the frozen contract commit,
    handing it the issue, the FROZEN authored test contract, the approved write scope, and EVERY
    token of both verification commands.

    technical (contract): the SECOND invoke observed HEAD == result.contract_commit; its prompt
    contains the full ISSUE_BODY, the write-scope path, the CONTIGUOUS frozen authored test blob (as
    one substring), and BOTH verification commands as ordered serializations (`" ".join(command)`) —
    scattered fragments or shuffled tokens fail.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    result = _call(record, fakes)

    assert fakes.head_at_impl == result.contract_commit
    impl = fakes.invoke_calls[1]["prompt"]
    assert ISSUE_BODY in impl
    assert WRITE_SCOPE[0] in impl
    # the FROZEN contract: the authored test blob appears verbatim as one contiguous block
    assert AUTHORED_BLOB.strip() in impl
    # both commands as ordered, contiguous serializations (rejects shuffled tokens)
    assert " ".join(ACCEPTANCE_COMMAND) in impl
    assert " ".join(BASELINE_COMMAND) in impl


def test_verification_runs_the_authoritative_acceptance_and_baseline_commands(
    make_git_repo, tmp_path, isolated_state_home
):
    """After Claude exits, the engine runs the authoritative acceptance and full baseline commands
    itself, through the baseline runner — each exactly once (no retry), both after implementation.

    technical (contract): run_baseline is called with BOTH the acceptance command and the baseline
    command, each EXACTLY once (two calls total), and both AFTER the implementation invoke.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    _call(record, fakes)

    assert fakes.baseline_calls.count(ACCEPTANCE_COMMAND) == 1
    assert fakes.baseline_calls.count(BASELINE_COMMAND) == 1
    assert len(fakes.baseline_calls) == 2
    first_baseline = min(i for i, step in enumerate(fakes.order) if step == "run_baseline")
    assert fakes.order.index("impl") < first_baseline


def test_claimed_success_does_not_override_a_non_green_authoritative_verdict(
    make_git_repo, tmp_path, isolated_state_home
):
    """Claude's self-report is never evidence: a provider that CLAIMS success but leaves a non-green
    acceptance suite does not land a candidate — the authoritative verify verdict decides.

    technical (contract): with an impl invoke returning InvocationStatus.OK and stdout
    "ALL TESTS PASS", but the authoritative ACCEPTANCE command classifying BEHAVIORAL_RED,
    run_candidate returns .paused True and .candidate_sha None, and the candidate HEAD stays at the
    contract commit (no impl commit).
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(
        candidate,
        base_sha,
        acceptance_verdict="red",
        baseline_verdict="green",
        impl_status="OK",
        impl_stdout="ALL TESTS PASS",
    )

    result = _call(record, fakes)

    assert result.paused is True
    assert result.candidate_sha is None
    assert _head(candidate) == result.contract_commit


def test_baseline_regression_pauses_even_when_acceptance_is_green(
    make_git_repo, tmp_path, isolated_state_home
):
    """BOTH authoritative results control landing: a green acceptance suite over a RED full baseline
    (the implementation broke a preexisting test) must PAUSE, not land.

    technical (contract): with the acceptance command GREEN but the baseline command BEHAVIORAL_RED,
    run_candidate returns .paused True and .candidate_sha None, and the candidate HEAD stays at the
    contract commit.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha, acceptance_verdict="green", baseline_verdict="red")

    result = _call(record, fakes)

    assert result.paused is True
    assert result.candidate_sha is None
    assert _head(candidate) == result.contract_commit


def test_acceptance_failure_pauses_even_when_baseline_is_green(
    make_git_repo, tmp_path, isolated_state_home
):
    """The mirror case: a green full baseline over a RED acceptance suite (the target behavior is
    still unmet) must PAUSE, not land.

    technical (contract): with the acceptance command BEHAVIORAL_RED but the baseline command GREEN,
    run_candidate returns .paused True and .candidate_sha None, and the candidate HEAD stays at the
    contract commit.
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha, acceptance_verdict="red", baseline_verdict="green")

    result = _call(record, fakes)

    assert result.paused is True
    assert result.candidate_sha is None
    assert _head(candidate) == result.contract_commit


def test_green_lands_as_a_separate_implementation_commit_after_the_contract_commit(
    make_git_repo, tmp_path, isolated_state_home
):
    """A green result is committed by the engine as a SEPARATE implementation commit whose parent is
    the contract commit — the contract and the implementation are two distinct commits.

    technical (contract): result.candidate_sha != result.contract_commit, and
    `git rev-parse candidate_sha^` == result.contract_commit (the impl commit is a direct child of
    the contract commit).
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    result = _call(record, fakes)

    assert result.candidate_sha != result.contract_commit
    assert _parent(candidate, result.candidate_sha) == result.contract_commit


def test_run_record_persists_the_candidate_fields_and_summarized_evidence(
    make_git_repo, tmp_path, isolated_state_home
):
    """The run record persists the eight candidate fields the downstream integration issues read,
    plus a summarized ``evidence`` field that captures BOTH authoritative commands.

    technical (contract): after a green run, store.RunStore().read(run_id) carries contract_commit,
    candidate_sha, candidate_worktree, base_sha, write_scope, contract_paths, acceptance_command,
    baseline_command with their golden values, and a STRUCTURED ``evidence`` mapping keyed
    "acceptance"/"baseline", each entry carrying the command plus the GOLDEN status (green),
    collected==1, executed==1, exit_code==0 — wrong counts/codes are rejected, not just missing words.
    """
    _run_candidate()
    from issueforge import store

    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    result = _call(record, fakes)

    saved = store.RunStore().read(run_id)
    assert saved["contract_commit"] == result.contract_commit
    assert saved["candidate_sha"] == result.candidate_sha
    assert saved["candidate_worktree"] == str(candidate)
    assert saved["base_sha"] == base_sha
    assert saved["write_scope"] == WRITE_SCOPE
    assert saved["contract_paths"] == CONTRACT_PATHS
    assert saved["acceptance_command"] == ACCEPTANCE_COMMAND
    assert saved["baseline_command"] == BASELINE_COMMAND

    # structured per-command evidence with GOLDEN values (both commands GREEN, 1 collected/executed,
    # exit 0) — a summary carrying wrong counts or exit codes must fail.
    ev = saved["evidence"]
    assert isinstance(ev, dict), "evidence must be a structured per-command mapping"
    for key, cmd in (("acceptance", ACCEPTANCE_COMMAND), ("baseline", BASELINE_COMMAND)):
        entry = ev[key]
        assert entry["command"] == cmd
        assert "green" in str(entry["status"]).lower(), f"{key} status must be GREEN"
        assert entry["collected"] == 1
        assert entry["executed"] == 1
        assert entry["exit_code"] == 0


def test_candidate_sha_resolves_to_a_clean_local_commit(
    make_git_repo, tmp_path, isolated_state_home
):
    """The produced candidate SHA is a real local commit and the worktree is clean at it.

    technical (contract): `git cat-file -e candidate_sha` resolves, the candidate worktree HEAD ==
    result.candidate_sha, and `git status --porcelain` is empty (no dirty/uncommitted state).
    """
    _run_candidate()
    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    result = _call(record, fakes)

    assert _sha_exists(candidate, result.candidate_sha)
    assert _head(candidate) == result.candidate_sha
    assert _porcelain(candidate) == ""


def test_no_push_pr_merge_override_or_cleanup_occurs_in_this_slice(
    make_git_repo, tmp_path, isolated_state_home, monkeypatch
):
    """This slice produces a local candidate only: no GitHub mutation, no push/PR/merge command runs,
    no worktree cleanup, no extra provider calls, and no closeout/override/retry state.

    technical (contract): github.apply is never called; no command reaching the subprocess seam is a
    `git push`, a `gh pr ...`, or a `... merge`; exactly TWO provider invocations occur; the
    candidate's remote refs are unchanged; the candidate worktree still exists; and neither the
    persisted record nor the events log carries push/PR/merge/closeout/override/retry state.
    """
    _run_candidate()
    import subprocess as _sp

    from issueforge import github, store
    from issueforge.store import events_path

    seen_argv: list[list[str]] = []
    real_popen = _sp.Popen

    def spy_popen(args, *a, **k):
        try:
            seen_argv.append([str(x) for x in args] if not isinstance(args, str) else [args])
        except TypeError:
            seen_argv.append([str(args)])
        return real_popen(args, *a, **k)

    monkeypatch.setattr(_sp, "Popen", spy_popen)

    gh_calls: list = []
    monkeypatch.setattr(github, "apply", lambda *a, **k: gh_calls.append((a, k)))

    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)
    before_refs = _remote_refs(candidate)

    _call(record, fakes)

    assert gh_calls == []
    assert len(fakes.invoke_calls) == 2
    for argv in seen_argv:
        assert "push" not in argv, f"forbidden push command ran: {argv}"
        assert not ("gh" in argv and "pr" in argv), f"forbidden gh pr command ran: {argv}"
        assert "merge" not in argv, f"forbidden merge command ran: {argv}"
    assert _remote_refs(candidate) == before_refs
    assert Path(candidate).exists()

    saved = store.RunStore().read(run_id)
    for forbidden in ("pr", "branch", "merged", "retry_budget", "closeout", "override", "review"):
        assert forbidden not in saved, f"forbidden record key {forbidden!r}"
    ev_path = events_path(run_id)
    events_text = ev_path.read_text(encoding="utf-8").lower() if ev_path.exists() else ""
    for bad in ("push", "pull_request", "pr_create", "merge", "closeout", "override", "retry"):
        assert bad not in events_text, f"forbidden event marker {bad!r}"


def test_run_candidate_passes_the_real_seam_required_arguments(
    make_git_repo, tmp_path, isolated_state_home
):
    """Argument compatibility with the REAL default seams: run_candidate resolves the normal checkout
    and the pytest adapter from the REGISTERED repository entry and passes prove_red / run_baseline
    exactly the arguments their real signatures require — so swapping the fakes for the real seams
    would not fail on a missing/incompatible argument.

    technical (contract): prove_red receives the GOLDEN authored test id (the real node id derived
    from the authored tests, not just any nonempty value), candidate_worktree == the candidate,
    base_checkout == the REGISTERED checkout (resolved, and NOT the candidate), base_sha == base_sha,
    and adapter IS the registry-resolved pytest adapter; run_baseline receives the same resolved
    adapter and the candidate worktree on every call.
    """
    _run_candidate()
    from issueforge.adapters.base import registry as adapter_registry

    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    fakes = _Fakes(candidate, base_sha)

    _call(record, fakes)

    expected_adapter = adapter_registry.resolve(framework="pytest", reporter="pytest")
    assert expected_adapter is not None

    pk = fakes.prove_red_kwargs
    assert pk is not None, "prove_red was never called with the real keyword-only arguments"
    # the GOLDEN authored id, derived from the authored tests — not a fabricated placeholder
    assert AUTHORED_NODE_ID in pk["targeted_ids"], (
        f"prove_red must receive the authored id {AUTHORED_NODE_ID!r}, got {pk['targeted_ids']!r}"
    )
    assert pk["candidate_worktree"].resolve() == candidate.resolve()
    assert pk["base_checkout"].resolve() == base_checkout.resolve()
    assert pk["base_checkout"].resolve() != candidate.resolve()
    assert pk["base_sha"] == base_sha
    assert pk["adapter"] is expected_adapter

    assert fakes.run_baseline_kwargs, "run_baseline was never called"
    for rb in fakes.run_baseline_kwargs:
        assert rb["adapter"] is expected_adapter
        assert rb["worktree"].resolve() == candidate.resolve()


def test_candidate_stage_is_pluggable_into_the_engine_run_stage_seam(
    make_git_repo, tmp_path, isolated_state_home
):
    """The candidate stage is engine-internal and plugs into the existing engine.run(stage=...) seam:
    driven as a stage against a registered repository whose normal checkout is DISTINCT from the
    candidate worktree, one run lands a completed candidate.

    technical (contract): engine.run("DandD#111", stage=<adapter calling run_candidate on the seeded
    record>, issue_open=lambda *a: True, new_run_id=lambda: run_id) completes; the candidate worktree
    HEAD advanced past base_sha to result.candidate_sha; and the registered checkout is a different
    directory from the candidate worktree.
    """
    _run_candidate()
    from issueforge import engine, registry, store

    candidate = make_git_repo(name="candidate")
    base_checkout = make_git_repo(name="base-checkout")
    for repo in (candidate, base_checkout):
        _run_git(repo, "config", "user.email", "engine@issueforge.invalid")
        _run_git(repo, "config", "user.name", "IssueForge Engine")
    base_sha = _head(candidate)
    registry.register(REPO_ALIAS, str(base_checkout))
    fakes = _Fakes(candidate, base_sha)

    inputs = {
        "repo": REPO_ALIAS,
        "issue": ISSUE_BODY,
        "candidate_worktree": str(candidate),
        "base_sha": base_sha,
        "write_scope": WRITE_SCOPE,
        "contract_paths": CONTRACT_PATHS,
        "acceptance_command": ACCEPTANCE_COMMAND,
        "baseline_command": BASELINE_COMMAND,
    }
    seen: dict = {}

    def stage(record):
        store.RunStore().apply(record["run_id"], lambda _r: inputs)
        merged = store.RunStore().read(record["run_id"])
        seen["result"] = engine.run_candidate(
            merged,
            profile=SimpleNamespace(name="fake"),
            approver=fakes.approver,
            invoke=fakes.invoke,
            prove_red=fakes.prove_red,
            run_baseline=fakes.run_baseline,
        )

    engine.run(
        "DandD#111", stage=stage, issue_open=lambda *a, **k: True, new_run_id=lambda: "run-114"
    )

    assert base_checkout.resolve() != candidate.resolve()
    assert _head(candidate) != base_sha
    assert _head(candidate) == seen["result"].candidate_sha
