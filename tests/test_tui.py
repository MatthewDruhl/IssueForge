"""PENDING acceptance contract for issue #152 (S23a) — read-only watch TUI.

S23a builds the FOUR read-only US-9.2 views whose data already flows today —
**queue position, current stage, logs, failures** — over the S5 event stream.

Binding design decisions (Matt, wave dispatch 2026-07-26 and the fix-round
resolutions, all on the issue):

* **Pure render + thin shell.** Pure functions map a run-store snapshot + the S5
  event stream to rendered text per view; the Textual ``App`` is a thin shell that
  tails ``events.jsonl`` and displays that pure output. This suite drives BOTH the
  pure functions AND a headless shell seam (no TTY, no textual pilot harness), so
  "the same event stream produces identical output" is asserted as function purity
  and the shell wiring is asserted without a terminal.
* **Logs view = event feed + persisted artifacts** (fix-round finding 27). The logs
  view renders the run's structured event feed AND its persisted, already-redacted
  run artifacts (``stdout.log``, ``baseline-stdout.log``, transcripts, ...). Both
  are read-only run-store data. The snapshot therefore carries an ``artifacts``
  source (documented below).
* **Failure order is "a documented stable order", not a specific sort** (finding
  23). The suite asserts determinism, not run-id ordering.
* **Stub-gone is behavioral, not a source scan** (finding 26): pinned on the shell's
  rendered output and the ``tui`` command's wiring, never on module source text.

The import surface this suite pins (the build must provide exactly these on
``issueforge.tui``)::

    # four pure per-view renderers, each returning a plain ``str``
    render_queue_view(snapshot, events) -> str
    render_stage_view(snapshot, events) -> str
    render_logs_view(snapshot, events) -> str
    render_failures_view(snapshot, events) -> str

    # thin-shell seams, headless-testable (no TTY)
    load_state() -> tuple[snapshot, events]        # reads the run store off disk
    render_all(snapshot, events) -> dict[str, str] # keys: queue/stage/logs/failures
    IssueForgeApp(loader=load_state)               # loader seam defaults to load_state
    IssueForgeApp.build_frames() -> dict[str, str] # render_all(*self._loader()), re-read each call

The two render arguments reproduce the LIVE store shapes (real structure,
synthetic content), built by the shell from the run store and handed to the pure
functions:

* ``snapshot`` — ``{"queue": <read_queue() dict>, "runs": {run_id: <read() manifest>},
  "artifacts": {run_id: {filename: text}}}``.
  ``snapshot["queue"]`` is exactly ``store.RunStore.read_queue()`` output:
  ``{"active": <run_id|None>, "queue": [run_id, ...]}`` (store.py:180-185; the
  admission queue documented at store.py:6). ``snapshot["runs"]`` maps every known
  run id to its manifest record — the exact dict ``store.RunStore.read()`` returns,
  whose fields are minted at admission (engine.py:306-313 and 343-345) plus, on a
  failed run, the ``{"type": ...}`` failure sub-record finalize persists
  (engine.py:231-238). ``snapshot["artifacts"]`` maps each run id to its persisted
  redacted artifacts — the files ``store.write_artifact`` lands under the run dir
  (store.py:293), e.g. ``_persist_captured``'s ``stdout.log`` (engine.py:98-101).
* ``events`` — ``{run_id: <replay_events(run_id) list>}``. Each list is exactly
  ``store.RunStore.replay_events()`` output (store.py:265-290): an ORDERED list of
  parsed JSON objects. Every object carries a ``"transition"`` string, and RICHER
  events carry EXTRA keys — the engine today emits ``{"transition": "authoring"}``
  (engine.py:489), ``{"transition": "approval", "revision": True, "approved": True}``
  (engine.py:667), and ``{"transition": "revision", "completed": [...]}``
  (engine.py:680), alongside the lifecycle transitions ``queued``/``running``/
  ``completed``/``failed`` (engine.py:347/351/245/239) and the stub-stage marker
  ``"stage"`` (engine.py:86). Renderers MUST tolerate multi-key event dicts; the
  transition value is NOT a closed set.

Rendering contract the tests rely on (documented so association is checkable
without pinning layout): each run occupies its own LINE, and a run's associated
datum (its queue position, or its failure type) appears on the SAME line as its
run id. Visual formatting beyond that is the builder's; the golden assertions pin
DATA, ASSOCIATION, ORDER, and DETERMINISM, not whitespace.

Anti-tautology posture (fix round): every view test (a) proves output is DERIVED
from its inputs by rendering a second, content-varied scenario and asserting the
output CHANGES accordingly, (b) proves PURITY by deep-copying inputs and asserting
they are unmutated after render, (c) asserts ``type(result) is str``, and (d) pins
determinism twice — render-twice equality AND permuted-irrelevant-input equality
(the ``runs``/``artifacts``/``events`` dicts rebuilt in reversed order, deep-copied
so no nested object is shared).

Pending convention (identical to the other suites in this wave, e.g.
tests/test_integrity_verify.py:611): every not-yet-implemented test carries a
literal ``@pytest.mark.xfail(strict=True, reason="PENDING (#152)")`` and touches
the not-yet-existing surface INSIDE its body, so the ``ImportError`` /
``AttributeError`` while ``issueforge.tui`` lacks that surface is the strict-xfail
today. Tests that pass TODAY carry NO marker — they are green HARNESS GUARDS,
present so a broken fixture cannot masquerade as the expected xfail count. #147 in
this wave adds a duration drift-guard; every test here stays well under a second
(the one real-admission guard does a single offline git init).
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from typer.testing import CliRunner

from issueforge import store
from issueforge.cli import app
from issueforge.paths import state_root

# Synthetic-but-live-shaped issue references the seeded runs carry.
_DANDD_REF = ("MatthewDruhl", "DandD", 111)
_FLEET_REF = ("MatthewDruhl", "FleetTrack", 42)
_FARM_REF = ("MatthewDruhl", "FarmMind", 7)

# The active run's stream (emission order): admission, dispatch, then the RICH
# authoring/approval/revision events the engine emits today (engine.py:489/667/680).
# The LAST transition ("revision") is what the current-stage view must derive.
_ACTIVE_STREAM = [
    {"transition": "queued"},
    {"transition": "running"},
    {"transition": "authoring"},
    {"transition": "approval", "revision": True, "approved": True},
    {"transition": "revision", "completed": ["op-1", "op-2"]},
]
_ACTIVE_LATEST = "revision"

_ACTIVE_ID = "run-aaaaaaaaaaaa"
_WAITER1_ID = "run-bbbbbbbbbbbb"
_WAITER2_ID = "run-eeeeeeeeeeee"
_FAILED1_ID = "run-cccccccccccc"
_FAILED2_ID = "run-dddddddddddd"

# Distinctive artifact content, so the logs view surfacing persisted artifacts has
# an unambiguous golden token that appears NOWHERE in the event feed.
_STDOUT_ART = "candidate worktree prepared\nSENTINEL_STDOUT_TOKEN\n"
_BASELINE_ART = "collected 12 items\nSENTINEL_BASELINE_12_PASSED\n"

# A distinctive event transition AND artifact seeded on a NON-active run, so the
# logs view's active-run SELECTION can be proven by CONTENT (event records carry no
# run id, so absence of other run ids does not prove selection — fix-round finding
# 3). A label-free renderer that aggregates every run's events/artifacts leaks these.
_NONACTIVE_EVENT = "nonactive-sentinel-transition"
_NONACTIVE_ART = "NONACTIVE_ARTIFACT_SENTINEL"


def _manifest(run_id: str, status: str, issue_ref, *, failure_type: str | None = None) -> dict:
    """A run manifest in the EXACT shape admission mints (engine.py:306-345).

    ``issue_ref`` is ``(owner, repo, number)``; on disk JSON stores it as a list,
    which is what ``store.read()`` returns. ``failure_type`` adds the
    ``{"type": ...}`` sub-record finalize persists for a failed run (engine.py:231).
    """
    owner, repo, number = issue_ref
    record = {
        "run_id": run_id,
        "status": status,
        "repo": repo,
        "slug": f"{owner}/{repo}",
        "issue_number": number,
        "issue_ref": [owner, repo, number],
        "default_branch": "main",
        "registered_checkout": f"/checkouts/{repo}",
    }
    if failure_type is not None:
        record["failure"] = {"type": failure_type}
    return record


def _seed_store() -> None:
    """Persist a realistic run store through the REAL store API (never hand-written
    JSON): one active running run (with the rich event stream + two persisted
    artifacts), TWO queued waiters (for FIFO/position coverage), and TWO failed runs
    (for failure association). ``queue.json`` names the active slot and the waiter
    FIFO. Uses ``apply(create=True)`` (store.py:123), ``append_event`` (store.py:254),
    ``write_artifact`` (store.py:293), and a lock-guarded ``write_queue_unlocked``
    (store.py:187) — exactly the calls engine.py makes.
    """
    s = store.RunStore()

    active = _manifest(_ACTIVE_ID, "running", _DANDD_REF)
    s.apply(_ACTIVE_ID, lambda _r: active, create=True)
    for event in _ACTIVE_STREAM:
        s.append_event(_ACTIVE_ID, event)
    s.write_artifact(_ACTIVE_ID, "stdout.log", _STDOUT_ART)
    s.write_artifact(_ACTIVE_ID, "baseline-stdout.log", _BASELINE_ART)

    waiter1 = _manifest(_WAITER1_ID, "queued", _FLEET_REF)
    s.apply(_WAITER1_ID, lambda _r: waiter1, create=True)
    s.append_event(_WAITER1_ID, {"transition": "queued"})

    waiter2 = _manifest(_WAITER2_ID, "queued", _FARM_REF)
    s.apply(_WAITER2_ID, lambda _r: waiter2, create=True)
    s.append_event(_WAITER2_ID, {"transition": "queued"})

    failed1 = _manifest(_FAILED1_ID, "failed", _FLEET_REF, failure_type="StubStageFailure")
    s.apply(_FAILED1_ID, lambda _r: failed1, create=True)
    for status in ("queued", "running", "failed"):
        s.append_event(_FAILED1_ID, {"transition": status})
    # A distinctive event + artifact on this NON-active run (see _NONACTIVE_* above).
    s.append_event(_FAILED1_ID, {"transition": _NONACTIVE_EVENT})
    s.write_artifact(_FAILED1_ID, "transcript.log", _NONACTIVE_ART + "\n")

    failed2 = _manifest(_FAILED2_ID, "failed", _DANDD_REF, failure_type="BaselineNotGreen")
    s.apply(_FAILED2_ID, lambda _r: failed2, create=True)
    for status in ("queued", "running", "failed"):
        s.append_event(_FAILED2_ID, {"transition": status})

    with s.locked():
        s.write_queue_unlocked({"active": _ACTIVE_ID, "queue": [_WAITER1_ID, _WAITER2_ID]})


def _load_snapshot() -> tuple[dict, dict]:
    """Build ``(snapshot, events)`` the way the thin shell will: read the queue, and
    every run's manifest + event stream + persisted artifacts off disk. This is the
    ONLY bridge between the persisted store and the pure render functions.
    """
    s = store.RunStore()
    runs_dir = Path(state_root()).resolve() / "runs"
    run_ids = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
    snapshot: dict = {"queue": s.read_queue(), "runs": {}, "artifacts": {}}
    events: dict = {}
    for rid in run_ids:
        snapshot["runs"][rid] = s.read(rid)
        events[rid] = s.replay_events(rid)
        arts: dict[str, str] = {}
        for path in sorted(store.run_dir(rid).iterdir()):
            if path.is_file() and path.name not in {"manifest.json", "events.jsonl"}:
                arts[path.name] = path.read_text(encoding="utf-8")
        snapshot["artifacts"][rid] = arts
    return snapshot, events


def _permuted(snapshot: dict, events: dict) -> tuple[dict, dict]:
    """The SAME inputs, DEEP-COPIED, with the ``runs``/``artifacts``/``events`` dict
    keys rebuilt in reversed insertion order. A correct pure view derives ordering
    from the data (queue FIFO / documented stable order), so this renders
    identically; a view that leaks dict iteration order reds the permuted-input
    assertion. Deep-copied so no nested list/dict is shared with the original (a
    mutation-in-render cannot hide behind aliasing — fix-round finding 13).
    """
    snap = copy.deepcopy(snapshot)
    evt = copy.deepcopy(events)
    perm_runs = {rid: snap["runs"][rid] for rid in reversed(list(snap["runs"]))}
    perm_arts = {rid: snap["artifacts"][rid] for rid in reversed(list(snap["artifacts"]))}
    perm_events = {rid: evt[rid] for rid in reversed(list(evt))}
    perm_snapshot = {"queue": snap["queue"], "runs": perm_runs, "artifacts": perm_arts}
    return perm_snapshot, perm_events


def _assert_view_contract(render, snapshot: dict, events: dict) -> str:
    """Render once and pin the cross-cutting contract every view must satisfy:
    returns a real ``str`` (finding 14); does NOT mutate its inputs (deep-copy +
    compare, finding 13); is deterministic render-twice AND under a
    permuted-irrelevant-input (deep-copied) — the same event stream produces
    identical output. Returns the rendered text for view-specific golden checks.
    """
    snap_before = copy.deepcopy(snapshot)
    events_before = copy.deepcopy(events)

    result = render(snapshot, events)

    assert type(result) is str
    assert snapshot == snap_before, "render mutated snapshot"
    assert events == events_before, "render mutated events"
    assert render(snapshot, events) == result
    assert render(*_permuted(snapshot, events)) == result
    return result


def _line_with(text: str, token: str) -> str:
    """The SINGLE rendered line containing ``token`` (one run per line — the
    documented rendering contract), for same-line association checks.
    """
    lines = [line for line in text.splitlines() if token in line]
    assert len(lines) == 1, f"expected {token!r} on exactly one line, found {len(lines)}"
    return lines[0]


def _digest_state_tree() -> dict[str, tuple[str, int]]:
    """A content+mtime fingerprint of every file under the state root, so a test can
    prove rendering wrote NOTHING (read-only): each file maps to (sha256, mtime_ns).
    """
    root = Path(state_root()).resolve()
    out: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            out[str(path.relative_to(root))] = (
                hashlib.sha256(data).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return out


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # click >= 8.2 always separates stderr
        return CliRunner()


def _widget_text(widget) -> str:
    """The visible text a composed Textual widget carries, read headlessly (no mount,
    no TTY). Tries the common content accessors across Textual widget types
    (``content``/``renderable`` attributes, then ``render()``); layout widgets like
    Header/Footer that need an app context to render simply contribute nothing.
    """
    parts: list[str] = []
    for attr in ("content", "renderable"):
        value = getattr(widget, attr, None)
        if value:
            parts.append(str(value))
    try:
        rendered = widget.render()
    except Exception:  # a widget that needs an active app to render (Header/Footer)
        rendered = None
    if rendered is not None:
        parts.append(str(rendered))
    return " ".join(parts)


# =========================================================================== harness guards
# Unmarked = GREEN today. They guard for REAL: isolation is proven against the
# fixture value; the manifest/event shapes are proven against a REAL admission (so
# engine field drift reds the guard); the bridge is proven to carry every run's
# stream AND artifacts.


def test_harness_guard_state_home_is_exactly_this_tests_isolated_root(isolated_state_home):
    """Plain English: this suite's state lives in exactly this test's throwaway
    directory — seeding a run store can never touch the developer's real state root.

    technical (contract): ``state_root()`` (paths.py:18) resolves to the SAME path
    as the ``isolated_state_home`` fixture value (tests/conftest.py:28), not merely a
    path that happens to contain a ``state-home`` component.
    """
    assert Path(state_root()).resolve() == isolated_state_home.resolve()


def test_harness_guard_fixture_manifest_matches_a_real_admission(make_git_repo):
    """Plain English: the manifest fields the fixture writes are exactly the fields a
    REAL run admission writes — so if the engine's manifest shape drifts, this guard
    reds instead of the suite silently testing a stale shape.

    technical (contract): register a temp repo and drive ``engine.run`` with the S4
    seams (``issue_open`` -> True, a no-op ``stage``, a fixed ``new_run_id``,
    tests/test_engine.py's real pattern); the persisted manifest's KEY SET equals
    ``_manifest(...)``'s key set, and every event the real run recorded is a dict
    carrying a string ``"transition"`` (engine.py:347/351/245). Corrects the earlier
    false "closed status set" claim (findings 20/21).
    """
    from issueforge import engine

    result = _runner().invoke(app, ["repo", "add", f"DandD:{make_git_repo()}"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)

    real = engine.run(
        "DandD#148",
        issue_open=lambda _slug, _n: True,
        new_run_id=lambda: "run-realadmission",
        stage=lambda _record: None,
    )

    assert set(real) == set(_manifest("run-realadmission", "running", _DANDD_REF))

    real_events = store.RunStore().replay_events("run-realadmission")
    assert real_events, "a real admission records at least one event"
    for event in real_events:
        assert isinstance(event, dict)
        assert isinstance(event.get("transition"), str)


def test_harness_guard_seeded_events_are_transition_dicts_that_allow_extra_keys():
    """Plain English: the seeded events are real S5 event records — each a dict with
    a string ``transition`` — and the fixture deliberately includes RICH events with
    extra fields, exactly like the ones the engine emits today, so any renderer that
    assumes a single-key event is caught.

    technical (contract): the active run's replayed stream equals ``_ACTIVE_STREAM``;
    every event is a dict with a string ``"transition"``; and the stream INCLUDES a
    multi-key ``approval`` event (``{"transition": "approval", "revision": True,
    "approved": True}``, engine.py:667) and a ``revision`` event carrying
    ``completed`` (engine.py:680). The transition value is NOT constrained to a
    closed set (findings 1/10/21).
    """
    _seed_store()
    s = store.RunStore()

    active_events = s.replay_events(_ACTIVE_ID)
    assert active_events == _ACTIVE_STREAM
    for event in active_events:
        assert isinstance(event, dict)
        assert isinstance(event["transition"], str)

    rich = [e for e in active_events if set(e) != {"transition"}]
    assert {"transition": "approval", "revision": True, "approved": True} in rich
    assert any(e["transition"] == "revision" and "completed" in e for e in rich)


def test_harness_guard_snapshot_bridge_carries_every_run_stream_and_artifacts():
    """Plain English: the snapshot the views consume includes EVERY run on disk, each
    run's FULL event stream, and the active run's persisted artifacts — the bridge,
    not the views, is what this guards.

    technical (contract): ``_load_snapshot()`` returns ``snapshot["runs"]`` and
    ``events`` keyed by all FIVE seeded run ids, each ``events[id]`` non-empty
    (matching that run's ``replay_events``), and ``snapshot["artifacts"][active]``
    carries both persisted artifacts with their content (findings 22).
    """
    _seed_store()
    snapshot, events = _load_snapshot()
    all_ids = {_ACTIVE_ID, _WAITER1_ID, _WAITER2_ID, _FAILED1_ID, _FAILED2_ID}

    assert set(snapshot["runs"]) == all_ids
    assert set(events) == all_ids
    for rid in all_ids:
        assert events[rid], f"{rid} stream missing from the bridge"
    assert events[_ACTIVE_ID] == _ACTIVE_STREAM

    active_arts = snapshot["artifacts"][_ACTIVE_ID]
    assert set(active_arts) == {"stdout.log", "baseline-stdout.log"}
    assert "SENTINEL_STDOUT_TOKEN" in active_arts["stdout.log"]
    assert "SENTINEL_BASELINE_12_PASSED" in active_arts["baseline-stdout.log"]


# =========================================================================== 1. queue position view


def test_queue_view_shows_active_and_waiters_with_positions_in_fifo_order():
    """Plain English: the queue view shows which run is running now and which runs
    are waiting behind it, each with its POSITION in line, in admission order — and
    reordering the waiters reorders the view.

    technical (contract): ``render_queue_view(snapshot, events)`` names the active
    run and both waiters; the active line is distinguishable from the waiter lines;
    each WAITER's line carries its 1-based FIFO position (waiter1 -> "1", waiter2 ->
    "2", same-line association via one-run-per-line), and waiter1 precedes waiter2.
    DERIVED-from-input: rendering a snapshot whose waiter FIFO is REVERSED puts
    waiter2 at position "1" and before waiter1, and the two renders differ. Purity,
    str-type, and determinism (render-twice + permuted) via the shared contract.
    """
    from issueforge.tui import render_queue_view

    _seed_store()
    snapshot, events = _load_snapshot()

    text = _assert_view_contract(render_queue_view, snapshot, events)

    assert _ACTIVE_ID in text
    assert "1" in _line_with(text, _WAITER1_ID)
    assert "2" in _line_with(text, _WAITER2_ID)
    assert text.index(_WAITER1_ID) < text.index(_WAITER2_ID)

    reordered = copy.deepcopy(snapshot)
    reordered["queue"]["queue"] = [_WAITER2_ID, _WAITER1_ID]
    text2 = render_queue_view(reordered, events)
    assert "1" in _line_with(text2, _WAITER2_ID)
    assert text2.index(_WAITER2_ID) < text2.index(_WAITER1_ID)
    assert text2 != text


# =========================================================================== 2. current stage view


def test_stage_view_derives_active_runs_current_stage_from_its_latest_event():
    """Plain English: the current-stage view tells you what the ACTIVE run is doing
    right now — which run, which issue it is building, and the latest step it reached
    — and only the active run, never the others.

    technical (contract): ``render_stage_view(snapshot, events)`` names the active
    run, surfaces the issue it is building (``DandD`` and ``111``), and reflects the
    LATEST transition of the active stream (``revision``). Irrelevant-run FILTERING:
    the waiter and failed run ids do NOT appear. DERIVED-from-input: appending a
    DIFFERENT final event (``{"transition": "completed"}``) to the active stream
    makes the view reflect ``completed`` and changes the output — proving the current
    stage is derived from the latest event, not hardcoded (finding 6). Purity,
    str-type, determinism via the shared contract.
    """
    from issueforge.tui import render_stage_view

    _seed_store()
    snapshot, events = _load_snapshot()

    text = _assert_view_contract(render_stage_view, snapshot, events)

    assert _ACTIVE_ID in text
    assert "DandD" in text and "111" in text
    assert _ACTIVE_LATEST in text
    for other in (_WAITER1_ID, _WAITER2_ID, _FAILED1_ID, _FAILED2_ID):
        assert other not in text, f"stage view must show only the active run, leaked {other}"

    advanced = copy.deepcopy(events)
    advanced[_ACTIVE_ID].append({"transition": "completed"})
    text2 = render_stage_view(snapshot, advanced)
    assert "completed" in text2
    assert text2 != text


# =========================================================================== 3. logs view


def test_logs_view_renders_active_event_feed_and_persisted_artifacts():
    """Plain English: the logs view is the active run's running feed — its structured
    events in the order they happened AND its persisted log artifacts — for the
    active run only, and it does not choke on the rich approval/revision events.

    technical (contract): ``render_logs_view(snapshot, events)`` renders the active
    run's event feed in EMISSION ORDER (``queued`` < ``running`` < ``authoring`` <
    ``approval`` < ``revision`` by position), tolerating the multi-key ``approval``/
    ``revision`` events (finding 10), AND surfaces the persisted artifact CONTENT
    (both ``SENTINEL_STDOUT_TOKEN`` and ``SENTINEL_BASELINE_12_PASSED``, finding 27).
    Active-run SELECTION: the other runs' ids do NOT appear (finding 9).
    DERIVED-from-input: appending a distinctive event (``{"transition":
    "waiting-for-merge"}``) to the active stream makes that token appear and changes
    the output (finding 8). Purity, str-type, determinism via the shared contract.
    """
    from issueforge.tui import render_logs_view

    _seed_store()
    snapshot, events = _load_snapshot()

    text = _assert_view_contract(render_logs_view, snapshot, events)

    order = ["queued", "running", "authoring", "approval", "revision"]
    positions = [text.index(tok) for tok in order]
    assert positions == sorted(positions), f"event feed out of emission order: {positions}"
    assert "SENTINEL_STDOUT_TOKEN" in text
    assert "SENTINEL_BASELINE_12_PASSED" in text
    for other in (_WAITER1_ID, _WAITER2_ID, _FAILED1_ID, _FAILED2_ID):
        assert other not in text, f"logs view must show only the active run, leaked {other}"
    # Active-run SELECTION proven by CONTENT: a distinctive event + artifact seeded on
    # a NON-active run must NOT appear (event records carry no run id, so id-absence
    # alone does not prove selection — a label-free aggregation leaks these). Finding 3.
    assert _NONACTIVE_EVENT not in text, "logs view aggregated a non-active run's event"
    assert _NONACTIVE_ART not in text, "logs view aggregated a non-active run's artifact"

    extended = copy.deepcopy(events)
    extended[_ACTIVE_ID].append({"transition": "waiting-for-merge"})
    text2 = render_logs_view(snapshot, extended)
    assert "waiting-for-merge" in text2
    assert text2 != text


# =========================================================================== 4. failures view


def test_failures_view_lists_only_failed_runs_each_with_its_own_failure_type():
    """Plain English: the failures view lists every run that failed, each next to the
    reason IT failed, and nothing that did not fail — and it reads the same every
    time (a documented stable order), regardless of read order.

    technical (contract): ``render_failures_view(snapshot, events)`` names both
    failed runs, and each failed run's line carries ITS OWN failure type
    (``run-cccc...`` <-> ``StubStageFailure``, ``run-dddd...`` <-> ``BaselineNotGreen``,
    same-line association — defeats a reversed-association or constant-list impl,
    finding 12). Non-failed runs are ABSENT (the running active run AND both queued
    waiters, finding 11). DERIVED-from-input: flipping a currently-running run to
    ``failed`` with a new type makes it appear with that type and changes the output.
    Determinism is asserted (render-twice + permuted) but NO specific ordering is
    required (finding 23). Purity, str-type via the shared contract.
    """
    from issueforge.tui import render_failures_view

    _seed_store()
    snapshot, events = _load_snapshot()

    text = _assert_view_contract(render_failures_view, snapshot, events)

    assert "StubStageFailure" in _line_with(text, _FAILED1_ID)
    assert "BaselineNotGreen" in _line_with(text, _FAILED2_ID)
    for non_failure in (_ACTIVE_ID, _WAITER1_ID, _WAITER2_ID):
        assert non_failure not in text, f"failures view leaked non-failed run {non_failure}"

    newly = copy.deepcopy(snapshot)
    newly["runs"][_WAITER1_ID]["status"] = "failed"
    newly["runs"][_WAITER1_ID]["failure"] = {"type": "NewlyFailedType"}
    text2 = render_failures_view(newly, events)
    assert "NewlyFailedType" in _line_with(text2, _WAITER1_ID)
    assert text2 != text


# =========================================================================== read-only invariant


def test_rendering_and_the_thin_shell_write_nothing_to_the_state_store():
    """Plain English: opening the watch views — and constructing/refreshing the
    Textual shell — never changes anything on disk. Closing the TUI cannot corrupt
    persisted state because the whole watch path only reads.

    technical (contract): a content+mtime fingerprint of every file under the state
    root is IDENTICAL after (a) calling ``render_all`` and (b) constructing
    ``IssueForgeApp`` and calling ``build_frames`` (which loads state via the default
    loader and refreshes) — the shell issues no engine command and no write (findings
    15/16, scoped to the state root per the fix-round decision).
    """
    from issueforge.tui import IssueForgeApp, render_all

    _seed_store()
    snapshot, events = _load_snapshot()
    before = _digest_state_tree()

    render_all(snapshot, events)

    app_obj = IssueForgeApp()
    app_obj.build_frames()
    app_obj.build_frames()  # a refresh re-read must also not write

    assert _digest_state_tree() == before


# =========================================================================== shell seams driven


def test_shell_seams_load_state_render_all_and_app_loader_are_driven():
    """Plain English: the three thin-shell seams actually work end to end — reading
    the store, assembling the four views, and letting the app use an injected reader.

    technical (contract): ``load_state()`` reads the seeded store and returns
    ``(snapshot, events)`` in the documented shape (snapshot keyed
    ``queue``/``runs``/``artifacts`` with the active slot and its artifacts; events
    keyed by every run id with the active stream intact). ``render_all(snapshot,
    events)`` returns a dict of EXACTLY the four view keys, each byte-equal to its
    pure renderer's output. ``IssueForgeApp(loader=load_state).build_frames()`` equals
    ``render_all(*load_state())`` — the loader seam is honoured. Drives the seams the
    other tests only referenced (fix-round finding 1).
    """
    from issueforge.tui import (
        IssueForgeApp,
        load_state,
        render_all,
        render_failures_view,
        render_logs_view,
        render_queue_view,
        render_stage_view,
    )

    _seed_store()

    snapshot, events = load_state()
    assert set(snapshot) == {"queue", "runs", "artifacts"}
    assert snapshot["queue"]["active"] == _ACTIVE_ID
    assert set(events) == {_ACTIVE_ID, _WAITER1_ID, _WAITER2_ID, _FAILED1_ID, _FAILED2_ID}
    assert events[_ACTIVE_ID] == _ACTIVE_STREAM
    assert "SENTINEL_STDOUT_TOKEN" in snapshot["artifacts"][_ACTIVE_ID]["stdout.log"]

    frames = render_all(snapshot, events)
    assert set(frames) == {"queue", "stage", "logs", "failures"}
    assert frames["queue"] == render_queue_view(snapshot, events)
    assert frames["stage"] == render_stage_view(snapshot, events)
    assert frames["logs"] == render_logs_view(snapshot, events)
    assert frames["failures"] == render_failures_view(snapshot, events)

    app_obj = IssueForgeApp(loader=load_state)
    assert app_obj.build_frames() == render_all(*load_state())


# =========================================================================== thin-shell integration


def test_thin_shell_builds_all_four_views_from_the_store_and_tails_new_events():
    """Plain English: the Textual shell reads the run store, builds all four views
    from the live data, and picks up new events as they are appended (it tails the
    stream) — provable without a terminal.

    technical (contract): ``IssueForgeApp().build_frames()`` (default ``load_state``
    loader) returns a dict with EXACTLY the four view keys, each a non-empty ``str``,
    reflecting seeded content (the active run id in the stage frame, a failure type
    in the failures frame), and NO frame carries "not implemented" (the stub is gone
    — behavioral, finding 17/26). TAILING: appending a new event to the active run
    and calling ``build_frames`` again reflects it — proving the shell re-reads the
    store rather than caching (findings 16/18).
    """
    from issueforge.tui import IssueForgeApp

    _seed_store()
    app_obj = IssueForgeApp()

    frames = app_obj.build_frames()
    assert set(frames) == {"queue", "stage", "logs", "failures"}
    for name, frame in frames.items():
        assert type(frame) is str and frame.strip(), f"empty frame {name!r}"
        assert "not implemented" not in frame.lower()
    assert _ACTIVE_ID in frames["stage"]
    assert "StubStageFailure" in frames["failures"]

    store.RunStore().append_event(_ACTIVE_ID, {"transition": "waiting-for-merge"})
    frames2 = app_obj.build_frames()
    assert "waiting-for-merge" in frames2["logs"]
    assert frames2 != frames

    # compose() must MOUNT the views, not the old stub Static (finding 2). Walk the
    # widgets compose() yields headlessly (no TTY) and read their renderable text:
    # the composed tree carries the frames' content (the active run id) and never the
    # "not implemented" stub sentence.
    composed = "\n".join(_widget_text(widget) for widget in IssueForgeApp().compose())
    assert "not implemented" not in composed.lower(), "stub Static survived compose()"
    assert _ACTIVE_ID in composed, "compose() did not mount the rendered views"


# =========================================================================== tui command wires shell


def test_tui_command_wires_the_views_app_without_a_tty(monkeypatch):
    """Plain English: running ``issueforge tui`` launches the real views app — not
    the old "not implemented" stub — and it does so without needing a terminal in
    the test.

    technical (contract): stubbing ``IssueForgeApp.run`` (so no TTY is required), the
    ``tui`` CLI command constructs an ``IssueForgeApp`` and runs it (exit 0), and the
    constructed app is the real views shell: ``build_frames()`` yields the four
    views. This pins observable command BEHAVIOR, not module source text (finding 26)
    — and reds today because the wired app has no ``build_frames`` (finding 17).
    """
    import issueforge.tui as tui_mod

    captured: dict = {}

    def _fake_run(self, *args, **kwargs):
        captured["app"] = self

    monkeypatch.setattr(tui_mod.IssueForgeApp, "run", _fake_run, raising=True)

    _seed_store()
    result = _runner().invoke(app, ["tui"])
    assert result.exit_code == 0, getattr(result, "stderr", result.stdout)

    app_obj = captured["app"]
    frames = app_obj.build_frames()
    assert set(frames) == {"queue", "stage", "logs", "failures"}
