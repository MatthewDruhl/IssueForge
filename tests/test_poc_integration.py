"""Committed PENDING acceptance suite for #115 (PoC-D): wire ``issueforge run ALIAS#N`` end-to-end.

This suite authors the OUTCOME contract for composing the three already-built Wave-1 seams —
``engine.run_candidate`` (#114), ``verify.issue_readiness`` (#112), and ``github.deliver_pr``
(#113) — as the engine's DEFAULT stage, driven on the BARE CLI path (``engine.run("DandD#111")``
with no ``stage=``/``gateway=`` kwargs) against a REAL temp DandD checkout with a FAKE Claude and a
FAKE GitHub.

The composed stage does NOT exist yet: the engine's default is still the stub ``_default_stage``
(``engine.py``), which appends one ``{"transition": "stage"}`` event and completes. So every test
here fails TODAY at a meaningful OUTCOME assertion (the run completes as a stub instead of composing
the three seams), and each carries the literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#115)")`` marker so the suite is GREEN now and
flips to real green when #115 builds the composition.

Design (LOCKED — module-level monkeypatch seams). The tests fake ONLY these five external
boundaries; everything else (offline ``workspace.fetch_default_sha`` + ``git fetch``, detached
linked-worktree creation, ``run_baseline``, ``prove_red``, ``issue_readiness``, the scope check, and
the contract commit) runs REAL:

1. ``issueforge.providers.invoke`` — fake Claude (authoring phase, then implementation phase).
2. ``issueforge.github.GhWriteGateway`` — the class the composed stage constructs; replaced with an
   HONEST recording gateway (``push`` records the pushed branch's real local sha; ``origin_sha``
   returns ONLY a pushed sha, never a HEAD fallback — so post-push verification is meaningful).
3. ``issueforge.github.issue_is_open`` — offline open-check that RECORDS its ``(slug, number)``.
4. ``issueforge.github.read_issue_body`` — a NEW seam that RECORDS its ``(slug, number)`` and returns
   the issue body + stated files (created here with ``raising=False``; #115 spec-dev adds it).
5. ``issueforge.engine._poc_approver`` — a NEW module-level approval seam
   (``_poc_approver(review) -> bool``) the composed stage calls with the real
   ``SimpleNamespace(diff=..., red_evidence=proof)`` review (created with ``raising=False``).

The registered DandD checkout has a LOCAL FETCHABLE bare origin so the REAL offline fetch succeeds,
while its persisted registry slug stays ``MatthewDruhl/DandD`` (registered under the GitHub URL, then
the origin URL is repointed at the local bare — the slug is read from the persisted registry, not the
live remote). Field-shape note the composed stage must NORMALIZE: #112 persists ``readiness`` as a
dict; #113 expects the flat top-level ``readiness == "ready"`` string + ``ready_sha``; the composed
#115 stage flattens to top-level ``readiness="ready"``, ``ready_sha``, and ``pr_url``.

Scope note: some deeper guarantees (full failure-stage attribution, byte-for-byte checkout
preservation beyond HEAD/porcelain, integrity-mutation readiness, and full composed resume/retry
duplicate-PR re-entry) are deferred to #124 and intentionally NOT asserted here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

PENDING = "PENDING (#115)"

SPEC = "DandD#111"
ALIAS = "DandD"
ISSUE_NUMBER = 111
EXPECTED_SLUG = "MatthewDruhl/DandD"

# Golden PR facts the FAKE gateway returns; the composed stage persists the flat ``pr_url`` from the
# gateway's returned url, so the golden below binds THIS exact url (not a value derived from a slug).
PR_NUMBER = 4242
PR_URL = f"https://github.com/MatthewDruhl/DandD/pull/{PR_NUMBER}"

# The DandD file layout the fake Claude drives: author a failing acceptance test, then implement it.
CONTRACT_PATH = "tests/test_greet.py"
WRITE_SCOPE_PATH = "src/dandd/greet.py"
OUT_OF_SCOPE_PATH = "src/dandd/extra.py"  # written by the scope-violation impl; NOT in write_scope.
# A test whose module IMPORTS cleanly at base (a WRONG-value ``greet`` stub is committed in the seed)
# so its base failure is a CALL-phase behavioral red (accepted by contract.prove_red), NOT a
# collection-phase ImportError (rejected as ``import_error``). The fake impl overwrites the stub with
# the correct value so acceptance goes GREEN.
_AUTHORED_TEST = "from dandd.greet import greet\n\n\ndef test_greetcase():\n    assert greet('sam') == 'hi sam'\n"
# The base stub: importable so the authored test fails in the CALL phase (behavioral_red), not at
# collection. The fake implementation phase OVERWRITES this with the correct value.
_BASE_IMPL_SOURCE = "def greet(name):\n    return 'WRONG'\n"
_IMPL_SOURCE = "def greet(name):\n    return f'hi {name}'\n"
_OUT_OF_SCOPE_SOURCE = "SENTINEL = 'out-of-scope change not in the approved write_scope'\n"

# ``python -m pytest`` runs with cwd=worktree, but ``dandd`` lives under ``src/``; a committed pytest
# ``pythonpath`` makes ``import dandd`` resolve so acceptance/baseline can reach GREEN in the worktree.
_PYPROJECT = '[tool.pytest.ini_options]\npythonpath = ["src"]\n'

# ``acceptance`` runs the authored acceptance test; ``baseline`` runs the whole committed suite. Both
# are argv arrays (config validates them); the composed #115 stage sources the acceptance command from
# here alongside the baseline the registry already reads.
DANDD_CONFIG = (
    'baseline = ["python", "-m", "pytest", "-p", "no:cacheprovider"]\n'
    'acceptance = ["python", "-m", "pytest", "tests/test_greet.py", "-p", "no:cacheprovider"]\n'
    'framework = "pytest"\n'
)

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


# --------------------------------------------------------------------------- git helpers


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=check, capture_output=True, text=True
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _porcelain(repo: Path) -> str:
    """The checkout's ``git status --porcelain`` — a FAILED status read RAISES (never inferred clean)."""
    result = _git(repo, "status", "--porcelain", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git status --porcelain failed in {repo}: {result.stderr.strip()}")
    return result.stdout


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0
    )


def _is_inside(child: Path, parent: Path) -> bool:
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


# --------------------------------------------------------------------------- fake Claude (invoke)


def _make_fake_invoke(impl_mode: str) -> SimpleNamespace:
    """A ``providers.invoke`` stand-in driving the two #114 phases in the candidate worktree.

    Call #0 (authoring) writes the failing acceptance test into the contract path. Call #1
    (implementation) depends on ``impl_mode``: ``"fix"`` writes only the in-scope implementation (so
    acceptance goes GREEN and run_candidate lands the candidate); ``"scope"`` writes the in-scope fix
    AND an OUT-OF-SCOPE file (so run_candidate still lands, but issue_readiness's scope predicate
    refuses); ``"nofix"`` writes nothing (acceptance stays RED, so run_candidate refuses the candidate
    at its OWN authoritative verification). The provider only edits files; it never runs git.
    """
    handle = SimpleNamespace(calls=[], impl_mode=impl_mode)

    def invoke(profile, prompt, *, cwd, run_id=None, role="primary", timeout=None, **_kw):
        cwd = Path(cwd)
        phase = "author" if not handle.calls else "impl"
        handle.calls.append({"phase": phase, "cwd": cwd, "prompt": prompt})
        if phase == "author":
            target = cwd / CONTRACT_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_AUTHORED_TEST)
        else:
            if handle.impl_mode in ("fix", "scope"):
                fix = cwd / WRITE_SCOPE_PATH
                fix.parent.mkdir(parents=True, exist_ok=True)
                fix.write_text(_IMPL_SOURCE)
            if handle.impl_mode == "scope":
                extra = cwd / OUT_OF_SCOPE_PATH
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_text(_OUT_OF_SCOPE_SOURCE)
        from issueforge.providers import InvocationStatus

        return SimpleNamespace(
            role=role,
            provider="fake",
            session_id=f"sess-{len(handle.calls)}",
            prompt=prompt,
            stdout="ok",
            stderr="",
            returncode=0,
            duration_ms=1.0,
            status=InvocationStatus.OK,
            timed_out=False,
            artifact_path=cwd / "transcript.txt",
        )

    handle.invoke = invoke
    return handle


# --------------------------------------------------------------------------- fake GitHub gateway


def _make_gateway_class(instances: list, *, default_branch: str = "main", pr: dict | None = None):
    """Return an HONEST ``GhWriteGateway`` replacement class; instances collect in ``instances``.

    ``push`` records the pushed branch's REAL local sha (git rev-parse of that branch in the bound
    checkout) into ``pushed``; ``origin_sha`` returns ONLY that recorded sha (else the sentinel
    ``"UNPUSHED"`` which FAILS the ``origin/<branch> == candidate_sha`` check). No HEAD fallback and no
    silent success, so a composed stage that skips the push cannot pass verification. ``open_pr``
    returns the fixed ``pr`` facts so the golden proves the REAL returned url is what gets persisted.
    """
    pr_facts = dict(pr if pr is not None else {"number": PR_NUMBER, "url": PR_URL})

    class _FakeGateway:
        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[str, dict]] = []
            self.pushed: dict = {}
            instances.append(self)

        @property
        def methods(self) -> list[str]:
            return [name for name, _ in self.calls]

        def _rec(self, name: str, **kw) -> None:
            self.calls.append((name, kw))

        def default_branch(self, *, repo, **_kw) -> str:
            self._rec("default_branch", repo=repo)
            return default_branch

        def push(self, *, repo, branch, checkout=None, **_kw) -> None:
            self._rec("push", repo=repo, branch=branch, checkout=checkout)
            sha = None
            if checkout is not None:
                result = subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", str(branch)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    sha = result.stdout.strip()
            self.pushed[branch] = sha

        def origin_sha(self, *, repo, branch, checkout=None, **_kw) -> str:
            self._rec("origin_sha", repo=repo, branch=branch, checkout=checkout)
            sha = self.pushed.get(branch)
            return sha if sha else "UNPUSHED"

        def open_pr(self, *, repo, base, head, issue, title, body, **_kw) -> dict:
            self._rec(
                "open_pr", repo=repo, base=base, head=head, issue=issue, title=title, body=body
            )
            return dict(pr_facts)

    return _FakeGateway


def _sole_gateway(instances: list):
    """The single composed gateway; asserts exactly one was constructed (composition ran delivery)."""
    assert len(instances) == 1, f"expected exactly one composed gateway, saw {len(instances)}"
    return instances[0]


def _open_pr_count(instances: list) -> int:
    return sum(gw.methods.count("open_pr") for gw in instances)


def _push_count(instances: list) -> int:
    return sum(gw.methods.count("push") for gw in instances)


# --------------------------------------------------------------------------- shared harness


def _seed_dandd(tmp_path: Path) -> SimpleNamespace:
    """Build a REAL temp DandD checkout with a LOCAL FETCHABLE bare origin and register it, then
    advance origin so a FRESH fetch is provably required.

    A bare origin holds the DandD pytest layout (green baseline) on ``main`` (S1); a clone is the
    checkout. The checkout is registered while its origin URL is the GitHub URL (so ``entry.slug``
    persists as ``MatthewDruhl/DandD`` and the default branch resolves), THEN the origin URL is
    repointed at the local bare. Finally origin/main is advanced by ONE commit (S2, pytest layout
    intact) WITHOUT fetching into the checkout — so the checkout's local HEAD stays S1 while origin is
    S2, and only a real ``git fetch origin main`` yields S2. Returns
    ``SimpleNamespace(checkout, stale_head=S1, fresh_tip=S2)``.
    """
    from issueforge import registry

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "src" / "dandd").mkdir(parents=True)
    (seed / "src" / "dandd" / "__init__.py").write_text("")
    # A WRONG-value greet stub committed at base: importable (so the authored test's base failure is a
    # CALL-phase behavioral red, not a collection ImportError) yet unexercised by the base suite (so
    # the baseline stays green at base). The fake impl phase overwrites it with the correct value.
    (seed / "src" / "dandd" / "greet.py").write_text(_BASE_IMPL_SOURCE)
    (seed / "tests").mkdir()
    (seed / "tests" / "test_baseline.py").write_text("def test_baseline():\n    assert True\n")
    # A committed pytest ``pythonpath`` so ``import dandd`` resolves from ``src/`` when pytest runs in
    # the worktree; without it the ``src/`` layout is unimportable and acceptance/baseline never green.
    (seed / "pyproject.toml").write_text(_PYPROJECT)
    # Ignore pytest's bytecode caches so the engine's ``git add -A`` implementation commit never stages
    # ``__pycache__/*.pyc`` (which the readiness scope predicate would otherwise flag as out-of-scope).
    (seed / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    (seed / ".issueforge.toml").write_text(DANDD_CONFIG)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "dandd baseline layout")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    _git(checkout, "config", "user.email", "engine@issueforge.invalid")
    _git(checkout, "config", "user.name", "IssueForge Engine")

    # Register under the GitHub URL (persists slug MatthewDruhl/DandD), then repoint at the local bare.
    _git(checkout, "remote", "set-url", "origin", "git@github.com:MatthewDruhl/DandD.git")
    entry = registry.register(ALIAS, str(checkout))
    assert entry.slug == EXPECTED_SLUG  # sanity: the persisted slug the composed path must resolve
    _git(checkout, "remote", "set-url", "origin", str(origin))

    stale_head = _head(
        checkout
    )  # S1: the checkout's local HEAD (what a fetch-skipping stage reuses)

    # Advance origin/main by ONE commit (S2) so a FRESH fetch is provably required: the checkout's
    # local HEAD stays S1 while origin/main moves to S2. The pytest layout is untouched (only a NOTES
    # file is added), so the baseline stays green at S2. We do NOT fetch into the checkout here (that
    # would populate its FETCH_HEAD/tracking to S2 and defeat the freshness proof); the advance clone's
    # successful push is itself the offline-fetchability proof.
    advance = tmp_path / "advance"
    subprocess.run(["git", "clone", "-q", str(origin), str(advance)], check=True)
    (advance / "NOTES.md").write_text("origin advanced to S2\n")
    _git(advance, "add", "-A")
    _git(advance, "commit", "-qm", "advance origin main to S2")
    _git(advance, "push", "-q", "origin", "main")
    fresh_tip = _head(advance)
    assert fresh_tip != stale_head  # S2 is genuinely ahead of the checkout's stale local HEAD

    return SimpleNamespace(checkout=checkout, stale_head=stale_head, fresh_tip=fresh_tip)


def _install_seams(
    monkeypatch, *, approve: bool = True, impl_mode: str = "fix", pr: dict | None = None
) -> SimpleNamespace:
    """Monkeypatch the five LOCKED external boundaries and return their recording handles."""
    from issueforge import engine, github, providers

    invoker = _make_fake_invoke(impl_mode)
    gateways: list = []
    gateway_cls = _make_gateway_class(gateways, pr=pr)
    issue_open_calls: list = []
    read_body_calls: list = []
    approver_reviews: list = []

    def fake_issue_open(slug, number, **_kw):
        issue_open_calls.append((slug, number))
        return True

    def fake_read_issue_body(slug, number, **_kw):
        read_body_calls.append((slug, number))
        return {
            "body": f"{EXPECTED_SLUG}#{ISSUE_NUMBER} GREET: greet(name) must return 'hi <name>'",
            "files": [WRITE_SCOPE_PATH],
            "contract_paths": [CONTRACT_PATH],
        }

    def fake_approver(review):
        approver_reviews.append(review)
        return approve

    monkeypatch.setattr(providers, "invoke", invoker.invoke)
    monkeypatch.setattr(github, "GhWriteGateway", gateway_cls)
    monkeypatch.setattr(github, "issue_is_open", fake_issue_open)
    monkeypatch.setattr(github, "read_issue_body", fake_read_issue_body, raising=False)
    monkeypatch.setattr(engine, "_poc_approver", fake_approver, raising=False)

    return SimpleNamespace(
        invoker=invoker,
        gateways=gateways,
        issue_open_calls=issue_open_calls,
        read_body_calls=read_body_calls,
        approver_reviews=approver_reviews,
    )


def _poc_doc_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "poc.md"


# =========================================================================== the suite


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_run_composes_end_to_end_to_waiting_for_merge(tmp_path, monkeypatch):
    """THE GOLDEN: bare ``issueforge run DandD#111`` drives the FULL delivery path — candidate ->
    readiness -> push -> origin verify -> one PR -> waiting-for-merge — with a two-commit candidate
    (contract then implementation) and the normal DandD checkout left untouched.

    technical (contract): after engine.run("DandD#111") the record has status == "waiting-for-merge",
    readiness == "ready" (flattened string), pr_url == the fake gateway's returned url
    "https://github.com/MatthewDruhl/DandD/pull/4242". The composed gateway's call sequence is EXACTLY
    ["default_branch","push","origin_sha","open_pr"] (open_pr never bypasses push/verify).
    contract_commit is a 40-hex commit DISTINCT from candidate_sha with ancestry
    base_sha -> contract_commit -> candidate_sha. candidate_sha is 40-hex and EQUALS both the pushed
    branch sha the gateway recorded AND ready_sha. base_sha == the FRESH origin tip (S2), not the
    checkout's stale local HEAD (S1). write_scope == the files read_issue_body returned for issue 111.
    The checkout's HEAD sha and `git status --porcelain` are identical before and after.
    """
    seeded = _seed_dandd(tmp_path)
    checkout = seeded.checkout
    handles = _install_seams(monkeypatch)
    before_head = _head(checkout)
    before_status = _porcelain(checkout)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    # Composition outcome — the stub run (status "completed", no composed fields) fails these.
    assert record["status"] == "waiting-for-merge"
    assert record.get("readiness") == "ready"
    assert record.get("pr_url") == PR_URL

    # The base was FETCHED fresh from origin (S2), not reused from the checkout's stale local HEAD (S1)
    # — a fetch-skipping stage that reuses local HEAD/FETCH_HEAD would bind S1 and fail here.
    assert record.get("base_sha") == seeded.fresh_tip
    assert record.get("base_sha") != seeded.stale_head

    # The approved write scope persisted for #112 is EXACTLY the files read_issue_body returned for
    # issue 111 — proving the seam's output feeds the persisted write_scope that the scope check reads,
    # not a hardcoded path.
    assert record.get("write_scope") == [WRITE_SCOPE_PATH]

    # FULL delivery path: the one composed gateway pushed and verified origin BEFORE opening the PR.
    gateway = _sole_gateway(handles.gateways)
    assert gateway.methods == ["default_branch", "push", "origin_sha", "open_pr"]

    # Two-commit candidate with the engine's contract commit frozen between base and implementation.
    worktree = Path(record["candidate_worktree"])
    base_sha = record["base_sha"]
    contract_commit = record["contract_commit"]
    candidate_sha = record["candidate_sha"]
    assert _FULL_SHA.match(contract_commit)
    assert _FULL_SHA.match(candidate_sha)
    assert contract_commit != candidate_sha
    assert contract_commit != base_sha
    assert _is_ancestor(worktree, base_sha, contract_commit)
    assert _is_ancestor(worktree, contract_commit, candidate_sha)

    # The pushed sha and the readiness signature both bind the exact candidate sha.
    branch = record["candidate_branch"]
    assert gateway.pushed.get(branch) == candidate_sha
    assert record.get("ready_sha") == candidate_sha

    # The normal DandD checkout is never mutated (all work happens in the isolated worktree).
    assert _head(checkout) == before_head
    assert _porcelain(checkout) == before_status


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_default_path_persists_composition_artifacts(tmp_path, monkeypatch):
    """The bare default path persists the composed artifacts the stub never writes — proving the real
    composition ran, via POSITIVE markers (no brittle stub-event negative).

    technical (contract): after engine.run("DandD#111") the record carries a 40-hex candidate_sha,
    readiness == "ready", record["pr"] is a dict, and status == "waiting-for-merge" — none of which
    the stub _default_stage produces.
    """
    _seed_dandd(tmp_path)
    _install_seams(monkeypatch)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert record.get("readiness") == "ready"
    assert isinstance(record.get("candidate_sha"), str) and _FULL_SHA.match(record["candidate_sha"])
    assert isinstance(record.get("pr"), dict)


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_alias_resolves_into_issue_read_and_open_check(tmp_path, monkeypatch):
    """The composed path resolves the DandD alias and reads the repo-qualified issue through the
    seams — a stage that hardcoded "DandD#111" instead of propagating the resolved slug fails.

    technical (contract): after engine.run("DandD#111") both github.issue_is_open AND the new
    github.read_issue_body seam were called with the RESOLVED registry slug "MatthewDruhl/DandD" and
    issue number 111 (read_issue_body is what the stub never calls).
    """
    _seed_dandd(tmp_path)
    handles = _install_seams(monkeypatch)

    from issueforge import engine

    engine.run(SPEC)

    assert (EXPECTED_SLUG, ISSUE_NUMBER) in handles.read_body_calls
    assert (EXPECTED_SLUG, ISSUE_NUMBER) in handles.issue_open_calls


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_human_approver_sees_test_diff_and_red_evidence(tmp_path, monkeypatch):
    """Before freezing the contract the composed stage shows the human approver the EXACT authored
    test diff AND the machine-checked red evidence — not an empty or fabricated review.

    technical (contract): after engine.run("DandD#111") the _poc_approver seam was called at least
    once, and the review it received carries a truthy .diff AND a truthy .red_evidence (the real
    review is SimpleNamespace(diff=<test diff>, red_evidence=<proof>)).
    """
    _seed_dandd(tmp_path)
    handles = _install_seams(monkeypatch)

    from issueforge import engine

    engine.run(SPEC)

    assert handles.approver_reviews, "the human approval seam was never consulted"
    review = handles.approver_reviews[0]
    assert getattr(review, "diff", None), "approver review is missing the authored test diff"
    assert getattr(review, "red_evidence", None), "approver review is missing the red evidence"


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_candidate_authored_in_detached_isolated_worktree_with_green_baseline(
    tmp_path, monkeypatch
):
    """Claude authors in a DETACHED linked worktree isolated from the normal checkout, and the
    committed DandD baseline is proven green before landing.

    technical (contract): after engine.run("DandD#111") the fake invoker's first (authoring) call ran
    in a cwd that is NOT the registered checkout and NOT inside it; that cwd equals the persisted
    candidate_worktree; the worktree's HEAD is detached (symbolic-ref -q HEAD is non-zero); and the
    persisted evidence records the baseline command GREEN. (Strict baseline-green-BEFORE-first-AI-edit
    ORDERING is deferred to #124; this M1 test asserts the observable green baseline + isolated
    detached worktree, not the internal ordering.)
    """
    checkout = _seed_dandd(tmp_path).checkout
    handles = _install_seams(monkeypatch)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert handles.invoker.calls, "authoring invoke was never called"
    author_cwd = Path(handles.invoker.calls[0]["cwd"])
    assert author_cwd.resolve() != checkout.resolve()
    assert not _is_inside(author_cwd, checkout)
    assert Path(record["candidate_worktree"]).resolve() == author_cwd.resolve()
    if author_cwd.exists():  # cleanup is deferred, so the detached worktree persists
        detached = _git(author_cwd, "symbolic-ref", "-q", "HEAD", check=False)
        assert detached.returncode != 0, "candidate worktree HEAD is not detached"

    baseline_ev = record["evidence"]["baseline"]
    assert "green" in str(baseline_ev["status"]).lower()


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_readiness_scope_gate_blocks_delivery(tmp_path, monkeypatch):
    """A candidate that PASSES run_candidate (acceptance + baseline green) but writes OUT-OF-SCOPE is
    refused by the #112 readiness scope predicate — proving the composition runs issue_readiness, not
    only run_candidate's own verification.

    technical (contract): with the impl writing both the in-scope fix (acceptance goes green so
    run_candidate lands the candidate) AND an out-of-scope file, after engine.run("DandD#111")
    readiness != "ready", status == "paused", and across every composed gateway push and open_pr were
    called ZERO times. Crucially the candidate LANDED (candidate_sha is a real 40-hex commit), so the
    refusal is necessarily POST-candidate = #112's scope predicate (run_candidate reads and persists
    write_scope but NEVER checks it; verify._scope_offenders does, on the PERSISTED write_scope ==
    the files read_issue_body returned for issue 111).
    """
    _seed_dandd(tmp_path)
    handles = _install_seams(monkeypatch, impl_mode="scope")

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused"
    assert record.get("readiness") != "ready"
    assert _push_count(handles.gateways) == 0
    assert _open_pr_count(handles.gateways) == 0

    # The candidate LANDED through run_candidate (acceptance green via the in-scope fix); a run that
    # refused the out-of-scope write INSIDE run_candidate, or paused before ever computing a candidate,
    # would have no candidate_sha. So a real 40-hex candidate_sha proves the refusal came AFTER the
    # candidate landed — i.e. from #112's scope predicate, which reads the PERSISTED write_scope.
    assert re.match(r"^[0-9a-f]{40}$", record["candidate_sha"])
    assert record["write_scope"] == [WRITE_SCOPE_PATH]


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_candidate_refused_when_acceptance_stays_red(tmp_path, monkeypatch):
    """When the fake implementation does NOT fix the failing test, run_candidate's OWN authoritative
    verification refuses the candidate (acceptance red) and delivery never happens.

    technical (contract): with the implementation phase leaving acceptance RED (impl_mode="nofix"),
    after engine.run("DandD#111") status == "paused", readiness != "ready", and across every composed
    gateway push and open_pr were called ZERO times. (This is the candidate-refused pause, distinct
    from the readiness scope gate above.)
    """
    _seed_dandd(tmp_path)
    handles = _install_seams(monkeypatch, impl_mode="nofix")

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused"
    assert record.get("readiness") != "ready"
    assert _push_count(handles.gateways) == 0
    assert _open_pr_count(handles.gateways) == 0


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_rejection_at_approval_pauses_without_side_effects(tmp_path, monkeypatch):
    """A human rejection at the contract-approval gate pauses the run with no GitHub mutation: no
    contract commit is frozen and the gateway is never pushed to or asked to open a PR.

    technical (contract): with _poc_approver monkeypatched to REJECT, after engine.run("DandD#111")
    the status is "paused" (not "waiting-for-merge"), the record has no contract_commit, and across
    every composed gateway push and open_pr were called ZERO times.
    """
    _seed_dandd(tmp_path)
    handles = _install_seams(monkeypatch, approve=False)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused"
    assert record["status"] != "waiting-for-merge"
    assert record.get("contract_commit") is None
    assert _push_count(handles.gateways) == 0
    assert _open_pr_count(handles.gateways) == 0


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_delivered_run_persists_pr_and_arms_duplicate_guard(tmp_path, monkeypatch):
    """A delivered run persists the full PR facts with status "waiting-for-merge", ARMING the
    idempotency guard (a re-entry over this record is a no-op).

    Full composed resume/retry re-entry (re-driving the same run through the composed entrypoint) is
    deferred to #124; here we assert the guard is armed by the composition — the least-impl-coupling
    faithful M1 check.

    technical (contract): after engine.run("DandD#111") the persisted record["pr"] equals
    {"number": 4242, "url": "https://github.com/MatthewDruhl/DandD/pull/4242", "status":
    "waiting-for-merge"} — the exact facts deliver_pr short-circuits on, so a second delivery over this
    record opens no duplicate PR.
    """
    _seed_dandd(tmp_path)
    _install_seams(monkeypatch)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record.get("pr") == {
        "number": PR_NUMBER,
        "url": PR_URL,
        "status": "waiting-for-merge",
    }


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_poc_doc_records_scope_command_and_deferral():
    """The PoC scope doc exists and records the proof command AND binds each deferred successor to
    deferral wording — a doc claiming #20/#21/#22 are done must fail.

    technical (contract): docs/poc.md exists, contains the proof command "issueforge run DandD#111",
    and for each of "#20"/"#21"/"#22" some line names the ref AND carries deferral wording
    ("defer" / "out of scope" / "not closed" / "successor").
    """
    doc = _poc_doc_path()
    assert doc.exists(), "docs/poc.md must record the PoC scope, proof command, and deferrals"
    text = doc.read_text(encoding="utf-8")
    assert "issueforge run DandD#111" in text

    defer_words = ("defer", "out of scope", "not closed", "successor")
    lines = text.splitlines()
    for ref in ("#20", "#21", "#22"):
        assert any(ref in line and any(w in line.lower() for w in defer_words) for line in lines), (
            f"{ref} must appear bound to deferral wording, not claimed as done"
        )
