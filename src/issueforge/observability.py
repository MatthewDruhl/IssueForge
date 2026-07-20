"""Deterministic observability evidence (issue #10, slice S8).

This slice owns the DETERMINISTIC evidence APIs S9 consumes and S15 enforces: the two
boundary classifiers (pre-authoring and post-diff) plus reconciliation against the
fixed, curated marker tuple; the G3 static check that library code never installs
global logging configuration; a target logger-convention detector (US-6.9: detected,
never imposed); and a runtime log-capture evidence collector with a sensitive-value
exclusion predicate.

Out of scope: the observability VERDICT itself (S9), redaction of IssueForge's own
artifacts (S4), and the enforcement call site (S15). No AI judgment is exercised here.
"""

from __future__ import annotations

import ast
import enum
import logging
from dataclasses import dataclass
from pathlib import Path


class BoundaryCategory(enum.Enum):
    """The closed set of seven boundary categories the v1 marker tuple detects."""

    HTTP = "HTTP"
    DATABASE = "DATABASE"
    SUBPROCESS = "SUBPROCESS"
    FILESYSTEM = "FILESYSTEM"
    QUEUE = "QUEUE"
    THIRD_PARTY = "THIRD_PARTY"
    AI = "AI"


# The fixed, curated v1 marker contract. Must equal the acceptance suite's GOLDEN_MARKERS
# exactly. Not a rule engine, not user-configurable, not read from the environment.
BOUNDARY_MARKERS: dict[BoundaryCategory, tuple[str, ...]] = {
    BoundaryCategory.SUBPROCESS: (
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "pty.spawn",
    ),
    BoundaryCategory.FILESYSTEM: (
        "open",
        "io.open",
        "os.open",
        "os.remove",
        "os.unlink",
        "os.mkdir",
        "os.makedirs",
        "pathlib.Path.open",
        "pathlib.Path.read_text",
        "pathlib.Path.read_bytes",
        "pathlib.Path.write_text",
        "pathlib.Path.write_bytes",
        "pathlib.Path.unlink",
        "pathlib.Path.mkdir",
        "shutil.copy",
        "shutil.move",
        "shutil.rmtree",
        "tempfile.NamedTemporaryFile",
        "tempfile.mkstemp",
    ),
    BoundaryCategory.HTTP: (
        "socket.socket",
        "socket.create_connection",
        "http.client.HTTPConnection",
        "http.client.HTTPSConnection",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "requests.request",
        "requests.Session",
        "httpx.get",
        "httpx.post",
        "httpx.request",
        "httpx.Client",
        "aiohttp.ClientSession",
    ),
    BoundaryCategory.DATABASE: (
        "sqlite3.connect",
        "psycopg.connect",
        "psycopg2.connect",
        "pymysql.connect",
        "asyncpg.connect",
        "sqlalchemy.create_engine",
    ),
    BoundaryCategory.QUEUE: (
        "queue.Queue",
        "queue.SimpleQueue",
        "multiprocessing.Queue",
        "pika.BlockingConnection",
        "kombu.Connection",
        "celery.Celery",
    ),
    BoundaryCategory.THIRD_PARTY: (
        "boto3.client",
        "boto3.resource",
        "botocore.session.Session",
        "stripe.Charge",
        "stripe.PaymentIntent",
        "twilio.rest.Client",
        "sendgrid.SendGridAPIClient",
        "googleapiclient.discovery.build",
    ),
    BoundaryCategory.AI: ("issueforge.providers.invoke",),
}


@dataclass(frozen=True)
class BoundaryEvidence:
    """One concrete boundary crossing: the category, its location, and the matched marker."""

    category: BoundaryCategory
    location: str
    marker: str


@dataclass(frozen=True)
class BoundaryVerdict:
    """The structured result of a boundary classification."""

    categories: frozenset[BoundaryCategory]
    evidence: tuple[BoundaryEvidence, ...]


# --- AST alias resolution (mirrors issueforge.boundary._Linter's canonical-name pattern) ------


class _AliasResolver(ast.NodeVisitor):
    """Walk a module tracking import aliases and yield (dotted-call-text, canonical-name) pairs."""

    def __init__(self) -> None:
        self.binding_scopes: list[dict[str, str | None]] = [{}]
        self.calls: list[tuple[ast.Call, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            local = alias.asname or root
            self.binding_scopes[-1][local] = alias.name if alias.asname else root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.binding_scopes[-1][local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def _canonical(self, dotted: str) -> str:
        first, separator, rest = dotted.partition(".")
        bound = first
        for scope in reversed(self.binding_scopes):
            if first in scope:
                bound = scope[first] or first
                break
        return bound + (separator + rest if separator else "")

    def visit_Call(self, node: ast.Call) -> None:
        dotted = ast.unparse(node.func)
        canonical = self._canonical(dotted)
        self.calls.append((node, canonical))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.binding_scopes.append({})
        self.generic_visit(node)
        self.binding_scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def _match_source(source: str, location: str) -> list[BoundaryEvidence]:
    """Match executable calls (never prose/comments/strings) in ``source`` against BOUNDARY_MARKERS."""
    import textwrap

    try:
        tree = ast.parse(source)
    except SyntaxError:
        try:
            # A diff's added lines may carry a uniform extra indent; dedent before re-trying so a
            # syntactically valid snippet still parses (raw, unindentable fragments still fail closed).
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError:
            return []
    resolver = _AliasResolver()
    resolver.visit(tree)

    marker_lookup: dict[str, BoundaryCategory] = {}
    for category, markers in BOUNDARY_MARKERS.items():
        for marker in markers:
            marker_lookup[marker] = category

    found: list[BoundaryEvidence] = []
    for _node, canonical in resolver.calls:
        # `open` is special: a bare Name call, not an attribute chain.
        candidates = {canonical}
        if canonical.rsplit(".", 1)[-1] == "open":
            candidates.add("open")
        for candidate in candidates:
            if candidate in marker_lookup:
                found.append(
                    BoundaryEvidence(
                        category=marker_lookup[candidate], location=location, marker=candidate
                    )
                )
                break
    return found


def _verdict_from_evidence(evidence: list[BoundaryEvidence]) -> BoundaryVerdict:
    categories = frozenset(e.category for e in evidence)
    return BoundaryVerdict(categories=categories, evidence=tuple(evidence))


def classify_prospective(
    issue_text: str, footprint_paths: list[Path], repo_root: Path
) -> BoundaryVerdict:
    """Classify the boundary crossings already present in the footprint files (pre-authoring).

    Deterministic AST match only — the issue prose never adds a category, and marker text in a
    comment or string literal never matches.
    """
    del issue_text, repo_root
    evidence: list[BoundaryEvidence] = []
    for path in footprint_paths:
        path = Path(path)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        evidence.extend(_match_source(source, str(path)))
    return _verdict_from_evidence(evidence)


def classify_diff(diff_text: str) -> BoundaryVerdict:
    """Classify the boundary crossings introduced by a unified diff's ADDED lines only."""
    added_lines: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added_lines.append(line[1:])
    source = "\n".join(added_lines)
    evidence = _match_source(source, "<diff>")
    return _verdict_from_evidence(evidence)


class UnanticipatedBoundary(Exception):
    """A diff crosses a boundary the approved verdict never anticipated (deterministic, non-waivable)."""


def reconcile(approved: BoundaryVerdict, actual: BoundaryVerdict) -> None:
    """Return None when every boundary ``actual`` crosses was already approved, else raise.

    The signature is exactly ``(approved, actual)`` — there is no override parameter; the halt is
    deterministic and not waivable (S15 owns enforcement).
    """
    unanticipated = actual.categories - approved.categories
    if unanticipated:
        names = ", ".join(sorted(c.name.lower() for c in unanticipated))
        raise UnanticipatedBoundary(f"unanticipated boundary crossings: {names}")


# --- G3: libraries never install global logging configuration ---------------------------------


@dataclass(frozen=True)
class GlobalLoggingViolation:
    """A library file that installs global logging configuration."""

    location: str
    message: str


class _GlobalLoggingVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.binding_scopes: list[dict[str, str | None]] = [{}]
        self.violations: list[GlobalLoggingViolation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            local = alias.asname or root
            self.binding_scopes[-1][local] = alias.name if alias.asname else root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.binding_scopes[-1][local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def _canonical(self, dotted: str) -> str:
        first, separator, rest = dotted.partition(".")
        bound = first
        for scope in reversed(self.binding_scopes):
            if first in scope:
                bound = scope[first] or first
                break
        return bound + (separator + rest if separator else "")

    def _add(self, node: ast.AST, message: str) -> None:
        lineno = getattr(node, "lineno", 0)
        self.violations.append(
            GlobalLoggingViolation(location=f"{self.filename}:{lineno}", message=message)
        )

    def visit_Call(self, node: ast.Call) -> None:
        dotted = ast.unparse(node.func)
        canonical = self._canonical(dotted)
        if canonical == "logging.basicConfig":
            self._add(node, "logging.basicConfig installs global logging configuration")
        elif canonical == "logging.getLogger" and not node.args and not node.keywords:
            self._add(node, "logging.getLogger() with no name binds the root logger")
        elif canonical.endswith(".addHandler"):
            target = ast.unparse(node.func).rsplit(".addHandler", 1)[0]
            if self._canonical(target) == "logging.root" or self._is_root_getlogger(node.func):
                self._add(node, "addHandler on the root logger mutates global logging state")
        self.generic_visit(node)

    def _is_root_getlogger(self, func: ast.AST) -> bool:
        if not isinstance(func, ast.Attribute):
            return False
        value = func.value
        if not isinstance(value, ast.Name):
            return False
        return self.binding_scopes[-1].get(value.id) == "__root_getlogger__" or any(
            scope.get(value.id) == "__root_getlogger__" for scope in self.binding_scopes
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            canonical = self._canonical(ast.unparse(node.value.func))
            if canonical == "logging.getLogger" and not node.value.args and not node.value.keywords:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.binding_scopes[-1][target.id] = "__root_getlogger__"
        self.generic_visit(node)


def check_global_logging(source_paths: list[Path]) -> list[GlobalLoggingViolation]:
    """Return G3 violations: library code installing global logging configuration.

    Flags ``logging.basicConfig(...)`` (including via an aliased import), a no-argument root
    ``logging.getLogger()``, and any root-handler mutation (``root.addHandler(...)``,
    ``logging.root.handlers.append(...)``). A named ``getLogger(__name__)`` and a named logger's
    own ``addHandler`` are clean.
    """
    violations: list[GlobalLoggingViolation] = []
    for path in source_paths:
        path = Path(path)
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        visitor = _GlobalLoggingVisitor(path.name)
        visitor.visit(tree)
        violations.extend(_check_handlers_append(source, path.name))
        violations.extend(visitor.violations)
    return violations


def _check_handlers_append(source: str, filename: str) -> list[GlobalLoggingViolation]:
    """Catch ``logging.root.handlers.append(...)`` / aliased-root-handlers.append style mutation."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    resolver = _AliasResolver()
    resolver.visit(tree)
    out: list[GlobalLoggingViolation] = []
    for node, canonical in resolver.calls:
        if canonical.endswith(".handlers.append") or canonical == "logging.root.handlers.append":
            base = canonical.rsplit(".append", 1)[0]
            if base in ("logging.root.handlers",) or base.startswith("logging.root."):
                out.append(
                    GlobalLoggingViolation(
                        location=f"{filename}:{node.lineno}",
                        message="root logger handlers mutated directly",
                    )
                )
    return out


# --- Logger-convention detector: reads the target, never imposes -------------------------------


@dataclass(frozen=True)
class LoggerConvention:
    """The target's own detected logging conventions."""

    factory: str | None
    levels: frozenset[str]
    format: str | None
    correlation_key: str | None


_LOG_LEVELS = {"debug", "info", "warning", "error", "critical", "exception"}


def detect_logger_convention(repo_root: Path) -> LoggerConvention:
    """Detect the target's logging conventions from its source: factory, levels, format, correlation."""
    repo_root = Path(repo_root)
    factory: str | None = None
    levels: set[str] = set()
    fmt: str | None = None

    for path in sorted(repo_root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        resolver = _AliasResolver()
        resolver.visit(tree)
        for node, canonical in resolver.calls:
            if canonical == "logging.getLogger" and factory is None:
                factory = "logging.getLogger"
            if canonical == "logging.Formatter" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    fmt = first.value
            attr = canonical.rsplit(".", 1)[-1]
            if attr in _LOG_LEVELS and "." in canonical:
                base = canonical.rsplit(".", 1)[0]
                # Heuristic: a call like `logger.info(...)` / `log.error(...)` on a bound logger
                # variable, not e.g. `logging.info`.
                if base not in ("logging",):
                    levels.add(attr)

    correlation_key: str | None = None
    if fmt:
        import re

        fields = re.findall(r"%\((\w+)\)s", fmt)
        for field in fields:
            if field not in ("asctime", "message", "levelname", "name", "created"):
                correlation_key = field
                break

    return LoggerConvention(
        factory=factory, levels=frozenset(levels), format=fmt, correlation_key=correlation_key
    )


# --- Runtime log capture + sensitive predicate + evidence collector ----------------------------


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def capture_logs(fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` once, returning ``(result, records)``; installs no lasting handler."""
    root = logging.getLogger()
    previous_level = root.level
    handler = _ListHandler()
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        result = fn(*args, **kwargs)
        return result, list(handler.records)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


def sensitive_values_present(records: list[logging.LogRecord], sensitive: set[str]) -> set[str]:
    """Return the subset of ``sensitive`` present in any record's fully-formatted message."""
    found: set[str] = set()
    for record in records:
        message = record.getMessage()
        for value in sensitive:
            if value in message:
                found.add(value)
    return found


@dataclass(frozen=True)
class LogEvidence:
    """The authoritative deterministic observability evidence from a success/failure run pair."""

    events_emitted: frozenset[str]
    leaked: frozenset[str]


def collect_log_evidence(
    success_path,
    failure_path,
    *,
    required_events: set[str],
    sensitive_fields: set[str],
    canaries: set[str],
) -> LogEvidence:
    """Run both callables capturing logs; the failure path may raise (captured, not propagated)."""
    _, success_records = capture_logs(success_path)

    failure_records: list[logging.LogRecord] = []
    root = logging.getLogger()
    previous_level = root.level
    handler = _ListHandler()
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        failure_path()
    except Exception:
        pass
    finally:
        failure_records = list(handler.records)
        root.removeHandler(handler)
        root.setLevel(previous_level)

    all_records = success_records + failure_records
    all_messages = [r.getMessage() for r in all_records]

    events_emitted = frozenset(
        event for event in required_events if any(event in message for message in all_messages)
    )
    leaked = frozenset(sensitive_values_present(all_records, sensitive_fields | canaries))

    return LogEvidence(events_emitted=events_emitted, leaked=leaked)
