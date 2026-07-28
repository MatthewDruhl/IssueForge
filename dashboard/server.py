"""IssueForge dashboard: stdlib HTTP server + JSON API over the real run store.

Run it with ``make dashboard`` (or ``uv run python dashboard/server.py``), then open
http://127.0.0.1:8787. Binds loopback only.

Two kinds of work reach the backend:

* **CLI actions** — allowlisted ``issueforge`` subcommands, run synchronously.
* **Jobs** — long-running subprocesses (an ``issueforge run``, or a MARVIN-era skill such as
  ``/spec-up`` driven through the ``claude`` CLI) spawned in the background with their combined
  output tailed into the browser. The skill bridge is the transitional path: it exists so the
  pipeline stays usable from here while the equivalent IssueForge stages are still being built.

Anything not on an allowlist is refused. Loopback is still a trust boundary.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from issueforge import paths, registry, store  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HOST, PORT = "127.0.0.1", 8787

# `issueforge` subcommands the dashboard may invoke synchronously. `run` is deliberately absent:
# it is long-running and goes through the job runner instead.
CLI_ACTIONS = {
    "pause": 1,
    "park": 1,
    "cancel": 1,
    "continue": 1,
    "reorder": 2,
    "queue": 0,
    "version": 0,
}
CLI_CHECKS = {
    "config-check": ["config", "check"],
    "provider-check": ["provider", "check"],
    "audit-check": ["audit", "check"],
    "lint-boundary": ["lint", "boundary"],
    "repo-list": ["repo", "list"],
    "repo-add": ["repo", "add"],
}
# Slash commands the skill bridge may launch through the `claude` CLI.
SKILLS = {"/spec-up", "/spec-dev", "/spec-wave", "/tdd", "/commit", "/merged", "/code-review"}
ARG_OK = re.compile(r"^[A-Za-z0-9 #,._/:-]{0,200}$")

JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _claude() -> str | None:
    """The `claude` binary: PATH first, then the standard user install location."""
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


# --- data -------------------------------------------------------------------


def _runs() -> list[dict]:
    root = paths.state_root() / "runs"
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        try:
            record = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append(
            {
                k: record.get(k)
                for k in (
                    "run_id",
                    "status",
                    "repo",
                    "slug",
                    "issue_number",
                    "stage",
                    "branch",
                    "pr_url",
                    "updated_at",
                )
            }
            | {"dir": d.name}
        )
    out.sort(key=lambda r: (r.get("updated_at") or "", r.get("run_id") or ""), reverse=True)
    return out


def _repos() -> list[dict]:
    try:
        return [
            {
                "alias": e.alias,
                "path": str(e.path),
                "slug": getattr(e, "slug", None),
                "default_branch": getattr(e, "default_branch", None),
            }
            for e in registry.Registry.load().entries()
        ]
    except Exception as exc:  # a missing/corrupt registry is data, not a crash
        return [{"alias": "(registry unreadable)", "path": str(exc)}]


def _run_detail(run_id: str) -> dict:
    d = paths.run_dir(run_id)
    manifest, events, artifacts = {}, [], []
    try:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        manifest = {"error": str(exc)}
    try:
        events = [
            json.loads(line)
            for line in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, ValueError):
        pass
    art = d / "artifacts"
    if art.is_dir():
        artifacts = sorted(p.name for p in art.iterdir())
    return {"manifest": manifest, "events": events, "artifacts": artifacts}


def _state() -> dict:
    try:
        queue = store.RunStore().read_queue()
    except Exception as exc:
        queue = {"active": None, "queue": [], "error": str(exc)}
    with _JOBS_LOCK:
        jobs = [{k: j[k] for k in ("id", "label", "cmd", "status", "rc")} for j in JOBS.values()][
            ::-1
        ]
    return {
        "repos": _repos(),
        "queue": queue,
        "runs": _runs(),
        "jobs": jobs,
        "state_root": str(paths.state_root()),
        "claude": _claude(),
    }


# --- actions ----------------------------------------------------------------


def _issueforge(args: list[str]) -> dict:
    proc = subprocess.run(
        ["uv", "run", "issueforge", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {
        "rc": proc.returncode,
        "out": proc.stdout + proc.stderr,
        "cmd": "issueforge " + " ".join(args),
    }


def _cli(body: dict) -> dict:
    action = body.get("action", "")
    args = [str(a) for a in body.get("args", [])]
    if any(not ARG_OK.match(a) for a in args):
        return {"rc": 2, "out": "refused: argument contains disallowed characters"}
    if action in CLI_CHECKS:
        return _issueforge([*CLI_CHECKS[action], *args])
    if action in CLI_ACTIONS:
        if len(args) != CLI_ACTIONS[action]:
            return {"rc": 2, "out": f"refused: {action} takes {CLI_ACTIONS[action]} argument(s)"}
        return _issueforge([action, *args])
    return {"rc": 2, "out": f"refused: {action!r} is not an allowlisted action"}


def _spawn(label: str, argv: list[str], cwd: Path) -> dict:
    job_id = uuid.uuid4().hex[:12]
    log = Path(tempfile.gettempdir()) / f"issueforge-dash-{job_id}.log"
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        argv, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True
    )
    job = {
        "id": job_id,
        "label": label,
        "cmd": shlex.join(argv),
        "status": "running",
        "rc": None,
        "log": str(log),
        "proc": proc,
    }
    with _JOBS_LOCK:
        JOBS[job_id] = job

    def wait() -> None:
        rc = proc.wait()
        handle.close()
        job["rc"], job["status"] = rc, ("ok" if rc == 0 else "failed")

    threading.Thread(target=wait, daemon=True).start()
    return {k: job[k] for k in ("id", "label", "cmd", "status", "rc")}


def _launch(body: dict) -> dict:
    kind = body.get("kind")
    arg = str(body.get("arg", "")).strip()
    if not ARG_OK.match(arg):
        return {"error": "refused: argument contains disallowed characters"}

    if kind == "skill":
        skill = body.get("skill", "")
        if skill not in SKILLS:
            return {"error": f"refused: {skill!r} is not an allowlisted skill"}
        claude = _claude()
        if not claude:
            return {"error": "the `claude` CLI was not found on PATH"}
        cwd = Path(body.get("cwd") or REPO)
        if not cwd.is_dir():
            return {"error": f"no such directory: {cwd}"}
        prompt = f"{skill} {arg}".strip()
        return _spawn(prompt, [claude, "-p", prompt], cwd)

    if kind == "run":
        # `issueforge run` answers both human gates up front when headless (#140).
        scope = [s for s in (body.get("scope") or []) if ARG_OK.match(str(s))]
        if not arg or not scope:
            return {"error": "run needs an ALIAS#N and at least one --scope path"}
        argv = ["uv", "run", "issueforge", "run", arg]
        for s in scope:
            argv += ["--scope", str(s)]
        if body.get("yes"):
            argv.append("--yes")
        return _spawn(f"run {arg}", argv, REPO)

    if kind == "make":
        target = body.get("target")
        if target not in {"test-quick", "test", "gate"}:
            return {"error": f"refused: make {target!r} is not allowlisted"}
        return _spawn(f"make {target}", ["make", target], REPO)

    return {"error": f"refused: unknown job kind {kind!r}"}


def _job_tail(job_id: str, offset: int) -> dict:
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return {"error": "no such job"}
    try:
        text = Path(job["log"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return {
        "status": job["status"],
        "rc": job["rc"],
        "offset": len(text),
        "chunk": text[offset:],
        "cmd": job["cmd"],
    }


def _job_stop(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return {"error": "no such job"}
    if job["status"] == "running":
        job["proc"].terminate()
    return {"ok": True}


# --- http -------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter console
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            return self._send(_state())
        if path.startswith("/api/run/"):
            return self._send(_run_detail(path.rsplit("/", 1)[-1]))
        if path.startswith("/api/job/"):
            return self._send(_job_tail(path.rsplit("/", 1)[-1], int(query.get("offset", [0])[0])))
        self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send({"error": "bad json"}, 400)
        path = urlparse(self.path).path
        if path == "/api/cli":
            return self._send(_cli(body))
        if path == "/api/launch":
            return self._send(_launch(body))
        if path.startswith("/api/job/") and path.endswith("/stop"):
            return self._send(_job_stop(path.split("/")[3]))
        self._send({"error": "not found"}, 404)


def main() -> None:
    print(f"IssueForge dashboard → http://{HOST}:{PORT}  (state: {paths.state_root()})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
