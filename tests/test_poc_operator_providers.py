"""Committed PENDING acceptance suite for #135 (amends #129): resolve provider roles from an
OPERATOR-level config, not the target repo's fetched committed ``.issueforge.toml``.

#129 shipped the composed run stage resolving ``roles.primary`` from the fetched repo config. Provider/
role config is operator/environment state, not a project property: DandD#188 keeps ``.issueforge.toml``
minimal, and ``provider-check --config <path>`` already reads providers from a standalone file. This
suite drives the move: role resolution reads an operator providers TOML located via
``paths.providers_config()`` (the ``ISSUEFORGE_PROVIDERS`` env var, else ``~/.config/issueforge/
providers.toml``), while the target repo's committed config stays the minimal build contract.

Every test fails TODAY at a meaningful OUTCOME assertion, and each carries the literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#135)")`` marker, so the suite is GREEN now and flips
to real green when #135 builds the operator-config resolution.

Anti-gaming design: the five tests pin OPERATOR-is-the-source, real ``roles.primary`` resolution, the
env→default resolution order, and repo-is-not-a-fallback:
- ``launches_operator_config_primary_profile``: a DECOY provider in the repo config AND a two-provider
  operator config (decoy table FIRST, real ``claude`` bound as ``roles.primary`` second, with NON-
  DEFAULT ``rate_limit``/``rate_limit_retries``). Full ``Profile`` equality kills repo-first,
  repo-then-operator-fallback, "pick the first table", and hardcoded-optional-field impls.
- ``default_providers_path_is_used_when_env_unset``: HOME isolated, ``ISSUEFORGE_PROVIDERS`` unset, a
  default ``~/.config/issueforge/providers.toml`` — kills an env-only resolver with no default.
- ``env_override_wins_over_default_providers_path``: both exist; the env override wins.
- ``repo_config_needs_no_provider_keys_to_run``: MINIMAL repo config (no providers/roles) still reaches
  waiting-for-merge — the repo contract stays minimal (DandD#188).
- ``missing_operator_config_pauses_even_when_repo_has_roles``: VALID repo roles but a MISSING operator
  config PAUSES — the operator config is required and the repo is never a fallback (a repo-config impl
  would succeed here).

Design (self-contained). The tests fake the same external boundaries as the #129 suite
(``providers.invoke`` recording its launched ``profile``, ``github.GhWriteGateway``,
``github.issue_is_open``, ``github.read_issue_body``, ``engine._poc_approver``,
``engine._poc_scope_approver``); everything else runs REAL, including ``config.load_roles`` against the
operator providers TOML the test writes and points ``ISSUEFORGE_PROVIDERS`` at.
"""

from __future__ import annotations

import pytest

import subprocess
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace


PENDING = "PENDING (#135)"

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

PROVIDER_NAME = "claude"
PROVIDER_EXECUTABLE = ["claude"]

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]

_MINIMAL_BUILD_CONFIG = (
    'baseline = ["python", "-m", "pytest", "-p", "no:cacheprovider"]\n'
    'acceptance = ["python", "-m", "pytest", "tests/test_greet.py", "-p", "no:cacheprovider"]\n'
    'framework = "pytest"\n'
)


# Distinctive NON-DEFAULT optional profile fields (defaults are rate_limit=None, rate_limit_retries=2),
# so a resolver that copies only the four argv fields and hardcodes the defaults fails full equality.
OP_RETRIES = 5
OP_RL_EXIT = 42


def _provider_table(
    name: str, profile_tag: str, *, retries: int | None = None, rl_exit: int | None = None
) -> str:
    """One ``[providers.<name>]`` table. ``retries``/``rl_exit`` add non-default optional fields."""
    lines = [
        f"[providers.{name}]",
        f"executable = [{name!r}]",
        f'start = ["-p", "{{prompt}}", "--session-id", "{{session}}", "--profile-tag", "{profile_tag}"]',
        'resume = ["-p", "{prompt}", "--resume", "{session}"]',
        'auth = ["auth", "status"]',
    ]
    if retries is not None:
        lines.append(f"rate_limit_retries = {retries}")
    if rl_exit is not None:
        lines.append(f"\n[providers.{name}.rate_limit]")
        lines.append(f'stderr_contains = "rate limit {profile_tag}"')
        lines.append(f"exit_code = {rl_exit}")
    return "\n".join(lines) + "\n"


def _provider_block(profile_tag: str) -> str:
    """A single-provider config bound as primary (the common single-profile case)."""
    return _provider_table(PROVIDER_NAME, profile_tag) + f'\n[roles]\nprimary = "{PROVIDER_NAME}"\n'


def _repo_config(profile_tag: str | None = None) -> str:
    """The repo's committed ``.issueforge.toml``. Minimal (build keys only) unless ``profile_tag`` is
    given, in which case a DECOY provider/role block is appended (to prove it is IGNORED for roles)."""
    text = _MINIMAL_BUILD_CONFIG
    if profile_tag is not None:
        text += "\n" + _provider_block(profile_tag)
    return text


def _operator_config_text(profile_tag: str) -> str:
    """A single-provider operator config (for the default-path / precedence tests)."""
    return _provider_block(profile_tag)


def _rich_operator_config(op_tag: str, decoy_tag: str) -> str:
    """An operator config with a DECOY provider table FIRST and the real ``claude`` provider second,
    bound as ``roles.primary`` and carrying NON-DEFAULT ``rate_limit``/``rate_limit_retries``. Forces a
    real ``roles.primary`` selection (not "pick the first table") and full optional-field resolution."""
    return (
        _provider_table("decoy", decoy_tag)
        + "\n"
        + _provider_table(PROVIDER_NAME, op_tag, retries=OP_RETRIES, rl_exit=OP_RL_EXIT)
        + f'\n[roles]\nprimary = "{PROVIDER_NAME}"\n'
    )


# --------------------------------------------------------------------------- git helpers


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=check, capture_output=True, text=True
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------- fake Claude (invoke)


def _make_fake_invoke(*, impl_mode: str = "fix") -> SimpleNamespace:
    handle = SimpleNamespace(calls=[], impl_mode=impl_mode)

    def invoke(profile, prompt, *, cwd, run_id=None, role="primary", timeout=None, **_kw):
        cwd = Path(cwd)
        phase = "author" if not handle.calls else "impl"
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


def _seed_dandd(tmp_path: Path, *, repo_profile_tag: str | None = None) -> SimpleNamespace:
    """A REAL temp DandD checkout with a LOCAL FETCHABLE bare origin (green baseline), registered under
    the persisted slug ``MatthewDruhl/DandD``, origin advanced one commit so a FRESH fetch is required.
    The committed ``.issueforge.toml`` is minimal unless ``repo_profile_tag`` embeds a decoy block."""
    from issueforge import registry

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
    (seed / ".issueforge.toml").write_text(_repo_config(repo_profile_tag))
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

    advance = tmp_path / "advance"
    subprocess.run(["git", "clone", "-q", str(origin), str(advance)], check=True)
    (advance / "NOTES.md").write_text("origin advanced to S2\n")
    _git(advance, "add", "-A")
    _git(advance, "commit", "-qm", "advance origin main to S2")
    _git(advance, "push", "-q", "origin", "main")
    fresh_tip = _head(advance)
    assert fresh_tip != stale_head

    return SimpleNamespace(checkout=checkout, stale_head=stale_head, fresh_tip=fresh_tip)


def _point_operator_config(
    tmp_path: Path, monkeypatch, text: str, *, name: str = "operator"
) -> str:
    """Write ``text`` to a providers TOML and point ``ISSUEFORGE_PROVIDERS`` at it. Returns ``text``."""
    path = tmp_path / f"{name}-providers.toml"
    path.write_text(text)
    monkeypatch.setenv("ISSUEFORGE_PROVIDERS", str(path))
    return text


def _isolate_home_default(tmp_path: Path, monkeypatch, text: str) -> str:
    """Isolate HOME to ``tmp_path`` and write the DEFAULT operator config
    (``~/.config/issueforge/providers.toml``). ``ISSUEFORGE_PROVIDERS`` is left UNSET so the default
    path is exercised. Returns ``text``. (Registry state stays isolated via conftest's
    ISSUEFORGE_STATE_HOME, which is independent of HOME.)"""
    monkeypatch.delenv("ISSUEFORGE_PROVIDERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    default = tmp_path / ".config" / "issueforge" / "providers.toml"
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text(text)
    return text


def _expected_primary_profile(operator_config: str):
    from issueforge import config

    return config.load_roles(tomllib.loads(operator_config)).primary


def _install_seams(monkeypatch, *, impl_mode: str = "fix") -> SimpleNamespace:
    from issueforge import engine, github, providers

    invoker = _make_fake_invoke(impl_mode=impl_mode)
    gateways: list = []
    gateway_cls = _make_gateway_class(gateways)

    monkeypatch.setattr(providers, "invoke", invoker.invoke)
    monkeypatch.setattr(github, "GhWriteGateway", gateway_cls)
    monkeypatch.setattr(github, "issue_is_open", lambda slug, number, **_kw: True)
    monkeypatch.setattr(
        github,
        "read_issue_body",
        lambda slug, number, **_kw: {
            "body": f"{EXPECTED_SLUG}#{ISSUE_NUMBER} GREET: greet(name) must return 'hi <name>'",
            "files": [],
            "contract_paths": [CONTRACT_PATH],
        },
        raising=False,
    )
    monkeypatch.setattr(engine, "_poc_approver", lambda review: True, raising=False)
    monkeypatch.setattr(
        engine, "_poc_scope_approver", lambda stated: [WRITE_SCOPE_PATH], raising=False
    )

    return SimpleNamespace(invoker=invoker, gateways=gateways)


# =========================================================================== the suite


@pytest.mark.slow
def test_composed_stage_launches_operator_config_primary_profile(tmp_path, monkeypatch):
    """The composed stage launches the profile from the OPERATOR providers config, and IGNORES a decoy
    provider block committed in the repo's ``.issueforge.toml`` — killing repo-first and repo-then-
    operator-fallback impls.

    technical (contract): the repo config carries a DECOY ``[providers.claude]`` (tag ``repo-<uuid>``);
    ``ISSUEFORGE_PROVIDERS`` points at an operator TOML with TWO providers — a ``[providers.decoy]``
    FIRST and ``[providers.claude]`` second bound as ``roles.primary``, carrying NON-DEFAULT
    ``rate_limit_retries`` (5) and ``rate_limit`` (exit_code 42). After engine.run("DandD#111") EVERY
    fake-invoke call's ``profile`` is byte-for-byte equal (full ``config.Profile`` dataclass) to
    ``config.load_roles(<operator config>).primary``: it is the ``claude`` profile (name != "decoy",
    proving ``roles.primary`` selection, not "first table"), its ``start`` carries the OPERATOR tag and
    NOT the repo decoy tag, and its NON-DEFAULT ``rate_limit_retries``/``rate_limit`` resolve (proving
    optional fields are read from the config, not hardcoded defaults). The run reaches
    ``status == "waiting-for-merge"``.
    """
    repo_tag = f"repo-{uuid.uuid4().hex}"
    op_tag = f"op-{uuid.uuid4().hex}"
    decoy_tag = f"decoy-{uuid.uuid4().hex}"
    _seed_dandd(tmp_path, repo_profile_tag=repo_tag)
    operator_config = _point_operator_config(
        tmp_path, monkeypatch, _rich_operator_config(op_tag, decoy_tag)
    )
    handles = _install_seams(monkeypatch)
    expected = _expected_primary_profile(operator_config)

    from issueforge import config, engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert handles.invoker.calls, "authoring invoke was never called"
    for call in handles.invoker.calls:
        profile = call["profile"]
        assert isinstance(profile, config.Profile), (
            f"profile is {type(profile)!r}, not config.Profile"
        )
        assert profile == expected  # full equality binds the OPERATOR-config primary profile
    # roles.primary selection (not "first table"): the claude profile, not the decoy.
    assert expected.name == PROVIDER_NAME
    assert op_tag in expected.start
    assert repo_tag not in expected.start and decoy_tag not in expected.start
    # Non-default optional fields are resolved from the config, not hardcoded defaults.
    assert expected.rate_limit_retries == OP_RETRIES
    assert expected.rate_limit is not None and expected.rate_limit.exit_code == OP_RL_EXIT


@pytest.mark.slow
def test_default_providers_path_is_used_when_env_unset(tmp_path, monkeypatch):
    """When ``ISSUEFORGE_PROVIDERS`` is UNSET, the default ``~/.config/issueforge/providers.toml`` is
    used — an ``os.environ["ISSUEFORGE_PROVIDERS"]``-only impl with no default resolution fails.

    technical (contract): with HOME isolated, ``ISSUEFORGE_PROVIDERS`` unset, and a default
    ``~/.config/issueforge/providers.toml`` carrying a sentinel ``claude`` profile, after
    engine.run("DandD#111") the run reaches ``status == "waiting-for-merge"`` and every fake-invoke
    call's ``profile`` equals ``config.load_roles(<default config>).primary`` (carries the sentinel tag).
    """
    home_tag = f"home-{uuid.uuid4().hex}"
    _seed_dandd(tmp_path)  # minimal repo config
    default_config = _isolate_home_default(tmp_path, monkeypatch, _operator_config_text(home_tag))
    handles = _install_seams(monkeypatch)
    expected = _expected_primary_profile(default_config)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert handles.invoker.calls, "authoring invoke was never called"
    for call in handles.invoker.calls:
        assert call["profile"] == expected
    assert home_tag in expected.start


@pytest.mark.slow
def test_env_override_wins_over_default_providers_path(tmp_path, monkeypatch):
    """When BOTH the default file and ``ISSUEFORGE_PROVIDERS`` exist, the env override WINS.

    technical (contract): with HOME isolated and a default ``~/.config/issueforge/providers.toml``
    carrying a DECOY-tagged profile, AND ``ISSUEFORGE_PROVIDERS`` pointing at a config with a DIFFERENT
    sentinel tag, after engine.run("DandD#111") every fake-invoke call's ``profile`` equals the ENV
    config's primary (its ``start`` carries the env sentinel tag and NOT the default's decoy tag), and
    the run reaches ``status == "waiting-for-merge"``.
    """
    env_tag = f"env-{uuid.uuid4().hex}"
    default_tag = f"default-{uuid.uuid4().hex}"
    _seed_dandd(tmp_path)
    # Isolate HOME and place a DECOY default file, then OVERRIDE via the env var (which _point sets).
    monkeypatch.setenv("HOME", str(tmp_path))
    default = tmp_path / ".config" / "issueforge" / "providers.toml"
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text(_operator_config_text(default_tag))
    env_config = _point_operator_config(
        tmp_path, monkeypatch, _operator_config_text(env_tag), name="env"
    )
    handles = _install_seams(monkeypatch)
    expected = _expected_primary_profile(env_config)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert handles.invoker.calls, "authoring invoke was never called"
    for call in handles.invoker.calls:
        assert call["profile"] == expected
    assert env_tag in expected.start and default_tag not in expected.start


@pytest.mark.slow
def test_repo_config_needs_no_provider_keys_to_run(tmp_path, monkeypatch):
    """A repo whose committed ``.issueforge.toml`` has NO provider/role keys runs end-to-end — provider
    config is operator-level, so the repo contract stays minimal (DandD#188 preserved).

    technical (contract): the seeded repo's committed ``.issueforge.toml`` parses to a key set with NO
    ``providers`` and NO ``roles`` (only build keys), and yet with a valid ``ISSUEFORGE_PROVIDERS``
    operator config, engine.run("DandD#111") reaches ``status == "waiting-for-merge"``.
    """
    seeded = _seed_dandd(tmp_path)  # minimal repo config, no decoy
    _point_operator_config(tmp_path, monkeypatch, _operator_config_text(f"op-{uuid.uuid4().hex}"))
    _install_seams(monkeypatch)

    committed = _git(seeded.checkout, "show", "HEAD:.issueforge.toml").stdout
    repo_keys = set(tomllib.loads(committed))
    assert "providers" not in repo_keys and "roles" not in repo_keys, repo_keys

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])
    assert record["status"] == "waiting-for-merge"


def test_missing_operator_config_pauses_even_when_repo_has_roles(tmp_path, monkeypatch):
    """A missing operator providers config pauses the run before any authoring side effect — EVEN when
    the repo config carries valid roles. Proves the operator config is required and the repo is never a
    fallback (a repo-config impl would resolve the repo roles and reach waiting-for-merge instead).

    technical (contract): with the repo config carrying valid ``[providers.claude]``/``[roles]`` AND
    ``ISSUEFORGE_PROVIDERS`` pointing at a NONEXISTENT path, after engine.run("DandD#111")
    ``status == "paused"`` with a ``pause_reason`` naming the provider configuration (contains
    "provider"), ``providers.invoke`` was called ZERO times, and across every composed gateway ``push``
    and ``open_pr`` were called ZERO times.
    """
    _seed_dandd(tmp_path, repo_profile_tag=f"repo-{uuid.uuid4().hex}")  # repo HAS valid roles
    monkeypatch.setenv("ISSUEFORGE_PROVIDERS", str(tmp_path / "does-not-exist.toml"))
    handles = _install_seams(monkeypatch)

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused"
    assert "provider" in str(record.get("pause_reason", "")).lower()
    assert handles.invoker.calls == [], "authoring ran despite missing operator provider config"
    assert _push_count(handles.gateways) == 0
    assert _open_pr_count(handles.gateways) == 0
