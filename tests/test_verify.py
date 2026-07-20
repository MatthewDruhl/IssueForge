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
