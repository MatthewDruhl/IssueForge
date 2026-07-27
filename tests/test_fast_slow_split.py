"""Acceptance suite for issue #147 — fast/slow test split with a drift guard.

The contract (from #147):

* Slow tests are marked ``@pytest.mark.slow``; the marker is REGISTERED in
  pyproject's ``[tool.pytest.ini_options]``.
* A new Makefile target ``test-quick`` runs the fast ring as
  ``uv run pytest -m "not slow" -n auto --dist worksteal``. Existing ``make
  test`` is UNCHANGED (still the full gate over every test, slow included).
* A drift guard: an UNMARKED test whose real duration exceeds ~5s FAILS with a
  message telling the author to mark it ``slow``. The guard is active ONLY under
  the fast ring, so ``make test`` is unaffected.
* CLAUDE.md's Development section documents ``test-quick`` as the inner-loop
  command.

How these tests avoid false greens (addressing the Codex review):

* The Makefile targets are pinned by the COMMAND ``make`` ACTUALLY EXECUTES via
  ``make -n`` (dry run), then argv-parsed with ``shlex`` — not by recipe-text
  substrings. Comments, variables, includes, and shell-split hazards are handled.
* The drift guard is exercised through the REAL fast-ring argv derived from
  ``make -n test-quick`` (so ``-n auto --dist worksteal`` and any recipe-loaded
  plugin are in force), run against a probe tree that also carries a copy of the
  repo's conftest chain. Whether the guard is a conftest hook or a recipe plugin
  is therefore left free.
* Probe trees live in ``tmp_path`` and NEVER mutate the live checkout, so they
  cannot race the repo's own xdist full-gate run. Whole-tree collections read the
  static checkout read-only.
* Guard outcomes are pinned by exit code AND exact outcome counts, with a fast
  unmarked control that must PASS and a setup-phase probe so total duration (not
  just the call phase) is guarded.
* Marker registration is proven behaviorally: a ``@pytest.mark.slow`` probe must
  collect clean under ``--strict-markers`` against the repo's config.

PENDING convention: a genuinely-red test carries EXACTLY
``@pytest.mark.xfail(strict=True, reason="PENDING (#147)")``. Genuinely slow
tests in this module additionally carry ``@pytest.mark.slow`` so this suite does
not itself drift into the fast ring once the marker exists (self-pinning). Green
guards that already hold at HEAD carry no xfail.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

# ~5s guard threshold; probes sleep past it (sleeps only overshoot).
THRESHOLD = 5.0
PROBE_SLEEP = 6.0


@lru_cache(maxsize=1)
def _repo_root() -> Path:
    """The checkout root (holds both Makefile and pyproject.toml), resolved LAZILY
    at first call — never at import — so importing this file from a draft location
    cannot raise. At the committed location (<repo>/tests/) the upward walk finds
    the root; a draft-in-place run fails here AT TEST TIME with a clear message."""
    start = Path(__file__).resolve().parent
    for cand in [start, *start.parents]:
        if (cand / "Makefile").is_file() and (cand / "pyproject.toml").is_file():
            return cand
    raise RuntimeError(
        f"no checkout root (Makefile+pyproject.toml) above {start}; "
        "run this suite from inside the issueforge checkout (tests/)"
    )


def _repo_pytest_ini_section() -> str:
    """The repo pyproject's ``[tool.pytest.ini_options]`` section text (verbatim,
    up to the next table header). Copied into probe trees so a guard loaded via
    the repo's own ``addopts`` (e.g. ``-p some_plugin``) survives into the probe."""
    text = (_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^\[tool\.pytest\.ini_options\].*?(?=^\[|\Z)", text)
    return m.group(0) if m else "[tool.pytest.ini_options]\n"


# --------------------------------------------------------------------------- #
# Makefile: pin the command MAKE EXECUTES, via `make -n` (dry run)            #
# --------------------------------------------------------------------------- #
def _make_dry_run(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-n", target],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
    )


def _pytest_argv(dry_run_stdout: str) -> list[str] | None:
    """Return the pytest flags (tokens AFTER the ``pytest`` token) from a
    ``make -n`` dry run, argv-parsed with shlex. None if no pytest command runs.
    Comments are not emitted by ``make -n``; variables/includes are expanded.
    Backslash-newline continuations (a multi-line recipe) are joined first, so a
    continued pytest command is parsed as the single command make runs."""
    merged = dry_run_stdout.replace("\\\n", " ")
    for line in merged.splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if "pytest" in tokens:
            return tokens[tokens.index("pytest") + 1 :]
    return None


def _flag_value(flags: list[str], *aliases: str) -> str | None:
    """Value of the first present option among ``aliases``, handling ``-x val``,
    ``--x=val``, ``--x val``, and short ``-xval`` forms. None if absent."""
    for i, tok in enumerate(flags):
        for a in aliases:
            if tok == a:
                return flags[i + 1] if i + 1 < len(flags) else ""
            if tok.startswith(a + "="):
                return tok[len(a) + 1 :]
            if not a.startswith("--") and tok.startswith(a) and len(tok) > len(a):
                return tok[len(a) :]
    return None


def _marker_expr(flags: list[str]) -> str | None:
    return _flag_value(flags, "-m", "--markexpr")


# --------------------------------------------------------------------------- #
# Probe trees (in tmp_path — never touch the live checkout)                   #
# --------------------------------------------------------------------------- #
def _build_probe_tree(tmp_path: Path, probe_body: str) -> Path:
    """Build a self-contained probe tree under ``tmp_path``:
    ``tree/pyproject.toml`` carries the repo's real ``[tool.pytest.ini_options]``
    (so a guard loaded through repo ``addopts`` — e.g. ``-p plugin`` — survives,
    and marker registration comes along), plus copies of the repo's root and
    tests conftests (so a conftest-based guard is active), and
    ``tree/tests/test_probe.py`` = ``probe_body``. rootdir anchors at ``tree``.
    """
    tree = tmp_path / "probe"
    (tree / "tests").mkdir(parents=True)
    # Copy the repo's pytest config; drop testpaths (we pass an explicit positional,
    # and testpaths would otherwise point at a path outside the probe). The remaining
    # path-relative entries (addopts --ignore of the parked observability files) are
    # interpreted against rootdir=tree, where they simply match nothing — harmless.
    section = re.sub(r"(?m)^\s*testpaths\s*=.*\n?", "", _repo_pytest_ini_section())
    (tree / "pyproject.toml").write_text(section, encoding="utf-8")
    for src, dest in (
        (_repo_root() / "conftest.py", tree / "conftest.py"),
        (_repo_root() / "tests" / "conftest.py", tree / "tests" / "conftest.py"),
    ):
        if src.is_file():
            shutil.copy(src, dest)
    (tree / "tests" / "test_probe.py").write_text(probe_body, encoding="utf-8")
    return tree


def _run_probe_via(
    target: str, probe_body: str, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run the probe tree through the REAL argv of Makefile ``target`` (derived
    from ``make -n``), preserving every flag (``-m``, ``-n auto``,
    ``--dist worksteal``, any recipe-loaded plugin). ``uv run`` is replaced by the
    current interpreter (the same project venv), which keeps issueforge importable
    for a plugin-based guard. Raises AssertionError if the target has no pytest
    command (so a missing ``test-quick`` fails red, not silently)."""
    dry = _make_dry_run(target)
    assert dry.returncode == 0, f"`make -n {target}` failed:\n{dry.stdout}\n{dry.stderr}"
    flags = _pytest_argv(dry.stdout)
    assert flags is not None, f"`make -n {target}` runs no pytest command:\n{dry.stdout}"
    tree = _build_probe_tree(tmp_path, probe_body)
    # -rA lists every test's outcome by nodeid, so we can attribute pass/fail/exclusion
    # exactly (xdist omits the "deselected" count from the summary line).
    argv = [
        sys.executable,
        "-m",
        "pytest",
        *flags,
        "-p",
        "no:cacheprovider",
        "-rA",
        "--tb=short",
        str(tree),
    ]
    return subprocess.run(argv, cwd=str(tree), capture_output=True, text=True)


def _reported_outcomes(text: str) -> dict[str, str]:
    """Map nodeid -> verdict from ``-rA`` summary lines (PASSED/FAILED/ERROR/...)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(\S+)", line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def _verdict(outcomes: dict[str, str], name: str) -> str | None:
    """The verdict for the single test whose nodeid contains ``name`` (None if the
    test never executed — e.g. deselected)."""
    hits = {nid: v for nid, v in outcomes.items() if name in nid}
    return next(iter(hits.values())) if len(hits) == 1 else None


def _summary_counts(text: str) -> dict[str, int]:
    """Parse pytest's terminal summary line into {outcome: count}."""
    summary = None
    for line in text.splitlines():
        if "=" in line and re.search(r"\bin\s+[\d.]+s", line):
            summary = line
    counts: dict[str, int] = {}
    if summary:
        for n, word in re.findall(
            r"(\d+)\s+(passed|failed|deselected|errors?|xfailed|xpassed|skipped)", summary
        ):
            key = "error" if word.startswith("error") else word
            counts[key] = counts.get(key, 0) + int(n)
    return counts


# --------------------------------------------------------------------------- #
# Whole-tree collection on the STATIC checkout (read-only)                    #
# --------------------------------------------------------------------------- #
def _collect(marker_args: list[str]) -> tuple[int, set[str]]:
    """Return (returncode, set of collected nodeids) for a ``--collect-only`` run
    on the real tree under ``marker_args``. The returncode is surfaced so a
    collection error (partial nodeids) cannot masquerade as a clean partition."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *marker_args,
        ],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
    )
    ids = {ln.strip() for ln in proc.stdout.splitlines() if "::" in ln}
    return proc.returncode, ids


# =========================================================================== #
# Green guards — already true at HEAD, must stay true                         #
# =========================================================================== #
def test_make_test_is_the_unchanged_full_gate():
    """`make test` still runs the entire suite: the command make executes carries
    NO test-narrowing selector of any kind — no marker filter, ``-k``, ``--ignore``,
    ``--ignore-glob``, ``--deselect``, collect-only, or positional subset — so no
    test is dropped from the final gate."""
    dry = _make_dry_run("test")
    assert dry.returncode == 0, dry.stderr
    flags = _pytest_argv(dry.stdout)
    assert flags is not None, f"`make -n test` runs no pytest command:\n{dry.stdout}"
    for label, aliases in (
        ("marker filter", ("-m", "--markexpr")),
        ("-k", ("-k",)),
        ("--ignore", ("--ignore",)),
        ("--ignore-glob", ("--ignore-glob",)),
        ("--deselect", ("--deselect",)),
    ):
        assert _flag_value(flags, *aliases) is None, f"full gate narrows via {label}: {flags}"
    assert not ({"--co", "--collect-only"} & set(flags)), f"full gate only collects: {flags}"
    for tok in flags:
        assert not (
            tok.endswith(".py") or "::" in tok or tok.startswith("tests/") or "slow" in tok
        ), f"full gate carries a test subset / marker leak: {tok!r} in {flags}"
    assert _flag_value(flags, "-n", "--numprocesses") == "auto", flags
    assert _flag_value(flags, "--dist") == "worksteal", flags


@pytest.mark.slow
def test_full_gate_does_not_apply_the_drift_guard(tmp_path):
    """Second layer for `make test` unchanged: run an UNMARKED over-threshold test
    through the real ``make test`` argv. The guard stays silent under the full
    gate (it is scoped to the fast ring), so the test passes and the full run is
    green. Behavioral complement to the argv check above."""
    body = f"import time\n\n\ndef test_unmarked_slow():\n    time.sleep({PROBE_SLEEP})\n"
    proc = _run_probe_via("test", body, tmp_path)
    counts = _summary_counts(proc.stdout)
    assert proc.returncode == 0, f"full gate went red:\n{proc.stdout}\n{proc.stderr}"
    assert counts.get("passed") == 1 and counts.get("failed", 0) == 0, counts


# =========================================================================== #
# Pending contract — red at HEAD, green once #147 lands                       #
# =========================================================================== #
def test_slow_marker_is_registered(tmp_path):
    """`slow` is a registered marker: a ``@pytest.mark.slow`` probe collects clean
    under ``--strict-markers`` against the repo's config. An unregistered marker
    makes ``--strict-markers`` error (nonzero exit); this proves registration
    behaviorally, not by string-matching pyproject."""
    probe = tmp_path / "test_marker_probe.py"
    probe.write_text("import pytest\n\n\n@pytest.mark.slow\ndef test_probe():\n    pass\n")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--strict-markers",
            "--rootdir",
            str(_repo_root()),
            "-c",
            str(_repo_root() / "pyproject.toml"),
            "-p",
            "no:cacheprovider",
            str(probe),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"slow not registered under --strict-markers:\n{proc.stdout}\n{proc.stderr}"
    )


def test_make_test_quick_runs_the_fast_ring():
    """`make test-quick` exists and the command it executes selects exactly
    ``not slow`` in parallel with worksteal — checked on the real argv, so
    ``-m not slow`` (shell-split wrong) or an expected string hidden in a comment
    cannot pass."""
    dry = _make_dry_run("test-quick")
    assert dry.returncode == 0, f"no `test-quick` target:\n{dry.stderr}"
    flags = _pytest_argv(dry.stdout)
    assert flags is not None, f"`make -n test-quick` runs no pytest command:\n{dry.stdout}"
    expr = _marker_expr(flags)
    assert expr is not None, f"fast ring applies no marker filter: {flags}"
    assert shlex.split(expr) == ["not", "slow"], f"marker expr is not `not slow`: {expr!r}"
    assert _flag_value(flags, "-n", "--numprocesses") == "auto", flags
    assert _flag_value(flags, "--dist") == "worksteal", flags


def test_claude_md_documents_test_quick_as_inner_loop():
    """CLAUDE.md's Development section names ``test-quick`` as the inner-loop
    command — anchored to that section, not any occurrence in the file."""
    text = (_repo_root() / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^##\s+Development\b.*?(?=^##\s|\Z)", text)
    assert m, "CLAUDE.md has no Development section"
    section = m.group(0)
    assert "test-quick" in section, "Development section does not mention test-quick"
    assert re.search(r"inner[- ]loop", section, re.I), (
        "test-quick not documented as the inner-loop command"
    )


@pytest.mark.slow
def test_fast_ring_excludes_slow_and_every_recorded_slow_test():
    """The fast ring partitions the suite (every not-slow test, no slow one; the
    full run still holds the slow tests), AND every test whose RECORDED duration
    exceeded the threshold is excluded from the fast ring. The recorded-duration
    check stops the implementation from marking one cheap token test and leaving
    the genuinely slow ones in the fast ring. General over whatever files are
    marked — not five specific filenames."""
    rc_all, all_ids = _collect([])
    rc_fast, fast_ids = _collect(["-m", "not slow"])
    rc_slow, slow_ids = _collect(["-m", "slow"])

    assert rc_all == 0, "full collection did not complete cleanly"
    assert rc_fast == 0, "fast-ring collection did not complete cleanly"
    # rc 5 == "no tests collected"; legitimate only while nothing is marked, which
    # the non-empty assertion below then fails. Any other nonzero is an error.
    assert rc_slow in (0, 5), "slow collection errored"

    assert all_ids, "expected to collect tests on the real tree"
    assert slow_ids, "no test is marked @pytest.mark.slow"
    assert fast_ids.isdisjoint(slow_ids)
    assert fast_ids | slow_ids == all_ids
    assert fast_ids == all_ids - slow_ids

    durations = json.loads((_repo_root() / ".test_durations").read_text(encoding="utf-8"))
    recorded_slow = {nid for nid, sec in durations.items() if sec > THRESHOLD}
    relevant = recorded_slow & all_ids  # ignore stale/removed nodeids
    assert relevant, "no recorded >5s tests are currently collected (unexpected)"
    offenders = relevant & fast_ids
    assert not offenders, (
        f"recorded-slow tests still in the fast ring (mark them slow): {sorted(offenders)[:5]}"
    )


@pytest.mark.slow
def test_drift_guard_fails_unmarked_slow_and_ignores_marked(tmp_path):
    """Through the real fast-ring argv (``make -n test-quick``, xdist and all): an
    UNMARKED over-threshold test is reported FAILED with a message to mark it
    slow; a fast unmarked control PASSES; a ``@pytest.mark.slow`` test is
    deselected. Pinned by exit code 1 and exact counts, so a hook that crashes
    (INTERNALERROR) or nukes every unmarked test cannot pass."""
    body = (
        "import time\n"
        "import pytest\n\n\n"
        f"def test_unmarked_slow():\n    time.sleep({PROBE_SLEEP})\n\n\n"
        "def test_fast_ok():\n    pass\n\n\n"
        "@pytest.mark.slow\n"
        f"def test_marked_slow():\n    time.sleep({PROBE_SLEEP})\n"
    )
    proc = _run_probe_via("test-quick", body, tmp_path)
    out = proc.stdout + proc.stderr
    counts = _summary_counts(proc.stdout)
    outcomes = _reported_outcomes(proc.stdout)

    assert proc.returncode == 1, f"expected exit 1 (tests failed), got {proc.returncode}:\n{out}"
    assert counts.get("failed") == 1, f"expected exactly 1 failed: {counts}\n{out}"
    assert counts.get("passed") == 1, f"expected the fast control to pass: {counts}\n{out}"
    assert counts.get("error", 0) == 0, f"guard raised instead of failing the test: {counts}\n{out}"
    # Attribution (xdist omits the deselected count, so read per-test outcomes):
    assert _verdict(outcomes, "test_unmarked_slow") == "FAILED", outcomes
    assert _verdict(outcomes, "test_fast_ok") == "PASSED", outcomes
    assert _verdict(outcomes, "test_marked_slow") is None, (
        f"marked test was not excluded: {outcomes}"
    )
    assert re.search(r"slow", out, re.I) and re.search(r"mark", out, re.I), out


@pytest.mark.slow
def test_drift_guard_counts_setup_phase_duration(tmp_path):
    """The guard measures TOTAL duration, not just the call phase: an unmarked test
    whose slowness is in FIXTURE SETUP is failed too, while a fast unmarked control
    passes. Pinned by exit 1 and exact counts.

    NOTE (contract): if summing setup+call+teardown proves genuinely
    unimplementable with pytest's per-phase report timing, that is a contract
    decision to raise — not something to drop silently; this test stays red until
    it is resolved."""
    body = (
        "import time\n"
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        f"def slow_setup():\n    time.sleep({PROBE_SLEEP})\n    yield\n\n\n"
        "def test_unmarked_slow_setup(slow_setup):\n    pass\n\n\n"
        "def test_fast_ok():\n    pass\n"
    )
    proc = _run_probe_via("test-quick", body, tmp_path)
    out = proc.stdout + proc.stderr
    counts = _summary_counts(proc.stdout)
    outcomes = _reported_outcomes(proc.stdout)

    assert proc.returncode == 1, f"expected exit 1 (tests failed), got {proc.returncode}:\n{out}"
    assert counts.get("failed") == 1, f"expected exactly 1 failed: {counts}\n{out}"
    assert counts.get("passed") == 1, f"expected the fast control to pass: {counts}\n{out}"
    assert counts.get("error", 0) == 0, f"guard raised instead of failing the test: {counts}\n{out}"
    assert _verdict(outcomes, "test_unmarked_slow_setup") == "FAILED", outcomes
    assert _verdict(outcomes, "test_fast_ok") == "PASSED", outcomes
    assert re.search(r"slow", out, re.I) and re.search(r"mark", out, re.I), out
