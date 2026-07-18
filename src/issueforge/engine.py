"""The engine: run one issue end-to-end through a stub stage (issue #8, S4).

``run(spec)`` resolves ``alias#n`` via the S3 registry, refuses a non-open issue (via the injectable
``issue_open`` seam) before minting anything, decides admission INSIDE the store lock against the
single active slot (else enqueues), runs the stub ``stage`` OUTSIDE the lock while capturing its
stdout/stderr through the redacting artifact writer, records the ``queued/running/completed``
transitions as events, and lands ``completed``. A second run only LANDS queued: the FIFO does not
auto-advance here (that is S5).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

from issueforge import github, store
from issueforge.registry import Registry

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"


def _default_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:12]


def _default_stage(record: dict) -> None:
    """The stub stage: mark progress with an observable event, then complete."""
    store.RunStore().append_event(record["run_id"], {"transition": "stage"})


def _persist_captured(
    s: store.RunStore, run_id: str, out: StringIO, err: StringIO, secrets: set[str] | None
) -> None:
    """Persist whatever stdout/stderr the stage produced, through the redacting artifact writer."""
    if out.getvalue():
        s.write_artifact(run_id, "stdout.log", out.getvalue(), secrets=secrets)
    if err.getvalue():
        s.write_artifact(run_id, "stderr.log", err.getvalue(), secrets=secrets)


def _release_active(s: store.RunStore, run_id: str) -> None:
    """Clear the single active slot iff it still holds ``run_id`` (under the store lock)."""
    with s.locked():
        queue = s.read_queue()
        if queue.get("active") == run_id:
            queue["active"] = None
            s.write_queue_unlocked(queue)


def run(
    spec: str,
    *,
    issue_open: Callable[[str, int], bool] | None = None,
    stage: Callable[[dict], Any] | None = None,
    new_run_id: Callable[[], str] | None = None,
    secrets: set[str] | None = None,
) -> dict:
    """Run ``alias#n`` to completion (or land it queued behind the active run)."""
    if issue_open is None:
        issue_open = github.issue_is_open
    if stage is None:
        stage = _default_stage
    if new_run_id is None:
        new_run_id = _default_run_id

    alias, _, raw_number = spec.partition("#")
    number = int(raw_number)

    entry = Registry.load().get(alias)  # RegistryError before any run when unregistered
    if not issue_open(entry.slug, number):
        raise ValueError(f"issue {entry.slug}#{number} is not open; refusing to run")

    run_id = new_run_id()
    s = store.RunStore(secrets=secrets)

    # Admission is decided INSIDE the store lock: a lock-free check-then-start would admit two.
    with s.locked():
        queue = s.read_queue()
        if queue.get("active") is None:
            admitted = True
            queue["active"] = run_id
            status = RUNNING
        else:
            admitted = False
            queue["queue"].append(run_id)
            status = QUEUED
        # Fail-safe order: write the QUEUE (active=run_id) BEFORE the manifest(status=running). A
        # crash between the two then leaves the slot OCCUPIED (a later run enqueues) rather than
        # empty (which would wrongly admit a second run = double-active). Full multi-file
        # transactionality / startup reconcile is deferred to #48.
        s.write_queue_unlocked(queue)
        s.write_record_unlocked(run_id, {"run_id": run_id, "status": status}, create=True)

    s.append_event(run_id, {"transition": QUEUED})
    if not admitted:
        return s.read(run_id)

    s.append_event(run_id, {"transition": RUNNING})
    record = s.read(run_id)

    out, err = StringIO(), StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            stage(record)
    except BaseException:
        # A raising stage must not brick the engine: persist captured output and release the active
        # slot (else queue.active stays set forever, blocking ALL future runs), then re-raise. S4
        # has no "failed" status, so the manifest simply stays "running".
        _persist_captured(s, run_id, out, err, secrets)
        _release_active(s, run_id)
        raise

    _persist_captured(s, run_id, out, err, secrets)

    with s.locked():
        # Fail-safe order: write the manifest(status=completed) BEFORE clearing queue.active. A
        # crash between the two then leaves the slot stuck-occupied (blocks new runs) rather than
        # cleared while the old manifest still says "running". Deferred: see #48.
        completed = {**s.read(run_id), "status": COMPLETED}
        s.write_record_unlocked(run_id, completed)
        queue = s.read_queue()
        if queue.get("active") == run_id:
            queue["active"] = None
            s.write_queue_unlocked(queue)
    s.append_event(run_id, {"transition": COMPLETED})
    return s.read(run_id)
