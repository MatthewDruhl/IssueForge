"""Committed PENDING acceptance suite for #48 — run-store crash-transactionality + reconcile.

The store's admission and completion each write TWO files under one lock (the manifest and
``queue.json``), but the pair is not crash-atomic: a crash between the two writes can leave
``queue.active`` pointing at a run whose manifest was never written (an ORPHAN active), or at a
completed run whose slot was never cleared (STUCK occupied). #48 makes that transition recoverable:
a startup reconcile / sweep that, run at the beginning of a mutating admission, restores a
consistent store — clearing a stuck-occupied slot and dropping an orphan ``active`` whose run has no
manifest.

This suite pins the RECOVERY BEHAVIOR, not the mechanism. It never asserts derive-active-from-
manifests vs a transactional commit; it asserts the observable postcondition on ``queue.json`` +
the manifests after a simulated torn write. The torn state is produced by driving the REAL public
store seams (``RunStore.locked`` + ``write_queue_unlocked`` + ``apply``) — the same primitives
admission and completion use — to write one side of the pair without the other, never by mocking
store internals.

Authored surface (ATDD): ``RunStore.reconcile()`` — an idempotent, lock-guarded sweep that makes
``queue.json`` consistent with the manifests (a no-op on an already-consistent store), plus the
admission-time wiring so ``engine.run`` reconciles before it decides the active slot (the hook
deferred at engine.py:241). "Recoverable/consistent" means: after reconcile, ``queue.active`` is
either ``None`` or names a run whose manifest exists AND is non-terminal.

Each test is ``@pytest.mark.xfail(strict=True, reason="PENDING (#48)")`` — reconcile does not exist
yet (``AttributeError``), and the admission hook is not wired, so every test fails today. Imports of
the pending surface live INSIDE each test. Dual-layer docstrings: a plain-English behavior line,
then the exact golden values under ``technical (contract):``.
"""

from __future__ import annotations

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


@pytest.mark.xfail(strict=True, reason="PENDING (#48)")
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


@pytest.mark.xfail(strict=True, reason="PENDING (#48)")
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


@pytest.mark.xfail(strict=True, reason="PENDING (#48)")
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


@pytest.mark.xfail(strict=True, reason="PENDING (#48)")
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

    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-new")

    assert store.RunStore().read("run-new")["status"] == "completed"
    q = store.RunStore().read_queue()
    assert q["active"] != "run-ghost"
    assert "run-ghost" not in q["queue"]
    _assert_recoverable(q)


# ---------------------------------------------------------------------------
# (e) orphan active WITH a valid queued waiter — the waiter is never stranded
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#48)")
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


@pytest.mark.xfail(strict=True, reason="PENDING (#48)")
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

    engine.run("DandD#148", issue_open=lambda s, n: True, new_run_id=lambda: "run-new")

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
