"""Acceptance suite for #48 — run-store crash-transactionality + reconcile (SHIPPED).

The store's admission and completion each write TWO files under one lock (the manifest and
``queue.json``), but the pair is not crash-atomic: a crash between the two writes can leave
``queue.active`` pointing at a run whose manifest was never written (an ORPHAN active), or at a
completed run whose slot was never cleared (STUCK occupied). #48 makes that transition recoverable:
a startup reconcile / sweep that, run at the beginning of a mutating admission, restores a
consistent store — clearing a stuck-occupied slot and dropping an orphan ``active`` whose run has no
manifest, and dropping any queued waiter that lacks a valid ``queued`` manifest.

This suite pins the RECOVERY BEHAVIOR, not the mechanism. It never asserts derive-active-from-
manifests vs a transactional commit; it asserts the observable postcondition on ``queue.json`` +
the manifests after a simulated torn write. The torn state is produced by driving the REAL public
store seams (``RunStore.locked`` + ``write_queue_unlocked`` + ``apply``) — the same primitives
admission and completion use — to write one side of the pair without the other, never by mocking
store internals.

Shipped surface: ``RunStore.reconcile()`` — an idempotent, lock-guarded sweep that makes
``queue.json`` consistent with the manifests across the ENTIRE queue (a no-op on an already-
consistent store), plus the admission-time wiring so ``engine.run`` reconciles before it decides the
active slot and drains any stranded waiter iteratively (no per-waiter recursion). "Recoverable/
consistent" means: after reconcile, ``queue.active`` is either ``None`` or names a run whose manifest
exists AND is non-terminal.

The suite is live (no xfail markers): reconcile and the admission wiring are implemented. Imports of
the surface live INSIDE each test. Dual-layer docstrings: a plain-English behavior line, then the
exact golden values under ``technical (contract):``.
"""

from __future__ import annotations

import json
import pytest

_TERMINAL = {"completed", "cancelled", "failed"}


def _seed_queue(active, queue=None):
    """Write queue.json directly through the store's public locked seam (a torn-write stand-in)."""
    from issueforge import store

    s = store.RunStore()
    with s.locked():
        s.write_queue_unlocked({"active": active, "queue": list(queue or [])})


def _seed_manifest(run_id, status):
    """Create a run manifest at a chosen status through the public apply seam."""
    from issueforge import store

    store.RunStore().apply(run_id, lambda r: {"run_id": run_id, "status": status}, create=True)


def _assert_recoverable(read_queue):
    """The queue is consistent: active is None or names an existing, non-terminal run."""
    from issueforge import store

    active = read_queue["active"]
    if active is None:
        return
    assert store.manifest_path(active).exists(), f"active {active!r} has no manifest"
    assert store.RunStore().read(active)["status"] not in _TERMINAL, (
        f"active {active!r} points at a terminal run"
    )


def _register(make_git_repo, alias="DandD"):
    from typer.testing import CliRunner

    from issueforge.cli import app

    result = CliRunner().invoke(app, ["repo", "add", f"{alias}:{make_git_repo()}"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)


# ---------------------------------------------------------------------------
# (a) orphan active — active names a run whose manifest was never written
# ---------------------------------------------------------------------------


def test_reconcile_drops_an_orphan_active_whose_run_has_no_manifest(isolated_state_home):
    """A crash between the fail-safe queue write and the manifest write leaves active pointing at a run with no manifest; reconcile drops that orphan and never fabricates a manifest for it.

    technical (contract): with queue.json seeded {"active":"run-ghost","queue":[]} and NO manifest
    for run-ghost (the torn state after admission's queue-first write crashed before the manifest),
    store.RunStore().reconcile() leaves store.RunStore().read_queue() == {"active":None,"queue":[]},
    store.manifest_path("run-ghost") still absent, and the queue is recoverable.
    """
    from issueforge import store

    _seed_queue("run-ghost", [])
    assert not store.manifest_path("run-ghost").exists()

    store.RunStore().reconcile()

    q = store.RunStore().read_queue()
    assert q == {"active": None, "queue": []}
    assert not store.manifest_path("run-ghost").exists()
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (b) stuck occupied — active names a completed run whose slot was never cleared
# ---------------------------------------------------------------------------


def test_reconcile_clears_a_slot_stuck_on_a_completed_run(isolated_state_home):
    """A crash between the completion manifest write and the slot release leaves active pointing at an already-completed run; reconcile clears that stuck-occupied slot.

    technical (contract): with a manifest for run-done at status "completed" and queue.json seeded
    {"active":"run-done","queue":[]} (the torn state after finalize wrote the terminal manifest but
    crashed before releasing the slot), store.RunStore().reconcile() leaves read_queue() ==
    {"active":None,"queue":[]}, the run-done manifest still "completed" (untouched), and the queue is
    recoverable.
    """
    from issueforge import store

    _seed_manifest("run-done", "completed")
    _seed_queue("run-done", [])

    store.RunStore().reconcile()

    q = store.RunStore().read_queue()
    assert q == {"active": None, "queue": []}
    assert store.RunStore().read("run-done")["status"] == "completed"
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (c) idempotent / no-op on an already-consistent store
# ---------------------------------------------------------------------------


def test_reconcile_is_a_noop_on_a_consistent_store_and_is_idempotent(isolated_state_home):
    """Reconcile changes nothing on an already-consistent store, and running it twice equals running it once — so read-only paths that reconcile never mutate a healthy store.

    technical (contract): (no-op) with a manifest for run-1 at "running" and queue.json
    {"active":"run-1","queue":[]} (a consistent store), the queue.json bytes and the run-1 manifest
    bytes are byte-for-byte identical before and after store.RunStore().reconcile(). (idempotent)
    starting from the torn orphan-active state {"active":"run-ghost","queue":[]} with no manifest,
    reconcile() once then reconcile() again yields identical queue.json bytes across the two calls
    (the second application is a no-op).
    """
    from issueforge import store

    # no-op on a consistent store
    _seed_manifest("run-1", "running")
    _seed_queue("run-1", [])
    queue_before = store.queue_path().read_bytes()
    manifest_before = store.manifest_path("run-1").read_bytes()

    store.RunStore().reconcile()

    assert store.queue_path().read_bytes() == queue_before
    assert store.manifest_path("run-1").read_bytes() == manifest_before

    # idempotent on a torn store: a second reconcile is a no-op over the first
    _seed_queue("run-ghost", [])
    store.RunStore().reconcile()
    after_first = store.queue_path().read_bytes()
    store.RunStore().reconcile()
    after_second = store.queue_path().read_bytes()
    assert after_first == after_second
    assert store.RunStore().read_queue()["active"] is None


# ---------------------------------------------------------------------------
# (d) admission after a crash still admits a new run (the buildable path works)
# ---------------------------------------------------------------------------


def test_admission_after_a_crash_reconciles_and_admits_a_new_run(
    make_git_repo, isolated_state_home
):
    """After a crash leaves an orphan active slot, the next admission reconciles first, so a brand-new run is admitted to the freed slot and runs to completion instead of enqueueing forever behind a phantom.

    technical (contract): with queue.json seeded {"active":"run-ghost","queue":[]} and no manifest
    for run-ghost, engine.run("DandD#148", issue_open=lambda s,n: True, new_run_id=lambda:"run-new")
    admits run-new (the admission-time reconcile drops the orphan) -> store.RunStore().read("run-new")
    ["status"] == "completed"; "run-ghost" appears in neither read_queue()["active"] nor its "queue";
    the final queue is recoverable.
    """
    from issueforge import engine, store

    _register(make_git_repo)
    _seed_queue("run-ghost", [])
    assert not store.manifest_path("run-ghost").exists()

    engine.run(
        "DandD#148",
        issue_open=lambda s, n: True,
        new_run_id=lambda: "run-new",
        stage=engine._default_stage,
    )

    assert store.RunStore().read("run-new")["status"] == "completed"
    q = store.RunStore().read_queue()
    assert q["active"] != "run-ghost"
    assert "run-ghost" not in q["queue"]
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (e) orphan active WITH a valid queued waiter — the waiter is never stranded
# ---------------------------------------------------------------------------


def test_reconcile_drops_the_orphan_active_but_never_strands_a_valid_waiter(isolated_state_home):
    """Dropping an orphan active must not throw away a legitimate queued waiter behind it: after reconcile the phantom is gone but the waiter (which has a valid non-terminal manifest) survives — promoted to active OR still queued, never lost.

    technical (contract): with a valid non-terminal manifest for run-waiter (status "queued") and
    queue.json seeded {"active":"run-ghost","queue":["run-waiter"]} where run-ghost has NO manifest,
    store.RunStore().reconcile() -> "run-ghost" is neither read_queue()["active"] nor in its "queue";
    run-waiter is still present, i.e. read_queue()["active"] == "run-waiter" OR "run-waiter" in
    read_queue()["queue"]; the run-waiter manifest still exists (non-terminal); the queue is
    recoverable.
    """
    from issueforge import store

    _seed_manifest("run-waiter", "queued")
    _seed_queue("run-ghost", ["run-waiter"])
    assert not store.manifest_path("run-ghost").exists()

    store.RunStore().reconcile()

    q = store.RunStore().read_queue()
    assert q["active"] != "run-ghost"
    assert "run-ghost" not in q["queue"]
    assert q["active"] == "run-waiter" or "run-waiter" in q["queue"], (
        f"the valid waiter was stranded/dropped: {q!r}"
    )
    assert store.manifest_path("run-waiter").exists()
    assert store.RunStore().read("run-waiter")["status"] not in _TERMINAL
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (f) recovery preserves FIFO — a freshly admitted run does not jump the waiter
# ---------------------------------------------------------------------------


def test_recovery_preserves_fifo_a_new_run_does_not_jump_a_pre_existing_waiter(
    make_git_repo, isolated_state_home, monkeypatch
):
    """Recovering from a crash preserves queue order: when a valid waiter was pending behind an orphan active, a run admitted AFTER the crash does not jump ahead of it — the pre-existing waiter is dispatched first, then the new run.

    technical (contract): with a valid non-terminal manifest for run-waiter (status "queued") and
    queue.json {"active":"run-ghost","queue":["run-waiter"]} (run-ghost has no manifest), monkeypatch
    engine._default_stage to append record["run_id"] to a list then complete; then
    engine.run("DandD#148", issue_open=lambda s,n: True, new_run_id=lambda:"run-new") drives the
    system forward. Both run-waiter and run-new appear in the recorded dispatch list and
    dispatched.index("run-waiter") < dispatched.index("run-new") (FIFO preserved, no jump);
    store.read("run-waiter")["status"] == store.read("run-new")["status"] == "completed"; "run-ghost"
    is gone from the queue; the final queue is recoverable. (Mechanism-agnostic: it does not matter
    whether reconcile promotes the waiter or the next advance does — only that order is preserved.)
    """
    from issueforge import engine, store

    _register(make_git_repo)
    _seed_manifest("run-waiter", "queued")
    _seed_queue("run-ghost", ["run-waiter"])
    assert not store.manifest_path("run-ghost").exists()

    dispatched: list[str] = []

    def recording_stage(record):
        dispatched.append(record["run_id"])
        store.RunStore().append_event(record["run_id"], {"transition": "stage"})

    monkeypatch.setattr(engine, "_default_stage", recording_stage)
    # #142 gate ruling (2026-07-27, final form): the drain dispatches the module-global composed
    # stage, so the crash-stranded waiter is promoted through THAT symbol — pin the recording stub on
    # it too so the waiter is observed (run-new still uses the explicit _default_stage stub below).
    monkeypatch.setattr(engine, "_poc_composed_stage", recording_stage)

    engine.run(
        "DandD#148",
        issue_open=lambda s, n: True,
        new_run_id=lambda: "run-new",
        stage=engine._default_stage,
    )

    assert "run-waiter" in dispatched, f"the pre-existing waiter was stranded: {dispatched!r}"
    assert "run-new" in dispatched, f"the new run never ran: {dispatched!r}"
    assert dispatched.index("run-waiter") < dispatched.index("run-new"), (
        f"a freshly admitted run jumped the pre-existing waiter: {dispatched!r}"
    )
    assert store.RunStore().read("run-waiter")["status"] == "completed"
    assert store.RunStore().read("run-new")["status"] == "completed"

    q = store.RunStore().read_queue()
    assert q["active"] != "run-ghost"
    assert "run-ghost" not in q["queue"]
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (g) a queued waiter whose manifest was never written is dropped, not promoted
# ---------------------------------------------------------------------------


def _seed_raw_manifest(run_id, payload):
    """Write raw manifest bytes directly (bypassing validation) to seed a malformed record."""
    from issueforge import store

    path = store.manifest_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _seed_many_queued(run_ids):
    """Seed many valid ``queued`` manifests + a queue of them under ONE lock (fast bulk seed)."""
    from issueforge import store

    s = store.RunStore()
    with s.locked():
        for run_id in run_ids:
            s.write_record_unlocked(run_id, {"run_id": run_id, "status": "queued"}, create=True)
        s.write_queue_unlocked({"active": None, "queue": list(run_ids)})


def test_reconcile_drops_a_queued_waiter_whose_manifest_is_missing(isolated_state_home):
    """A queued waiter whose manifest write was torn (an orphan waiter) is dropped by reconcile — never promoted to active, never crashing the next admission on a phantom.

    technical (contract): with queue.json seeded {"active":None,"queue":["run-phantom"]} and NO
    manifest for run-phantom (the torn state after a waiter's queue append crashed before its manifest
    write), store.RunStore().reconcile() leaves read_queue() == {"active":None,"queue":[]},
    manifest_path("run-phantom") still absent, and the queue recoverable.
    """
    from issueforge import store

    _seed_queue(None, ["run-phantom"])
    assert not store.manifest_path("run-phantom").exists()

    store.RunStore().reconcile()

    q = store.RunStore().read_queue()
    assert q == {"active": None, "queue": []}
    assert not store.manifest_path("run-phantom").exists()
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (h) a queued waiter whose manifest is terminal is dropped, not resurrected
# ---------------------------------------------------------------------------


def test_reconcile_drops_a_queued_waiter_whose_manifest_is_terminal(isolated_state_home):
    """A queued FIFO entry whose manifest is already terminal is dropped by reconcile — never re-marked running and re-dispatched (no resurrection of a finished run).

    technical (contract): with a live manifest for run-live (status "running"), a terminal manifest
    for run-old-done (status "completed"), and queue.json {"active":"run-live","queue":
    ["run-old-done"]}, store.RunStore().reconcile() leaves read_queue() == {"active":"run-live",
    "queue":[]} (waiter dropped, active untouched), read("run-old-done")["status"] still "completed"
    (untouched), and the queue recoverable.
    """
    from issueforge import store

    _seed_manifest("run-live", "running")
    _seed_manifest("run-old-done", "completed")
    _seed_queue("run-live", ["run-old-done"])

    store.RunStore().reconcile()

    q = store.RunStore().read_queue()
    assert q == {"active": "run-live", "queue": []}
    assert store.RunStore().read("run-old-done")["status"] == "completed"
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (i) a long persistent FIFO drains fully without blowing the recursion limit
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_recovery_drains_a_long_persistent_fifo_without_recursionerror(
    make_git_repo, isolated_state_home, monkeypatch
):
    """A crash-recovery of a very long queue drains iteratively: a 1200-deep FIFO of valid waiters runs to completion on the next admission without a per-waiter recursion frame blowing the stack.

    technical (contract): with 1200 valid queued manifests seeded as queue.json {"active":None,
    "queue":[run-w0000..run-w1199]}, engine.run("DandD#148", issue_open=lambda s,n:True,
    new_run_id=lambda:"run-new") drains every waiter (no RecursionError) -> read(run-w0000)/
    read(run-w1199)/read(run-new) all "completed"; read_queue() == {"active":None,"queue":[]}.
    """
    from issueforge import engine, store

    # #142 gate ruling (2026-07-27, final form): drain dispatches the module-global composed stage;
    # pin it to the no-op stub so the 1200 crash-stranded waiters drain via the stub (they carry no
    # composed context) exactly as this recursion-depth test intends.
    monkeypatch.setattr(engine, "_poc_composed_stage", engine._default_stage)

    _register(make_git_repo)
    waiters = [f"run-w{i:04d}" for i in range(1200)]
    _seed_many_queued(waiters)

    engine.run(
        "DandD#148",
        issue_open=lambda s, n: True,
        new_run_id=lambda: "run-new",
        stage=engine._default_stage,
    )

    assert store.RunStore().read(waiters[0])["status"] == "completed"
    assert store.RunStore().read(waiters[-1])["status"] == "completed"
    assert store.RunStore().read("run-new")["status"] == "completed"
    q = store.RunStore().read_queue()
    assert q == {"active": None, "queue": []}
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (j) a malformed/unknown-status active manifest is not treated as a live run
# ---------------------------------------------------------------------------


def test_reconcile_does_not_treat_a_malformed_active_manifest_as_live(isolated_state_home):
    """An active slot pointing at a manifest with an unknown/invalid status is not silently declared live — reconcile frees the slot (quarantine) rather than leaving a malformed record wedging admission, and never mutates the bad manifest.

    technical (contract): with a raw manifest for run-bad at an unknown status "bogus" (one
    validate_record rejects) and queue.json {"active":"run-bad","queue":[]},
    store.RunStore().reconcile() leaves read_queue() == {"active":None,"queue":[]}, the run-bad
    manifest bytes byte-for-byte unchanged, and the queue recoverable.
    """
    from issueforge import store

    _seed_raw_manifest("run-bad", json.dumps({"run_id": "run-bad", "status": "bogus"}))
    _seed_queue("run-bad", [])
    before = store.manifest_path("run-bad").read_bytes()

    store.RunStore().reconcile()

    q = store.RunStore().read_queue()
    assert q == {"active": None, "queue": []}
    assert store.manifest_path("run-bad").read_bytes() == before
    _assert_recoverable(q)
