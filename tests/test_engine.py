"""Committed PENDING acceptance suite for #8 (S4) — the engine: run end-to-end through a stub stage.

``issueforge.engine`` does not exist yet, so each test is ``xfail(strict=True)`` (ImportError while
pending). Dual-layer docstrings.

The engine interface this suite authors (ATDD):
- ``engine.QUEUED`` / ``engine.RUNNING`` / ``engine.COMPLETED`` == "queued"/"running"/"completed".
- ``engine.run(spec, *, issue_open=None, stage=None, new_run_id=None, secrets=None) -> dict``
  resolves the alias via the S3 registry, validates the issue is OPEN via the injectable
  ``issue_open`` seam, admits the run to the single active slot INSIDE the store lock (else
  enqueues to ``store.queue_path()`` ``{"active": <run-id|None>, "queue": [...]}``), runs the stub
  ``stage`` OUTSIDE the lock capturing its stdout/stderr through the redacting writer, records the
  transitions as events, and lands ``completed``. ``new_run_id`` is the id seam; ``secrets`` is the
  redaction set applied to captured output.

Queue control (pause/park/cancel/resume/continue) and auto-advancing the FIFO are S5, out of scope
here: a second run only has to LAND queued behind the active one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

from issueforge.cli import app


def _register(make_git_repo, alias="DandD"):
    result = CliRunner().invoke(app, ["repo", "add", f"{alias}:{make_git_repo()}"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)


def _queue_state():
    from issueforge import store

    return json.loads(store.queue_path().read_text(encoding="utf-8"))


def _transitions(run_id):
    from issueforge import store

    return [e["transition"] for e in store.RunStore().replay_events(run_id)]


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_run_completes_through_the_stub_stage_with_an_observable_stage_event(
    make_git_repo, isolated_state_home
):
    """A run for an open issue runs through the stub stage — proven by a persisted stage event — and lands completed with state on disk.

    technical (contract): engine.run("DandD#148", issue_open=lambda s, n: True,
    new_run_id=lambda: "run-1", stage=<appends event {"transition": "stage"}>) ->
    record["status"] == engine.COMPLETED; the "stage" transition is recorded before "completed";
    store.RunStore().read("run-1")["status"] == "completed".
    """
    from issueforge import engine, store

    _register(make_git_repo)

    def stage(record):
        store.RunStore().append_event(record["run_id"], {"transition": "stage"})

    record = engine.run(
        "DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1", stage=stage
    )

    assert record["status"] == engine.COMPLETED
    assert store.RunStore().read("run-1")["status"] == "completed"
    seq = _transitions("run-1")
    assert "stage" in seq and seq.index("stage") < seq.index("completed")


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_run_records_the_exact_queued_running_completed_transition_sequence(
    make_git_repo, isolated_state_home
):
    """The run's append-only event log records exactly the queued -> running -> completed transitions, each under one canonical schema.

    technical (contract): after engine.run("DandD#148", issue_open=lambda s, n: True,
    new_run_id=lambda: "run-1") with the default stub stage, every event has a "transition" key,
    and the transition sequence (ignoring any interior stage event) is exactly
    ["queued", "running", "completed"] with no duplicates.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1")

    events = store.RunStore().replay_events("run-1")
    assert all("transition" in e for e in events)
    seq = [e["transition"] for e in events]
    core = [t for t in seq if t in ("queued", "running", "completed")]
    assert core == ["queued", "running", "completed"]


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_run_command_completes_end_to_end_through_the_cli(
    make_git_repo, isolated_state_home, monkeypatch
):
    """The user-visible `issueforge run alias#n` command completes a run end to end.

    technical (contract): with DandD registered and github.issue_is_open monkeypatched to True,
    CliRunner invoking ["run", "DandD#148"] exits 0; exactly one run under state_root()/runs has
    manifest status "completed"; and that run's event stream proves the default stub stage
    actually ran (a "stage" transition recorded before "completed"), so a CLI that merely writes
    "completed" cannot pass.
    """
    from issueforge import github, store

    _register(make_git_repo)
    monkeypatch.setattr(github, "issue_is_open", lambda *a, **k: True)

    result = CliRunner().invoke(app, ["run", "DandD#148"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)

    runs = list((isolated_state_home / "runs").iterdir())
    completed = [d for d in runs if store.RunStore().read(d.name)["status"] == "completed"]
    assert len(completed) == 1

    seq = _transitions(completed[0].name)
    assert "stage" in seq, "the default stub stage must record a stage event"
    assert seq.index("stage") < seq.index("completed")


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_run_refuses_a_non_open_issue_without_minting_a_run(make_git_repo, isolated_state_home):
    """A run against a closed or missing issue is refused before any run is persisted.

    technical (contract): engine.run("DandD#148", issue_open=lambda s, n: False,
    new_run_id=lambda: "run-1") raises; store.run_dir("run-1") does not exist.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    with pytest.raises(Exception):
        engine.run("DandD#148", issue_open=lambda s, n: False, new_run_id=lambda: "run-1")
    assert not store.run_dir("run-1").exists()


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_run_refuses_an_unregistered_alias(isolated_state_home):
    """A run against an unregistered alias is refused (the engine resolves through the S3 registry).

    technical (contract): with an empty registry, engine.run("Nope#1", issue_open=lambda s, n:
    True, new_run_id=lambda: "run-1") raises.
    """
    from issueforge import engine

    with pytest.raises(Exception):
        engine.run("Nope#1", issue_open=lambda s, n: True, new_run_id=lambda: "run-1")


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_concurrent_admission_admits_exactly_one_run_the_other_enqueues(
    make_git_repo, isolated_state_home
):
    """Two runs racing an empty active slot: exactly one is admitted and enters its stage; the other lands queued. Admission is decided inside the store lock (a lock-free check-then-start would admit both).

    technical (contract): two threads call engine.run simultaneously (a barrier) with stub
    stages that block if entered -> exactly one run is "running" and entered its stage; the other
    is "queued" and never entered a stage; queue_path()["active"] is the running run and its
    "queue" holds only the other; no worker raised.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    barrier = threading.Barrier(2)
    release = threading.Event()
    entered: list[str] = []
    entered_lock = threading.Lock()
    errors: list[BaseException] = []

    def stage_for(rid):
        def stage(record):
            with entered_lock:
                entered.append(rid)
            release.wait(timeout=10)

        return stage

    def worker(rid, spec):
        try:
            barrier.wait(timeout=10)
            engine.run(
                spec, issue_open=lambda s, n: True, new_run_id=lambda: rid, stage=stage_for(rid)
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    ta = threading.Thread(target=worker, args=("run-A", "DandD#148"))
    tb = threading.Thread(target=worker, args=("run-B", "DandD#149"))
    ta.start()
    tb.start()
    try:
        deadline = time.time() + 10
        while len(entered) < 1 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)  # give a buggy second admission time to (wrongly) enter
        assert len(entered) == 1, f"exactly one run must be admitted, got {entered}"

        active = entered[0]
        other = "run-B" if active == "run-A" else "run-A"
        state = _queue_state()
        assert state["active"] == active
        assert state["queue"] == [other]
        assert store.RunStore().read(active)["status"] == "running"
        assert store.RunStore().read(other)["status"] == "queued"
    finally:
        release.set()
        ta.join(timeout=10)
        tb.join(timeout=10)
    assert errors == []


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_further_runs_enqueue_in_exact_fifo_order_behind_the_active_run(
    make_git_repo, isolated_state_home
):
    """While one run holds the active slot, further runs land in the FIFO queue in exact submission order.

    technical (contract): run A held active by a blocking stage; then engine.run for run-B then
    run-C (each returns status engine.QUEUED) -> queue_path() == {"active": "run-A",
    "queue": ["run-B", "run-C"]}.
    """
    from issueforge import engine

    _register(make_git_repo)
    release = threading.Event()
    a_running = threading.Event()

    def blocking_stage(record):
        a_running.set()
        release.wait(timeout=10)

    ta = threading.Thread(
        target=lambda: engine.run(
            "DandD#148",
            issue_open=lambda s, n: True,
            new_run_id=lambda: "run-A",
            stage=blocking_stage,
        )
    )
    ta.start()
    try:
        assert a_running.wait(timeout=10)
        b = engine.run("DandD#149", issue_open=lambda s, n: True, new_run_id=lambda: "run-B")
        c = engine.run("DandD#150", issue_open=lambda s, n: True, new_run_id=lambda: "run-C")
        assert b["status"] == engine.QUEUED
        assert c["status"] == engine.QUEUED
        assert _queue_state() == {"active": "run-A", "queue": ["run-B", "run-C"]}
    finally:
        release.set()
        ta.join(timeout=10)


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_captured_stub_stdout_and_stderr_are_redacted_at_the_producer(
    make_git_repo, isolated_state_home
):
    """The stub stage's captured stdout and stderr are persisted through the redacting writer, so a secret printed by the stage never lands in an artifact.

    technical (contract): engine.run("DandD#148", issue_open=lambda s, n: True,
    new_run_id=lambda: "run-1", secrets={"CANARY-OUT", "CANARY-ERR"}, stage=<prints "CANARY-OUT"
    to stdout and "CANARY-ERR" to stderr>) -> the persisted artifacts under run_dir("run-1")
    (excluding the manifest) contain "[REDACTED]" and never the canaries.
    """
    from issueforge import engine, store

    _register(make_git_repo)

    def stage(record):
        print("line CANARY-OUT line")
        print("line CANARY-ERR line", file=sys.stderr)

    engine.run(
        "DandD#148",
        issue_open=lambda s, n: True,
        new_run_id=lambda: "run-1",
        stage=stage,
        secrets={"CANARY-OUT", "CANARY-ERR"},
    )

    blob = "".join(
        p.read_text(encoding="utf-8")
        for p in store.run_dir("run-1").rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )
    assert "CANARY-OUT" not in blob
    assert "CANARY-ERR" not in blob
    assert "[REDACTED]" in blob


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_run_state_survives_a_fresh_process_reload(make_git_repo, isolated_state_home):
    """A completed run's state is on disk and reloads intact in a separate process.

    technical (contract): after engine.run("DandD#148", issue_open=lambda s, n: True,
    new_run_id=lambda: "run-1"), a separate Python process with the same ISSUEFORGE_STATE_HOME
    reads store.RunStore().read("run-1")["status"] == "completed".
    """
    from issueforge import engine

    _register(make_git_repo)
    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1")

    code = (
        "from issueforge import store\n"
        "print('status=' + store.RunStore().read('run-1')['status'])\n"
    )
    env = {**os.environ, "ISSUEFORGE_STATE_HOME": str(isolated_state_home)}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "status=completed" in result.stdout.splitlines()
