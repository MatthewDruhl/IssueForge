"""New unit tests for the Codex build-gate hardening fixes on #8 (S4).

These are ADDITIVE proofs for the store/engine/io hardening; the frozen acceptance suites
(test_store.py / test_engine.py / test_github.py) stay untouched. Each test targets one fix:

1. write_artifact rejects a name that escapes the run dir.
2. a custom validate cannot bypass validate_record.
3. create=True on an already-existing run raises.
4. overlapping secrets are redacted longest-first (no leaked suffix).
5. a torn final event line is healed on the next append, then replays cleanly.
7. a raising stage releases the active slot (does not brick the engine).
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from issueforge import engine, store
from issueforge.cli import app


def _register(make_git_repo, alias="DandD"):
    result = CliRunner().invoke(app, ["repo", "add", f"{alias}:{make_git_repo()}"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)


# --- fix 1: write_artifact path escape --------------------------------------


@pytest.mark.parametrize("bad", ["../../queue.json", "/etc/passwd", "sub/dir.log", "..", "a/../b"])
def test_write_artifact_rejects_a_name_that_escapes_the_run_dir(isolated_state_home, bad):
    store.RunStore().apply("r1", lambda r: {"status": "running"}, create=True)
    with pytest.raises(ValueError):
        store.write_artifact("r1", bad, "data")
    # Nothing escaped: the only file under the run dir is still the manifest.
    names = sorted(p.name for p in store.run_dir("r1").iterdir())
    assert names == ["manifest.json"]


def test_write_artifact_accepts_a_bare_filename(isolated_state_home):
    store.RunStore().apply("r1", lambda r: {"status": "running"}, create=True)
    path = store.write_artifact("r1", "out.log", "hello")
    assert path == store.run_dir("r1") / "out.log"
    assert path.read_text(encoding="utf-8") == "hello"


# --- fix 2: custom validate cannot bypass validate_record -------------------


def test_a_custom_validate_runs_additively_and_cannot_replace_validate_record(isolated_state_home):
    seen: list[dict] = []

    def custom(merged):
        seen.append(dict(merged))  # a permissive custom validator that would "approve" anything

    # validate_record still fires: a bad status is rejected even though custom approves it.
    with pytest.raises(ValueError):
        store.RunStore().apply("r1", lambda r: {"status": "bogus"}, validate=custom, create=True)
    assert not store.manifest_path("r1").exists()
    assert seen, "the custom validator must still be invoked"


def test_a_custom_validate_that_raises_still_raises(isolated_state_home):
    def custom(merged):
        raise RuntimeError("custom veto")

    with pytest.raises(RuntimeError, match="custom veto"):
        store.RunStore().apply("r1", lambda r: {"status": "queued"}, validate=custom, create=True)
    assert not store.manifest_path("r1").exists()


def test_validate_record_still_runs_on_a_plain_apply(isolated_state_home, monkeypatch):
    calls: list[int] = []
    original = store.validate_record

    def spy(record):
        calls.append(1)
        return original(record)

    monkeypatch.setattr(store, "validate_record", spy)
    store.RunStore().apply("r1", lambda r: {"status": "queued"}, create=True)
    assert calls, "validate_record must run on apply"


# --- fix 3: create=True on an existing run raises ---------------------------


def test_create_true_on_an_existing_run_raises(isolated_state_home):
    s = store.RunStore()
    s.apply("r1", lambda r: {"n": 0}, create=True)
    with pytest.raises(FileExistsError):
        s.apply("r1", lambda r: {"n": 1}, create=True)
    # The original record is untouched.
    assert store.RunStore().read("r1")["n"] == 0


def test_write_record_unlocked_create_true_on_existing_raises(isolated_state_home):
    s = store.RunStore()
    s.apply("r1", lambda r: {"status": "queued"}, create=True)
    with s.locked():
        with pytest.raises(FileExistsError):
            s.write_record_unlocked("r1", {"status": "running"}, create=True)


# --- fix 4: secret redaction longest-first ----------------------------------


def test_overlapping_secrets_are_redacted_longest_first(isolated_state_home):
    s = store.RunStore(secrets={"TOKEN", "TOKEN123"})
    s.apply("r1", lambda r: {"status": "running"}, create=True)
    path = s.write_artifact("r1", "out.log", "value=TOKEN123 end")
    text = path.read_text(encoding="utf-8")
    assert "TOKEN123" not in text
    assert "TOKEN" not in text  # no leaked "123" suffix from a short-first replace
    assert store.REDACTED in text


# --- fix 5: torn final line self-heals on next append -----------------------


def test_a_torn_final_line_is_healed_on_the_next_append_then_replays_cleanly(isolated_state_home):
    s = store.RunStore()
    s.apply("r1", lambda r: {"status": "queued"}, create=True)
    events = store.events_path("r1")

    # Simulate a crash: a good line, then a torn/unterminated final line (no newline).
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(json.dumps({"t": "a"}) + "\n" + '{"t": "b", "par', encoding="utf-8")

    # The next append must drop the torn tail, not fuse onto it.
    s.append_event("r1", {"t": "c"})

    raw = events.read_text(encoding="utf-8")
    assert '"par' not in raw, "the torn tail must be dropped, not left as a malformed middle line"
    assert store.RunStore().replay_events("r1") == [{"t": "a"}, {"t": "c"}]


def test_append_onto_a_well_formed_stream_keeps_prior_lines(isolated_state_home):
    s = store.RunStore()
    s.apply("r1", lambda r: {"status": "queued"}, create=True)
    s.append_event("r1", {"t": "a"})
    s.append_event("r1", {"t": "b"})
    assert store.RunStore().replay_events("r1") == [{"t": "a"}, {"t": "b"}]


# --- fix 7: a raising stage releases the active slot ------------------------


def test_a_raising_stage_releases_the_active_slot_and_persists_output(
    make_git_repo, isolated_state_home
):
    _register(make_git_repo)

    def boom(record):
        print("partial CANARY output")
        raise RuntimeError("stage blew up")

    with pytest.raises(RuntimeError, match="stage blew up"):
        engine.run(
            "DandD#148",
            issue_open=lambda s, n: True,
            new_run_id=lambda: "run-1",
            stage=boom,
            secrets={"CANARY"},
        )

    # The slot is released: a crashed stage must not brick all future runs.
    queue = json.loads(store.queue_path().read_text(encoding="utf-8"))
    assert queue["active"] is None

    # Captured output was still persisted, redacted.
    blob = "".join(
        p.read_text(encoding="utf-8")
        for p in store.run_dir("run-1").rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )
    assert "CANARY" not in blob
    assert store.REDACTED in blob


def test_the_engine_still_admits_a_new_run_after_a_stage_crash(make_git_repo, isolated_state_home):
    _register(make_git_repo)

    def boom(record):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        engine.run(
            "DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-1", stage=boom
        )

    # A fresh run is admitted (RUNNING), proving the slot was freed.
    record = engine.run(
        "DandD#149",
        issue_open=lambda s, n: True,
        new_run_id=lambda: "run-2",
        stage=engine._default_stage,
    )
    assert record["status"] == engine.COMPLETED
