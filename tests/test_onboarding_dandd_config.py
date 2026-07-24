"""GREEN regression guard: DandD's golden ``.issueforge.toml`` onboards through IssueForge's seams.

Issue #188 commits a ``.issueforge.toml`` to the DandD repo. #188 is explicitly scoped OUT of "any
IssueForge implementation", so this file exercises ONLY existing IssueForge behavior and is therefore
a GREEN regression guard (no ``xfail``), not a red->green PENDING suite. It drives the real onboarding
seams against a synthetic, hermetic git repo carrying the golden DandD configuration:

    framework = "pytest"
    reporter  = "pytest"
    baseline  = ["uv","run","--extra","dev","--directory","backend","python","-m","pytest","tests/","-n","4"]
    acceptance= ["uv","run","--extra","dev","--directory","backend","python","-m","pytest"]

Seams proven, end to end:

* ``issueforge config check <repo>`` -> exit 0 and reports the pytest adapter
  (``cli.config_check`` -> ``config.load_config`` + ``PytestAdapter.probe``).
* ``issueforge repo add ALIAS:PATH`` under an empty ``ISSUEFORGE_STATE_HOME`` -> ``registry.register``
  persists the normalized owner/repo slug and the repo's real default branch.
* ``verify.establish_green_baseline`` -- the REAL fetch -> isolate -> baseline onboarding
  orchestration -- produces a DETACHED linked worktree OUTSIDE the normal checkout and reads the
  baseline command from THAT worktree's committed HEAD (never the normal, possibly-dirty checkout).
  Proven two ways: (a) the EXACT golden ``["uv",...,"-n","4"]`` argv committed in the fetched repo is
  the command the orchestration hands its baseline runner (command-passthrough, via a spy runner);
  (b) the orchestration reports a GREEN baseline end to end over a fast committed command, while the
  normal checkout's HEAD and index stay byte-identical before and after.

Baseline-hermeticity choice
---------------------------
The golden DandD baseline (``uv run --extra dev --directory backend python -m pytest tests/ -n 4``)
cannot actually RUN inside a throwaway synthetic repo: there is no ``backend/`` tree, no ``uv`` project
to sync, and ``-n 4`` needs xdist. So the two claims are split:

* Command passthrough is proven against the EXACT golden config: the orchestration fetches a repo whose
  committed ``.issueforge.toml`` is the real golden config, and a spy baseline runner captures the argv
  the orchestration passes it. The assertion is ``captured == GOLDEN_BASELINE`` (executable ``["uv",...]``
  form, verbatim) -- a regression that ignored the committed command or mangled the ``["uv",...]`` form
  fails here. The heavy command never executes because the spy intercepts it.
* The GREEN execution path is proven against a fast, equivalent committed baseline
  (``["-m","pytest","tests/"]`` -- the interpreter is supplied by the provisioned environment, exactly
  as the real ``verify.BASELINE`` does), run through the REAL ``verify.run_baseline`` and an INJECTED
  clean provisioner (host interpreter + host pytest/report-log -- the same hermetic seam IssueForge's
  own ``verify`` suite uses), so there is no network call and no venv build.

Origin-URL note: ``repo add`` normalizes the ``origin`` remote to an ``Owner/Repo`` slug (a github-style
URL), while ``fetch_default_sha`` needs a genuinely fetchable ``origin`` (a local bare repo). A single
remote URL cannot be both, so the slug/branch seam and the fetch/worktree seam each build their own
synthetic repo carrying the golden config -- both hermetic, both exercising real IssueForge code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from issueforge.cli import app

_GIT_ID = ["-c", "user.name=IssueForge Tests", "-c", "user.email=tests@issueforge.invalid"]

# The exact committed contract shared with DandD's own acceptance suite (test_issueforge_config.py).
GOLDEN_CONFIG = (
    'framework = "pytest"\n'
    'reporter = "pytest"\n'
    'baseline = ["uv", "run", "--extra", "dev", "--directory", "backend", '
    '"python", "-m", "pytest", "tests/", "-n", "4"]\n'
    'acceptance = ["uv", "run", "--extra", "dev", "--directory", "backend", '
    '"python", "-m", "pytest"]\n'
)
GOLDEN_BASELINE = [
    "uv", "run", "--extra", "dev", "--directory", "backend",
    "python", "-m", "pytest", "tests/", "-n", "4",
]  # fmt: skip

# Fast hermetic equivalent for the worktree-green seam (see the module docstring). Interpreter-form,
# so the provisioned environment supplies the interpreter exactly like the real ``verify.BASELINE``.
FAST_CONFIG = 'framework = "pytest"\nreporter = "pytest"\nbaseline = ["-m", "pytest", "tests/"]\n'
PASSING_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"

# A DISTINCT baseline committed to the local checkout HEAD only (never pushed) -- the tripwire that
# proves onboarding reads the FETCHED worktree's committed config, not the local checkout HEAD.
TRIPWIRE_CONFIG = 'framework = "pytest"\nreporter = "pytest"\nbaseline = ["tripwire-local-head", "MUST-NOT-BE-USED"]\n'
TRIPWIRE_BASELINE = ["tripwire-local-head", "MUST-NOT-BE-USED"]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # click >= 8.2 always separates stderr
        return CliRunner()


def _rev_parse(repo: Path, rev: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev], check=True, capture_output=True, text=True
    ).stdout.strip()


def _index_tree(repo: Path) -> str:
    """The tree sha the checkout's index currently represents (``git write-tree`` is a read of the
    index into an object; it moves neither HEAD nor the index), used as a stable index identity."""
    return subprocess.run(
        ["git", "-C", str(repo), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _is_detached(worktree: Path) -> bool:
    """A worktree is detached exactly when ``symbolic-ref -q HEAD`` exits 1 (HEAD is not symbolic)."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "symbolic-ref", "-q", "HEAD"], capture_output=True, text=True
    )
    return result.returncode == 1


def _is_inside(child: Path, parent: Path) -> bool:
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


def _repo_with_remote_slug(root: Path, name: str, *, origin: str, config: str) -> Path:
    """A repo with the golden config committed at HEAD, a github-style ``origin``, and a resolvable
    ``refs/remotes/origin/HEAD`` -- everything ``config check`` and ``repo add`` read (neither fetches)."""
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(config)
    _git(repo, "add", ".issueforge.toml")
    _git(repo, "commit", "-qm", "cfg")
    _git(repo, "remote", "add", "origin", origin)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return repo


def _clone_from_local_origin(root: Path, *, files: dict[str, str], branch: str = "main") -> Path:
    """A checkout cloned from a real local bare ``origin`` (so ``fetch_default_sha`` truly fetches),
    carrying ``files`` committed on ``branch`` and a clone-set ``refs/remotes/origin/HEAD``."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(origin)], check=True)
    seed = root / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(seed)], check=True)
    for rel, content in files.items():
        target = seed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", branch)
    checkout = root / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    return checkout


def _clean_provisioner():
    """The injected hermetic provisioner seam: the host interpreter, an allowlist env (never a copy
    of the ambient os.environ), and a unique artifact dir -- the same shape ``verify``'s own suite
    injects, so ``run_baseline`` executes on the host with no venv build and no network."""

    def _provision(worktree, frozen_deps=None):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        artifact = Path(worktree).parent / f"if-env-{Path(worktree).name}"
        return SimpleNamespace(
            interpreter=sys.executable, env=env, artifact_dir=artifact, network=False
        )

    return _provision


def test_config_check_reports_pytest_adapter_on_golden_config(tmp_path):
    """``issueforge config check`` on the golden DandD config exits 0 and reports the pytest adapter.

    Exercises ``cli.config_check`` -> ``config.load_config`` (committed object) + ``PytestAdapter``.
    """
    repo = _repo_with_remote_slug(
        tmp_path, "dandd", origin="git@github.com:MatthewDruhl/DandD.git", config=GOLDEN_CONFIG
    )

    result = _runner().invoke(app, ["config", "check", str(repo)])

    assert result.exit_code == 0, result.stderr
    assert "adapter: pytest" in result.stdout
    assert "reporter=pytest" in result.stdout
    # The committed golden baseline survives the resolve/print round trip.
    assert "--extra" in result.stdout and "'uv'" in result.stdout


def test_repo_add_persists_normalized_slug_and_default_branch(tmp_path):
    """``issueforge repo add`` under an empty state home persists the normalized slug + default branch.

    The autouse ``isolated_state_home`` fixture points ``ISSUEFORGE_STATE_HOME`` at an empty per-test
    directory, so this is the issue's "empty temporary ``ISSUEFORGE_STATE_HOME``" precondition.
    """
    from issueforge.registry import Registry

    repo = _repo_with_remote_slug(
        tmp_path, "dandd", origin="git@github.com:MatthewDruhl/DandD.git", config=GOLDEN_CONFIG
    )

    result = _runner().invoke(app, ["repo", "add", f"DandD:{repo}"])

    assert result.exit_code == 0, result.stderr
    entry = Registry.load().get("DandD")
    assert entry.slug == "MatthewDruhl/DandD"
    assert entry.default_branch == "main"
    assert entry.framework == "pytest"
    assert entry.reporter == "pytest"
    assert entry.baseline == GOLDEN_BASELINE
    assert entry.path == repo.resolve()


def test_onboarding_passes_the_exact_committed_golden_argv_to_the_baseline_runner(tmp_path):
    """The onboarding orchestration reads the EXACT golden ``["uv",...,"-n","4"]`` argv from the
    FETCHED worktree's committed HEAD and hands it, verbatim, to its baseline runner.

    Drives the real ``verify.establish_green_baseline`` (fetch -> isolate -> baseline) with the real
    workspace seam over a fetchable synthetic clone whose committed ``.issueforge.toml`` is the golden
    DandD config. A spy runner captures the command the orchestration passes it (and returns a GREEN
    verdict so the green-gated dispatch fires). The captured argv must equal the golden array exactly,
    in executable ``["uv",...]`` form -- so a regression that ignored the committed command, read it
    from the normal checkout, or mangled the ``["uv",...]`` executable form fails this test.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    checkout = _clone_from_local_origin(
        tmp_path, files={".issueforge.toml": GOLDEN_CONFIG, "tests/test_ok.py": PASSING_TEST}
    )

    # TRIPWIRE: commit a DISTINCT baseline to the LOCAL checkout HEAD without pushing it -- origin
    # (and thus the fetched worktree) keeps the golden config. This forces the two HEADs to diverge,
    # so "reads the fetched worktree HEAD" and "reads the local checkout HEAD" become distinguishable:
    # an impl reading the local checkout would capture TRIPWIRE_BASELINE and fail the assertion below.
    (checkout / ".issueforge.toml").write_text(TRIPWIRE_CONFIG)
    _git(checkout, "add", ".issueforge.toml")
    _git(checkout, "commit", "-qm", "local tripwire baseline (unpushed)")
    local_head = _rev_parse(checkout, "HEAD")
    origin_head = _rev_parse(checkout, "refs/remotes/origin/main")
    assert local_head != origin_head, "tripwire must diverge local checkout HEAD from origin HEAD"

    captured: dict = {}
    green = verify.Evidence(
        status=BaselineStatus.GREEN,
        collected=1,
        executed=1,
        nodes=(),
        report_present=True,
        report_dir=None,
        exit_code=0,
        interpreter="spy",
        env_root=None,
        network=False,
    )

    def _spy_runner(worktree, command, *, adapter=None, provisioner=None):
        captured["command"] = list(command)
        captured["worktree"] = worktree
        return green

    dispatched: list = []

    outcome = verify.establish_green_baseline(
        checkout,
        adapter=PytestAdapter(),
        run_baseline=_spy_runner,
        dispatch=lambda **kw: dispatched.append(kw),
    )

    # The orchestration reached the baseline runner with the EXACT committed golden argv read from
    # the FETCHED worktree -- NOT the tripwire baseline sitting on the local checkout HEAD.
    assert captured["command"] == GOLDEN_BASELINE
    assert captured["command"] != TRIPWIRE_BASELINE
    # It ran that command in the ISOLATED worktree (at the fetched origin HEAD), not the checkout.
    assert Path(captured["worktree"]).resolve() == Path(outcome.worktree).resolve()
    assert not _is_inside(outcome.worktree, checkout)
    assert _is_detached(outcome.worktree)
    assert _rev_parse(outcome.worktree, "HEAD") == origin_head
    # Green-gated: not paused, and dispatch fired exactly once carrying that worktree.
    assert outcome.paused is False
    assert outcome.status is BaselineStatus.GREEN
    assert len(dispatched) == 1
    assert Path(dispatched[0]["worktree"]).resolve() == Path(outcome.worktree).resolve()


def test_onboarding_reports_green_over_the_committed_command_leaving_checkout_untouched(tmp_path):
    """The onboarding orchestration reports a GREEN baseline running the committed command in a
    detached worktree OUTSIDE the checkout, and the normal checkout's HEAD + index are unchanged.

    Drives the real ``verify.establish_green_baseline`` with the real ``verify.run_baseline`` and an
    injected clean provisioner over a fetchable synthetic clone whose committed baseline is the fast
    equivalent command (see the module docstring). The command is read from the fetched worktree's
    committed HEAD by the orchestration itself -- this test never loads config from the checkout.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    checkout = _clone_from_local_origin(
        tmp_path, files={".issueforge.toml": FAST_CONFIG, "tests/test_ok.py": PASSING_TEST}
    )
    origin_tip = _rev_parse(checkout, "refs/remotes/origin/main")
    head_before = _rev_parse(checkout, "HEAD")
    index_before = _index_tree(checkout)

    outcome = verify.establish_green_baseline(
        checkout, adapter=PytestAdapter(), provisioner=_clean_provisioner()
    )

    assert outcome.paused is False, outcome.pause_reason
    assert outcome.status is BaselineStatus.GREEN
    assert outcome.evidence is not None
    assert outcome.evidence.exit_code == 0
    assert outcome.evidence.executed == 1
    # Detached linked worktree, OUTSIDE the checkout, at exactly the fetched default-branch tip.
    assert not _is_inside(outcome.worktree, checkout)
    assert _is_detached(outcome.worktree)
    assert _rev_parse(outcome.worktree, "HEAD") == origin_tip

    # The normal checkout is untouched by the whole onboarding operation.
    assert _rev_parse(checkout, "HEAD") == head_before
    assert _index_tree(checkout) == index_before
