"""Committed PENDING acceptance suite for #115 (PoC-D): wire ``issueforge run ALIAS#N`` end-to-end.

This suite authors the OUTCOME contract for composing the three already-built Wave-1 seams —
``engine.run_candidate`` (#114), ``verify.issue_readiness`` (#112), and ``github.deliver_pr``
(#113) — as the engine's DEFAULT stage, driven on the BARE CLI path (``engine.run("DandD#111")``
with no ``stage=``/``gateway=`` kwargs) against a real temp DandD checkout with a FAKE Claude and a
FAKE GitHub.

The composed stage does NOT exist yet: the engine's default is still the stub ``_default_stage``
(``engine.py``), which appends one ``{"transition": "stage"}`` event and completes. So every test
here fails TODAY at a meaningful outcome assertion (the run completes as a stub instead of composing
the three seams), and each carries the literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#115)")`` marker so the suite is GREEN now and
flips to real green when #115 builds the composition.

Design (LOCKED by the human — module-level monkeypatch seams). The tests fake ONLY these five
external boundaries; everything else (worktree creation, ``run_baseline``, ``prove_red``,
``issue_readiness``, scope check, contract commit) runs REAL:

1. ``issueforge.providers.invoke`` — fake Claude (authoring phase, then implementation phase).
2. ``issueforge.github.GhWriteGateway`` — the class the composed stage constructs; replaced so the
   stage's ``GhWriteGateway(...)`` yields the recording fake gateway.
3. ``issueforge.github.issue_is_open`` — offline open-check (also ``engine.run``'s ``issue_open``
   default).
4. ``issueforge.github.read_issue_body`` — a NEW module seam the stage uses to read issue #111's
   body + stated files offline (created here with ``raising=False``; #115 spec-dev adds it).
5. ``issueforge.engine._poc_approver`` — a NEW module-level human-approval seam
   (``_poc_approver(review) -> bool``) the composed stage calls (created here with ``raising=False``;
   #115 spec-dev adds it).

Field-shape note the composed stage must NORMALIZE: #112 persists ``readiness`` as a dict plus a
nested ``ready_sha``; #113 expects ``readiness == "ready"`` (string) plus a top-level ``ready_sha``.
The composed #115 stage flattens #112's verdict to top-level ``readiness="ready"`` (string),
``ready_sha``, and ``pr_url``. The golden test asserts those flat fields.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

PENDING = "PENDING (#115)"

SPEC = "DandD#111"
ALIAS = "DandD"
ISSUE_NUMBER = 111

# Golden PR facts the FAKE gateway returns; the composed stage persists the flat ``pr_url`` from the
# gateway's returned url, so the golden below binds THIS exact url (not a value derived from a slug).
PR_NUMBER = 4242
PR_URL = f"https://github.com/MatthewDruhl/DandD/pull/{PR_NUMBER}"

# The DandD file layout the fake Claude drives: author a failing acceptance test, then implement it.
CONTRACT_PATH = "tests/test_greet.py"
WRITE_SCOPE_PATH = "src/dandd/greet.py"
# A test that is genuinely RED at base (``dandd.greet`` is absent) and GREEN after the fake impl.
_AUTHORED_TEST = "from dandd.greet import greet\n\n\ndef test_greetcase():\n    assert greet('sam') == 'hi sam'\n"
_IMPL_SOURCE = "def greet(name):\n    return f'hi {name}'\n"

DANDD_CONFIG = (
    'baseline = ["python", "-m", "pytest", "-p", "no:cacheprovider"]\nframework = "pytest"\n'
)


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


# --------------------------------------------------------------------------- fake Claude (invoke)


def _make_fake_invoke() -> SimpleNamespace:
    """A ``providers.invoke`` stand-in driving the two phases the #114 path runs in the candidate
    worktree: call #0 (authoring) writes the failing acceptance test into the contract path; call #1
    (implementation) writes the implementation into the write-scope path so the exact acceptance
    command lands GREEN. ``impl_fixes=False`` skips the implementation write so acceptance stays RED
    and the candidate is refused (the readiness/gate test). Returns a handle exposing the recorded
    calls and the callable itself. The provider only edits files; it never runs git."""
    handle = SimpleNamespace(calls=[], impl_fixes=True)

    def invoke(profile, prompt, *, cwd, run_id=None, role="primary", timeout=None, **_kw):
        cwd = Path(cwd)
        phase = "author" if not handle.calls else "impl"
        handle.calls.append({"phase": phase, "cwd": cwd, "prompt": prompt})
        if phase == "author":
            target = cwd / CONTRACT_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_AUTHORED_TEST)
        elif handle.impl_fixes:
            target = cwd / WRITE_SCOPE_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_IMPL_SOURCE)
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
    """Return a ``GhWriteGateway`` replacement class whose instances record every call in order.

    Every constructed instance appends itself to ``instances`` so a test can inspect the gateway the
    composed stage built internally. ``origin_sha`` echoes the pushed branch's LOCAL sha offline
    (git rev-parse of the candidate branch, falling back to HEAD, in the bound checkout) — the
    network-free stand-in for reading ``origin/<branch>`` after a real push — so post-push
    verification passes exactly when the candidate branch points at the candidate sha. ``open_pr``
    returns the fixed ``pr`` facts, so the golden proves the REAL returned url is what gets persisted.
    """
    pr_facts = dict(pr if pr is not None else {"number": PR_NUMBER, "url": PR_URL})

    class _FakeGateway:
        def __init__(self, *args, **kwargs):
            self.calls: list[tuple[str, dict]] = []
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

        def origin_sha(self, *, repo, branch, checkout=None, **_kw) -> str:
            self._rec("origin_sha", repo=repo, branch=branch, checkout=checkout)
            if checkout is None:
                return "unknown"
            for ref in (branch, "HEAD"):
                result = subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", str(ref)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            return "unknown"

        def open_pr(self, *, repo, base, head, issue, title, body, **_kw) -> dict:
            self._rec(
                "open_pr", repo=repo, base=base, head=head, issue=issue, title=title, body=body
            )
            return dict(pr_facts)

    return _FakeGateway


def _open_pr_count(instances: list) -> int:
    """Total ``open_pr`` calls across every constructed fake gateway (0 when none were built)."""
    return sum(gw.methods.count("open_pr") for gw in instances)


def _push_count(instances: list) -> int:
    return sum(gw.methods.count("push") for gw in instances)


# --------------------------------------------------------------------------- shared harness


def _seed_dandd(make_git_repo) -> Path:
    """Build a REAL temp DandD checkout with a committed pytest layout (green baseline) and register
    it under the ``DandD`` alias. The fake authoring/implementation invoke drives ``tests/`` red then
    green against THIS repo; ``run_candidate`` (real) creates its detached candidate worktree from it.
    """
    from issueforge import registry

    checkout = make_git_repo(
        name="dandd",
        origin="git@github.com:MatthewDruhl/DandD.git",
        branch="main",
        config=DANDD_CONFIG,
    )
    _run_git(checkout, "config", "user.email", "engine@issueforge.invalid")
    _run_git(checkout, "config", "user.name", "IssueForge Engine")
    (checkout / "src" / "dandd").mkdir(parents=True, exist_ok=True)
    (checkout / "src" / "dandd" / "__init__.py").write_text("")
    (checkout / "tests").mkdir(parents=True, exist_ok=True)
    (checkout / "tests" / "test_baseline.py").write_text("def test_baseline():\n    assert True\n")
    _run_git(checkout, "add", "-A")
    _run_git(checkout, "commit", "-qm", "dandd baseline layout")

    registry.register(ALIAS, str(checkout))
    return checkout


def _install_seams(
    monkeypatch, *, approve: bool = True, impl_fixes: bool = True, pr: dict | None = None
) -> SimpleNamespace:
    """Monkeypatch the five LOCKED external boundaries and return their handles.

    ``providers.invoke`` -> fake Claude; ``github.GhWriteGateway`` -> recording fake (its instances
    collected in ``gateways``); ``github.issue_is_open`` -> offline True; ``github.read_issue_body``
    (NEW, raising=False) -> the issue body + the stated files the human approves; ``engine._poc_approver``
    (NEW, raising=False) -> auto-approve or auto-reject. Everything else runs real.
    """
    from issueforge import engine, github, providers

    invoker = _make_fake_invoke()
    invoker.impl_fixes = impl_fixes
    gateways: list = []
    gateway_cls = _make_gateway_class(gateways, pr=pr)

    monkeypatch.setattr(providers, "invoke", invoker.invoke)
    monkeypatch.setattr(github, "GhWriteGateway", gateway_cls)
    monkeypatch.setattr(github, "issue_is_open", lambda slug, number, **_kw: True)
    monkeypatch.setattr(
        github,
        "read_issue_body",
        lambda slug, number, **_kw: {
            "body": f"{ALIAS}#{ISSUE_NUMBER} GREET: greet(name) must return 'hi <name>'",
            "files": [WRITE_SCOPE_PATH],
        },
        raising=False,
    )
    monkeypatch.setattr(engine, "_poc_approver", lambda review: approve, raising=False)

    return SimpleNamespace(invoker=invoker, gateways=gateways)


def _transitions(run_id: str) -> list[str]:
    """Every ``transition`` value in the run's event stream, in order (empty when no events)."""
    from issueforge.store import events_path

    path = events_path(run_id)
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        if "transition" in event:
            out.append(event["transition"])
    return out


def _poc_doc_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "poc.md"


# =========================================================================== the suite


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_run_composes_end_to_end_to_waiting_for_merge(make_git_repo, monkeypatch):
    """THE GOLDEN: the bare ``issueforge run DandD#111`` composes candidate -> readiness -> delivery
    into one waiting-for-merge PR, opening exactly one PR and leaving the normal DandD checkout
    byte-for-byte untouched.

    technical (contract): after engine.run("DandD#111") the persisted record has status ==
    "waiting-for-merge", readiness == "ready" (the flattened string), and pr_url == the fake
    gateway's returned url "https://github.com/MatthewDruhl/DandD/pull/4242"; the fake gateway's
    open_pr was called EXACTLY once; and the registered checkout's HEAD sha AND `git status
    --porcelain` are identical before and after the run.
    """
    checkout = _seed_dandd(make_git_repo)
    handles = _install_seams(monkeypatch)
    before_head = _head(checkout)
    before_status = _porcelain(checkout)

    from issueforge import engine, store

    result = engine.run(SPEC)
    run_id = result["run_id"]
    record = store.RunStore().read(run_id)

    # Composition outcome — the stub run (status "completed", no pr fields) fails every one of these.
    assert record["status"] == "waiting-for-merge"
    assert record.get("readiness") == "ready"
    assert record.get("pr_url") == PR_URL
    assert _open_pr_count(handles.gateways) == 1

    # The normal DandD checkout is never mutated (all work happens in the candidate worktree).
    assert _head(checkout) == before_head
    assert _porcelain(checkout) == before_status


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_default_path_uses_real_composition_not_stub(make_git_repo, monkeypatch):
    """The bare default path runs the real composition, not the stub stage: the stub's marker event is
    gone and the candidate/readiness/pr facts the composition produces are present.

    technical (contract): after engine.run("DandD#111") the run's event transitions do NOT contain
    the stub's "stage" transition, and the persisted record carries candidate_sha, readiness, and pr
    (the composed fields the stub never writes).
    """
    _seed_dandd(make_git_repo)
    _install_seams(monkeypatch)

    from issueforge import engine, store

    result = engine.run(SPEC)
    run_id = result["run_id"]
    record = store.RunStore().read(run_id)

    assert "stage" not in _transitions(run_id), "the stub _default_stage event must be absent"
    assert record.get("candidate_sha")
    assert record.get("readiness") == "ready"
    assert isinstance(record.get("pr"), dict)


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_rejection_at_approval_pauses_without_side_effects(make_git_repo, monkeypatch):
    """A human rejection at the contract-approval gate pauses the run and performs no GitHub mutation:
    no contract commit is frozen and the gateway is never pushed to or asked to open a PR.

    technical (contract): with _poc_approver monkeypatched to REJECT, after engine.run("DandD#111")
    the persisted status is "paused" (not "waiting-for-merge"), the record has no contract_commit, and
    across every constructed fake gateway push and open_pr were called ZERO times.
    """
    _seed_dandd(make_git_repo)
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
def test_readiness_gate_blocks_delivery(make_git_repo, monkeypatch):
    """When the fake implementation does NOT fix the failing test, the candidate is not ready and
    delivery is blocked: the run does not reach waiting-for-merge and no PR is pushed or opened.

    technical (contract): with the implementation phase leaving acceptance RED (impl_fixes=False), the
    authoritative verification/readiness gate refuses; after engine.run("DandD#111") the persisted
    status is "paused", readiness != "ready", and across every fake gateway push and open_pr were
    called ZERO times.
    """
    _seed_dandd(make_git_repo)
    handles = _install_seams(monkeypatch, impl_fixes=False)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused"
    assert record["status"] != "waiting-for-merge"
    assert record.get("readiness") != "ready"
    assert _push_count(handles.gateways) == 0
    assert _open_pr_count(handles.gateways) == 0


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_second_delivery_does_not_duplicate_pr(make_git_repo, monkeypatch):
    """Re-delivering the same already-delivered run is a no-op: exactly one PR is ever opened and the
    persisted PR facts are unchanged.

    technical (contract): after a successful engine.run("DandD#111") the composed gateway opened
    exactly one PR; feeding the resulting persisted record back through github.deliver_pr with a fresh
    gateway opens NO second PR (its open_pr count stays 0) and the persisted record["pr"] is
    byte-identical to after the first delivery.
    """
    _seed_dandd(make_git_repo)
    handles = _install_seams(monkeypatch)

    from issueforge import engine, github, store

    result = engine.run(SPEC)
    run_id = result["run_id"]
    record = store.RunStore().read(run_id)

    assert _open_pr_count(handles.gateways) == 1
    first_pr = dict(record["pr"])

    second_gateway_instances: list = []
    second_gateway = _make_gateway_class(second_gateway_instances)()
    github.deliver_pr(record, gateway=second_gateway, store=store.RunStore())

    assert second_gateway.methods.count("open_pr") == 0
    assert second_gateway.methods.count("push") == 0
    assert store.RunStore().read(run_id)["pr"] == first_pr


@pytest.mark.xfail(strict=True, reason="PENDING (#115)")
def test_poc_doc_records_scope_and_proof_command():
    """The PoC scope doc exists and records both the proof command and the deferred full-v1
    successors, so the PoC's boundary (it does not close #20/#21/#22) is written down.

    technical (contract): docs/poc.md exists and its text contains the proof command
    "issueforge run DandD#111" AND references each deferred successor "#20", "#21", and "#22".
    """
    doc = _poc_doc_path()
    assert doc.exists(), "docs/poc.md must record the PoC scope, proof command, and deferrals"
    text = doc.read_text(encoding="utf-8")
    assert "issueforge run DandD#111" in text
    assert "#20" in text
    assert "#21" in text
    assert "#22" in text
