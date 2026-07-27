"""Shared fixtures for the acceptance suites.

``make_git_repo`` is the temp-git-repo factory named in issue #7 (S3): it builds a throwaway
Git repository with a chosen ``origin`` URL, a resolvable remote default branch
(``refs/remotes/origin/HEAD``), and an optionally-committed ``.issueforge.toml``, so registry
tests can register real clones without a network. ``isolated_state_home`` points
``ISSUEFORGE_STATE_HOME`` at a per-test directory so registration never reads or writes the
developer's real state root.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

_GIT_ID = ["-c", "user.name=IssueForge Tests", "-c", "user.email=tests@issueforge.invalid"]

DEFAULT_CONFIG = 'baseline = ["pytest"]\nframework = "pytest"\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path, monkeypatch) -> Path:
    """Isolate IssueForge's state root per test so registration never touches real home."""
    home = tmp_path / "state-home"
    home.mkdir()
    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(home))
    return home


@pytest.fixture
def make_git_repo(tmp_path):
    """Return a factory building a temp Git repo with a chosen origin, branch, and config.

    ``branch`` is the checked-out local branch. ``default_branch`` (falling back to ``branch``)
    is what ``refs/remotes/origin/HEAD`` points at — the offline source of a clone's remote
    default branch. ``set_origin_head=False`` leaves it unset (unresolvable default branch).
    ``dirty`` writes divergent working-tree content AFTER the commit, without staging it.
    """

    def _make(
        name: str = "repo",
        *,
        origin: str | None = "git@github.com:Owner/DandD.git",
        branch: str = "main",
        default_branch: str | None = None,
        set_origin_head: bool = True,
        config: str | None = DEFAULT_CONFIG,
        commit_config: bool = True,
        dirty: str | None = None,
        parent: Path | None = None,
    ) -> Path:
        repo = (parent or tmp_path) / name
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
        # A seed commit so HEAD exists even when the config is left uncommitted.
        subprocess.run(
            ["git", "-C", str(repo), *_GIT_ID, "commit", "--allow-empty", "-qm", "seed"],
            check=True,
        )
        if origin is not None:
            _git(repo, "remote", "add", "origin", origin)
            if set_origin_head:
                target = default_branch or branch
                _git(
                    repo,
                    "symbolic-ref",
                    "refs/remotes/origin/HEAD",
                    f"refs/remotes/origin/{target}",
                )
        if config is not None:
            (repo / ".issueforge.toml").write_text(config)
            if commit_config:
                _git(repo, "add", ".issueforge.toml")
                subprocess.run(
                    ["git", "-C", str(repo), *_GIT_ID, "commit", "-qm", "cfg"], check=True
                )
        if dirty is not None:
            (repo / ".issueforge.toml").write_text(dirty)
        return repo

    return _make


# A fake provider CLI (issue #11 / S7): a deterministic, network-free stand-in for a real
# subscription AI CLI. Its behaviour is driven ENTIRELY by argv, so a Profile's start/resume/auth
# templates fully determine the child's stdout/stderr/exit/duration — the guarded-launch properties
# are exercised against a real child process, not a mock. Flags come first; the prompt is the
# trailing positional. It reads stdin ONLY with --read-stdin, so a test that does not opt in can
# never hang on an inherited stdin (the DEVNULL property is asserted by the one test that opts in).
_FAKE_PROVIDER_SCRIPT = textwrap.dedent(
    '''\
    """A fake provider CLI. Behaviour is fully argv-driven; no network, no real provider."""
    import argparse
    import sys
    import time


    def main() -> int:
        parser = argparse.ArgumentParser()
        parser.add_argument("--exit", type=int, default=0)
        parser.add_argument("--out", action="append", default=[])
        parser.add_argument("--err", action="append", default=[])
        parser.add_argument("--sleep", type=float, default=0.0)
        parser.add_argument("--read-stdin", action="store_true")
        parser.add_argument("--echo-args", action="store_true")
        parser.add_argument("rest", nargs="*")
        args = parser.parse_args()

        if args.read_stdin:
            data = sys.stdin.read()
            sys.stdout.write(f"STDIN_BYTES={len(data)}\\n")
        for line in args.out:
            sys.stdout.write(line + "\\n")
        if args.echo_args:
            sys.stdout.write(" ".join(args.rest) + "\\n")
        for line in args.err:
            sys.stderr.write(line + "\\n")
        sys.stdout.flush()
        sys.stderr.flush()
        # Sleep AFTER flushing so a timeout test still captures the already-emitted partial output.
        if args.sleep:
            time.sleep(args.sleep)
        return args.exit


    if __name__ == "__main__":
        raise SystemExit(main())
    '''
)


@pytest.fixture
def fake_provider_script(tmp_path) -> Path:
    """Write the fake provider CLI to disk and return its path.

    Build a ``Profile`` whose ``executable`` is ``[sys.executable, str(fake_provider_script)]`` and
    whose ``start``/``resume``/``auth`` argv templates carry the flags above; ``invoke`` then runs it
    through the real subprocess seam.
    """
    script = tmp_path / "fake_provider.py"
    script.write_text(_FAKE_PROVIDER_SCRIPT)
    return script


# --------------------------------------------------------------------------- #
# Fast-ring drift guard (#147)                                                #
# --------------------------------------------------------------------------- #
# The `make test-quick` fast ring runs with ``-m "not slow"``. This guard fails
# any UNMARKED test that runs longer than the ~5s threshold under that ring, with
# a message telling the author to mark it ``slow`` — so a newly-slow test can't
# silently rejoin the fast ring. It is scoped to the fast ring only (keyed on the
# ``not slow`` marker expression), so the full ``make test`` gate is unaffected.
# Duration is TOTAL setup+call time (a fixture-heavy test is guarded too), summed
# per worker before the call report is finalized.

_DRIFT_THRESHOLD_SECONDS = 5.0


def _fast_ring_active(config) -> bool:
    return getattr(config.option, "markexpr", "").strip() == "not slow"


def _recorded_fast(item) -> bool:
    """True when the committed ``.test_durations`` records this test at or under the
    threshold. Live wall time balloons under parallel machine load (a 3s test can
    read 8s while another suite hogs the cores), so a test with a known-fast record
    is never failed on live time alone; only an UNRECORDED test (new — exactly what
    the guard exists to catch) or a recorded-slow one can trip the guard.
    """
    import json

    cache = getattr(item.config, "_drift_recorded", None)
    if cache is None:
        path = Path(str(item.config.rootpath)) / ".test_durations"
        try:
            cache = json.loads(path.read_text())
        except (OSError, ValueError):
            cache = {}
        item.config._drift_recorded = cache
    recorded = cache.get(item.nodeid)
    return recorded is not None and recorded <= _DRIFT_THRESHOLD_SECONDS


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _fast_ring_active(item.config) or item.get_closest_marker("slow"):
        return
    acc = getattr(item.session, "_drift_durations", None)
    if acc is None:
        acc = item.session._drift_durations = {}
    acc[item.nodeid] = acc.get(item.nodeid, 0.0) + report.duration
    if report.when == "call" and report.passed:
        total = acc[item.nodeid]
        if total > _DRIFT_THRESHOLD_SECONDS and not _recorded_fast(item):
            report.outcome = "failed"
            report.longrepr = (
                f"drift guard: {item.nodeid} took {total:.1f}s "
                f"(> {_DRIFT_THRESHOLD_SECONDS:.0f}s) but is not marked slow. "
                f"Add @pytest.mark.slow so the fast ring (`make test-quick`) skips it."
            )
