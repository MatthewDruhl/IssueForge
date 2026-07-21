"""Committed PENDING acceptance suite for #12 (S6) — the green-baseline runner (verify.py).

Owns "establish an isolated GREEN baseline, pausing on anything not provably green BEFORE any AI
touches a file", the evidence fusion (exit code × report present/absent/complete × content ×
timeout flag), the fresh-report-dir-per-invocation rule, and the G14 authoritative environment.

The whole point of S6 is that "exit 0" is not green, so the classify state matrix is grounded in
REAL pytest runs here — not hand-fed records: a suite of all-skipped tests exits 0, a setup or
teardown failure is not a call failure, an xfail is not a real failure, a syntax error is a
collection error, a bad flag is a usage error, a missing interpreter never launches. Each is a
real target the runner executes. ``test_adapters.py`` keeps the exhaustive synthetic matrix for
the rare exit codes; this file proves the load-bearing cases against reality.

``issueforge.verify`` does not exist yet, so every test imports it inside its body and is
``@pytest.mark.xfail(strict=True, reason="PENDING (#12)")``. The git/target scaffolding below
touches no IssueForge code, so it lives at module top.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


# The baseline command as it appears in .issueforge.toml, minus the interpreter — the runner
# supplies the interpreter from the PROVISIONED environment (G14), never the candidate's.
BASELINE = ["-m", "pytest"]

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]


def _make_target(root: Path, name: str, body: str, *, config: str | None = None) -> Path:
    """Write a throwaway target repo with one test module (and optional .issueforge.toml)."""
    repo = root / name
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_target.py").write_text(textwrap.dedent(body))
    if config is not None:
        (repo / ".issueforge.toml").write_text(config)
    return repo


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _origin_with_checkout(root: Path, *, branch: str, files: dict[str, str]) -> tuple[Path, Path]:
    """A bare origin (default branch=branch, carrying ``files``) plus a checkout cloned from it."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(origin)], check=True)
    seed = root / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(seed)], check=True)
    for rel, content in files.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", branch)
    checkout = root / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    return origin, checkout


def _clean_provisioner(interpreter: str | None = None):
    """A provisioner seam yielding a hermetic handle: a fixed interpreter, an ALLOWLIST env, and a
    UNIQUE artifact dir per invocation.

    The env is a small explicit allowlist (never a copy of the candidate's os.environ), so a
    variable a candidate sets — IF_SABOTAGE, PYTEST_ADDOPTS — is absent from the authoritative run
    (the injectable form of "separately-provisioned authoritative environment", G14).
    """
    from types import SimpleNamespace

    interp = interpreter or sys.executable
    counter = {"n": 0}

    def _provision(worktree, frozen_deps=None):
        counter["n"] += 1
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        artifact = Path(worktree).parent / f"if-env-{Path(worktree).name}-{counter['n']}"
        return SimpleNamespace(interpreter=interp, env=env, artifact_dir=artifact, network=False)

    return _provision


def _adapter():
    from issueforge.adapters.pytest_adapter import PytestAdapter

    return PytestAdapter()


def _run_baseline(worktree, **kw):
    from issueforge import verify

    kw.setdefault("adapter", _adapter())
    kw.setdefault("provisioner", _clean_provisioner())
    return verify.run_baseline(worktree, BASELINE, **kw)


def _invocation_records() -> list[dict]:
    """Every persisted subprocess-boundary record under the isolated state root."""
    from issueforge.paths import state_root

    root = Path(state_root()) / "invocations"
    return (
        [json.loads(p.read_text()) for p in root.glob("*/invocation.json")] if root.exists() else []
    )


# =============================================================== real-path classify anchors


def test_two_passing_tests_classify_as_green(tmp_path):
    """Two genuinely passing tests are GREEN, executed == 2, exit code captured as 0.

    technical (contract): run_baseline over a two-passing-test target -> evidence.status is
    BaselineStatus.GREEN, evidence.executed == 2, evidence.report_present is True, and
    evidence.exit_code == 0 (the regression is self-proving — green is not inferred from a
    residual). Real pytest, real report-log, real classify.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "green",
        """
        def test_a():
            assert 1 + 1 == 2

        def test_b():
            assert "x" in "xyz"
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.status is BaselineStatus.GREEN
    assert ev.executed == 2
    assert ev.report_present is True
    assert ev.exit_code == 0


def test_all_skipped_suite_exits_zero_but_is_not_green(tmp_path):
    """THE false-green trap: an all-skipped suite exits 0 (exit_code == 0) yet is ALL_SKIPPED.

    technical (contract): a target whose two tests are @pytest.mark.skip; pytest exit code is 0.
    run_baseline -> status is BaselineStatus.ALL_SKIPPED (not GREEN), executed == 0, and
    exit_code == 0 — proving the classifier saw a zero exit and still refused green.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "skipped",
        """
        import pytest

        @pytest.mark.skip(reason="pending")
        def test_a():
            assert False

        @pytest.mark.skip(reason="pending")
        def test_b():
            assert False
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.status is BaselineStatus.ALL_SKIPPED
    assert ev.status is not BaselineStatus.GREEN
    assert ev.executed == 0
    assert ev.exit_code == 0


def test_a_real_xfail_at_exit_zero_is_not_green(tmp_path):
    """A real xfailing test exits 0 but is never GREEN — xfail is excluded from the conjunction.

    technical (contract): a target with one passing test and one @pytest.mark.xfail test that
    fails as expected (real pytest xfail, exit 0). run_baseline -> status is NOT GREEN (the
    xfailed node is not a present-and-passed expected node).
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "xfail",
        """
        import pytest

        def test_ok():
            assert True

        @pytest.mark.xfail(reason="known")
        def test_expected_fail():
            assert False
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.exit_code == 0
    assert ev.status is not BaselineStatus.GREEN


def test_a_real_mixed_pass_and_skip_is_not_green(tmp_path):
    """One passing + one skipped test (exit 0) is not GREEN — a skipped expected node breaks the
    conjunction even though nothing failed.

    technical (contract): a target with test_ok (passes) and test_skipped (@pytest.mark.skip).
    run_baseline -> exit_code == 0, executed == 1, status is NOT GREEN.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "mixed",
        """
        import pytest

        def test_ok():
            assert True

        @pytest.mark.skip(reason="pending")
        def test_skipped():
            assert False
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.exit_code == 0
    assert ev.executed == 1
    assert ev.status is not BaselineStatus.GREEN


def test_zero_collected_is_a_third_state_neither_green_nor_red(tmp_path):
    """Zero collected is BROKEN — its own state (pytest exit 5), never green and never red.

    technical (contract): a target directory with NO tests; pytest exits 5. run_baseline ->
    status is BaselineStatus.NO_TESTS_COLLECTED, collected == 0, neither GREEN nor BEHAVIORAL_RED.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = tmp_path / "empty"
    (worktree / "tests").mkdir(parents=True)
    (worktree / "tests" / "test_target.py").write_text("# no tests here\n")

    ev = _run_baseline(worktree)
    assert ev.status is BaselineStatus.NO_TESTS_COLLECTED
    assert ev.collected == 0
    assert ev.status is not BaselineStatus.GREEN
    assert ev.status is not BaselineStatus.BEHAVIORAL_RED


def test_a_call_failure_is_behavioral_red_with_a_phase_aware_record(tmp_path):
    """A failing test body (exit 1) is BEHAVIORAL_RED with a call-phase FAILED node record.

    technical (contract): a target with one passing and one asserting-False test; run_baseline ->
    status is BaselineStatus.BEHAVIORAL_RED, and the failing node has a call-phase Outcome.FAILED
    record with a non-empty longrepr.
    """
    from issueforge.adapters.base import BaselineStatus, Outcome

    worktree = _make_target(
        tmp_path,
        "red",
        """
        def test_ok():
            assert True

        def test_bad():
            assert 1 == 2
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.status is BaselineStatus.BEHAVIORAL_RED
    failed = [n for n in ev.nodes if n.phase == "call" and n.outcome is Outcome.FAILED]
    assert failed and all(n.longrepr for n in failed)


def test_a_setup_failure_is_not_green_and_is_phase_aware(tmp_path):
    """A failure in a fixture (setup phase) is not green, and the failing phase is 'setup', not
    'call' — an implementation that inspects only call records cannot call this green.

    technical (contract): a target whose test uses a fixture that raises at setup; run_baseline ->
    status is NOT GREEN, and a node record has phase 'setup' with Outcome.FAILED (or BROKEN error).
    """
    from issueforge.adapters.base import BaselineStatus, Outcome

    worktree = _make_target(
        tmp_path,
        "setupfail",
        """
        import pytest

        @pytest.fixture
        def boom():
            raise RuntimeError("setup blew up")

        def test_uses_fixture(boom):
            assert True
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.status is not BaselineStatus.GREEN
    setup_bad = [
        n for n in ev.nodes if n.phase == "setup" and n.outcome in (Outcome.FAILED, Outcome.BROKEN)
    ]
    assert setup_bad


def test_a_teardown_failure_is_not_green(tmp_path):
    """A failure during teardown is not green even though the call passed — the runner must fuse
    every phase, not just the call.

    technical (contract): a target whose fixture raises on teardown while the test body passes;
    run_baseline -> status is NOT GREEN, and a teardown-phase node record carries a failing outcome.
    """
    from issueforge.adapters.base import BaselineStatus, Outcome

    worktree = _make_target(
        tmp_path,
        "teardownfail",
        """
        import pytest

        @pytest.fixture
        def leaky():
            yield 1
            raise RuntimeError("teardown blew up")

        def test_body_passes(leaky):
            assert leaky == 1
        """,
    )
    ev = _run_baseline(worktree)
    assert ev.status is not BaselineStatus.GREEN
    teardown_bad = [
        n
        for n in ev.nodes
        if n.phase == "teardown" and n.outcome in (Outcome.FAILED, Outcome.BROKEN)
    ]
    assert teardown_bad


def test_a_real_syntax_error_is_a_collection_error(tmp_path):
    """A module that fails to import (syntax error) is a COLLECTION_ERROR (pytest exit 2), a
    BROKEN diagnostic distinct from a behavioral red.

    technical (contract): a target whose test module has a syntax error; run_baseline -> status is
    BaselineStatus.COLLECTION_ERROR, not BEHAVIORAL_RED and not GREEN.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = tmp_path / "syntax"
    (worktree / "tests").mkdir(parents=True)
    (worktree / "tests" / "test_target.py").write_text("def test_a(:\n    assert True\n")

    ev = _run_baseline(worktree)
    assert ev.status is BaselineStatus.COLLECTION_ERROR
    assert ev.status not in (BaselineStatus.BEHAVIORAL_RED, BaselineStatus.GREEN)


def test_an_unrecognized_flag_is_a_usage_error(tmp_path):
    """An unrecognized command flag (pytest exit 4) is a USAGE_ERROR, never conflated with a
    collection error.

    technical (contract): run_baseline with a baseline command carrying a bogus flag -> status is
    BaselineStatus.USAGE_ERROR.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(tmp_path, "usage", "def test_a():\n    assert True\n")
    ev = verify.run_baseline(
        worktree,
        ["-m", "pytest", "--no-such-flag"],
        adapter=_adapter(),
        provisioner=_clean_provisioner(),
    )
    assert ev.status is BaselineStatus.USAGE_ERROR


def test_a_missing_interpreter_is_launch_failed(tmp_path):
    """A provisioned interpreter that cannot be launched yields LAUNCH_FAILED — the process never
    produced evidence, distinct from a timeout.

    technical (contract): a provisioner whose interpreter path does not exist; run_baseline ->
    status is BaselineStatus.LAUNCH_FAILED and report_present is False.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(tmp_path, "nolaunch", "def test_a():\n    assert True\n")
    missing = str(tmp_path / "no-such-python")
    ev = _run_baseline(worktree, provisioner=_clean_provisioner(interpreter=missing))
    assert ev.status is BaselineStatus.LAUNCH_FAILED
    assert ev.report_present is False


# =============================================== report is real report-log, fresh, outside repo


def test_report_is_fresh_report_log_jsonl_outside_the_repo_per_invocation(tmp_path):
    """The report is genuine pytest report-log JSONL, in a fresh directory OUTSIDE the repo,
    distinct per invocation — a JUnit-XML or fabricated-JSONL implementation cannot pass.

    technical (contract): two run_baseline calls yield report_dir values that differ and are not
    inside the worktree; each holds a *.jsonl whose lines are report-log records (objects carrying
    a '$report_type'); and a persisted invocation record shows the baseline argv carried a
    '--report-log' option (report-log preferred over JUnit XML, per the issue).
    """
    worktree = _make_target(tmp_path, "fresh", "def test_a():\n    assert True\n")
    first = _run_baseline(worktree)
    second = _run_baseline(worktree)

    assert first.report_dir != second.report_dir
    for report_dir in (first.report_dir, second.report_dir):
        rd = Path(report_dir).resolve()
        assert worktree.resolve() not in rd.parents and rd != worktree.resolve()
        jsonl = list(rd.glob("*.jsonl"))
        assert jsonl, f"expected a JSONL report under {rd}"
        lines = [ln for ln in jsonl[0].read_text().splitlines() if ln.strip()]
        assert any("$report_type" in json.loads(ln) for ln in lines), "not real report-log records"

    argvs = [" ".join(r["argv"]) for r in _invocation_records()]
    assert any("--report-log" in a for a in argvs), "baseline did not use --report-log"


def test_a_real_hang_times_out_leaving_no_report_and_never_consuming_a_stale_one(tmp_path):
    """A REAL hang is killed at the timeout, leaves NO report in the fresh dir, and never consumes
    a stale report from a previous run.

    technical (contract): seed an OLD report file on disk; the target's one test sleeps far past
    the timeout; run_baseline(timeout=1.0) kills it. evidence.status is BaselineStatus.TIMEOUT,
    evidence.report_present is False, the fresh report_dir contains no report file, and the status
    is not derived from the seeded stale report.
    """
    from issueforge.adapters.base import BaselineStatus

    stale = tmp_path / "stale-report.jsonl"
    stale.write_text('{"$report_type": "TestReport", "when": "call", "outcome": "passed"}\n')

    worktree = _make_target(
        tmp_path,
        "slow",
        """
        import time

        def test_hangs():
            time.sleep(30)
        """,
    )
    ev = _run_baseline(worktree, timeout=1.0)
    assert ev.status is BaselineStatus.TIMEOUT
    assert ev.report_present is False
    if ev.report_dir is not None and Path(ev.report_dir).exists():
        assert not list(Path(ev.report_dir).glob("*.jsonl"))


def test_the_baseline_launch_is_an_argv_array_in_the_worktree_with_report_log(tmp_path):
    """The baseline is launched as an argv ARRAY (no shell) in the worktree, via the provisioned
    interpreter, carrying --report-log — proven from the persisted subprocess-boundary evidence.

    technical (contract): after a real run_baseline over a passing target, a persisted invocation
    record exists whose argv is a LIST beginning with the provisioned interpreter, containing a
    'pytest' invocation and a '--report-log' option, and whose cwd is the worktree. Structured
    subprocess evidence is recorded for the baseline (Logging & observability requirement).
    """
    worktree = _make_target(tmp_path, "argv", "def test_a():\n    assert True\n")
    _run_baseline(worktree)

    records = _invocation_records()
    assert records, "no subprocess-boundary evidence was recorded"
    launches = [
        r
        for r in records
        if isinstance(r["argv"], list)
        and r["argv"]
        and r["argv"][0] == sys.executable
        and "pytest" in " ".join(r["argv"])
        and any(a.startswith("--report-log") for a in r["argv"])
    ]
    assert launches, "no provisioned-interpreter pytest+report-log launch was recorded"
    assert any(Path(r["cwd"]).resolve() == worktree.resolve() for r in launches)


# ============================================================ report completeness (incomplete)


def test_an_incomplete_report_at_exit_zero_is_not_green(tmp_path, monkeypatch):
    """Exit 0 with an INCOMPLETE report (an expected node has no terminal record) is not green —
    completeness is part of the evidence, not just the exit code.

    technical (contract): canonical_collect expects {a, b}, but the process returns exit 0 with a
    report containing only a's passing record (b's records truncated/absent). run_baseline ->
    status is NOT GREEN (the report is incomplete for an expected node).
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "incomplete",
        """
        def test_a():
            assert True

        def test_b():
            assert True
        """,
    )

    # Force the executed report to omit b entirely while collection still expects both.
    from issueforge import process

    real_run = process.run

    def _truncating_run(argv, *, cwd, timeout, env=None, secrets=None):
        result = real_run(argv, cwd=cwd, timeout=timeout, env=env, secrets=secrets)
        report_opt = [a for a in argv if a.startswith("--report-log")]
        if report_opt and "--collect-only" not in argv:
            path = Path(report_opt[0].split("=", 1)[1])
            if path.exists():
                kept = [
                    ln
                    for ln in path.read_text().splitlines()
                    if ln.strip() and "::test_b" not in ln
                ]
                path.write_text("\n".join(kept) + "\n")
        return result

    monkeypatch.setattr(process, "run", _truncating_run)
    ev = verify.run_baseline(
        worktree, BASELINE, adapter=_adapter(), provisioner=_clean_provisioner()
    )
    assert ev.exit_code == 0
    assert ev.status is not BaselineStatus.GREEN


# =========================================== run_baseline consumes canonical_collect's ids


def test_run_baseline_uses_canonical_collect_ids_as_the_expected_set(tmp_path):
    """The expected-id set comes from canonical_collect, not from the execution report — a node
    canonical_collect expects that never appears in the run makes the baseline non-green.

    technical (contract): wrap the adapter so canonical_collect returns the real ids PLUS a phantom
    id that the run will never emit. Over an all-passing target, run_baseline -> status is NOT
    GREEN (the phantom expected id is absent). An implementation that derives 'expected' from the
    report alone would wrongly return GREEN.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    base = _adapter()

    class _GhostAdapter:
        framework = base.framework
        reporter = base.reporter

        def __getattr__(self, name):
            return getattr(base, name)

        def canonical_collect(self, invocation):
            collection = base.canonical_collect(invocation)
            ids = tuple(sorted((*collection.ids, "tests/test_target.py::ghost")))
            from types import SimpleNamespace

            return SimpleNamespace(ids=ids, selection=getattr(collection, "selection", None))

    worktree = _make_target(tmp_path, "ghost", "def test_a():\n    assert True\n")
    ev = verify.run_baseline(
        worktree, BASELINE, adapter=_GhostAdapter(), provisioner=_clean_provisioner()
    )
    assert ev.status is not BaselineStatus.GREEN


# =================================================================== G14 authoritative env


def test_candidate_env_mutations_cannot_change_the_authoritative_result(tmp_path, monkeypatch):
    """G14: candidate environment mutations cannot change the authoritative result — the baseline
    runs in the separately-provisioned allowlist env, not the candidate's.

    technical (contract): the target passes only when IF_SABOTAGE is unset, PYTEST_ADDOPTS is
    unset, and a candidate-injected importable package (`if_candidate_poison`) is NOT importable.
    A candidate sets IF_SABOTAGE=1, PYTEST_ADDOPTS='-k nonexistent', AND puts a poison package on
    PYTHONPATH (the issue's "edits the target's installed packages / sets a test-runner env var"
    vector). run_baseline with the hermetic provisioner (allowlist env excludes all three) still
    classifies GREEN — no candidate mutation reaches the authoritative run.
    """
    from issueforge.adapters.base import BaselineStatus

    # A candidate-controlled importable package the authoritative run must never see.
    poison_dir = tmp_path / "candidate_site"
    poison_dir.mkdir()
    (poison_dir / "if_candidate_poison.py").write_text("VALUE = 'leaked'\n")

    worktree = _make_target(
        tmp_path,
        "hermetic",
        """
        import importlib.util
        import os

        def test_env_and_packages_are_clean():
            assert os.environ.get("IF_SABOTAGE") != "1"
            assert "PYTEST_ADDOPTS" not in os.environ
            assert importlib.util.find_spec("if_candidate_poison") is None
        """,
    )
    monkeypatch.setenv("IF_SABOTAGE", "1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nonexistent")
    monkeypatch.setenv("PYTHONPATH", str(poison_dir))

    ev = _run_baseline(worktree)
    assert ev.status is BaselineStatus.GREEN


def test_default_provisioner_uses_a_separate_interpreter_under_an_owned_root(tmp_path):
    """The DEFAULT provisioning path builds a SEPARATE authoritative interpreter under an
    IssueForge-owned root (network off) — not the host interpreter, not the target's environment.

    technical (contract): run_baseline with the adapter's DEFAULT provisioner over a passing target
    -> status is BaselineStatus.GREEN; the authoritative interpreter (evidence.interpreter) is a
    real path UNDER state_root() and is NOT sys.executable (a host-interpreter stub fails this);
    the env_root is under state_root() and outside the target tree; and network is off. This is the
    single real-provision anchor proving a SEPARATE authoritative interpreter; the fast seam
    proves candidate-package/env isolation (PYTHONPATH + env-var poisoning) reliably.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus
    from issueforge.paths import state_root

    worktree = _make_target(tmp_path, "realenv", "def test_a():\n    assert True\n")
    ev = verify.run_baseline(worktree, BASELINE, adapter=_adapter())  # default provisioner
    assert ev.status is BaselineStatus.GREEN

    root = Path(state_root()).resolve()
    interp = Path(ev.interpreter).resolve()
    assert interp != Path(sys.executable).resolve(), "authoritative run used the HOST interpreter"
    assert root in interp.parents, (
        "authoritative interpreter is not under the IssueForge-owned root"
    )
    env_root = Path(ev.env_root).resolve()
    assert root in env_root.parents or env_root == root
    assert worktree.resolve() not in env_root.parents
    assert ev.network is False


# =========================================== establish orchestration: pause BEFORE any AI


def test_establish_continues_on_green_over_a_real_non_main_default_branch(tmp_path):
    """The happy path: a real fetch of a non-'main' default branch, an isolated worktree, and a
    GREEN baseline loaded from the tree's committed command -> the run continues (not paused) and
    the dispatch hook is invoked exactly once.

    technical (contract): a bare origin on default branch 'trunk' carrying a passing test_ok, a
    FAILING test_broken, and an .issueforge.toml whose committed baseline selects only test_ok
    (`-k test_ok`). establish_green_baseline(checkout, adapter, provisioner=clean, dispatch=spy) ->
    paused is False, status is BaselineStatus.GREEN, worktree is a real path, and dispatch is called
    once. A hardcoded `["-m", "pytest"]` command (ignoring the committed selection) would run
    test_broken and go red — so green here proves the committed baseline command was used.

    The command must come from the ISOLATED WORKTREE (fetched origin), not the normal checkout: the
    checkout's own .issueforge.toml is then dirtied to select the FAILING test, so an implementation
    that reads config from the checkout instead of the worktree would go red. Green proves the
    worktree's committed config was the source.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    _origin, checkout = _origin_with_checkout(
        tmp_path,
        branch="trunk",
        files={
            "tests/test_target.py": (
                "def test_ok():\n    assert True\n\n\ndef test_broken():\n    assert False\n"
            ),
            ".issueforge.toml": (
                'baseline = ["-m", "pytest", "-k", "test_ok"]\nframework = "pytest"\n'
            ),
        },
    )
    # Dirty the CHECKOUT's config to select the failing test. Reading it (instead of the isolated
    # worktree's committed config) would run test_broken and go red.
    (checkout / ".issueforge.toml").write_text(
        'baseline = ["-m", "pytest", "-k", "test_broken"]\nframework = "pytest"\n'
    )
    dispatched = []
    outcome = verify.establish_green_baseline(
        checkout,
        adapter=_adapter(),
        provisioner=_clean_provisioner(),
        dispatch=lambda **k: dispatched.append(k),
    )
    assert outcome.paused is False
    assert outcome.status is BaselineStatus.GREEN
    assert outcome.worktree is not None and Path(outcome.worktree).exists()
    assert len(dispatched) == 1


def test_establish_pauses_on_failed_fetch_without_running_baseline_or_dispatch(tmp_path):
    """A failed fetch pauses BEFORE any baseline runs and BEFORE any AI dispatch — the suite never
    runs on a stale tree.

    technical (contract): a workspace whose fetch fails; establish_green_baseline(..., dispatch=spy,
    with the real run_baseline replaced by a tripwire) -> paused is True, worktree is None,
    pause_reason mentions fetch, and neither the baseline tripwire nor the dispatch spy fired.
    """
    from issueforge import verify

    ran = {"baseline": False, "dispatch": False}
    verify_run = verify.run_baseline

    class _FailingWorkspace:
        def fetch_default_sha(self, checkout):
            from types import SimpleNamespace

            return SimpleNamespace(sha=None, ok=False, reason="fetch failed")

    outcome = verify.establish_green_baseline(
        tmp_path / "checkout",
        adapter=_adapter(),
        workspace=_FailingWorkspace(),
        run_baseline=lambda *a, **k: ran.__setitem__("baseline", True),
        dispatch=lambda **k: ran.__setitem__("dispatch", True),
    )
    assert outcome.paused is True
    assert outcome.worktree is None
    assert "fetch" in (outcome.pause_reason or "").lower()
    assert ran == {"baseline": False, "dispatch": False}
    assert verify_run is verify.run_baseline  # tripwire was injected, not a global mutation


def test_establish_pauses_on_unprovable_isolation_without_running_baseline(tmp_path):
    """Unprovable isolation pauses before the baseline runs — a runner must not proceed to execute
    a suite in a worktree it could not prove isolated.

    technical (contract): a workspace that fetches cleanly but returns a worktree with ok=False /
    isolated=False; establish_green_baseline(..., run_baseline=tripwire, dispatch=spy) -> paused is
    True and neither the baseline tripwire nor the dispatch spy fired.
    """
    from issueforge import verify

    ran = {"baseline": False, "dispatch": False}

    class _UnisolatedWorkspace:
        def fetch_default_sha(self, checkout):
            from types import SimpleNamespace

            return SimpleNamespace(sha="abc123", ok=True, reason=None)

        def create_isolated_worktree(self, checkout, sha, **k):
            from types import SimpleNamespace

            return SimpleNamespace(path=None, ok=False, isolated=False, reason="index changed")

    outcome = verify.establish_green_baseline(
        tmp_path / "checkout",
        adapter=_adapter(),
        workspace=_UnisolatedWorkspace(),
        run_baseline=lambda *a, **k: ran.__setitem__("baseline", True),
        dispatch=lambda **k: ran.__setitem__("dispatch", True),
    )
    assert outcome.paused is True
    assert ran == {"baseline": False, "dispatch": False}


def test_establish_pauses_on_a_non_green_baseline_without_dispatch(tmp_path):
    """A non-green baseline pauses the run and never dispatches AI, carrying the non-green status.

    technical (contract): a workspace that fetches + isolates cleanly, but the injected baseline
    returns BEHAVIORAL_RED evidence; establish_green_baseline(..., dispatch=spy) -> paused is True,
    outcome.status is BaselineStatus.BEHAVIORAL_RED, and the dispatch spy never fired.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    dispatched = []

    class _CleanWorkspace:
        def fetch_default_sha(self, checkout):
            from types import SimpleNamespace

            return SimpleNamespace(sha="abc123", ok=True, reason=None)

        def create_isolated_worktree(self, checkout, sha, **k):
            from types import SimpleNamespace

            wt = tmp_path / "wt"
            wt.mkdir(exist_ok=True)
            return SimpleNamespace(path=wt, ok=True, isolated=True, reason=None)

    def _red_baseline(*a, **k):
        from types import SimpleNamespace

        return SimpleNamespace(
            status=BaselineStatus.BEHAVIORAL_RED,
            executed=1,
            collected=1,
            nodes=(),
            report_present=True,
            report_dir=tmp_path / "r",
            exit_code=1,
        )

    outcome = verify.establish_green_baseline(
        tmp_path / "checkout",
        adapter=_adapter(),
        workspace=_CleanWorkspace(),
        run_baseline=_red_baseline,
        dispatch=lambda **k: dispatched.append(k),
    )
    assert outcome.paused is True
    assert outcome.status is BaselineStatus.BEHAVIORAL_RED
    assert dispatched == []


# ======================================================================
# Issue #58 — S6 hardening: adversarial tests reproducing the 14 Codex-found
# defects the happy-path S6 suite missed. Each is PENDING until the #58 build.
# ======================================================================


# ---- helper for #8 ----
def _appending_provisioner(extra_line: str):
    """A provisioner seam whose 'interpreter' is a REAL wrapper script: it runs the provisioned
    pytest verbatim, then APPENDS one extra report-log record (``extra_line``) to the real
    --report-log before exiting with pytest's own exit code.

    Only the injectable ``provisioner`` seam (a production API param) is used — no issueforge
    internal is patched. collect-only runs carry no --report-log, so the wrapper leaves them
    untouched and canonical_collect's expected-id set never sees the injected node; the node
    appears ONLY in the execution report, exactly the "contradictory report log" the contract
    forbids from being classified GREEN.
    """
    from types import SimpleNamespace

    def _provision(worktree, frozen_deps=None):
        wdir = Path(worktree).parent / f"if-wrap-{Path(worktree).name}"
        wdir.mkdir(exist_ok=True)
        wrapper = wdir / "wrap-python"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'"{sys.executable}" "$@"\n'
            "code=$?\n"
            'rl=""\n'
            'for a in "$@"; do\n'
            '  case "$a" in --report-log=*) rl="${a#--report-log=}";; esac\n'
            "done\n"
            f'if [ -n "$rl" ] && [ -f "$rl" ]; then printf \'%s\\n\' \'{extra_line}\' >> "$rl"; fi\n'
            "exit $code\n"
        )
        os.chmod(wrapper, 0o755)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        return SimpleNamespace(interpreter=str(wrapper), env=env, artifact_dir=wdir, network=False)

    return _provision


# ===== #58 defect #1 =====
def test_baseline_argv_executable_resolved_in_env_not_prepended_to_interpreter(tmp_path):
    """The committed baseline command names its own executable; the runner resolves that
    executable inside the authoritative environment instead of blindly prepending the provisioned
    interpreter. So the real grammar baseline = ["pytest"] (the shape S1's config loader actually
    produces, per tests/test_config.py and conftest's DEFAULT_CONFIG) over one genuinely passing
    test classifies GREEN end-to-end, and ["uv", "run", "pytest"] launches "uv" as the executable
    rather than handing "uv" to the interpreter as a script argument.

    Today the runner builds ``[interpreter, *base_command, --report-log=...]`` (verify.py:120 and
    the mirror in pytest_adapter.canonical_collect), so ["pytest"] becomes ``<py> pytest`` — pytest
    is treated as a script path, the process exits 2, and the baseline is COLLECTION_ERROR, never
    GREEN. A correct fix resolves the registered argv's executable in the authoritative env.

    technical (contract):
      * run_baseline(worktree, ["pytest"], adapter=PytestAdapter(), provisioner=clean) over a
        target whose one test passes -> evidence.status is BaselineStatus.GREEN, evidence.executed
        == 1, evidence.exit_code == 0. (Current impl: status COLLECTION_ERROR, exit_code 2.)
      * run_baseline(worktree, ["uv", "run", "pytest"], ...) -> at least one persisted invocation
        record whose argv contains both "run" and "pytest" (the uv baseline launch/collect), and
        EVERY such record's argv[0] is NOT the provisioned interpreter (sys.executable). Current
        impl records argv[0] == sys.executable with "uv" demoted to argv[1].
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    interpreter = sys.executable
    provisioner = _clean_provisioner(interpreter)
    adapter = _adapter()

    # Sub-case 1: the real committed grammar baseline = ["pytest"] over one passing test -> GREEN.
    # Prepending the interpreter turns "pytest" into a bogus script path (exit 2), so GREEN here
    # proves the executable was resolved in the authoritative env, not blindly prefixed.
    worktree = _make_target(tmp_path, "grammar", "def test_ok():\n    assert True\n")
    ev = verify.run_baseline(worktree, ["pytest"], adapter=adapter, provisioner=provisioner)
    assert ev.status is BaselineStatus.GREEN
    assert ev.executed == 1
    assert ev.exit_code == 0

    # Sub-case 2: ["uv", "run", "pytest"] must resolve "uv" as the executable, never demote it to a
    # script arg behind the interpreter. Proven from the persisted subprocess-boundary evidence.
    uv_worktree = _make_target(tmp_path, "uvgrammar", "def test_ok():\n    assert True\n")
    verify.run_baseline(
        uv_worktree, ["uv", "run", "pytest"], adapter=adapter, provisioner=provisioner
    )
    uv_launches = [r for r in _invocation_records() if "run" in r["argv"] and "pytest" in r["argv"]]
    assert uv_launches, "no uv baseline launch was recorded"
    assert all(r["argv"][0] != interpreter for r in uv_launches), (
        "interpreter was blindly prepended to the uv baseline command"
    )


# ===== #58 defect #2 =====
def test_missing_or_malformed_committed_baseline_config_pauses_not_defaults(tmp_path):
    """An isolated worktree whose committed config is missing, malformed, carries an empty
    baseline, or a non-list baseline must PAUSE the run (no AI dispatch) instead of silently
    substituting a default ``["-m", "pytest"]``. The baseline is MANDATORY and comes from the
    committed git object, so a passing target with no usable committed baseline must never be
    greened and dispatched.

    technical (contract): for each committed tree carrying a genuinely passing
    tests/test_target.py but a BAD config -- (1) no .issueforge.toml at all, (2) malformed TOML,
    (3) baseline = [], (4) baseline = "pytest" (a string, not an argv array) -- a real fetch of
    default branch 'trunk', a real isolated worktree, and establish_green_baseline(checkout,
    adapter, provisioner=clean, dispatch=spy) -> outcome.paused is True, the dispatch spy is
    NEVER called (dispatched == []), and outcome.status is not BaselineStatus.GREEN. The current
    impl substitutes ["-m", "pytest"], runs the passing target, classifies GREEN, and dispatches
    -- so green-and-dispatch on any case proves the mandatory committed baseline was skipped and
    read from the (symlink-able) worktree filesystem rather than from the committed HEAD object.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    passing = "def test_ok():\n    assert True\n"
    cases = {
        "missing": {"tests/test_target.py": passing},
        "malformed": {"tests/test_target.py": passing, ".issueforge.toml": "baseline = [\n"},
        "empty_baseline": {"tests/test_target.py": passing, ".issueforge.toml": "baseline = []\n"},
        "non_list_baseline": {
            "tests/test_target.py": passing,
            ".issueforge.toml": 'baseline = "pytest"\n',
        },
    }

    for case, files in cases.items():
        case_root = tmp_path / case
        case_root.mkdir()
        _origin, checkout = _origin_with_checkout(case_root, branch="trunk", files=files)

        dispatched = []
        outcome = verify.establish_green_baseline(
            checkout,
            adapter=_adapter(),
            provisioner=_clean_provisioner(),
            dispatch=lambda **k: dispatched.append(k),
        )
        assert outcome.paused is True, f"{case}: expected pause, got status {outcome.status!r}"
        assert dispatched == [], f"{case}: AI was dispatched on an invalid committed baseline"
        assert outcome.status is not BaselineStatus.GREEN, f"{case}: greened on invalid config"


# ===== #58 defect #7 =====
def test_failed_or_timed_out_canonical_collect_refuses_green(tmp_path):
    """A --collect-only that exits NONZERO must not seed a GREEN baseline: the runner cannot
    trust node ids scraped from a broken collection, so run_baseline must refuse green.

    The target's two tests pass when actually run, but a conftest hook raises during collection
    ONLY under --collect-only (session.config.option.collectonly), so the collect step exits
    nonzero while still printing the two `tests/test_target.py::` node ids to stdout. Today's
    canonical_collect ignores that nonzero exit, scrapes those `::` lines as the expected set, and
    the subsequent real run (collectonly False) passes both — yielding a FALSE GREEN. A correct
    impl treats the failed collection as non-green evidence and never classifies GREEN.

    technical (contract): a target with test_a + test_b (both pass on a normal run) plus a
    conftest.py whose pytest_sessionfinish raises RuntimeError when session.config.option.collectonly
    is set. The interpreter's `-m pytest --collect-only -q` exits nonzero (RuntimeError from the
    hook) but emits `tests/test_target.py::test_a` and `tests/test_target.py::test_b` on stdout.
    run_baseline over this target -> evidence.status is NOT BaselineStatus.GREEN. (On the current
    impl the scraped-ids-ignoring-exit path returns status GREEN, collected==2, executed==2,
    exit_code==0 — the defect.)
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "collectfail",
        """
        def test_a():
            assert True

        def test_b():
            assert 1 + 1 == 2
        """,
    )
    # A collect-only-gated sabotage: the hook raises during --collect-only (exit nonzero) but the
    # node ids are already printed to stdout; the real run (collectonly False) passes cleanly.
    (worktree / "conftest.py").write_text(
        textwrap.dedent(
            """
            def pytest_sessionfinish(session, exitstatus):
                if session.config.option.collectonly:
                    raise RuntimeError("collect-only sabotage")
            """
        )
    )
    ev = _run_baseline(worktree)
    assert ev.status is not BaselineStatus.GREEN


# ===== #58 defect #8 =====
def test_incomplete_or_contradictory_report_log_is_not_green(tmp_path):
    """A contradictory report log is not GREEN: the one expected node passes, but an UNEXPECTED
    node carries a BROKEN record in the same report. GREEN must be a whole-report property (no
    failed/BROKEN record anywhere), not just "every expected node passed" — a runner that only
    inspects the expected nodes' own records would wrongly bless this run.

    technical (contract): a target with a single test_a (real pytest, exit 0, real --report-log).
    The provisioner seam supplies a REAL wrapper interpreter that runs pytest verbatim, then appends
    ONE extra report-log record for an UNEXPECTED node
    (nodeid 'tests/test_target.py::test_ghost', when 'call', outcome 'errored' -> Outcome.BROKEN)
    to the real report before exiting with pytest's own code. canonical_collect (no --report-log)
    still expects only {test_a}, so the ghost appears ONLY in the execution report. run_baseline ->
    evidence.exit_code == 0 (self-proving: exit 0 was seen) AND evidence.status is NOT
    BaselineStatus.GREEN (the unexpected BROKEN record forbids green). Current impl classifies GREEN
    because _is_green only checks _node_passed over expected ids and never consults the whole-report
    has_failure signal.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    ghost_line = (
        '{"$report_type": "TestReport", "nodeid": "tests/test_target.py::test_ghost", '
        '"when": "call", "outcome": "errored", "longrepr": "unexpected node blew up"}'
    )
    worktree = _make_target(tmp_path, "contradict", "def test_a():\n    assert True\n")
    ev = verify.run_baseline(
        worktree,
        BASELINE,
        adapter=_adapter(),
        provisioner=_appending_provisioner(ghost_line),
    )
    assert ev.exit_code == 0
    assert ev.status is not BaselineStatus.GREEN


# ===== #58 defect #9 =====
def test_broken_executions_classify_broken_not_behavioral_red(tmp_path):
    """A broken execution (the interpreter dies from a signal, not a clean call-phase failure) is
    BROKEN, never BEHAVIORAL_RED. BEHAVIORAL_RED is reserved for a complete report with a real
    call-phase failure at exit 1 — a signaled/aborted run has no such evidence and must not be
    mislabeled as a behavioral red baseline.

    technical (contract): a target whose one test calls os.abort() crashes pytest with SIGABRT, so
    the subprocess dies with a NEGATIVE (signaled) exit code and timed_out is False. run_baseline ->
    evidence.exit_code is not None and < 0 (a genuine signaled death, not a timeout), and
    evidence.status is BaselineStatus.BROKEN — explicitly NOT BaselineStatus.BEHAVIORAL_RED. The
    current impl falls _status through to BEHAVIORAL_RED for any non-green, non-zero exit; the fix
    maps signaled/reportless/unknown/infra cases to BROKEN and reserves BEHAVIORAL_RED for a
    complete report with an expected call-phase failure at exit 1.
    """
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(
        tmp_path,
        "aborted",
        """
        import os

        def test_crashes_the_interpreter():
            os.abort()
        """,
    )
    ev = _run_baseline(worktree)
    # A genuine signaled death, not a timeout — the raw negative exit is the broken signal.
    assert ev.exit_code is not None and ev.exit_code < 0
    # The load-bearing contract: a signaled/broken execution is NOT a behavioral red.
    assert ev.status is not BaselineStatus.BEHAVIORAL_RED
    assert ev.status is BaselineStatus.BROKEN


# ===== #58 defect #12 =====
def test_provisioning_or_runner_failure_pauses_never_crashes(tmp_path):
    """A provisioning/runner failure PAUSES the run with a typed non-green status and never
    dispatches AI, instead of letting the exception escape and crash the engine before any AI
    is gated. Missing uv, a write failure, or a provisioner blowing up must all become paused
    evidence, not an uncaught traceback.

    technical (contract): a real offline bare origin (default branch 'trunk') + checkout carrying
    a passing test_ok and a committed pytest baseline; a provisioner seam that raises RuntimeError
    the moment run_baseline provisions the authoritative env. establish_green_baseline(checkout,
    adapter, provisioner=boom, dispatch=spy) returns WITHOUT raising: outcome.paused is True,
    outcome.status is a BaselineStatus member that is NOT BaselineStatus.GREEN (typed non-green,
    not None), and the dispatch spy never fired (dispatched == []). Today the RuntimeError escapes
    run_baseline (verify.py:93, adapter.provision_environment) and establish_green_baseline
    (verify.py:217, the runner call), so the call raises instead of returning a paused outcome.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    _origin, checkout = _origin_with_checkout(
        tmp_path,
        branch="trunk",
        files={
            "tests/test_target.py": "def test_ok():\n    assert True\n",
            ".issueforge.toml": 'baseline = ["-m", "pytest"]\nframework = "pytest"\n',
        },
    )

    def _boom_provisioner(worktree, frozen_deps=None):
        # A provisioner that cannot build the authoritative env (e.g. uv missing) raises here.
        raise RuntimeError("provisioner exploded: uv unavailable")

    dispatched = []
    outcome = verify.establish_green_baseline(
        checkout,
        adapter=_adapter(),
        provisioner=_boom_provisioner,
        dispatch=lambda **k: dispatched.append(k),
    )
    assert outcome.paused is True
    assert isinstance(outcome.status, BaselineStatus)
    assert outcome.status is not BaselineStatus.GREEN
    assert dispatched == []


# ======================================================================
# PR #60 hardening — adversarial tests for the 11 GREEN-safety gaps Codex
# found in the S6 hardening build. PENDING until the fix closes them.
# ======================================================================


# ---- helper for hardening #2 ----
def _committed_manifest_target(
    root: Path,
    name: str,
    body: str,
    *,
    requirements: str,
    dependencies: list[str],
) -> Path:
    """A git-committed target carrying one test module plus a FROZEN dependency manifest: a pinned
    requirements.txt and a pyproject.toml whose ``[project].dependencies`` pins the same specs.

    Both forms are committed so run_baseline can discover the manifest from whichever committed
    representation the fix reads; the target is a real repo (``git init`` + commit) so a
    discovery that reads the committed git object, not just the worktree filesystem, still resolves.
    """
    repo = root / name
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_target.py").write_text(textwrap.dedent(body))
    (repo / "requirements.txt").write_text(requirements)
    deps_array = "[" + ", ".join(f'"{spec}"' for spec in dependencies) + "]"
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "if-target"\nversion = "0"\ndependencies = {deps_array}\n'
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _manifest_provisioner():
    """A provisioner seam modelling _provision_default's manifest install: it makes EXACTLY the
    frozen deps it is handed importable in the authoritative interpreter, by writing a stub module
    per dep onto a controlled PYTHONPATH allowlist env.

    A dep the manifest never named is never importable, and ``frozen_deps=None`` (today's
    run_baseline call) installs nothing — so a baseline's ``import <dep>`` only succeeds when
    run_baseline discovered the target's committed manifest and passed that dep through. The env is
    a small explicit allowlist (never a copy of the candidate's os.environ), like _clean_provisioner.
    """
    import uuid
    from types import SimpleNamespace

    def _dep_names(frozen_deps: object) -> list[str]:
        if not frozen_deps:
            return []
        items = frozen_deps.keys() if isinstance(frozen_deps, dict) else frozen_deps
        names: list[str] = []
        for spec in items:
            name = ""
            for char in str(spec):
                if not (char.isalnum() or char in "-_."):
                    break
                name += char
            if name:
                names.append(name.replace("-", "_"))
        return names

    def _provision(worktree, frozen_deps=None):
        site = Path(worktree).parent / f"if-manifest-site-{Path(worktree).name}-{uuid.uuid4().hex}"
        site.mkdir(exist_ok=True)
        for dep in _dep_names(frozen_deps):
            (site / f"{dep}.py").write_text("__version__ = '1.0.0'\n")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONPATH": str(site),
        }
        return SimpleNamespace(
            interpreter=sys.executable, env=env, artifact_dir=site, network=False
        )

    return _provision


# ---- helper for hardening #3 ----
def _corrupt_git_worktree(root: Path, files: dict[str, str]) -> tuple[Path, str]:
    """A REAL isolated worktree whose git metadata is unreadable/corrupt while its working files
    remain on disk.

    Builds a committed git repo (default branch 'trunk') carrying ``files``, adds a linked worktree
    at ``<root>/wt`` checked out to HEAD, then deletes the worktree's backing admin dir
    (``main/.git/worktrees``) so the worktree's ``.git`` file points at nothing. A real
    ``git -C <wt> rev-parse --verify HEAD`` / ``git show HEAD:.issueforge.toml`` in that worktree
    then exits 128, exactly the "isolated worktree exists but its git metadata is unreadable/corrupt"
    condition — but the checked-out tests/test_target.py stays on disk, so a runner that falls back
    to a default baseline can still run (and wrongly green) the target. Returns ``(worktree, sha)``.
    """
    main = root / "main"
    main.mkdir(parents=True)
    for rel, content in files.items():
        p = main / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(main)], check=True)
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "seed")
    sha = _git(main, "rev-parse", "HEAD").stdout.strip()
    wt = root / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "HEAD")
    # Corrupt: remove the worktree's backing admin dir so its git metadata is unreadable (exit 128).
    shutil.rmtree(main / ".git" / "worktrees")
    return wt, sha


# ---- helper for hardening #5 ----
def _collect_only_stdout_polluter(extra_line: str):
    """A provisioner seam whose 'interpreter' is a REAL wrapper script: it runs the provisioned
    pytest verbatim, then -- ONLY when the invocation carries --collect-only -- prints one extra
    line (``extra_line``) to stdout after pytest's own collection output, before exiting with
    pytest's own code.

    Only the injectable ``provisioner`` seam (a production API param) is used -- no issueforge
    internal is patched. The extra line is a plausible node id followed by trailing garbage, so it
    drives canonical_collect's real --collect-only stdout parser (which today scrapes any line
    containing '::') with malformed collection output that still exits 0. Execution (non-collect)
    runs carry no --collect-only, so the wrapper leaves the real run untouched.
    """
    from types import SimpleNamespace

    def _provision(worktree, frozen_deps=None):
        wdir = Path(worktree).parent / f"if-collectwrap-{Path(worktree).name}"
        wdir.mkdir(exist_ok=True)
        wrapper = wdir / "wrap-python"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'"{sys.executable}" "$@"\n'
            "code=$?\n"
            "co=0\n"
            'for a in "$@"; do case "$a" in --collect-only) co=1;; esac; done\n'
            f"if [ \"$co\" = \"1\" ]; then printf '%s\\n' '{extra_line}'; fi\n"
            "exit $code\n"
        )
        os.chmod(wrapper, 0o755)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        return SimpleNamespace(interpreter=str(wrapper), env=env, artifact_dir=wdir, network=False)

    return _provision


# ---- helper for hardening #6 ----
def _truncating_no_sessionfinish_provisioner():
    """A provisioner seam whose 'interpreter' is a REAL wrapper script: it runs the provisioned
    pytest verbatim, then rewrites the real --report-log so the terminal SessionFinish record is
    REMOVED and a truncated/corrupt JSON line is appended, before exiting with pytest's own code.

    Only the injectable ``provisioner`` seam (a production API param) is used — no issueforge
    internal is patched. collect-only runs carry no --report-log, so the wrapper leaves them
    untouched and canonical_collect's expected-id set is unaffected. The passing TestReport records
    survive verbatim; only the clean session-end marker is dropped and a malformed line appended —
    exactly the corrupt/truncated, no-terminal-SessionFinish report the contract forbids from
    being classified GREEN.
    """
    from types import SimpleNamespace

    def _provision(worktree, frozen_deps=None):
        wdir = Path(worktree).parent / f"if-corrupt-{Path(worktree).name}"
        wdir.mkdir(exist_ok=True)
        corruptor = wdir / "corrupt_report.py"
        corruptor.write_text(
            textwrap.dedent(
                """
                import sys
                p = sys.argv[1]
                lines = [l for l in open(p).read().splitlines() if l.strip()]
                kept = [l for l in lines if '"SessionFinish"' not in l]
                with open(p, "w") as f:
                    f.write("\\n".join(kept))
                    f.write("\\n")
                    f.write('{"$report_type": "TestRep')
                """
            )
        )
        wrapper = wdir / "wrap-python"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'"{sys.executable}" "$@"\n'
            "code=$?\n"
            'rl=""\n'
            'for a in "$@"; do\n'
            '  case "$a" in --report-log=*) rl="${a#--report-log=}";; esac\n'
            "done\n"
            f'if [ -n "$rl" ] && [ -f "$rl" ]; then "{sys.executable}" "{corruptor}" "$rl"; fi\n'
            "exit $code\n"
        )
        os.chmod(wrapper, 0o755)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        return SimpleNamespace(interpreter=str(wrapper), env=env, artifact_dir=wdir, network=False)

    return _provision


# ===== #58 hardening (PR#60 Codex gap #1) =====
def test_default_provisioner_resolves_baseline_executable_under_env_root_not_host_path(tmp_path):
    """The DEFAULT provisioning path must launch the committed bare-name baseline (["pytest"], the
    grammar the S1 config loader actually produces) from the AUTHORITATIVE environment it just
    built, never a pytest that happens to sit on the host PATH. The whole point of provisioning a
    separate env is that the baseline runs against the authoritative interpreter's own installed
    packages; if the runner resolves ["pytest"] against the host PATH, the authoritative env is
    decorative and the baseline can go green off host-leaked packages the authoritative env never
    installed. So the executable actually launched for the baseline must live UNDER the
    authoritative env_root, not on the host.

    Today _provision_default hands build_launch_argv an env whose PATH is the raw host PATH
    (pytest_adapter.py:87), so shutil.which("pytest", ...) resolves the HOST pytest and the
    provisioned env_root/bin/pytest is never used (process.py build_launch_argv). The persisted
    launch records show argv[0] == the host .venv/bin/pytest, outside env_root.

    technical (contract): run_baseline(worktree, ["pytest"], adapter=PytestAdapter()) with the
    DEFAULT provisioner (no provisioner= injected) over a target whose one test passes. From the
    persisted subprocess-boundary evidence, EVERY launch record carrying a "--report-log" option
    (the baseline execution) must have argv[0] resolving to a path UNDER
    Path(evidence.env_root).resolve() (e.g. <env_root>/bin/pytest). Current impl: argv[0] resolves
    to the host interpreter's pytest (e.g. <repo>/.venv/bin/pytest), which is NOT under env_root, so
    the assertion fails — the host pytest was launched, not the authoritative one.
    """
    from issueforge import verify

    # DEFAULT provisioning path: a real, separate venv under state_root() (no injected seam), so the
    # only way ["pytest"] can be launched authoritatively is by resolving it against env_root/bin.
    worktree = _make_target(tmp_path, "hostleak", "def test_ok():\n    assert True\n")
    ev = verify.run_baseline(worktree, ["pytest"], adapter=_adapter())

    env_root = Path(ev.env_root).resolve()
    launches = [
        r
        for r in _invocation_records()
        if isinstance(r["argv"], list)
        and r["argv"]
        and any(str(a).startswith("--report-log") for a in r["argv"])
    ]
    assert launches, "no baseline pytest launch was recorded"
    for r in launches:
        exe = Path(str(r["argv"][0])).resolve()
        assert env_root in exe.parents, (
            f"baseline launched {exe}, not a pytest under the authoritative env_root {env_root} "
            "(host PATH leaked into executable resolution)"
        )


# ===== #58 hardening (PR#60 Codex gap #2) =====
def test_run_baseline_discovers_and_installs_the_target_frozen_manifest(tmp_path):
    """Through the REAL run_baseline path, a target that commits a frozen manifest naming a pinned
    dependency, and whose baseline imports that dependency, classifies GREEN — because run_baseline
    DISCOVERS the target's committed manifest and hands it to the authoritative-environment
    provisioner, so the pinned dep is importable in the separate run. A baseline that imports a dep
    the manifest does NOT name is never GREEN.

    Today run_baseline always calls provision_environment(worktree, None, ...) (verify.py:96): the
    real baseline path never supplies the target's frozen manifest — only the direct
    provision_environment unit test does. So the pinned dep is absent from the authoritative env,
    the baseline's `import <dep>` fails at collection, and the run is a COLLECTION_ERROR instead of
    GREEN. A correct fix discovers the committed manifest (a pinned requirements.txt /
    pyproject.toml dependency list) and passes it as the frozen_deps the provisioner installs.

    The provisioner seam here is faithful to the real _provision_default: it makes exactly the deps
    it is handed importable in the authoritative interpreter (a stub module per dep on a controlled
    PYTHONPATH) — never a dep the manifest did not name, and nothing at all when it is handed None.
    So GREEN can only come from run_baseline having discovered and passed the target's own manifest.

    technical (contract):
      * a git-committed target whose tests/test_target.py does `import if_pinned_dep` (asserting its
        __version__ == "1.0.0"), a committed requirements.txt pinning `if_pinned_dep==1.0.0`, and a
        committed pyproject.toml with dependencies == ["if_pinned_dep==1.0.0"]. run_baseline(target,
        ["-m","pytest"], adapter=PytestAdapter(), provisioner=_manifest_provisioner()) ->
        evidence.status is BaselineStatus.GREEN, evidence.executed == 1, evidence.exit_code == 0.
        (Current impl: run_baseline passes frozen_deps=None, the seam installs nothing, the import
        fails during --collect-only, status is BaselineStatus.COLLECTION_ERROR at exit_code 2.)
      * a control target committing requirements.txt/pyproject deps that pin a DIFFERENT dep
        (`if_other_dep==2.0.0`) while its baseline does `import if_absent_dep` (not in the manifest)
        -> evidence.status is NOT BaselineStatus.GREEN — a manifest that does not name the imported
        dep cannot green, so a fix cannot pass by installing everything unconditionally.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    baseline = ["-m", "pytest"]

    green = _committed_manifest_target(
        tmp_path,
        "manifest_green",
        """
        import if_pinned_dep

        def test_uses_pinned_dep():
            assert if_pinned_dep.__version__ == "1.0.0"
        """,
        requirements="if_pinned_dep==1.0.0\n",
        dependencies=["if_pinned_dep==1.0.0"],
    )
    ev = verify.run_baseline(
        green, baseline, adapter=_adapter(), provisioner=_manifest_provisioner()
    )
    assert ev.status is BaselineStatus.GREEN
    assert ev.executed == 1
    assert ev.exit_code == 0

    # Control: the manifest names a different dep than the one the baseline imports, so even a
    # correct fix leaves the imported dep absent -> not GREEN. Guards against an "install
    # everything" fix that would trivially satisfy the primary case.
    control = _committed_manifest_target(
        tmp_path,
        "manifest_control",
        """
        import if_absent_dep

        def test_needs_unlisted_dep():
            assert if_absent_dep.__version__ == "1.0.0"
        """,
        requirements="if_other_dep==2.0.0\n",
        dependencies=["if_other_dep==2.0.0"],
    )
    ev_control = verify.run_baseline(
        control, baseline, adapter=_adapter(), provisioner=_manifest_provisioner()
    )
    assert ev_control.status is not BaselineStatus.GREEN


# ===== #58 hardening (PR#60 Codex gap #3) =====
def test_corrupt_worktree_git_metadata_pauses_not_defaults(tmp_path):
    """An isolated worktree that really exists on disk but whose git metadata is corrupt or
    unreadable must PAUSE the run, never silently fall back to a default baseline and dispatch AI.

    The runner reads the MANDATORY baseline from the worktree's committed HEAD object. If
    ``git rev-parse HEAD`` / ``git show HEAD:.issueforge.toml`` fail because the worktree's git
    metadata is broken, there is no readable committed baseline at all. The run must stop before any
    AI touches a file. It must NOT treat a broken git worktree as if it were a unit-seam context and
    substitute the forbidden ``["-m", "pytest"]`` default, which would green a passing target and
    dispatch on a baseline nobody committed.

    Here a real committed git repo (default branch 'trunk') carrying a passing test and a committed
    pytest baseline gets a linked worktree, then the worktree's backing admin dir is deleted so
    ``git rev-parse``/``git show`` in the worktree really exit 128 while the checked-out working
    files remain on disk. A workspace seam fetches and isolates cleanly and hands this corrupt
    worktree to the runner.

    technical (contract): with a real corrupt-metadata worktree (git rev-parse --verify HEAD exits
    128) injected via the workspace seam, establish_green_baseline(checkout, adapter,
    workspace=corrupt, provisioner=clean, dispatch=spy) -> outcome.paused is True, the dispatch spy
    NEVER fired (dispatched == []), and outcome.status is not BaselineStatus.GREEN. Current impl:
    _committed_baseline sees rev-parse returncode != 0, returns (False, None), the caller
    substitutes _SEAM_DEFAULT_BASELINE ["-m", "pytest"], runs the passing target, classifies GREEN,
    and dispatches -- outcome.status is BaselineStatus.GREEN and paused is False.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    wt, sha = _corrupt_git_worktree(
        tmp_path,
        {
            "tests/test_target.py": "def test_ok():\n    assert True\n",
            ".issueforge.toml": 'baseline = ["-m", "pytest"]\nframework = "pytest"\n',
        },
    )
    # Precondition: the worktree exists but its git metadata is genuinely unreadable (real exit 128).
    probe = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True
    )
    assert probe.returncode != 0, "precondition: worktree git metadata must be unreadable"

    class _CorruptWorktreeWorkspace:
        """Fetches + isolates cleanly, then hands back the corrupt-metadata worktree as isolated."""

        def fetch_default_sha(self, checkout):
            from types import SimpleNamespace

            return SimpleNamespace(sha=sha, ok=True, reason=None)

        def create_isolated_worktree(self, checkout, sha, **k):
            from types import SimpleNamespace

            return SimpleNamespace(path=wt, ok=True, isolated=True, reason=None)

    dispatched = []
    outcome = verify.establish_green_baseline(
        tmp_path / "checkout",
        adapter=_adapter(),
        workspace=_CorruptWorktreeWorkspace(),
        provisioner=_clean_provisioner(),
        dispatch=lambda **k: dispatched.append(k),
    )
    assert outcome.paused is True, f"expected pause, got status {outcome.status!r}"
    assert dispatched == [], "AI dispatched on an unreadable committed baseline"
    assert outcome.status is not BaselineStatus.GREEN


# ===== #58 hardening (PR#60 Codex gap #4) =====
def test_committed_baseline_read_ignores_ambient_git_redirect(tmp_path, monkeypatch):
    """The MANDATORY committed baseline is read from the REAL isolated worktree, even when the
    ambient environment carries a decoy git redirect. A hostile or leftover GIT_DIR /
    GIT_WORK_TREE / GIT_INDEX_FILE pointing at ANOTHER repository must not be able to swap in a
    different committed baseline command: the config read is scoped to the worktree with those
    location-redirecting GIT_* variables scrubbed, exactly as the workspace snapshot/fetch git
    reads already are.

    Set up a real offline origin/checkout whose committed .issueforge.toml selects only the
    passing test, plus a separate DECOY repo whose committed .issueforge.toml selects the FAILING
    test. Point ambient GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE at the decoy, then establish the
    baseline. The run must GREEN off the real worktree's own committed command and dispatch once.
    Today verify._committed_baseline runs `git rev-parse HEAD` / `git show HEAD:.issueforge.toml`
    with no env= (verify.py:227), inheriting the ambient GIT_DIR, so it reads the DECOY's
    `-k test_broken`, runs it in the real worktree, and pauses BEHAVIORAL_RED — no dispatch.

    technical (contract): a bare origin on default branch 'trunk' carrying a passing test_ok, a
    FAILING test_broken, and a committed .issueforge.toml baseline = ["-m","pytest","-k","test_ok"];
    a separate decoy git repo whose committed .issueforge.toml baseline = ["-m","pytest","-k",
    "test_broken"]. With GIT_DIR=<decoy>/.git, GIT_WORK_TREE=<decoy>, GIT_INDEX_FILE=<decoy>/.git/
    index set in the ambient env, establish_green_baseline(checkout, adapter=PytestAdapter(),
    provisioner=clean, dispatch=spy) -> outcome.paused is False, outcome.status is
    BaselineStatus.GREEN, and the dispatch spy fired exactly once (len(dispatched) == 1). Current
    impl: the decoy baseline is read (-k test_broken), the real worktree's failing test runs, and
    outcome.status is BaselineStatus.BEHAVIORAL_RED with paused True and dispatched == [].
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    # Real origin/checkout: the committed baseline selects ONLY the passing test.
    _origin, checkout = _origin_with_checkout(
        tmp_path / "real",
        branch="trunk",
        files={
            "tests/test_target.py": (
                "def test_ok():\n    assert True\n\n\ndef test_broken():\n    assert False\n"
            ),
            ".issueforge.toml": (
                'baseline = ["-m", "pytest", "-k", "test_ok"]\nframework = "pytest"\n'
            ),
        },
    )

    # Decoy repo whose COMMITTED config selects the FAILING test; an ambient GIT_DIR pointing here
    # would substitute this command for the real worktree's mandatory committed baseline.
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(decoy)], check=True)
    (decoy / ".issueforge.toml").write_text(
        'baseline = ["-m", "pytest", "-k", "test_broken"]\nframework = "pytest"\n'
    )
    _git(decoy, "add", "-A")
    _git(decoy, "commit", "-qm", "decoy")

    # Redirect ambient git location AFTER all repos are built (so their construction is unaffected).
    decoy_git = decoy / ".git"
    monkeypatch.setenv("GIT_DIR", str(decoy_git))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy_git / "index"))

    dispatched = []
    outcome = verify.establish_green_baseline(
        checkout,
        adapter=_adapter(),
        provisioner=_clean_provisioner(),
        dispatch=lambda **k: dispatched.append(k),
    )
    assert outcome.paused is False
    assert outcome.status is BaselineStatus.GREEN
    assert len(dispatched) == 1


# ===== #58 hardening (PR#60 Codex gap #5) =====
def test_malformed_collect_only_output_invalidates_the_collection(tmp_path):
    """A --collect-only that exits 0 but prints a plausible node id followed by trailing garbage
    must make the collection INVALID: its scraped ids cannot be trusted to seed a green baseline.
    Collection validity is not just "exit 0 and no timeout" -- stdout that does not parse as clean
    node ids is broken evidence, and a broken collection can never be laundered into a trusted
    expected-id set. Trusting a source that emits garbage risks silently missing real tests, so the
    whole collection is treated as invalid, not partially scraped.

    technical (contract): a target with one genuinely passing tests/test_target.py::test_a, plus a
    provisioner seam whose REAL wrapper interpreter runs pytest verbatim and, ONLY on the
    --collect-only step (which still exits 0), appends one malformed line
    'tests/test_target.py::test_a MALFORMED TRAILER' (a well-formed node id followed by garbage) to
    stdout after pytest's own collection output. canonical_collect(invocation) over that invocation
    -> the returned collection's ``ok`` is False -- the malformed stdout is untrusted collection
    evidence. ``ok`` is exactly the field verify.run_baseline reads at its "not collection.ok ->
    empty the expected set" trust gate (verify.py:129), so ok=False is what forces the run to refuse
    green off a malformed collection. Current impl: collection.ok is True, because validity is only
    ``returncode == 0 and not timed_out`` and the "::" line is scraped as an id with no grammar
    check (pytest_adapter.py:170); collection.ids is
    ('tests/test_target.py::test_a', 'tests/test_target.py::test_a MALFORMED TRAILER') -- the
    garbage trailer is silently accepted as a node id.
    """
    from types import SimpleNamespace

    from issueforge.adapters.pytest_adapter import PytestAdapter

    adapter = PytestAdapter()
    worktree = _make_target(tmp_path, "malformed_collect", "def test_a():\n    assert True\n")
    provisioner = _collect_only_stdout_polluter("tests/test_target.py::test_a MALFORMED TRAILER")
    handle = adapter.provision_environment(worktree, None, provisioner=provisioner)
    invocation = SimpleNamespace(
        worktree=worktree,
        interpreter=handle.interpreter,
        command=list(BASELINE),
        env=getattr(handle, "env", None),
    )
    collection = adapter.canonical_collect(invocation)
    # The malformed collect-only stdout (exit 0) must invalidate the collection -- exactly the
    # ``ok`` gate verify.run_baseline consults before trusting the scraped ids as the expected set.
    assert collection.ok is False


# ===== #58 hardening (PR#60 Codex gap #6) =====
def test_corrupt_truncated_report_without_terminal_sessionfinish_is_not_green(tmp_path):
    """A report that carries the expected passing records but then gets cut off — a corrupt,
    truncated final line and NO terminal SessionFinish record — must NOT classify GREEN. GREEN
    requires a well-formed, cleanly-terminated report, not just "the expected nodes passed
    somewhere in the bytes we could parse". A run whose report ends abruptly (the process was
    killed mid-flush, the log was cut, a garbage line was appended) is untrustworthy evidence, so
    a malformed line silently dropped and a missing session-end marker must both forbid green.

    Today _parse_report_log swallows any un-parseable line (json.JSONDecodeError -> continue) and
    the classifier never requires a terminal SessionFinish record, so the surviving passing
    TestReport records alone win GREEN — the exact false-green this pins.

    technical (contract): a target with a single passing test_a runs under a REAL wrapper
    interpreter (the injectable provisioner seam) that runs pytest verbatim, then rewrites the real
    --report-log so its terminal ``$report_type == "SessionFinish"`` record is REMOVED and a
    truncated/corrupt JSON line ('{"$report_type": "TestRep') is appended, before exiting with
    pytest's own code. The collect-only run carries no --report-log and is left untouched, so
    canonical_collect still expects exactly {test_a}. run_baseline -> evidence.exit_code == 0 (the
    passing exit was genuinely seen) AND evidence.status is NOT BaselineStatus.GREEN (the report is
    corrupt/truncated with no terminal SessionFinish). Current impl: status GREEN, exit_code 0,
    report_present True, executed 1 — the defect.
    """
    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus

    worktree = _make_target(tmp_path, "corrupt", "def test_a():\n    assert True\n")
    ev = verify.run_baseline(
        worktree,
        BASELINE,
        adapter=_adapter(),
        provisioner=_truncating_no_sessionfinish_provisioner(),
    )
    assert ev.exit_code == 0
    assert ev.status is not BaselineStatus.GREEN
