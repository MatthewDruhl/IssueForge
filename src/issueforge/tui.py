"""Read-only watch TUI for IssueForge (issue #152, S23a).

Pure render + thin shell: four pure functions map a run-store snapshot + the S5
event stream to rendered text per view, and a thin Textual ``App`` tails the store
and displays that output. The whole watch path only reads — no engine command, no
write.

Snapshot shape (built by ``load_state`` from the run store)::

    {"queue": <read_queue() dict>,
     "runs": {run_id: <read() manifest>},
     "artifacts": {run_id: {filename: text}}}

``events`` maps each run id to its ordered ``replay_events`` list. Event dicts
always carry a string ``"transition"`` and may carry extra keys (the engine emits
rich ``approval``/``revision`` events); renderers tolerate any of them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static

from issueforge import store
from issueforge.paths import state_root

_VIEW_KEYS = ("queue", "stage", "logs", "failures")


def _run_label(run_id: str, runs: dict) -> str:
    """A one-line identity for a run: its id and the issue it is building."""
    record = runs.get(run_id) or {}
    slug = record.get("slug", "")
    number = record.get("issue_number", "")
    return f"{run_id}  {slug}#{number}"


def _format_event(event: dict) -> str:
    """One event as a line: the transition, then any extra keys it carries."""
    transition = event.get("transition", "")
    extras = {key: value for key, value in event.items() if key != "transition"}
    if extras:
        return f"{transition} {extras}"
    return str(transition)


def render_queue_view(snapshot: dict, events: dict) -> str:
    """The admission queue: the active run, then each waiter with its FIFO position."""
    queue = snapshot["queue"]
    runs = snapshot["runs"]
    active = queue.get("active")
    waiters = queue.get("queue", [])

    lines = ["Queue"]
    if active is not None:
        lines.append(f"ACTIVE  {_run_label(active, runs)}")
    else:
        lines.append("ACTIVE  (none)")
    for position, run_id in enumerate(waiters, start=1):
        lines.append(f"  {position}. {_run_label(run_id, runs)}")
    return "\n".join(lines) + "\n"


def render_stage_view(snapshot: dict, events: dict) -> str:
    """The active run's current stage, derived from the latest event it recorded."""
    active = snapshot["queue"].get("active")
    lines = ["Current stage"]
    if active is None:
        lines.append("(no active run)")
        return "\n".join(lines) + "\n"

    record = snapshot["runs"].get(active) or {}
    stream = events.get(active) or []
    latest = stream[-1].get("transition") if stream else "(none)"
    lines.append(f"{active}  {record.get('slug', '')}#{record.get('issue_number', '')}")
    lines.append(f"stage: {latest}")
    return "\n".join(lines) + "\n"


def render_logs_view(snapshot: dict, events: dict) -> str:
    """The active run's feed: its structured events in emission order, then its
    persisted (already-redacted) artifacts. Active run only.
    """
    active = snapshot["queue"].get("active")
    lines = ["Logs"]
    if active is None:
        lines.append("(no active run)")
        return "\n".join(lines) + "\n"

    lines.append(f"run {active}")
    lines.append("-- events --")
    for event in events.get(active) or []:
        lines.append(_format_event(event))
    lines.append("-- artifacts --")
    artifacts = snapshot["artifacts"].get(active) or {}
    for name in sorted(artifacts):
        lines.append(f"[{name}]")
        lines.append(artifacts[name])
    return "\n".join(lines) + "\n"


def render_failures_view(snapshot: dict, events: dict) -> str:
    """Every failed run, each on its own line beside its own failure type. A
    documented stable order (run id), so read order never changes the output.
    """
    runs = snapshot["runs"]
    lines = ["Failures"]
    failed = sorted(
        run_id for run_id, record in runs.items() if (record or {}).get("status") == "failed"
    )
    for run_id in failed:
        failure = (runs[run_id].get("failure") or {}).get("type", "unknown")
        lines.append(f"{run_id}  {failure}")
    return "\n".join(lines) + "\n"


def load_state() -> tuple[dict, dict]:
    """Read the run store off disk into ``(snapshot, events)`` for the pure views.

    The ONLY bridge between the persisted store and the render functions: the queue,
    and every run's manifest, event stream, and persisted artifacts.
    """
    run_store = store.RunStore()
    runs_dir = Path(state_root()).resolve() / "runs"
    run_ids = (
        sorted(path.name for path in runs_dir.iterdir() if path.is_dir())
        if runs_dir.exists()
        else []
    )
    snapshot: dict = {"queue": run_store.read_queue(), "runs": {}, "artifacts": {}}
    events: dict = {}
    for run_id in run_ids:
        snapshot["runs"][run_id] = run_store.read(run_id)
        events[run_id] = run_store.replay_events(run_id)
        artifacts: dict[str, str] = {}
        for path in sorted(store.run_dir(run_id).iterdir()):
            if path.is_file() and path.name not in {"manifest.json", "events.jsonl"}:
                artifacts[path.name] = path.read_text(encoding="utf-8")
        snapshot["artifacts"][run_id] = artifacts
    return snapshot, events


def render_all(snapshot: dict, events: dict) -> dict[str, str]:
    """Assemble the four views into a dict, each value byte-equal to its renderer."""
    return {
        "queue": render_queue_view(snapshot, events),
        "stage": render_stage_view(snapshot, events),
        "logs": render_logs_view(snapshot, events),
        "failures": render_failures_view(snapshot, events),
    }


class IssueForgeApp(App[None]):
    """Thin read-only watch shell: re-reads the store and shows the four views."""

    TITLE = "IssueForge"
    SUB_TITLE = "Watch"

    def __init__(self, loader: Callable[[], tuple[dict, dict]] = load_state, **kwargs) -> None:
        super().__init__(**kwargs)
        self._loader = loader

    def build_frames(self) -> dict[str, str]:
        """Re-read the store through the loader and render the four views afresh."""
        return render_all(*self._loader())

    def compose(self) -> ComposeResult:
        frames = self.build_frames()
        yield Header()
        for key in _VIEW_KEYS:
            yield Static(frames[key], markup=False)
        yield Footer()
