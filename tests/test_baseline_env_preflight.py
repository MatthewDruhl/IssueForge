"""PENDING acceptance contract for issue #141: baseline-env preflight + real USAGE_ERROR diagnostics.

The live ``issueforge run DandD#111`` paused with ``baseline_not_green: BaselineStatus.USAGE_ERROR``
and nothing else. Two defects sit behind that one line:

1. **No preflight.** ``repo add`` never checks that the repo's OWN baseline environment can load the
   report-log reporter. A baseline that self-provisions (``uv run --extra dev ... -m pytest``) bypasses
   the adapter's provisioned venv (``pytest_adapter._provision_default`` installs ``pytest-reportlog``
   there, not in the repo's env), so ``--report-log`` (injected at ``verify._execute_baseline``) cannot
   be honoured and pytest fails -> ``USAGE_ERROR``, mid-run, with no actionable message.
2. **Lost diagnostics.** ``verify.Evidence`` carries ``exit_code`` but no stdout/stderr, and
   ``run_baseline`` DISCARDS ``result.stdout``/``result.stderr``. The pause persists only
   ``{status, pause_reason}``, so the subprocess's actual complaint never reaches the run record.
   (``engine._persist_captured``'s ``stdout.log``/``stderr.log`` are the STAGE's redirected streams,
   not the subprocess's -- which is why the live run only kept the scope-gate echo.)

Design decisions this suite encodes (fixed on the issue, 2026-07-26)
-------------------------------------------------------------------
**The probe has THREE outcomes, not two.**

* PASS -- the baseline interpreter ran and the reporter loaded -> onboard normally.
* REJECT -- the baseline command RAN and conclusively lacks the reporter -> fail loud, name the
  dependency, do not register.
* INCONCLUSIVE -- the baseline command could not start at all (missing directory, missing
  interpreter, bad invocation) -> do NOT reject; the probe learned nothing about the plugin.

The third outcome is what makes the design jointly satisfiable with the mandatory green guard
``test_onboarding_dandd_config.py::test_repo_add_persists_normalized_slug_and_default_branch``, which
drives DandD's real ``uv run --extra dev --directory backend ...`` baseline against a synthetic repo
with no ``backend/`` on disk and does NOT stub the seam. That failure is inconclusive; the live DandD
failure this issue was filed for was conclusive. All three outcomes are pinned below, and the
INCONCLUSIVE pair deliberately mirrors that guard's shape.

Two further fixed decisions:

* The probe goes through an INJECTABLE MODULE-LEVEL SEAM on ``issueforge.cli`` whose DEFAULT genuinely
  probes (a default no-op fails the REJECT test).
* The preflight lives in the CLI ``repo_add`` AFTER ``registry.register``; ``register`` stays pure.
  Pinned two ways: ``register`` called directly on an unusable repo still succeeds, AND the seam
  observes the alias ALREADY registered when it is called.
* Diagnostics persist as ``baseline-stdout.log`` / ``baseline-stderr.log`` (distinct from
  ``_persist_captured``'s ``stdout.log`` / ``stderr.log``) through ``store.write_artifact``.

Fixture design (the load-bearing part)
--------------------------------------
The PASS and REJECT environments are indistinguishable **without running them**. Each is a directory
holding a ``python`` shim and a ``site/pytest_reportlog/__init__.py`` that shadows the real
distribution. Across the pair:

* the two ``site/`` trees have identical file names AND byte-identical contents;
* the two shims are byte-identical after masking 32-hex runs; the ONLY difference is the value of an
  opaque ``IF141_ENV_KEY`` token.

The shared, byte-identical gate module hashes that key and compares it to a digest baked into itself.
Wrong key -> ``ModuleNotFoundError('pytest_reportlog')``. Right key -> the gate drops its own directory
from ``sys.path``, deletes itself from ``sys.modules`` and re-imports, so the REAL distribution answers
and ``--report-log`` genuinely works. Deciding which environment is which therefore requires computing
a SHA-256 preimage relation, i.e. actually executing the interpreter -- no filesystem listing, no file
content comparison, no path name, no committed repo byte discriminates them.

KNOWN, DELIBERATE LIMIT of that claim (adversarial review, dismissed as non-blocking with reason).
The claim above is about PLAUSIBLE implementations, not about information-theoretic secrecy. A
sufficiently adversarial implementation could read the shim's exported key AND the gate's baked-in
digest, recompute the hash itself, and decide without executing anything. That is not closable: any
on-disk fixture is statically inspectable, and two environments that BEHAVE differently must differ
on disk somewhere. Two reasons it is accepted rather than chased:

1. The contract is the OUTCOME (reject a conclusively-unusable environment, accept a usable one, stay
   silent when the probe is inconclusive), not the mechanism. In the real world a repo's environment
   either has the distribution installed or it does not, and that IS statically visible -- so an
   implementation that inspects site-packages and returns the RIGHT answer is fragile, not wrong.
   Requiring execution would over-specify the contract.
2. The implementations this suite actually exists to reject -- always-pass, always-reject, a hardcoded
   package list, or parsing ``pyproject.toml``/requirements text -- are all still caught, because none
   of them produces the right answer across the PASS / REJECT / INCONCLUSIVE trio.

The two INCONCLUSIVE fixtures never reach pytest at all: one names an interpreter that does not exist,
the other is a ``uv``-shaped launcher that refuses because its ``--directory`` is missing (exactly the
mandatory guard's situation). Neither says anything about the reporter, and both must still onboard.

For the diagnostics behaviours the baseline shim emits FOUR INDEPENDENT ``uuid4`` sentinels -- one
stdout/stderr pair when invoked with ``--collect-only``, a different pair otherwise. No token is
derivable from another (no shared tag, no role substring), so an implementation that persists the
collection probe's output cannot rename its way into the execution assertions.

Pending convention: every not-yet-implemented test carries a literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#141)")``. Tests that pass TODAY carry no marker;
the unmarked ones are green HARNESS GUARDS, present so that a broken fixture cannot masquerade as the
expected xfail count.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

# The REAL PoC harness (a real temp DandD checkout with a local fetchable bare origin, registered;
# plus the five locked external boundaries). Imported, never modified -- the same reuse
# ``tests/test_poc_composed_unit.py`` already does.
from test_poc_integration import SPEC, _git, _install_seams, _seed_dandd

import issueforge
from issueforge.cli import app

_GIT_ID = ["-c", "user.name=IssueForge Tests", "-c", "user.email=tests@issueforge.invalid"]

PASSING_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"

# The runtime-only discriminator. Its VALUE differs between the two environments; nothing else does.
_ENV_KEY_VAR = "IF141_ENV_KEY"

# Byte-identical in BOTH environments. Only the key exported by the shim decides what it does.
_GATE_SOURCE = '''\
"""A shadowing ``pytest_reportlog``. Identical in both fixtures; the runtime key decides."""

import hashlib
import os
import sys

_EXPECT = "{digest}"

if hashlib.sha256(os.environ.get("{var}", "").encode()).hexdigest() != _EXPECT:
    raise ModuleNotFoundError("No module named 'pytest_reportlog'")

# Right key: step out of the way so the REAL distribution answers this import.
_shadow = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path = [p for p in sys.path if os.path.abspath(p) != _shadow]
del sys.modules[__name__]
import pytest_reportlog as _real  # noqa: E402

sys.modules[__name__] = _real
'''

# Byte-identical in both environments after masking 32-hex runs (the env directory and the key).
_SHIM_TEMPLATE = '#!/bin/sh\nPYTHONPATH="{site}"\nexport PYTHONPATH\n{var}="{key}"\nexport {var}\nexec "{python}" "$@"\n'

# A ``uv``-shaped launcher that refuses when its ``--directory`` is missing and therefore never
# reaches pytest -- the mandatory DandD guard's exact situation, and the INCONCLUSIVE outcome.
_LAUNCHER_TEMPLATE = """#!/bin/sh
prev=""
want=""
for arg in "$@"; do
  if [ "$prev" = "--directory" ]; then want="$arg"; fi
  prev="$arg"
done
if [ -n "$want" ] && [ ! -d "$want" ]; then
  echo "error: No such directory: \\`$want\\`" >&2
  exit 2
fi
exec "{python}" "$@"
"""

# Emits a DIFFERENT, INDEPENDENT stdout/stderr pair for collection than for execution, so an
# implementation that persists the ``--collect-only`` probe's output instead of the baseline run's
# cannot pass -- and cannot derive one pair from the other.
_SENTINEL_TEMPLATE = """#!/bin/sh
for arg in "$@"; do
  if [ "$arg" = "--collect-only" ]; then
    echo "{collect_out}"
    echo "{collect_err}" >&2
    exit 4
  fi
done
echo "{exec_out}"
echo "{exec_err}" >&2
exit 4
"""


# --------------------------------------------------------------------------- fixtures


def _write_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    os.chmod(path, 0o755)
    return path


def _env_pair(root: Path) -> SimpleNamespace:
    """Build the PASS/REJECT environment pair. See the module docstring: the two are separable only
    by executing them, because the sole difference is which opaque key hashes to the digest baked
    into the shared, byte-identical gate module."""
    good_key = uuid.uuid4().hex
    bad_key = uuid.uuid4().hex
    digest = hashlib.sha256(good_key.encode()).hexdigest()
    gate = _GATE_SOURCE.format(digest=digest, var=_ENV_KEY_VAR)

    def _build(key: str) -> Path:
        env_dir = root / f"env-{uuid.uuid4().hex}"
        package = env_dir / "site" / "pytest_reportlog"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(gate)
        return _write_exec(
            env_dir / "python",
            _SHIM_TEMPLATE.format(
                site=package.parent, var=_ENV_KEY_VAR, key=key, python=sys.executable
            ),
        )

    return SimpleNamespace(healthy=_build(good_key), unhealthy=_build(bad_key))


def _sentinels() -> SimpleNamespace:
    """Four INDEPENDENT per-test tokens. No shared tag and no role word, so no token can be derived
    from another by substitution -- only the invocation that actually emitted it can supply it."""
    return SimpleNamespace(
        exec_out=f"IF141-{uuid.uuid4().hex}",
        exec_err=f"IF141-{uuid.uuid4().hex}",
        collect_out=f"IF141-{uuid.uuid4().hex}",
        collect_err=f"IF141-{uuid.uuid4().hex}",
    )


def _sentinel_baseline(root: Path, tokens: SimpleNamespace) -> Path:
    """A baseline executable that exits 4 (pytest's USAGE_ERROR code) either way, but says something
    unrelated when it is being collected versus when it is being executed."""
    return _write_exec(
        root / f"baseline-{uuid.uuid4().hex}",
        _SENTINEL_TEMPLATE.format(
            collect_out=tokens.collect_out,
            collect_err=tokens.collect_err,
            exec_out=tokens.exec_out,
            exec_err=tokens.exec_err,
        ),
    )


def _launcher(root: Path) -> Path:
    """A ``uv``-shaped launcher that fails before reaching pytest when ``--directory`` is missing."""
    return _write_exec(
        root / f"uv-{uuid.uuid4().hex}", _LAUNCHER_TEMPLATE.format(python=sys.executable)
    )


# --------------------------------------------------------------------------- helpers


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # click >= 8.2 always separates stderr
        return CliRunner()


def _output(result) -> str:
    """Everything the CLI wrote, wherever it wrote it -- the suite pins the MESSAGE, not the stream."""
    parts = [result.stdout or ""]
    try:
        parts.append(result.stderr or "")
    except ValueError:  # stderr not separately captured on this click
        pass
    return "".join(parts)


def _normalized(text: str) -> str:
    """Lowercase with ``-``/``_`` stripped, so ``pytest-reportlog``, ``pytest_reportlog`` and
    ``report-log`` all normalize to a form containing ``reportlog``."""
    return re.sub(r"[-_]", "", text.lower())


def _local_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _onboardable_repo(root: Path, name: str, *, slug: str, baseline: list[str]) -> Path:
    """A registrable clone: a git root, a committed ``.issueforge.toml`` declaring ``baseline``, one
    passing test, a github-style ``origin`` and a resolvable ``refs/remotes/origin/HEAD``."""
    repo = root / name
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_ok.py").write_text(PASSING_TEST)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    rendered = ", ".join(f'"{part}"' for part in baseline)
    (repo / ".issueforge.toml").write_text(
        f'framework = "pytest"\nreporter = "pytest"\nbaseline = [{rendered}]\n'
    )
    _local_git(repo, "add", "-A")
    _local_git(repo, "commit", "-qm", "cfg")
    _local_git(repo, "remote", "add", "origin", f"git@github.com:{slug}.git")
    _local_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return repo


def _env_repo(root: Path, name: str, *, slug: str, shim: Path) -> Path:
    """An onboardable clone whose baseline runs ``shim`` as its OWN interpreter -- an absolute path,
    exactly like DandD's ``uv``-fronted command (NOT interpreter-form), so onboarding has to probe
    THAT environment rather than the adapter's provisioned venv."""
    return _onboardable_repo(root, name, slug=slug, baseline=[str(shim), "-m", "pytest", "tests/"])


def _host_provisioner(worktree, frozen_deps=None):
    """The hermetic provisioner seam: the host interpreter and an allowlist env (never a copy of the
    ambient ``os.environ``), so ``run_baseline`` executes on the host with no venv build and no
    network. It sets NO ``denies_network``, so the run stays a plain host subprocess."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    return SimpleNamespace(
        interpreter=sys.executable,
        env=env,
        artifact_dir=Path(worktree).parent / f"if-env-{Path(worktree).name}",
        env_root=None,
        network=False,
    )


def _git_repo_with(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("worktree\n")
    _local_git(repo, "add", "-A")
    _local_git(repo, "commit", "-qm", "seed")
    return repo


def _repo_root() -> Path:
    """The checkout root, derived from the INSTALLED package rather than from this file's position,
    so the suite is correct wherever it is collected from. Guarded green by
    ``test_harness_guard_repo_root_and_doc_candidates_resolve``."""
    return Path(issueforge.__file__).resolve().parents[2]


def _doc_candidates() -> list[Path]:
    root = _repo_root()
    return [root / "README.md", *sorted((root / "docs").glob("*.md"))]


def _sections(text: str) -> list[str]:
    """Split Markdown into heading-delimited sections, so co-occurrence means "in the same passage"
    rather than "somewhere in a 600-line file"."""
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            sections.append([line])
        else:
            sections[-1].append(line)
    return ["\n".join(block) for block in sections if block]


def _looks_like_preflight_seam(name: str) -> bool:
    flat = _normalized(name)
    if "preflight" in flat:
        return True
    return "baseline" in flat and any(word in flat for word in ("probe", "env", "check"))


def _preflight_seam_name() -> str:
    """The name of the module-level preflight seam on ``issueforge.cli``.

    Deliberately discovered rather than hardcoded: the CONTRACT is "an injectable module-level
    callable that performs the baseline-environment preflight", not one particular spelling. Exactly
    one such name must exist, otherwise stubbing it is ambiguous.
    """
    from issueforge import cli

    candidates = sorted(
        name
        for name, value in vars(cli).items()
        if callable(value) and _looks_like_preflight_seam(name)
    )
    assert len(candidates) == 1, (
        "issueforge.cli must expose exactly ONE module-level baseline-env preflight seam so "
        f"existing tests can stub it; found {candidates}"
    )
    return candidates[0]


def _probe_import(shim: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(shim), "-c", "import pytest_reportlog"], capture_output=True, text=True
    )


def _probe_pytest(shim: Path, tests_dir: Path, report: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(shim),
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            f"--report-log={report}",
            str(tests_dir),
        ],
        capture_output=True,
        text=True,
        cwd=tests_dir.parent,
    )


def _tests_dir(root: Path) -> Path:
    path = root / f"probe-tests-{uuid.uuid4().hex}"
    path.mkdir()
    (path / "test_ok.py").write_text(PASSING_TEST)
    return path


# =========================================================================== green harness guards
#
# Unmarked on purpose. Under ``xfail(strict=True)`` a fixture that silently fails to build still
# reports as an expected failure, so the PENDING tests below cannot police their own setup. These do.


def test_harness_guard_the_two_baseline_envs_are_indistinguishable_without_running_them(tmp_path):
    """Plain English: the "good environment" and "broken environment" fixtures are the same files with
    the same contents; the only thing that differs is one opaque password, and you cannot tell which
    one is correct without actually running the interpreter.

    technical (contract): the two ``site/`` trees have identical relative file listings and
    byte-identical file contents; the two shim scripts are identical after masking every 32-hex run
    (the env directory name and the key), and their two keys are both 32-hex and differ. Behaviourally
    ``<shim> -c "import pytest_reportlog"`` exits 0 for one and nonzero for the other naming
    ``pytest_reportlog``, and a real ``<shim> -m pytest --report-log=<f> <tests>`` exits 0 for one
    (writing the report file) and nonzero for the other naming ``pytest_reportlog``.
    """
    pair = _env_pair(tmp_path)

    listings = []
    contents = []
    for shim in (pair.healthy, pair.unhealthy):
        site = shim.parent / "site"
        files = sorted(path.relative_to(site).as_posix() for path in site.rglob("*"))
        listings.append(files)
        contents.append([(site / name).read_bytes() for name in files if (site / name).is_file()])
    assert listings[0] == listings[1], f"the fixtures differ by file layout: {listings}"
    assert contents[0] == contents[1], "the fixtures differ by shadow-package bytes"

    def _key(shim: Path) -> str:
        match = re.search(rf'{_ENV_KEY_VAR}="([0-9a-f]+)"', shim.read_text())
        assert match, shim.read_text()
        return match.group(1)

    keys = [_key(pair.healthy), _key(pair.unhealthy)]
    assert keys[0] != keys[1]
    assert all(len(key) == 32 for key in keys), keys
    masked = [
        re.sub(r"[0-9a-f]{32}", "<HEX>", shim.read_text())
        for shim in (pair.healthy, pair.unhealthy)
    ]
    assert masked[0] == masked[1], f"the shims are distinguishable by inspection:\n{masked}"

    assert _probe_import(pair.healthy).returncode == 0
    broken = _probe_import(pair.unhealthy)
    assert broken.returncode != 0
    assert "pytest_reportlog" in broken.stderr

    tests_dir = _tests_dir(tmp_path)
    report = tmp_path / "good.jsonl"
    good = _probe_pytest(pair.healthy, tests_dir, report)
    assert good.returncode == 0, good.stdout + good.stderr
    assert report.is_file(), "the healthy env must genuinely honour --report-log"
    bad = _probe_pytest(pair.unhealthy, tests_dir, tmp_path / "bad.jsonl")
    assert bad.returncode != 0, bad.stdout + bad.stderr
    assert "pytest_reportlog" in (bad.stdout + bad.stderr)


def test_harness_guard_the_inconclusive_baselines_never_reach_pytest(tmp_path):
    """Plain English: the two "cannot even start" fixtures really cannot start, and what they say has
    nothing to do with the report-log plugin -- so accepting them is not the probe being lazy.

    technical (contract): the missing-interpreter baseline raises ``FileNotFoundError`` on launch. The
    ``uv``-shaped launcher, run with ``--directory backend`` from a repo that has no ``backend/``,
    exits 2, names the missing directory on stderr, and its combined output normalized contains
    neither ``reportlog`` nor a pytest session header.
    """
    missing = tmp_path / f"no-such-interpreter-{uuid.uuid4().hex}"
    assert not missing.exists()
    with pytest.raises(FileNotFoundError):
        subprocess.run([str(missing), "-m", "pytest"], capture_output=True, text=True)

    launcher = _launcher(tmp_path)
    repo = tmp_path / "no-backend"
    repo.mkdir()
    result = subprocess.run(
        [
            str(launcher),
            "run",
            "--extra",
            "dev",
            "--directory",
            "backend",
            "python",
            "-m",
            "pytest",
        ],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "backend" in result.stderr
    combined = _normalized(result.stdout + result.stderr)
    assert "reportlog" not in combined, (
        "an inconclusive failure must say nothing about the reporter"
    )
    assert "testsession" not in combined, "the launcher must never reach a pytest session"


def test_harness_guard_the_sentinel_baseline_separates_collection_from_execution(tmp_path):
    """Plain English: the fake test command says something completely unrelated when it is merely
    being listed than when it is actually being run, so "we saved the output" can only be satisfied by
    the run.

    technical (contract): the four tokens are independent ``uuid4`` values sharing no substring beyond
    the ``IF141-`` prefix. Invoking the shim with ``--collect-only -q`` yields the collection pair and
    NEITHER execution token; invoking it without ``--collect-only`` yields the execution pair and
    NEITHER collection token. Both invocations exit 4.
    """
    tokens = _sentinels()
    values = [tokens.exec_out, tokens.exec_err, tokens.collect_out, tokens.collect_err]
    assert len(set(values)) == 4
    suffixes = [value.removeprefix("IF141-") for value in values]
    assert len(set(suffixes)) == 4 and all(len(s) == 32 for s in suffixes), suffixes

    shim = _sentinel_baseline(tmp_path, tokens)
    collected = subprocess.run([str(shim), "--collect-only", "-q"], capture_output=True, text=True)
    executed = subprocess.run([str(shim), "--report-log=x"], capture_output=True, text=True)

    assert collected.returncode == 4 and executed.returncode == 4
    assert tokens.collect_out in collected.stdout and tokens.collect_err in collected.stderr
    assert tokens.exec_out not in collected.stdout + collected.stderr
    assert tokens.exec_err not in collected.stdout + collected.stderr
    assert tokens.exec_out in executed.stdout and tokens.exec_err in executed.stderr
    assert tokens.collect_out not in executed.stdout + executed.stderr
    assert tokens.collect_err not in executed.stdout + executed.stderr


def test_harness_guard_the_registry_state_home_is_isolated_per_test():
    """Plain English: each test starts with an empty registry, so "the repo is not registered" means
    something.

    technical (contract): ``Registry.load().entries() == []`` at test start, and the registry file
    lives under this test's ``ISSUEFORGE_STATE_HOME`` (conftest's autouse ``isolated_state_home``).
    """
    from issueforge.registry import Registry

    assert Registry.load().entries() == []
    assert str(Registry.registry_path()).startswith(os.environ["ISSUEFORGE_STATE_HOME"])


def test_harness_guard_evidence_is_constructible_with_todays_fields():
    """Plain English: the diagnostics change must ADD to the evidence record, not rewrite it.

    technical (contract): ``verify.Evidence`` is still constructible from exactly today's ten
    keyword fields, so adding ``stdout``/``stderr`` must give them defaults rather than break every
    existing caller (``tests/test_onboarding_dandd_config.py`` constructs one directly).
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    evidence = verify.Evidence(
        status=BaselineStatus.GREEN,
        collected=1,
        executed=1,
        nodes=("t::a",),
        report_present=True,
        report_dir="/tmp/x",
        exit_code=0,
        interpreter="/usr/bin/python3",
        env_root=None,
        network=False,
    )
    assert evidence.exit_code == 0


def test_harness_guard_repo_root_and_doc_candidates_resolve():
    """Plain English: the docs check is looking at this checkout's real documentation.

    technical (contract): ``_repo_root()`` (derived from ``issueforge.__file__``) contains
    ``pyproject.toml`` and ``README.md``, and ``_doc_candidates()`` yields at least two existing
    Markdown files.
    """
    root = _repo_root()
    assert (root / "pyproject.toml").is_file(), root
    assert (root / "README.md").is_file(), root
    existing = [path for path in _doc_candidates() if path.is_file()]
    assert len(existing) >= 2, existing


def _prepare_sentinel_run(tmp_path: Path, monkeypatch, tokens: SimpleNamespace) -> Path:
    """Seed the real PoC harness, commit the sentinel baseline to the fetched tip, and swap ONLY the
    environment provisioner for the hermetic host seam. The baseline itself stays a real subprocess.
    """
    from issueforge.adapters import pytest_adapter

    seeded = _seed_dandd(tmp_path)
    _install_seams(monkeypatch)
    shim = _sentinel_baseline(tmp_path, tokens)

    advance = tmp_path / "advance-141"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(advance)], check=True)
    (advance / ".issueforge.toml").write_text(
        f'framework = "pytest"\nreporter = "pytest"\nbaseline = ["{shim}"]\nacceptance = ["{shim}"]\n'
    )
    _git(advance, "add", "-A")
    _git(advance, "commit", "-qm", "baseline that exits 4")
    _git(advance, "push", "-q", "origin", "main")
    assert seeded.checkout.exists()

    monkeypatch.setattr(pytest_adapter, "_provision_default", _host_provisioner)
    return shim


def test_harness_guard_the_composed_run_reaches_the_usage_error_pause(tmp_path, monkeypatch):
    """Plain English: the end-to-end fixture really does drive a run that stops because the project's
    test command failed to start -- so if the diagnostics test later fails, it fails about diagnostics
    and not about the fixture.

    technical (contract): with the sentinel baseline committed to the fetched tip and the hermetic
    host provisioner installed, ``engine.run(SPEC)`` returns a record with ``status == "paused"`` and
    a ``pause_reason`` containing ``usage_error`` (case-insensitive), and its run directory exists.
    """
    from issueforge import engine, store

    tokens = _sentinels()
    _prepare_sentinel_run(tmp_path, monkeypatch, tokens)

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "paused", record
    assert "usage_error" in str(record.get("pause_reason", "")).lower(), record
    assert store.run_dir(result["run_id"]).is_dir()


def test_register_itself_stays_free_of_the_baseline_env_preflight(tmp_path):
    """Plain English: the new check belongs to the ``repo add`` command, not to the registration
    function -- so the rest of the codebase can register a repo without a live environment.

    GREEN today and it must STAY green: half of the location constraint. If the preflight is
    implemented inside ``registry.register`` instead of in the CLI, this test reds. The other half
    (that it runs AFTER register, not before) is
    ``test_the_preflight_seam_runs_after_register_not_before``.

    technical (contract): ``registry.register("PURE", <the REJECT repo>)`` -- the same clone
    ``repo add`` must refuse -- returns an ``Entry`` whose ``path`` is the resolved repo, and the
    alias is afterwards resolvable via ``Registry.load().get("PURE")``.
    """
    from issueforge.registry import Registry, register

    pair = _env_pair(tmp_path)
    repo = _env_repo(tmp_path, "pure", slug="MatthewDruhl/Pure", shim=pair.unhealthy)

    entry = register("PURE", str(repo))

    assert entry.path == repo.resolve()
    assert Registry.load().get("PURE").path == repo.resolve()


# =========================================================================== 1. preflight: PASS


def test_repo_add_accepts_a_repo_whose_baseline_env_can_load_the_report_log_reporter(tmp_path):
    """Plain English: a project whose test environment CAN load the report-log plugin onboards
    normally -- the check must not cry wolf.

    GREEN today (there is no preflight yet). It is the false-positive half of the PASS/REJECT
    discrimination: it stays green only if the new preflight genuinely probes the baseline environment
    rather than refusing everything, and it reds the moment a preflight over-refuses.

    technical (contract): given a clone whose baseline is ``["<shim>", "-m", "pytest", "tests/"]``
    where ``<shim>`` is the PASS half of a pair that is separable from the REJECT half only by
    execution, ``issueforge repo add GOODENV:<path>`` exits 0, prints ``registered GOODENV``, and
    ``Registry.load().get("GOODENV").path == repo.resolve()``.
    """
    from issueforge.registry import Registry

    pair = _env_pair(tmp_path)
    repo = _env_repo(tmp_path, "healthy", slug="MatthewDruhl/Healthy", shim=pair.healthy)

    result = _runner().invoke(app, ["repo", "add", f"GOODENV:{repo}"])

    assert result.exit_code == 0, _output(result)
    assert "registered GOODENV" in result.stdout
    assert Registry.load().get("GOODENV").path == repo.resolve()


# =========================================================================== 2. preflight: REJECT


def test_repo_add_refuses_a_repo_whose_baseline_env_cannot_load_the_report_log_reporter(tmp_path):
    """Plain English: if you try to onboard a project whose own test environment RAN and turned out to
    lack the report-log plugin, ``issueforge repo add`` stops right there, tells you which plugin is
    missing, and does not leave the project half-registered -- instead of accepting it and failing
    hours later with a cryptic "usage error".

    This is the conclusive case: the interpreter starts, pytest starts, and the plugin is demonstrably
    absent. Paired with the PASS test above (an environment separable only by running it) and the two
    INCONCLUSIVE tests below (commands that never start), so a preflight that always refuses, always
    passes, reads a hardcoded package list, reads the committed pyproject, inspects argv, or inspects
    the environment's files cannot satisfy all four.

    technical (contract): given a clone whose baseline is ``["<shim>", "-m", "pytest", "tests/"]``
    where ``<shim>`` is the REJECT half of the pair, ``issueforge repo add BADENV:<path>``
    (a) exits NONZERO,
    (b) writes output (stdout or stderr) whose normalized form (lowercased, ``-``/``_`` stripped)
        contains ``reportlog``, and
    (c) leaves ``BADENV`` UNREGISTERED -- no entry carries that alias and
        ``Registry.load().get("BADENV")`` raises ``RegistryError``.
    """
    from issueforge.registry import Registry, RegistryError

    pair = _env_pair(tmp_path)
    repo = _env_repo(tmp_path, "unhealthy", slug="MatthewDruhl/Unhealthy", shim=pair.unhealthy)

    result = _runner().invoke(app, ["repo", "add", f"BADENV:{repo}"])

    assert result.exit_code != 0, f"repo add accepted an unusable baseline env: {_output(result)!r}"
    assert "reportlog" in _normalized(_output(result)), (
        f"the refusal must name the missing dependency; got: {_output(result)!r}"
    )

    aliases = [entry.alias for entry in Registry.load().entries()]
    assert "BADENV" not in aliases, f"a refused repo add left the repo registered: {aliases}"
    with pytest.raises(RegistryError):
        Registry.load().get("BADENV")


# =========================================================================== 3. preflight: INCONCLUSIVE


def test_repo_add_onboards_a_repo_whose_baseline_interpreter_does_not_exist(tmp_path):
    """Plain English: if the project's test command cannot even start, onboarding does NOT reject it
    for a missing plugin -- because nothing was learned about the plugin.

    GREEN today and it must STAY green. Together with the launcher test below it is the INCONCLUSIVE
    outcome: a probe that treats "the command failed" as "the reporter is missing" reds here.

    technical (contract): given a clone whose committed baseline names an interpreter path that does
    not exist, ``issueforge repo add NOSTART:<path>`` exits 0, prints ``registered NOSTART``, and the
    entry is in the registry.
    """
    from issueforge.registry import Registry

    missing = tmp_path / f"no-such-interpreter-{uuid.uuid4().hex}"
    assert not missing.exists()
    repo = _env_repo(tmp_path, "nostart", slug="MatthewDruhl/NoStart", shim=missing)

    result = _runner().invoke(app, ["repo", "add", f"NOSTART:{repo}"])

    assert result.exit_code == 0, _output(result)
    assert "registered NOSTART" in result.stdout
    assert Registry.load().get("NOSTART").path == repo.resolve()


def test_repo_add_onboards_a_repo_whose_launcher_fails_before_reaching_pytest(tmp_path):
    """Plain English: the same tolerance applies to the real-world shape -- a ``uv run --directory
    backend`` command in a checkout that has no ``backend/`` never reaches pytest, so onboarding must
    not blame the report-log plugin for it.

    GREEN today and it must STAY green. This deliberately mirrors the mandatory committed guard
    ``test_onboarding_dandd_config.py::test_repo_add_persists_normalized_slug_and_default_branch``,
    which drives exactly this command shape against a repo with no ``backend/`` and does NOT stub the
    seam. If a future preflight ever reds that guard, it reds here first, inside this issue's own
    suite, with a message saying why.

    technical (contract): given a clone whose committed baseline is
    ``["<uv-shim>", "run", "--extra", "dev", "--directory", "backend", "python", "-m", "pytest",
    "tests/"]`` and which has no ``backend/`` directory, ``issueforge repo add DANDDLIKE:<path>``
    exits 0, prints ``registered DANDDLIKE``, and the entry is in the registry.
    """
    from issueforge.registry import Registry

    launcher = _launcher(tmp_path)
    repo = _onboardable_repo(
        tmp_path,
        "dandd-like",
        slug="MatthewDruhl/DanDDLike",
        baseline=[
            str(launcher),
            "run",
            "--extra",
            "dev",
            "--directory",
            "backend",
            "python",
            "-m",
            "pytest",
            "tests/",
        ],
    )
    assert not (repo / "backend").exists()

    result = _runner().invoke(app, ["repo", "add", f"DANDDLIKE:{repo}"])

    assert result.exit_code == 0, _output(result)
    assert "registered DANDDLIKE" in result.stdout
    assert Registry.load().get("DANDDLIKE").path == repo.resolve()


# =========================================================================== 4. the seam


def test_the_preflight_probe_is_an_injectable_seam_that_can_be_stubbed_clean(tmp_path, monkeypatch):
    """Plain English: the new environment check can be substituted by callers and tests that do not
    have a live environment.

    technical (contract): ``issueforge.cli`` exposes exactly one module-level callable whose name
    marks it as the baseline-env preflight seam (``preflight`` in the name, or ``baseline`` plus one
    of ``probe``/``env``/``check``). With ``monkeypatch.setattr(cli, <that name>, lambda *a, **k:
    None)``, ``repo add BADENV:<the REJECT repo>`` exits 0, prints ``registered BADENV`` and the entry
    is in the registry -- i.e. the CLI consults the seam rather than probing unconditionally. The
    DEFAULT still genuinely probes; a default no-op fails the REJECT test above.
    """
    from issueforge import cli
    from issueforge.registry import Registry

    pair = _env_pair(tmp_path)
    repo = _env_repo(tmp_path, "stubbed", slug="MatthewDruhl/Stubbed", shim=pair.unhealthy)

    monkeypatch.setattr(cli, _preflight_seam_name(), lambda *args, **kwargs: None)

    result = _runner().invoke(app, ["repo", "add", f"BADENV:{repo}"])

    assert result.exit_code == 0, _output(result)
    assert "registered BADENV" in result.stdout
    assert Registry.load().get("BADENV").path == repo.resolve()


def test_the_cli_surfaces_the_preflight_seams_own_message_verbatim(tmp_path, monkeypatch):
    """Plain English: whatever the environment check complains about is what you see on screen -- the
    command does not swallow it and print something generic of its own.

    The mirror image of the previous test: there the seam said "fine" about a broken repo and the CLI
    accepted; here the seam objects to a PASS repo and the CLI must refuse with the seam's words.
    Together they prove the decision is driven by the seam's return value.

    technical (contract): with the seam stubbed to return a fresh per-test ``uuid4`` message,
    ``repo add`` on the PASS clone exits NONZERO and that exact token appears in its output. The
    seam's contract is therefore: ``None`` == usable (PASS or INCONCLUSIVE), non-empty string == an
    actionable refusal message the CLI surfaces.
    """
    from issueforge import cli

    token = f"IF141-SEAM-{uuid.uuid4().hex}"
    pair = _env_pair(tmp_path)
    repo = _env_repo(tmp_path, "objected", slug="MatthewDruhl/Objected", shim=pair.healthy)

    monkeypatch.setattr(cli, _preflight_seam_name(), lambda *args, **kwargs: token)

    result = _runner().invoke(app, ["repo", "add", f"OBJECTED:{repo}"])

    assert result.exit_code != 0, _output(result)
    assert token in _output(result), (
        f"the CLI must surface the preflight's own message; got: {_output(result)!r}"
    )


def test_the_preflight_seam_runs_after_register_not_before(tmp_path, monkeypatch):
    """Plain English: the environment check happens after the project has been recorded, not before --
    so registration keeps its own rules and the check is a separate, later step.

    The second half of the location constraint. ``test_register_itself_stays_free_of_the_baseline_env_
    preflight`` rules out "inside ``registry.register``"; this rules out "in the CLI but BEFORE
    ``register``", which would otherwise satisfy every other assertion in this suite.

    technical (contract): the seam is replaced by a recorder that reads the registry at call time and
    returns ``None``. After ``repo add ORDERED:<PASS repo>`` the recorder must have been called at
    all, and the aliases it observed must already include ``ORDERED``.
    """
    from issueforge import cli

    observed: dict[str, list[str]] = {}

    def _recorder(*args, **kwargs):
        from issueforge.registry import Registry

        observed["aliases"] = [entry.alias for entry in Registry.load().entries()]
        return None

    pair = _env_pair(tmp_path)
    repo = _env_repo(tmp_path, "ordered", slug="MatthewDruhl/Ordered", shim=pair.healthy)

    monkeypatch.setattr(cli, _preflight_seam_name(), _recorder)

    result = _runner().invoke(app, ["repo", "add", f"ORDERED:{repo}"])

    assert result.exit_code == 0, _output(result)
    assert "aliases" in observed, "repo add never called the preflight seam"
    assert "ORDERED" in observed["aliases"], (
        "the preflight ran BEFORE register; it must observe the alias already registered, "
        f"but saw {observed['aliases']}"
    )


# =========================================================================== 5. diagnostics


def test_baseline_evidence_carries_the_subprocess_stdout_stderr_and_exit_code(tmp_path):
    """Plain English: when IssueForge runs a project's test command and that command complains, the
    complaint itself is kept -- not thrown away in favour of a one-word label.

    technical (contract): ``verify.run_baseline`` over a baseline executable that writes one unique
    per-test token to stdout, a DIFFERENT unique token to stderr, and exits 4, returns Evidence with
    ``status is BaselineStatus.USAGE_ERROR`` and ``exit_code == 4`` (true today) AND
    ``evidence.stdout`` containing the EXECUTION stdout token while ``evidence.stderr`` contains the
    EXECUTION stderr token, each absent from the other field, and NEITHER field containing either
    ``--collect-only`` token. All four tokens are independent ``uuid4`` values, so only genuinely
    captured output from the right invocation can satisfy this.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    tokens = _sentinels()
    worktree = _git_repo_with(tmp_path, "worktree")
    shim = _sentinel_baseline(tmp_path, tokens)

    evidence = verify.run_baseline(
        worktree, [str(shim)], adapter=PytestAdapter(), provisioner=_host_provisioner
    )

    # Already true today: the classification and the exit code survive.
    assert evidence.status is BaselineStatus.USAGE_ERROR
    assert evidence.exit_code == 4

    captured_out = getattr(evidence, "stdout", None)
    captured_err = getattr(evidence, "stderr", None)
    assert captured_out is not None, "Evidence must carry the baseline subprocess stdout"
    assert captured_err is not None, "Evidence must carry the baseline subprocess stderr"
    assert tokens.exec_out in captured_out, "the baseline RUN's real stdout was not captured"
    assert tokens.exec_err in captured_err, "the baseline RUN's real stderr was not captured"
    # The two streams stay distinguishable (a merged blob is not the contract).
    assert tokens.exec_err not in captured_out
    assert tokens.exec_out not in captured_err
    # Capturing the collection probe instead of the run does not count.
    for token in (tokens.collect_out, tokens.collect_err):
        assert token not in captured_out + captured_err


def test_usage_error_pause_persists_the_baseline_stdout_stderr_and_exit_code(tmp_path, monkeypatch):
    """Plain English: when a run stops because the project's test command failed to even start
    properly, the saved run folder contains that command's actual output and exit code -- so you can
    read what went wrong without re-running anything by hand.

    Drives the REAL composed engine path (``engine.run`` -> fetch -> isolated worktree -> the REAL
    ``verify.run_baseline``) against a committed baseline that emits fresh per-test sentinels and exits
    4. Only the environment provisioner is swapped for the hermetic host seam, so the baseline is a
    genuine subprocess whose output nothing in IssueForge could know in advance. That the run reaches
    this pause at all is proved separately and unmarked by
    ``test_harness_guard_the_composed_run_reaches_the_usage_error_pause``.

    technical (contract): after ``engine.run(SPEC)`` the run directory contains ``baseline-stdout.log``
    holding the EXECUTION stdout token (and neither the execution stderr token nor either collection
    token) and ``baseline-stderr.log`` holding the EXECUTION stderr token (same exclusions); the exit
    code 4 is surfaced on the record -- either in ``pause_reason`` as a standalone ``4`` or under any
    record key whose name contains ``exit``; and the stage-capture artifact ``stdout.log``, if
    present, does NOT contain the baseline's tokens (the two capture layers stay distinct).
    """
    from issueforge import engine, store

    tokens = _sentinels()
    _prepare_sentinel_run(tmp_path, monkeypatch, tokens)

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])
    reason = str(record.get("pause_reason", ""))

    run_dir = store.run_dir(result["run_id"])
    stdout_artifact = run_dir / "baseline-stdout.log"
    stderr_artifact = run_dir / "baseline-stderr.log"
    present = sorted(path.name for path in run_dir.iterdir()) if run_dir.is_dir() else []
    assert stdout_artifact.is_file(), f"no baseline-stdout.log; run dir holds {present}"
    assert stderr_artifact.is_file(), f"no baseline-stderr.log; run dir holds {present}"

    stdout_text = stdout_artifact.read_text()
    stderr_text = stderr_artifact.read_text()
    assert tokens.exec_out in stdout_text, "the baseline RUN's real stdout was not persisted"
    assert tokens.exec_err in stderr_text, "the baseline RUN's real stderr was not persisted"
    assert tokens.exec_err not in stdout_text
    assert tokens.exec_out not in stderr_text
    for token in (tokens.collect_out, tokens.collect_err):
        assert token not in stdout_text + stderr_text, (
            "the collection probe's output was persisted instead of the baseline run's"
        )

    stage_capture = run_dir / "stdout.log"
    if stage_capture.is_file():
        assert tokens.exec_out not in stage_capture.read_text(), (
            "the baseline capture must not be conflated with the stage capture"
        )

    exit_on_record = any(
        "exit" in str(key).lower() and value == 4 for key, value in record.items()
    ) or bool(re.search(r"\b4\b", reason))
    assert exit_on_record, f"the baseline exit code 4 is not surfaced on the record: {record!r}"


# =========================================================================== 6. docs


def test_onboarding_docs_state_the_baseline_env_report_log_requirement():
    """Plain English: the onboarding documentation tells you, in one place, that the environment which
    runs your baseline command needs the report-log plugin.

    Deliberately unopinionated about wording -- it pins that the requirement is STATED, and that it is
    stated as a property of the baseline's environment rather than as three unrelated words scattered
    across a long file. The prose itself still needs a human read at the gate.

    technical (contract): at least one heading-delimited section of ``README.md`` or ``docs/*.md``
    contains, normalized (lowercased, ``-``/``_`` stripped), ALL of: ``reportlog``, ``baseline``, and
    at least one of ``environment`` / ``interpreter`` / ``virtualenv`` / ``venv``. Today no section in
    any of those files contains ``reportlog`` at all.
    """
    hits = []
    for path in _doc_candidates():
        if not path.is_file():
            continue
        for section in _sections(path.read_text()):
            flat = _normalized(section)
            if (
                "reportlog" in flat
                and "baseline" in flat
                and any(w in flat for w in ("environment", "interpreter", "virtualenv", "venv"))
            ):
                hits.append(path.name)
                break

    assert hits, (
        "no onboarding doc states, in a single section, that the BASELINE's ENVIRONMENT must be able "
        f"to load the report-log reporter; searched {[p.name for p in _doc_candidates()]}"
    )
