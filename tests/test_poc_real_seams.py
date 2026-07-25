"""Committed PENDING acceptance suite for #129 (PoC-D real-run seams): wire the two production seams
the M1 build (#115/#128) stubbed so a real ``issueforge run DandD#111`` can launch the config-resolved
provider and gate the write scope through a human BEFORE authoring.

#115 shipped ``engine._poc_composed_stage`` with two deliberate stubs this suite drives out:

1. **Fake provider profile.** The composed stage calls ``run_candidate(..., profile=SimpleNamespace(
   name="poc"), invoke=providers.invoke)``. A real run needs the config-resolved ``roles.primary``
   (a real ``config.Profile`` whose ``executable``/``start``/``resume`` ``providers.invoke`` renders),
   resolved from the FETCHED worktree's committed ``.issueforge.toml``.
2. **No pre-authoring scope gate.** ``write_scope`` comes from ``read_issue_body``'s ``files`` (``[]``
   for the plain-bug DandD#111), and the only human approval is the POST-authoring diff review. #115's
   criterion requires the human to approve the write scope BEFORE authoring; that gate must exist as a
   NEW module-level seam ``engine._poc_scope_approver(stated_files) -> list[str] | None``.

Every test here fails TODAY at a meaningful OUTCOME assertion, and each carries the literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#129)")`` marker so the suite is GREEN now and flips
to real green when #129 builds the wiring.

Anti-gaming design (hardened after Codex round 1): the profile is proven by FULL ``config.Profile``
equality against the profile resolved INDEPENDENTLY from the FRESH (S2) committed config, whose provider
argv carries a per-run RUNTIME sentinel that differs from the STALE (S1) checkout's — so a hardcoded
profile, a partial construction, a stale-checkout read, or a ``load_roles`` bypass all fail. The
persisted write scope carries a per-run runtime sentinel path so a hardcoded constant fails. The scope
gate's exact call argument and call sequence are pinned, and the REAL (unpatched) ``_poc_scope_approver``
default is exercised directly so an auto-approving ``return files`` production impl cannot pass.

Design (self-contained; same LOCKED external boundaries as the #115 suite plus the new scope gate). The
tests fake ONLY these six external boundaries; everything else runs REAL, including ``config.load_roles``
against a fetched ``.issueforge.toml`` carrying real ``[providers.*]`` + ``[roles]`` tables:

1. ``issueforge.providers.invoke`` — fake Claude that RECORDS the ``profile`` it was launched with.
2. ``issueforge.github.GhWriteGateway`` — the honest recording gateway the composed stage constructs.
3. ``issueforge.github.issue_is_open`` — offline open-check.
4. ``issueforge.github.read_issue_body`` — returns ``files=[]`` (like the real DandD#111: no machine-
   readable scope block), so the persisted write scope can ONLY come from the pre-authoring gate.
5. ``issueforge.engine._poc_approver`` — the POST-authoring authored-diff approval (approves here).
6. ``issueforge.engine._poc_scope_approver`` — the NEW pre-authoring scope gate (created with
   ``raising=False``; #129 spec-dev adds it). T4 exercises the REAL default (unpatched).
"""

from __future__ import annotations

import subprocess
import tempfile
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

PENDING = "PENDING (#129)"

SPEC = "DandD#111"
ALIAS = "DandD"
ISSUE_NUMBER = 111
EXPECTED_SLUG = "MatthewDruhl/DandD"

PR_NUMBER = 4242
PR_URL = f"https://github.com/MatthewDruhl/DandD/pull/{PR_NUMBER}"

CONTRACT_PATH = "tests/test_greet.py"
WRITE_SCOPE_PATH = "src/dandd/greet.py"

_AUTHORED_TEST = "from dandd.greet import greet\n\n\ndef test_greetcase():\n    assert greet('sam') == 'hi sam'\n"
_BASE_IMPL_SOURCE = "def greet(name):\n    return 'WRONG'\n"
_IMPL_SOURCE = "def greet(name):\n    return f'hi {name}'\n"

_PYPROJECT = '[tool.pytest.ini_options]\npythonpath = ["src"]\n'

PROVIDER_NAME = "claudecli"
PROVIDER_EXECUTABLE = ["claude"]

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]


def _config_text() -> str:
    """The committed ``.issueforge.toml``: the minimal build contract (baseline/acceptance/framework).
    Provider/role config is operator-level (#135), resolved from ``ISSUEFORGE_PROVIDERS``, not here."""
    return (
        'baseline = ["python", "-m", "pytest", "-p", "no:cacheprovider"]\n'
        'acceptance = ["python", "-m", "pytest", "tests/test_greet.py", "-p", "no:cacheprovider"]\n'
        'framework = "pytest"\n'
    )


# --------------------------------------------------------------------------- git helpers


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=check, capture_output=True, text=True
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _porcelain(repo: Path) -> str:
    result = _git(repo, "status", "--porcelain", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git status --porcelain failed in {repo}: {result.stderr.strip()}")
    return result.stdout


# --------------------------------------------------------------------------- fake Claude (invoke)


def _make_fake_invoke(seq: list, *, impl_mode: str = "fix") -> SimpleNamespace:
    """A ``providers.invoke`` stand-in that RECORDS the launched ``profile`` and drives the two #114
    phases. ``seq`` is the shared ordering log so a test can prove the scope gate ran BEFORE the first
    authoring invocation."""
    handle = SimpleNamespace(calls=[], impl_mode=impl_mode)

    def invoke(profile, prompt, *, cwd, run_id=None, role="primary", timeout=None, **_kw):
        cwd = Path(cwd)
        phase = "author" if not handle.calls else "impl"
        seq.append("invoke")
        handle.calls.append({"phase": phase, "cwd": cwd, "prompt": prompt, "profile": profile})
        if phase == "author":
            target = cwd / CONTRACT_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_AUTHORED_TEST)
        elif handle.impl_mode == "fix":
            fix = cwd / WRITE_SCOPE_PATH
            fix.parent.mkdir(parents=True, exist_ok=True)
            fix.write_text(_IMPL_SOURCE)
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


def _make_gateway_class(instances: list, *, default_branch: str = "main"):
    pr_facts = {"number": PR_NUMBER, "url": PR_URL}

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


def _push_count(instances: list) -> int:
    return sum(gw.methods.count("push") for gw in instances)


def _open_pr_count(instances: list) -> int:
    return sum(gw.methods.count("open_pr") for gw in instances)


# --------------------------------------------------------------------------- shared harness


def _seed_dandd(tmp_path: Path) -> SimpleNamespace:
    """A REAL temp DandD checkout with a LOCAL FETCHABLE bare origin (green baseline), registered under
    the persisted slug ``MatthewDruhl/DandD``. The STALE (S1) checkout HEAD trails the FRESH (S2) origin
    tip by one commit, so a stage that reads the config from the stale registered checkout instead of the
    fetched tip runs obsolete commands. Returns the fresh (S2) config text."""
    from issueforge import registry

    fresh_config = _config_text()

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "src" / "dandd").mkdir(parents=True)
    (seed / "src" / "dandd" / "__init__.py").write_text("")
    (seed / "src" / "dandd" / "greet.py").write_text(_BASE_IMPL_SOURCE)
    (seed / "tests").mkdir()
    (seed / "tests" / "test_baseline.py").write_text("def test_baseline():\n    assert True\n")
    (seed / "pyproject.toml").write_text(_PYPROJECT)
    (seed / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    (seed / ".issueforge.toml").write_text(_config_text())  # S1: stale checkout HEAD
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "dandd baseline layout")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    _git(checkout, "config", "user.email", "engine@issueforge.invalid")
    _git(checkout, "config", "user.name", "IssueForge Engine")

    _git(checkout, "remote", "set-url", "origin", "git@github.com:MatthewDruhl/DandD.git")
    entry = registry.register(ALIAS, str(checkout))
    assert entry.slug == EXPECTED_SLUG
    _git(checkout, "remote", "set-url", "origin", str(origin))

    stale_head = _head(checkout)

    # Advance origin/main by ONE commit (S2), so only a real fetch of the tip yields the fresh base. The
    # pytest layout is untouched, so the baseline stays green at S2. We do NOT fetch into the checkout
    # (that would defeat the freshness proof); the advance clone's successful push is the offline-
    # fetchability proof.
    advance = tmp_path / "advance"
    subprocess.run(["git", "clone", "-q", str(origin), str(advance)], check=True)
    (advance / ".issueforge.toml").write_text(fresh_config)  # S2 (identical minimal build contract)
    (advance / "NOTES.md").write_text("origin advanced to S2\n")
    _git(advance, "add", "-A")
    _git(advance, "commit", "-qm", "advance origin main to S2")
    _git(advance, "push", "-q", "origin", "main")
    fresh_tip = _head(advance)
    assert fresh_tip != stale_head

    return SimpleNamespace(
        checkout=checkout,
        stale_head=stale_head,
        fresh_tip=fresh_tip,
        fresh_config=fresh_config,
    )


def _expected_primary_profile(fresh_config: str):
    """The EXACT ``config.Profile`` the composed stage must launch: ``roles.primary`` resolved from the
    FRESH (S2) committed config the same way production must, via ``config.load_roles``."""
    from issueforge import config

    return config.load_roles(tomllib.loads(fresh_config)).primary


def _install_seams(monkeypatch, *, scope_return, impl_mode: str = "fix") -> SimpleNamespace:
    """Monkeypatch the six external boundaries. ``scope_return`` is what the NEW pre-authoring scope gate
    returns (a file list to approve, or ``None`` to reject). ``read_issue_body`` returns ``files=[]`` so
    the persisted write scope can ONLY come from the scope gate, and the gate's recorded call argument is
    exactly that ``[]``."""
    from issueforge import engine, github, providers

    # Role resolution is operator-level (#135): point ISSUEFORGE_PROVIDERS at a valid providers config so
    # the composed run resolves ``roles.primary`` from the operator config, not the repo's committed one.
    providers_toml = Path(tempfile.mkdtemp()) / "providers.toml"
    providers_toml.write_text(
        f"[providers.{PROVIDER_NAME}]\n"
        f"executable = {PROVIDER_EXECUTABLE!r}\n"
        'start = ["-p", "{prompt}"]\n'
        'resume = ["-p", "{prompt}", "--resume", "{session}"]\n'
        'auth = ["auth", "status"]\n'
        "\n[roles]\n"
        f'primary = "{PROVIDER_NAME}"\n'
    )
    monkeypatch.setenv("ISSUEFORGE_PROVIDERS", str(providers_toml))

    seq: list = []
    invoker = _make_fake_invoke(seq, impl_mode=impl_mode)
    gateways: list = []
    gateway_cls = _make_gateway_class(gateways)
    read_body_calls: list = []
    scope_calls: list = []

    def fake_issue_open(slug, number, **_kw):
        return True

    def fake_read_issue_body(slug, number, **_kw):
        read_body_calls.append((slug, number))
        return {
            "body": f"{EXPECTED_SLUG}#{ISSUE_NUMBER} GREET: greet(name) must return 'hi <name>'",
            "files": [],
            "contract_paths": [CONTRACT_PATH],
        }

    def fake_approver(review):
        return True

    def fake_scope_approver(stated_files):
        seq.append("scope_gate")
        scope_calls.append(list(stated_files) if stated_files is not None else stated_files)
        return scope_return

    monkeypatch.setattr(providers, "invoke", invoker.invoke)
    monkeypatch.setattr(github, "GhWriteGateway", gateway_cls)
    monkeypatch.setattr(github, "issue_is_open", fake_issue_open)
    monkeypatch.setattr(github, "read_issue_body", fake_read_issue_body, raising=False)
    monkeypatch.setattr(engine, "_poc_approver", fake_approver, raising=False)
    monkeypatch.setattr(engine, "_poc_scope_approver", fake_scope_approver, raising=False)

    return SimpleNamespace(
        seq=seq,
        invoker=invoker,
        gateways=gateways,
        read_body_calls=read_body_calls,
        scope_calls=scope_calls,
    )


# =========================================================================== the suite


# NOTE: the #129 profile test (``test_composed_stage_launches_config_resolved_primary_profile``) was
# REMOVED here by #135 as a logged contract amendment: role resolution moves from the fetched repo
# ``.issueforge.toml`` to an operator-level providers config, so its replacement lives in
# ``tests/test_poc_operator_providers.py::test_composed_stage_launches_operator_config_primary_profile``.


def test_pre_authoring_scope_gate_persists_human_approved_write_scope(tmp_path, monkeypatch):
    """The pre-authoring scope gate is consulted with the issue's stated files BEFORE the first authoring
    invocation, and the human-approved file list (a per-run value the gate returns) becomes the persisted
    ``write_scope`` — proving the scope source is the gate's RETURN, not ``read_issue_body`` (``files=[]``)
    or a hardcoded constant.

    technical (contract): with ``read_issue_body`` returning ``files=[]`` and the scope gate approving
    ``["src/dandd/greet.py", "<per-run sentinel path>"]``, after engine.run("DandD#111") the persisted
    ``write_scope`` equals that EXACT two-element list (the sentinel path is allowed-but-unchanged, so it
    does not affect readiness), the gate was called with the stated files ``[]`` (``scope_calls == [[]]``),
    the gate was recorded BEFORE the first ``providers.invoke`` call, and the run reaches
    ``status == "waiting-for-merge"``. A stage sourcing ``write_scope`` from ``read_issue_body["files"]``
    (``[]``) makes the in-scope ``greet.py`` an out-of-scope offender → ``not_ready`` → ``paused``; a
    stage hardcoding the public constant misses the per-run sentinel path.
    """
    _seed_dandd(tmp_path)
    sentinel_scope = [WRITE_SCOPE_PATH, f"src/dandd/allowed_{uuid.uuid4().hex}.py"]
    handles = _install_seams(monkeypatch, scope_return=sentinel_scope)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert (
        record.get("write_scope") == sentinel_scope
    )  # the gate's exact return is persisted verbatim

    # The gate was consulted with the stated files (read_issue_body's []), and BEFORE any authoring.
    assert handles.scope_calls == [[]]
    assert handles.seq.index("scope_gate") < handles.seq.index("invoke")


def test_scope_rejection_pauses_before_authoring_without_side_effects(tmp_path, monkeypatch):
    """A rejection at the pre-authoring scope gate pauses the run BEFORE any other work: the gate is the
    ONLY thing consulted, and no authoring invocation, worktree edit, push, or PR occurs.

    technical (contract): with the scope gate rejecting (returns ``None``), after engine.run("DandD#111")
    ``status == "paused"`` with a ``pause_reason`` naming the scope gate (contains "scope"), the gate was
    consulted exactly once with the stated files (``scope_calls == [[]]``) and NOTHING else ran before it
    (``seq == ["scope_gate"]``), ``providers.invoke`` was called ZERO times, across every composed gateway
    ``push`` and ``open_pr`` were called ZERO times, and the normal DandD checkout's HEAD sha and
    ``git status --porcelain`` are byte-for-byte identical before and after.
    """
    seeded = _seed_dandd(tmp_path)
    checkout = seeded.checkout
    handles = _install_seams(monkeypatch, scope_return=None)
    before_head = _head(checkout)
    before_status = _porcelain(checkout)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused"
    assert "scope" in str(record.get("pause_reason", "")).lower()
    assert handles.scope_calls == [
        []
    ]  # the gate WAS consulted (an impl that just paused would not)
    assert handles.seq == ["scope_gate"]  # nothing ran before the gate; the gate rejected first
    assert handles.invoker.calls == [], "authoring/implementation ran despite scope rejection"
    assert _push_count(handles.gateways) == 0
    assert _open_pr_count(handles.gateways) == 0
    assert _head(checkout) == before_head
    assert _porcelain(checkout) == before_status


def test_real_scope_approver_default_is_a_genuine_human_gate(monkeypatch):
    """The REAL (unpatched) ``engine._poc_scope_approver`` default is a genuine human gate: it reads the
    human's chosen file list from stdin and returns it; empty input or a closed stdin (EOF) REJECTS.
    A production impl that auto-approves (e.g. ``return files``) fails the reject cases.

    technical (contract): ``engine._poc_scope_approver(["src/dandd/greet.py"])`` returns
    ``["src/dandd/x.py", "src/dandd/y.py"]`` when the human enters ``"src/dandd/x.py src/dandd/y.py"``
    (the human's entry, NOT the stated argument, is returned — so the gate cannot be a pass-through of its
    argument); returns ``None`` when the human enters an empty line; and returns ``None`` on ``EOFError``
    (closed stdin). The gate never auto-approves.
    """
    from issueforge import engine

    def _answer(text):
        def _input(_prompt=""):
            return text

        return _input

    # Explicit human entry is returned as the approved list (proves it reads the human, not the argument).
    monkeypatch.setattr("builtins.input", _answer("src/dandd/x.py src/dandd/y.py"))
    assert engine._poc_scope_approver(["src/dandd/greet.py"]) == [
        "src/dandd/x.py",
        "src/dandd/y.py",
    ]

    # Empty entry rejects.
    monkeypatch.setattr("builtins.input", _answer(""))
    assert engine._poc_scope_approver(["src/dandd/greet.py"]) is None

    # A closed stdin (EOF) rejects, never auto-approves.
    def _eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert engine._poc_scope_approver(["src/dandd/greet.py"]) is None
