"""Committed PENDING acceptance suite for #8 (S4) — the run store: one locked write path.

The store module does not exist yet, so each test is ``xfail(strict=True)`` (ImportError while
pending). Dual-layer docstrings: a plain-English behavior line, then the exact golden values under
``technical (contract):``.

The store interface this suite authors (ATDD):
- ``store.run_dir/manifest_path/events_path/queue_path/lock_path`` all resolve under
  ``paths.state_root()`` (``ISSUEFORGE_STATE_HOME``); the lock is one store-level file.
- ``store.require_int(name, value)`` returns a real int, raising TypeError on ``bool``/non-int.
- ``store.validate_record(record)`` is the ONE validator, called on BOTH write and read.
- ``RunStore.apply(run_id, transform, *, validate=None, create=False)`` is the ONE mutation
  primitive: an fcntl-locked read→transform→validate→atomic-write, existence checked INSIDE the
  lock. The atomic write routes through ``io.WriteSeam.write_text_atomic``.
- ``RunStore.read`` re-validates; ``append_event``/``replay_events`` own the append-only
  ``events.jsonl`` and discard only a torn trailing line.
- ``store.write_artifact(run_id, name, text, *, secrets)`` is the ONLY artifact writer; it redacts
  each secret to ``[REDACTED]``. engine/github never write store files directly (mechanically
  enforced, asserted below).

Deferred (per /spec-up): the redaction canary covers only producers that exist now; hidden model
reasoning / provider auth / env dumps are S7; the exhaustive canary is S24.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

REDACTED = "[REDACTED]"


def _snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _source(module_basename: str) -> str:
    import issueforge

    return (Path(issueforge.__file__).parent / module_basename).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- layout


def test_every_store_path_resolves_under_the_state_root(isolated_state_home):
    """The manifest, events, queue, and the single lock all resolve under the IssueForge state root.

    technical (contract): with run id "r1", store.manifest_path("r1"), store.events_path("r1"),
    store.run_dir("r1") sit under state_root()/runs/r1; store.queue_path() and store.lock_path()
    sit directly under state_root(); manifest_path == runs/r1/manifest.json and events_path ==
    runs/r1/events.jsonl.
    """
    from issueforge import store

    root = isolated_state_home.resolve()
    assert store.manifest_path("r1") == root / "runs" / "r1" / "manifest.json"
    assert store.events_path("r1") == root / "runs" / "r1" / "events.jsonl"
    assert store.run_dir("r1") == root / "runs" / "r1"
    assert store.queue_path().parent == root
    assert store.lock_path().parent == root


# --------------------------------------------------------------------------- the apply primitive


def test_apply_creates_persists_and_transform_sees_current_state(isolated_state_home):
    """apply creates a run's manifest, persists it, and its transform sees the current on-disk state so a counter increments.

    technical (contract): apply("r1", lambda r: {"n": 0}, create=True) then two
    apply("r1", lambda r: {"n": r["n"] + 1}) -> read("r1")["n"] == 2 and
    manifest_path("r1").exists().
    """
    from issueforge import store

    s = store.RunStore()
    s.apply("r1", lambda r: {"n": 0}, create=True)
    s.apply("r1", lambda r: {"n": r["n"] + 1})
    s.apply("r1", lambda r: {"n": r["n"] + 1})

    assert store.manifest_path("r1").exists()
    assert store.RunStore().read("r1")["n"] == 2


def test_only_the_store_persists_engine_and_github_never_write_run_files(isolated_state_home):
    """No module other than the store writes run files: engine.py and github.py contain no direct file-write or atomic-seam calls — the single write path is mechanical, not a convention.

    technical (contract): AST-scanning src/issueforge/engine.py and github.py, there is no call
    to open(...) in a write mode, no .write_text/.write_bytes, no os.replace, and no
    .write_text_atomic; all persistence goes through issueforge.store.
    """
    banned_attrs = {"write_text", "write_bytes", "write_text_atomic", "replace"}
    for module in ("engine.py", "github.py"):
        tree = ast.parse(_source(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    assert func.attr not in banned_attrs, f"{module}: direct {func.attr} write"
                if isinstance(func, ast.Name) and func.id == "open":
                    modes = [a for a in node.args if isinstance(a, ast.Constant)]
                    assert not any("w" in str(m.value) or "a" in str(m.value) for m in modes), (
                        f"{module}: direct open() write"
                    )


def test_manifest_persistence_routes_through_the_atomic_write_seam(
    isolated_state_home, monkeypatch
):
    """The store persists the manifest through io.WriteSeam.write_text_atomic — the sanctioned atomic seam, not a store-local raw replace.

    technical (contract): with WriteSeam.write_text_atomic wrapped by a spy,
    apply("r1", lambda r: {"status": "queued"}, create=True) records a call whose target
    resolves to store.manifest_path("r1").
    """
    from issueforge import store
    from issueforge.io import WriteSeam

    targets: list[Path] = []
    original = WriteSeam.write_text_atomic

    def spy(self, path, data, *a, **k):
        targets.append(Path(path).resolve())
        return original(self, path, data, *a, **k)

    monkeypatch.setattr(WriteSeam, "write_text_atomic", spy)
    store.RunStore().apply("r1", lambda r: {"status": "queued"}, create=True)

    assert store.manifest_path("r1").resolve() in targets


def test_require_int_rejects_bool_and_non_int_and_gates_a_persisted_field(isolated_state_home):
    """Persisted int fields raise on bool/non-int both in the helper AND through the real write/read validator — no coercion mints a valid-looking record.

    technical (contract): require_int("n", 5) == 5; require_int with True / "5" / 1.0 raises
    TypeError. apply("r1", lambda r: {"status": "running", "attempts": True}, create=True) raises
    and writes nothing; a planted manifest with attempts=True is rejected by read("r1").
    """
    from issueforge import store

    assert store.require_int("n", 5) == 5
    for bad in (True, "5", 1.0):
        with pytest.raises(TypeError):
            store.require_int("n", bad)

    with pytest.raises(Exception):
        store.RunStore().apply("r1", lambda r: {"status": "running", "attempts": True}, create=True)
    assert not store.manifest_path("r1").exists()

    planted = store.manifest_path("r2")
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(json.dumps({"status": "running", "attempts": True}), encoding="utf-8")
    with pytest.raises(Exception):
        store.RunStore().read("r2")


def test_the_same_validate_record_gates_both_apply_and_read(isolated_state_home, monkeypatch):
    """Both apply (write) and read call the ONE exported validate_record — not two divergent validators.

    technical (contract): with store.validate_record wrapped by a spy, apply("r1", lambda r:
    {"status": "queued"}, create=True) invokes the spy, and read("r1") invokes it again; the
    spy's call count increases across both.
    """
    from issueforge import store

    calls: list[int] = []
    original = store.validate_record

    def spy(record):
        calls.append(1)
        return original(record)

    monkeypatch.setattr(store, "validate_record", spy)
    store.RunStore().apply("r1", lambda r: {"status": "queued"}, create=True)
    after_write = len(calls)
    assert after_write >= 1

    store.RunStore().read("r1")
    assert len(calls) > after_write


def test_existence_is_decided_inside_the_lock_no_phantom_no_toctou(isolated_state_home):
    """The existence check happens INSIDE the lock: apply(create=False) on a truly-missing run refuses and mints no phantom; but a create=False that waited on the lock while another apply CREATED the run under it then sees the run and succeeds (a pre-lock check would have wrongly refused).

    technical (contract): apply("ghost", lambda r: {"x": 1}) (no create) raises naming "ghost"
    and run_dir("ghost") stays absent. Separately: thread A calls apply("r1", <transform that
    signals ready then blocks>, create=True) holding the lock; while A holds it, thread B calls
    apply("r1", lambda r: {"seen": True}, create=False) which blocks on the lock; releasing A lets
    A create r1 and B then acquires the lock, observes r1 exists, and succeeds (does not raise) ->
    read("r1")["seen"] is True.
    """
    from issueforge import store

    with pytest.raises(Exception) as exc:
        store.RunStore().apply("ghost", lambda r: {"x": 1})
    assert "ghost" in str(exc.value)
    assert not store.run_dir("ghost").exists()

    a_ready = threading.Event()
    release = threading.Event()
    b_error: list[BaseException] = []
    b_done = threading.Event()

    def a_transform(r):
        a_ready.set()
        release.wait(timeout=10)
        return {"status": "queued"}

    def run_a():
        store.RunStore().apply("r1", a_transform, create=True)

    def run_b():
        a_ready.wait(timeout=10)
        try:
            store.RunStore().apply("r1", lambda r: {"seen": True}, create=False)
        except BaseException as exc:  # noqa: BLE001
            b_error.append(exc)
        finally:
            b_done.set()

    ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
    ta.start()
    tb.start()
    try:
        assert a_ready.wait(timeout=10)
        assert not b_done.wait(timeout=0.3), "B entered before A released the lock"
        release.set()
        assert b_done.wait(timeout=10)
    finally:
        release.set()
        ta.join(timeout=10)
        tb.join(timeout=10)

    assert b_error == [], f"B wrongly refused an existing run: {b_error}"
    assert store.RunStore().read("r1")["seen"] is True


# --------------------------------------------------------------------------- under-lock proofs


def test_validation_runs_under_the_lock_a_raise_leaves_bytes_unchanged_and_blocks_a_competitor(
    isolated_state_home,
):
    """Validation runs INSIDE the lock: while one apply is in its (blocking) validator, a competing apply cannot proceed; when the validator raises the manifest is byte-unchanged and the competitor then lands.

    technical (contract): with r1 at {"n": 0}, apply A uses a validator that signals then blocks
    then raises ValueError; a competing apply B ({"n": r["n"] + 100}) started meanwhile does NOT
    complete while A is in its validator (competitor_done stays unset for 0.4s); after A's
    validator is released and raises, A wrote nothing and B lands -> read("r1")["n"] == 100; the
    manifest bytes equal the pre-A snapshot merged only by B.
    """
    from issueforge import store

    s = store.RunStore()
    s.apply("r1", lambda r: {"n": 0}, create=True)

    in_validator = threading.Event()
    release = threading.Event()
    competitor_done = threading.Event()

    def validator(merged):
        store.validate_record(merged)
        in_validator.set()
        release.wait(timeout=10)
        raise ValueError("reject A")

    def run_a():
        with pytest.raises(ValueError):
            s.apply("r1", lambda r: {"n": r["n"] + 1}, validate=validator)

    def run_b():
        in_validator.wait(timeout=10)
        s.apply("r1", lambda r: {"n": r["n"] + 100})
        competitor_done.set()

    ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
    ta.start()
    tb.start()
    try:
        assert in_validator.wait(timeout=10)
        assert not competitor_done.wait(timeout=0.4), "competitor entered while A held the lock"
        release.set()
        assert competitor_done.wait(timeout=10)
    finally:
        release.set()
        ta.join(timeout=10)
        tb.join(timeout=10)

    assert store.RunStore().read("r1")["n"] == 100


def test_lock_spans_the_whole_read_modify_write_no_lost_update(isolated_state_home):
    """The lock spans the entire read-modify-write, so concurrent writers with a widened stale-read window do not lose an update.

    technical (contract): with r1 at {"n": 0}, 20 threads each apply a transform that reads n,
    sleeps 5ms (widening the stale-read window a lock-free impl would lose on), then returns
    n + 1 -> read("r1")["n"] == 20 and no worker raised.
    """
    from issueforge import store

    store.RunStore().apply("r1", lambda r: {"n": 0}, create=True)
    errors: list[BaseException] = []

    def bump():
        def transform(r):
            value = r["n"]
            time.sleep(0.005)
            return {"n": value + 1}

        try:
            store.RunStore().apply("r1", transform)
        except BaseException as exc:  # noqa: BLE001 — surface any worker failure
            errors.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert store.RunStore().read("r1")["n"] == 20


def test_advisory_lock_is_released_when_the_holder_process_is_killed(isolated_state_home):
    """The store lock is an OS advisory lock: kill -9 a process holding it inside an apply, and a fresh process can still apply — the kernel released it (a bare lockfile would strand).

    technical (contract): a child process calls RunStore().apply("r1", <transform that signals
    ready then sleeps>, create=True), holding the store lock; the parent SIGKILLs it; a fresh
    RunStore().apply("r2", ..., create=True) in the parent then completes without blocking.
    """
    import signal

    from issueforge import store

    ready = isolated_state_home / "held.flag"
    child_src = textwrap.dedent(
        f"""
        import pathlib, time
        from issueforge import store
        def transform(r):
            pathlib.Path({str(ready)!r}).write_text("held")
            time.sleep(60)
            return {{"status": "queued"}}
        store.RunStore().apply("r1", transform, create=True)
        """
    )
    env = {**os.environ, "ISSUEFORGE_STATE_HOME": str(isolated_state_home)}
    child = subprocess.Popen([sys.executable, "-c", child_src], env=env)
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "child never entered the locked apply"

        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)

        done = threading.Event()

        def fresh():
            store.RunStore().apply("r2", lambda r: {"status": "queued"}, create=True)
            done.set()

        t = threading.Thread(target=fresh)
        t.start()
        assert done.wait(timeout=10), "the lock stranded after kill -9"
        t.join(timeout=10)
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=10)


# --------------------------------------------------------------------------- durability


def test_atomic_write_uses_same_dir_temp_and_os_replace_and_a_failed_replace_preserves_the_file(
    isolated_state_home, monkeypatch
):
    """The atomic write stages a temp file in the SAME directory and finishes with os.replace(temp, dest); a failed replace leaves the previous manifest intact with no leftover files.

    technical (contract): a spy on os.replace records (src, dst) with Path(src).parent ==
    Path(dst).parent == run_dir("r1"); after a successful create, patching os.replace to raise
    OSError makes apply("r1", lambda r: {"status": "running"}) raise, the manifest bytes stay
    "queued", and run_dir("r1") gains no extra files versus before the failed write.
    """
    from issueforge import store

    seen: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def spy_replace(src, dst, *a, **k):
        seen.append((Path(src), Path(dst)))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", spy_replace)
    s = store.RunStore()
    s.apply("r1", lambda r: {"status": "queued"}, create=True)

    assert seen, "atomic write must call os.replace"
    src, dst = seen[-1]
    assert src.parent == dst.parent == store.run_dir("r1").resolve()
    assert dst.resolve() == store.manifest_path("r1").resolve()

    before_bytes = _snapshot(store.manifest_path("r1"))
    before_listing = sorted(p.name for p in store.run_dir("r1").iterdir())

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        s.apply("r1", lambda r: {"status": "running"})

    assert _snapshot(store.manifest_path("r1")) == before_bytes
    assert sorted(p.name for p in store.run_dir("r1").iterdir()) == before_listing


def test_write_text_atomic_seam_exists_and_is_atomic(isolated_state_home):
    """io.WriteSeam.write_text_atomic writes through the sanctioned seam and replaces atomically under an allowed root.

    technical (contract): WriteSeam().write_text_atomic(state_root()/runs/x/m.json, '{"a":1}')
    writes the file with that content; the boundary lint over the real package still passes
    (the atomic method lives inside the seam).
    """
    from issueforge.io import WriteSeam
    from issueforge.paths import state_root

    target = Path(state_root()) / "runs" / "x" / "m.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    WriteSeam().write_text_atomic(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'

    lint = subprocess.run(
        [sys.executable, "-m", "issueforge", "lint", "boundary"], capture_output=True, text=True
    )
    assert lint.returncode == 0, lint.stderr


def test_remove_scratch_seam_removes_registered_scratch_and_refuses_outside(
    isolated_state_home, tmp_path
):
    """io.WriteSeam.remove_scratch removes a run-owned subtree under a REGISTERED scratch root and
    refuses (BoundaryViolation) any path outside one — the sanctioned deletion primitive (#82 item 2).

    technical (contract): after allow_scratch(dest), a materialized dest/sub subtree is removed by
    remove_scratch(dest/sub) and no longer exists; remove_scratch of a path under NO registered
    scratch root raises BoundaryViolation and leaves that path intact.
    """
    from issueforge.io import BoundaryViolation, WriteSeam

    seam = WriteSeam()
    dest = tmp_path / "pkt"
    seam.allow_scratch(dest)
    sub = dest / "review-abc"
    seam.write_text(sub / "diff.txt", "d")
    assert sub.is_dir()

    assert seam.remove_scratch(sub) == sub.resolve()
    assert not sub.exists()
    assert dest.exists() and list(dest.rglob("*")) == []

    outside = tmp_path / "unregistered"
    outside.mkdir()
    (outside / "keep.txt").write_text("k")
    with pytest.raises(BoundaryViolation):
        seam.remove_scratch(outside)
    assert (outside / "keep.txt").exists()


# --------------------------------------------------------------------------- append-only events


def test_events_are_append_only_prior_bytes_never_rewritten(isolated_state_home):
    """Events are append-only: each append leaves every preceding byte unchanged and replay returns them in order.

    technical (contract): after create, append_event("r1", {"t": "a"}) then {"t": "b"} then
    {"t": "c"} -> the byte prefix of events.jsonl after each append is a strict prefix of the
    file after the next append; replay_events("r1") == [{"t": "a"}, {"t": "b"}, {"t": "c"}].
    """
    from issueforge import store

    s = store.RunStore()
    s.apply("r1", lambda r: {"status": "queued"}, create=True)

    prefixes = []
    for event in ({"t": "a"}, {"t": "b"}, {"t": "c"}):
        s.append_event("r1", event)
        prefixes.append(store.events_path("r1").read_bytes())

    assert prefixes[0] == prefixes[1][: len(prefixes[0])]
    assert prefixes[1] == prefixes[2][: len(prefixes[1])]
    assert store.RunStore().replay_events("r1") == [{"t": "a"}, {"t": "b"}, {"t": "c"}]


def test_only_a_torn_final_line_is_discarded_a_malformed_middle_line_raises(isolated_state_home):
    """Replay discards ONLY an unparseable, unterminated final line; a valid final line without a trailing newline still replays, and a malformed MIDDLE line raises rather than being silently swallowed.

    technical (contract): events file [ {"t":"a"}\\n {"t":"b"}\\n {"t":"c","par ] (torn final,
    no newline) -> replay == [a, b]. A file [ {"t":"a"}\\n {"t":"b"} ] (valid final, no trailing
    newline) -> replay == [a, b]. A file [ {"t":"a"}\\n GARBAGE\\n {"t":"c"}\\n ] (malformed
    middle) -> replay raises.
    """
    from issueforge import store

    store.RunStore().apply("r1", lambda r: {"status": "queued"}, create=True)
    events = store.events_path("r1")

    events.write_text(
        json.dumps({"t": "a"}) + "\n" + json.dumps({"t": "b"}) + "\n" + '{"t": "c", "par',
        encoding="utf-8",
    )
    assert store.RunStore().replay_events("r1") == [{"t": "a"}, {"t": "b"}]

    events.write_text(json.dumps({"t": "a"}) + "\n" + json.dumps({"t": "b"}), encoding="utf-8")
    assert store.RunStore().replay_events("r1") == [{"t": "a"}, {"t": "b"}]

    events.write_text(
        json.dumps({"t": "a"}) + "\nGARBAGE\n" + json.dumps({"t": "c"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(Exception):
        store.RunStore().replay_events("r1")


# --------------------------------------------------------------------------- redaction / boundary


def test_the_redacting_writer_scrubs_secrets_from_the_persisted_artifact(isolated_state_home):
    """The redacting writer replaces each known secret with [REDACTED] before the artifact lands.

    technical (contract): write_artifact("r1", "out.log", "token=SEKRET-123 done",
    secrets={"SEKRET-123"}) -> the persisted file contains "[REDACTED]" and not "SEKRET-123".
    """
    from issueforge import store

    store.RunStore().apply("r1", lambda r: {"status": "running"}, create=True)
    path = store.write_artifact("r1", "out.log", "token=SEKRET-123 done", secrets={"SEKRET-123"})

    text = path.read_text(encoding="utf-8")
    assert REDACTED in text
    assert "SEKRET-123" not in text


def test_append_event_redacts_secrets_from_the_event_stream_producer(isolated_state_home):
    """The event-stream producer is redacted at its real persistence path: a secret carried in an appended event is scrubbed when the RunStore was given that secret.

    technical (contract): RunStore(secrets={"CANARY-EVENT"}).append_event("r1",
    {"msg": "saw CANARY-EVENT"}) -> the persisted events.jsonl line contains "[REDACTED]" and
    never "CANARY-EVENT".
    """
    from issueforge import store

    s = store.RunStore(secrets={"CANARY-EVENT"})
    s.apply("r1", lambda r: {"status": "running"}, create=True)
    s.append_event("r1", {"msg": "saw CANARY-EVENT"})

    raw = store.events_path("r1").read_text(encoding="utf-8")
    assert "CANARY-EVENT" not in raw
    assert REDACTED in raw


def test_a_raw_artifact_write_outside_the_seam_is_a_boundary_violation(tmp_path):
    """Bypassing the redacting writer is mechanically impossible: the boundary lint reports a write-surface violation for a module that writes a file by open() or Path.write_text, and passes the real package.

    technical (contract): boundary.check_tree over a synthetic module doing Path(p).write_text(x)
    AND one doing open(p, "w") each returns a violation whose kind is "write-surface"; and
    `issueforge lint boundary` exits 0 on the real package.
    """
    import issueforge.store  # noqa: F401 — red while S4 is pending; guards the landed slice
    from issueforge.boundary import check_tree

    rogue = tmp_path / "rogue"
    rogue.mkdir()
    (rogue / "a.py").write_text(
        "from pathlib import Path\ndef f(p, x):\n    Path(p).write_text(x)\n", encoding="utf-8"
    )
    (rogue / "b.py").write_text("def g(p, x):\n    open(p, 'w').write(x)\n", encoding="utf-8")
    violations = check_tree(rogue, deps=frozenset())
    assert violations, "raw artifact writes must be flagged"
    assert any("write-surface" in str(v) for v in violations)

    lint = subprocess.run(
        [sys.executable, "-m", "issueforge", "lint", "boundary"], capture_output=True, text=True
    )
    assert lint.returncode == 0, lint.stderr


def test_the_io_seam_exemption_is_method_scoped_no_stray_writer_in_io():
    """The boundary lint's io.py exemption is method-scoped: every raw filesystem write in io.py lives inside a WriteSeam method, so no stray artifact-writing function can hide in the seam module.

    technical (contract): AST-scanning src/issueforge/io.py, every call to a write primitive
    (.write_text / .write_text_atomic / .write_bytes / os.replace / open(...,'w')) occurs
    lexically inside a function that is a method of the WriteSeam class (nested under
    `class WriteSeam`).
    """
    import issueforge.store  # noqa: F401 — red while S4 is pending; guards the landed slice

    tree = ast.parse(_source("io.py"))

    seam_functions: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WriteSeam":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef):
                    seam_functions.add(id(child))

    # Map each write call to its enclosing FunctionDef.
    def enclosing_funcs(root):
        stack = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, n):
                stack.append(n)
                self.generic_visit(n)
                stack.pop()

            def visit_Call(self, n):
                func = n.func
                is_write = (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"write_text", "write_text_atomic", "write_bytes", "replace"}
                ) or (isinstance(func, ast.Name) and func.id == "open")
                if is_write:
                    assert stack, "a raw write in io.py sits outside any function"
                    assert id(stack[-1]) in seam_functions, (
                        "a raw write in io.py is outside a WriteSeam method"
                    )
                self.generic_visit(n)

        V().visit(root)

    enclosing_funcs(tree)
