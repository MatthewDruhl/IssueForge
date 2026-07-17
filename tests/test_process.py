"""Committed PENDING acceptance suite for #6 (S1) — the subprocess seam.

Every test imports the not-yet-built ``issueforge.process`` symbols INSIDE its body so the
ImportError is the expected xfail; importing at module top would ERROR at collection.
"""

import dataclasses
import os
import subprocess
import sys
import time

import pytest


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_run_returns_result_on_nonzero_exit_never_raises(tmp_path, monkeypatch):
    """A failing command returns a result carrying the child's real values, it never raises.

    Contract: run(["false"], cwd=repo, timeout=5) -> returncode == 1, timed_out is False,
    no exception; AND run([py, "-c", print OUT / write ERR / exit 3]) -> returncode == 3,
    "OUT" in stdout, "ERR" in stderr (values originate from the child, not a fixed stub).
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    r1 = run(["false"], cwd=repo, timeout=5)
    assert r1.returncode == 1
    assert r1.timed_out is False

    code = "import sys\nprint('OUT')\nsys.stderr.write('ERR')\nsys.exit(3)\n"
    r2 = run([sys.executable, "-c", code], cwd=repo, timeout=5)
    assert r2.returncode == 3
    assert "OUT" in r2.stdout
    assert "ERR" in r2.stderr


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_stdout_and_stderr_captured_separately_including_on_failure(tmp_path, monkeypatch):
    """stdout and stderr are captured separately and are never dropped, even on a non-zero exit.

    Contract: a child writes SENTINEL_OUT to stdout and SENTINEL_ERR to stderr and exits
    non-zero -> result.stdout contains SENTINEL_OUT and NOT SENTINEL_ERR; result.stderr
    contains SENTINEL_ERR and NOT SENTINEL_OUT.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    code = (
        "import sys\n"
        "sys.stdout.write('SENTINEL_OUT')\n"
        "sys.stderr.write('SENTINEL_ERR')\n"
        "sys.exit(2)\n"
    )
    result = run([sys.executable, "-c", code], cwd=repo, timeout=5)

    assert "SENTINEL_OUT" in result.stdout
    assert "SENTINEL_ERR" not in result.stdout
    assert "SENTINEL_ERR" in result.stderr
    assert "SENTINEL_OUT" not in result.stderr


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_timeout_sets_distinct_flag_enforced_generally(tmp_path, monkeypatch):
    """A timeout is its own state, enforced for any long-running command, not special-cased.

    Contract: a blocking helper, run(helper, timeout=0.3) -> timed_out is True and elapsed
    < 5s (not the helper's full sleep); a second differently-shaped blocking argv -> also
    timed_out is True; run(["true"], timeout=5) -> timed_out is False.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    start = time.monotonic()
    r1 = run([sys.executable, "-c", "import time\ntime.sleep(5)\n"], cwd=repo, timeout=0.3)
    elapsed = time.monotonic() - start
    assert r1.timed_out is True
    assert elapsed < 5

    r2 = run(["sleep", "5"], cwd=repo, timeout=0.3)
    assert r2.timed_out is True

    r3 = run(["true"], cwd=repo, timeout=5)
    assert r3.timed_out is False


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_timeout_kills_process_group_descendant_does_not_survive(tmp_path, monkeypatch):
    """On timeout the whole process group dies, a spawned descendant does not survive.

    Contract: invoked argv spawns a grandchild that writes ``ready`` immediately then
    ``sentinel`` after 3s, and the direct child blocks; run(argv, timeout=0.5) then wait 4s
    -> ready exists (descendant started) AND sentinel does NOT exist (killed with the group,
    not just the parent); control run(argv, timeout=10) -> sentinel DOES exist.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    grand = tmp_path / "grandchild.py"
    grand.write_text(
        "import sys, time\n"
        "ready, sentinel = sys.argv[1], sys.argv[2]\n"
        "open(ready, 'w').write('ready')\n"
        "time.sleep(3)\n"
        "open(sentinel, 'w').write('sentinel')\n"
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys, time\n"
        "grand, ready, sentinel = sys.argv[1], sys.argv[2], sys.argv[3]\n"
        "subprocess.Popen([sys.executable, grand, ready, sentinel])\n"
        "time.sleep(30)\n"
    )

    ready = tmp_path / "ready.txt"
    sentinel = tmp_path / "sentinel.txt"
    argv = [sys.executable, str(child), str(grand), str(ready), str(sentinel)]
    run(argv, cwd=repo, timeout=0.5)
    time.sleep(4)
    assert ready.exists()
    assert not sentinel.exists()

    ready2 = tmp_path / "ready2.txt"
    sentinel2 = tmp_path / "sentinel2.txt"
    argv2 = [sys.executable, str(child), str(grand), str(ready2), str(sentinel2)]
    run(argv2, cwd=repo, timeout=10)
    assert sentinel2.exists()


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_command_result_is_frozen_dataclass_with_exactly_six_ordered_fields():
    """The result is a frozen dataclass with exactly the six required fields.

    Contract: dataclasses.is_dataclass(CommandResult) is True and the field names in order
    are ["argv", "returncode", "stdout", "stderr", "duration_ms", "timed_out"]; assigning
    result.returncode = 2 raises FrozenInstanceError.
    """
    from issueforge.process import CommandResult

    assert dataclasses.is_dataclass(CommandResult)
    names = [f.name for f in dataclasses.fields(CommandResult)]
    assert names == ["argv", "returncode", "stdout", "stderr", "duration_ms", "timed_out"]

    result = CommandResult(
        argv=["true"],
        returncode=0,
        stdout="",
        stderr="",
        duration_ms=0.0,
        timed_out=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.returncode = 2


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_command_result_records_real_values_not_placeholders(tmp_path, monkeypatch):
    """The result records the run's real argv, output, and a real duration.

    Contract: known argv printing HELLO, run(argv, timeout=5) -> result.argv == argv,
    "HELLO" in result.stdout, and result.duration_ms is a number >= 0 consistent with a
    real elapsed time.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    argv = [sys.executable, "-c", "print('HELLO')"]
    result = run(argv, cwd=repo, timeout=5)

    assert result.argv == argv
    assert "HELLO" in result.stdout
    assert isinstance(result.duration_ms, (int, float))
    assert result.duration_ms >= 0


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_run_uses_in_process_subprocess_shell_false_new_session(tmp_path, monkeypatch):
    """The seam runs in-process with no shell and its own session, never an external tool.

    Contract: spy on subprocess.run, run(["true"], timeout=5) -> subprocess.run called once
    with shell falsy, start_new_session=True, and a timeout= value; argv[0] is never
    timeout/gtimeout and no external timeout executable is invoked.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    calls = []
    real = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)

    run(["true"], cwd=repo, timeout=5)

    def _argv(call):
        args, kwargs = call
        return list(args[0]) if args else kwargs.get("args")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert _argv(calls[0]) == ["true"]
    assert not kwargs.get("shell", False)
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("timeout") is not None
    for call in calls:
        argv = _argv(call)
        if argv:
            assert argv[0] not in ("timeout", "gtimeout")


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_run_honors_cwd_and_injected_env(tmp_path, monkeypatch):
    """run honors the cwd and env it is handed.

    Contract: a child prints os.getcwd() and os.environ["IF_MARKER"], run(argv, cwd=repo,
    timeout=5, env={"IF_MARKER": "xyz123", ...}) -> stdout contains str(repo) and xyz123.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    code = "import os\nprint(os.getcwd())\nprint(os.environ['IF_MARKER'])\n"
    env = {**os.environ, "IF_MARKER": "xyz123"}
    result = run([sys.executable, "-c", code], cwd=repo, timeout=5, env=env)

    assert str(repo) in result.stdout
    assert "xyz123" in result.stdout


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_signal_termination_preserved_and_not_marked_timed_out(tmp_path, monkeypatch):
    """A signal-killed child is recorded as such, not flattened to a plain failure or timeout.

    Contract: a child raises SIGKILL on itself, run(argv, timeout=5) -> result.returncode is
    the signal-derived value (negative on POSIX) and result.timed_out is False.
    """
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    code = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
    result = run([sys.executable, "-c", code], cwd=repo, timeout=5)

    assert result.returncode < 0
    assert result.timed_out is False


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_invocation_emits_observability_fields(tmp_path, monkeypatch):
    """Every invocation emits the five boundary fields for success, failure, and timeout alike.

    Contract: capturing the emitted invocation record, a success / a non-zero exit / a
    timeout run each emit an event carrying {argv, cwd, duration_ms, returncode, timed_out}
    (all five present); the raw stdout/stderr body is NOT in the emitted record.
    """
    from issueforge import process
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    records = []
    monkeypatch.setattr(process, "emit_invocation", lambda record: records.append(record))

    def _fields(rec):
        if isinstance(rec, dict):
            return dict(rec)
        if dataclasses.is_dataclass(rec) and not isinstance(rec, type):
            return dataclasses.asdict(rec)
        keys = ("argv", "cwd", "duration_ms", "returncode", "timed_out")
        return {k: getattr(rec, k) for k in keys if hasattr(rec, k)}

    run([sys.executable, "-c", "print('OBSERVED_BODY')"], cwd=repo, timeout=5)
    run(["false"], cwd=repo, timeout=5)
    run([sys.executable, "-c", "import time\ntime.sleep(5)\n"], cwd=repo, timeout=0.3)

    assert len(records) == 3
    required = {"argv", "cwd", "duration_ms", "returncode", "timed_out"}
    for rec in records:
        data = _fields(rec)
        assert required <= set(data)
        assert "stdout" not in data
        assert "stderr" not in data
        assert "OBSERVED_BODY" not in str(data)


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_fresh_artifact_dir_per_invocation_through_seam_static_path_never_used(
    tmp_path, monkeypatch
):
    """Each invocation gets a fresh artifact directory created through the guarded seam.

    Contract: spy the S25 write seam, two run(...) invocations -> two DISTINCT directories,
    each created through the seam, each outside repo and under state_root(); a pre-populated
    static path is never read or written.
    """
    from issueforge.io import WriteSeam
    from issueforge.paths import state_root
    from issueforge.process import run

    monkeypatch.setenv("ISSUEFORGE_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()

    static = repo / "artifacts_static"
    static.mkdir()
    (static / "stale.txt").write_text("stale")

    from pathlib import Path

    written = []
    real_write = WriteSeam.write_text

    def spy(self, path, data, encoding="utf-8"):
        target = real_write(self, path, data, encoding)
        written.append(Path(target))
        return target

    monkeypatch.setattr(WriteSeam, "write_text", spy)

    run(["true"], cwd=repo, timeout=5)
    run(["true"], cwd=repo, timeout=5)

    dirs = {p.parent for p in written}
    assert len(dirs) == 2
    for directory in dirs:
        assert state_root() == directory or state_root() in directory.parents
        assert repo != directory and repo not in directory.parents

    assert list(static.iterdir()) == [static / "stale.txt"]
    assert not any(static == p or static in p.parents for p in written)
