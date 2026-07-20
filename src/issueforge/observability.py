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
import re
import textwrap
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


# ---------------------------------------------------------------------------
# AST call collector: alias resolution + shadowing + Path-receiver awareness
# (mirrors issueforge.boundary._Linter's canonical-name pattern, extended so a
# shadowed name and an arbitrary receiver never produce a false match)
# ---------------------------------------------------------------------------

_PATH_INSTANCE = object()  # sentinel: a name provably bound to a pathlib.Path instance
_PATH_METHODS = frozenset(
    {"open", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "mkdir"}
)


class _CallCollector(ast.NodeVisitor):
    """Collect every call with the canonical marker candidates it could match.

    Tracks import aliases AND local bindings that SHADOW a module name (parameters, local
    ``def``/``class``, assignments, ``for``/``with``/``except as`` targets) so a shadowed name
    resolves to nothing. Attribute calls resolve their receiver: a ``pathlib.Path`` method only
    matches when the receiver is provably a ``Path(...)`` construction or a name bound to one —
    never an arbitrary receiver like ``client.open()``.
    """

    def __init__(self, seed: dict[str, object] | None = None) -> None:
        # ``seed`` pre-populates the module scope with aliases established elsewhere in the same
        # file (e.g. an ``import`` in an earlier diff hunk), so a later hunk's call resolves it.
        self.scopes: list[dict[str, object]] = [dict(seed) if seed else {}]
        self.calls: list[tuple[ast.Call, frozenset[str]]] = []

    # --- scope + resolution -------------------------------------------------
    def _resolve(self, name: str) -> object:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return name

    def _resolve_dotted(self, node: ast.AST) -> str | None:
        """Canonical dotted name for a Name / pure attribute-chain-of-Names, else None."""
        attrs: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        val = self._resolve(cur.id)
        if not isinstance(val, str):  # None (shadowed) or _PATH_INSTANCE
            return None
        attrs.reverse()
        return ".".join([val, *attrs])

    def _is_path_constructor(self, func: ast.AST) -> bool:
        return self._resolve_dotted(func) == "pathlib.Path"

    def _is_path_instance(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            return self._is_path_constructor(node.func)
        if isinstance(node, ast.Name):
            return self._resolve(node.id) is _PATH_INSTANCE
        return False

    def _binding_for_value(self, value: ast.AST | None) -> object:
        if isinstance(value, ast.Call) and self._is_path_constructor(value.func):
            return _PATH_INSTANCE
        return None  # anything else shadows an import of the same name

    def _record_targets(self, target: ast.AST, binding: object) -> None:
        if isinstance(target, ast.Name):
            self.scopes[-1][target.id] = binding
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._record_targets(element, None)
        elif isinstance(target, ast.Starred):
            self._record_targets(target.value, None)
        # Attribute / Subscript targets do not rebind a bare name.

    # --- imports ------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            local = alias.asname or root
            self.scopes[-1][local] = alias.name if alias.asname else root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.scopes[-1][local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    # --- calls --------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        candidates: set[str] = set()
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _PATH_METHODS
            and self._is_path_instance(func.value)
        ):
            candidates.add(f"pathlib.Path.{func.attr}")
        dotted = self._resolve_dotted(func)
        if dotted is not None:
            candidates.add(dotted)
        self.calls.append((node, frozenset(candidates)))
        self.generic_visit(node)

    # --- scopes + shadowing -------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scopes[-1][node.name] = None  # a local def shadows a same-named import/builtin
        self.scopes.append({})
        for arg in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            self.scopes[-1][arg.arg] = None
        if node.args.vararg:
            self.scopes[-1][node.args.vararg.arg] = None
        if node.args.kwarg:
            self.scopes[-1][node.args.kwarg.arg] = None
        self.generic_visit(node)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.scopes.append({})
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.scopes[-1][arg.arg] = None
        if node.args.vararg:
            self.scopes[-1][node.args.vararg.arg] = None
        if node.args.kwarg:
            self.scopes[-1][node.args.kwarg.arg] = None
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes[-1][node.name] = None
        self.scopes.append({})
        self.generic_visit(node)
        self.scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        binding = self._binding_for_value(node.value)
        for target in node.targets:
            self._record_targets(target, binding)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        binding = self._binding_for_value(node.value) if node.value is not None else None
        if isinstance(node.target, ast.Name):
            self.scopes[-1][node.target.id] = binding
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record_targets(node.target, None)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._record_targets(node.target, self._binding_for_value(node.value))
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._record_targets(node.target, None)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._record_targets(item.optional_vars, None)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.scopes[-1][node.name] = None
        self.generic_visit(node)


def _safe_parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        try:
            return ast.parse(textwrap.dedent(source))
        except SyntaxError:
            return None


def _collect_calls(
    tree: ast.AST, seed: dict[str, object] | None = None
) -> list[tuple[ast.Call, frozenset[str]]]:
    collector = _CallCollector(seed)
    collector.visit(tree)
    return collector.calls


def _evidence_from_calls(
    calls: list[tuple[ast.Call, frozenset[str]]], location: str
) -> list[BoundaryEvidence]:
    """Match collected calls against the CURRENT ``BOUNDARY_MARKERS`` (read at call time)."""
    lookup: dict[str, BoundaryCategory] = {}
    for category, markers in BOUNDARY_MARKERS.items():
        for marker in markers:
            lookup[marker] = category

    found: list[BoundaryEvidence] = []
    for _node, candidates in calls:
        for candidate in sorted(candidates):
            if candidate in lookup:
                found.append(
                    BoundaryEvidence(
                        category=lookup[candidate], location=location, marker=candidate
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
    for raw_path in footprint_paths:
        path = Path(raw_path)
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        tree = _safe_parse(source)
        if tree is None:
            continue
        evidence.extend(_evidence_from_calls(_collect_calls(tree), str(path)))
    return _verdict_from_evidence(evidence)


def _try_parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _parse_diff_hunks(diff_text: str) -> list[tuple[str, list[tuple[str, bool]]]]:
    """Split a unified diff into (file_path, hunk_fragment) pairs.

    Metadata lines (``diff --git``, ``index``, ``--- ``, ``+++ ``, ``@@``, ``\\``) are skipped and
    never parsed. Each hunk fragment is the NEW-side sequence of ``(content, is_added)`` — context
    (``  ``) and added (``+``) lines in order, with removed (``-``) lines dropped — so an alias in
    unchanged context is retained. Files are kept separate (no cross-file bleed).
    """
    hunks: list[tuple[str, list[tuple[str, bool]]]] = []
    path = "<diff>"
    fragment: list[tuple[str, bool]] | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 4 and parts[-1].startswith("b/"):
                path = parts[-1][2:]
            fragment = None
            continue
        if line.startswith("+++ "):
            candidate = line[4:].strip()
            if candidate.startswith("b/"):
                candidate = candidate[2:]
            if candidate and candidate != "/dev/null":
                path = candidate
            fragment = None
            continue
        if line.startswith("--- ") or line.startswith("index ") or line.startswith("diff "):
            continue
        if line.startswith("@@"):
            fragment = []
            hunks.append((path, fragment))
            continue
        if fragment is None:
            continue
        if line.startswith("\\"):  # "\\ No newline at end of file"
            continue
        if line.startswith("+"):
            fragment.append((line[1:], True))
        elif line.startswith("-"):
            continue
        else:  # context line (leading space or a blank line)
            fragment.append((line[1:] if line.startswith(" ") else line, False))
    return hunks


def _hunk_evidence(
    fragment: list[tuple[str, bool]], location: str, seed: dict[str, object] | None = None
) -> list[BoundaryEvidence]:
    """Match a single hunk's ADDED-line calls, resolving aliases against its retained context.

    The fragment is parsed as-is; if it does not parse standalone (a hunk is often an indented
    body fragment) it is dedented, and failing that wrapped in a synthetic function so an indented
    body parses. The context lines are ALWAYS retained (never degraded to added-lines-only) so a
    context-defined alias still resolves; a marker counts only on an added line. ``seed`` carries the
    file's imports from its OTHER hunks so a cross-hunk alias resolves too.
    """
    contents = [content for content, _ in fragment]
    added_at = {index + 1 for index, (_, is_added) in enumerate(fragment) if is_added}
    raw = "\n".join(contents)

    tree = _try_parse(raw)
    if tree is not None:
        return _filter_added(tree, added_at, location, seed)

    dedented = textwrap.dedent(raw)
    tree = _try_parse(dedented)
    if tree is not None:  # dedent preserves the line-to-added mapping
        return _filter_added(tree, added_at, location, seed)

    indented_body = "\n".join(
        ("    " + line) if line.strip() else line for line in dedented.split("\n")
    )
    wrapped = "def __hunk__():\n" + indented_body
    tree = _try_parse(wrapped)
    if tree is not None:  # the synthetic def shifts every body line down by one
        return _filter_added(tree, {lineno + 1 for lineno in added_at}, location, seed)
    return []


def _file_import_seed(fragments: list[list[tuple[str, bool]]]) -> dict[str, object]:
    """Alias map of the imports across ALL of a file's hunks (context + added lines).

    A module-level import at the top of a file is in scope everywhere below it, but a diff splits
    the file into separate hunks; collecting the file's import aliases and seeding every hunk's
    analysis with them lets an alias imported in one hunk resolve a call in another hunk of the SAME
    file. Removed lines are already dropped from the fragments, so a deleted import never seeds.

    ONLY module-level (column-0) imports seed the file: a FUNCTION-LOCAL import (indented) is scoped
    to its own body and must not leak an alias into an unrelated function elsewhere in the file.
    """
    import_lines = [
        content
        for fragment in fragments
        for content, _is_added in fragment
        if content[:1] not in (" ", "\t")  # module-level only (no leading indentation)
        and content.startswith(("import ", "from "))
    ]
    if not import_lines:
        return {}
    collector = _CallCollector()
    tree = _try_parse("\n".join(import_lines))
    if tree is not None:
        collector.visit(tree)
    else:  # a stray unparseable line: fall back to importing each line independently
        for line in import_lines:
            line_tree = _try_parse(line)
            if line_tree is not None:
                collector.visit(line_tree)
    return dict(collector.scopes[0])


def _filter_added(
    tree: ast.AST, added_linenos: set[int], location: str, seed: dict[str, object] | None = None
) -> list[BoundaryEvidence]:
    calls = [
        (node, candidates)
        for node, candidates in _collect_calls(tree, seed)
        if node.lineno in added_linenos
    ]
    return _evidence_from_calls(calls, location)


def classify_diff(diff_text: str) -> BoundaryVerdict:
    """Classify the boundary crossings introduced by a unified diff's ADDED lines only.

    The diff is parsed per file and per hunk; each hunk's added-line calls are matched with its
    unchanged context retained AND with the file's imports from its other hunks seeded, so aliases
    resolve across hunks. Removed boundaries and added comments/strings never count, and files never
    bleed into one another (the import seed is per file).
    """
    by_file: dict[str, list[list[tuple[str, bool]]]] = {}
    for path, fragment in _parse_diff_hunks(diff_text):
        by_file.setdefault(path, []).append(fragment)

    evidence: list[BoundaryEvidence] = []
    for path, fragments in by_file.items():
        seed = _file_import_seed(fragments)
        for fragment in fragments:
            if not any(is_added for _, is_added in fragment):
                continue
            evidence.extend(_hunk_evidence(fragment, path, seed))
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


# ---------------------------------------------------------------------------
# G3: libraries never install global logging configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlobalLoggingViolation:
    """A library file that installs global logging configuration."""

    location: str
    message: str


_HANDLERS_MUTATORS = frozenset({"append", "extend", "insert", "remove", "clear"})


class _G3Visitor(ast.NodeVisitor):
    """Flag global logging configuration: basicConfig, no-arg root getLogger, root-handler mutation.

    Tracks the ``logging`` import alias and every name bound to a root logger (through regular and
    annotated assignments) so a root reached indirectly is still caught, while a NAMED logger and
    its own handler stay clean.
    """

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.imports: dict[str, str] = {}
        self.root_names: set[str] = set()
        self.violations: list[GlobalLoggingViolation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            local = alias.asname or root
            self.imports[local] = alias.name if alias.asname else root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.imports[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def _canon(self, node: ast.AST) -> str | None:
        attrs: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        head = self.imports.get(cur.id, cur.id)
        attrs.reverse()
        return ".".join([head, *attrs])

    def _no_arg_getlogger(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and self._canon(node.func) == "logging.getLogger"
            and not node.args
            and not node.keywords
        )

    def _is_root_expr(self, node: ast.AST) -> bool:
        if self._no_arg_getlogger(node):
            return True
        if self._canon(node) == "logging.root":
            return True
        return isinstance(node, ast.Name) and node.id in self.root_names

    def _add(self, node: ast.AST, message: str) -> None:
        lineno = getattr(node, "lineno", 0)
        self.violations.append(
            GlobalLoggingViolation(location=f"{self.filename}:{lineno}", message=message)
        )

    def visit_Call(self, node: ast.Call) -> None:
        canonical = self._canon(node.func)
        if canonical == "logging.basicConfig":
            self._add(node, "logging.basicConfig installs global logging configuration")
        if self._no_arg_getlogger(node):
            self._add(node, "no-argument logging.getLogger() binds the root logger")
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver = node.func.value
            if method in ("addHandler", "removeHandler") and self._is_root_expr(receiver):
                self._add(node, f"{method} on the root logger mutates global logging state")
            if (
                method in _HANDLERS_MUTATORS
                and isinstance(receiver, ast.Attribute)
                and receiver.attr == "handlers"
                and self._is_root_expr(receiver.value)
            ):
                self._add(node, f"root logger handlers.{method}() mutates global logging state")
        self.generic_visit(node)

    def _track_binding(self, target: ast.AST, value: ast.AST | None) -> None:
        if not isinstance(target, ast.Name):
            return
        if value is not None and self._is_root_expr(value):
            self.root_names.add(target.id)
        else:
            self.root_names.discard(target.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "handlers"
                and self._is_root_expr(target.value)
            ):
                self._add(node, "assignment to the root logger's handlers list")
        for target in node.targets:
            self._track_binding(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Attribute)
            and node.target.attr == "handlers"
            and self._is_root_expr(node.target.value)
        ):
            self._add(node, "assignment to the root logger's handlers list")
        self._track_binding(node.target, node.value)
        self.generic_visit(node)


def check_global_logging(source_paths: list[Path]) -> list[GlobalLoggingViolation]:
    """Return G3 violations: library code installing global logging configuration.

    Flags ``logging.basicConfig(...)`` (including via an aliased import), a no-argument root
    ``logging.getLogger()``, and every root-handler mutation (``addHandler``, and
    ``handlers.append/extend/insert/remove/clear`` or assignment to ``.handlers`` on a root
    logger reached directly or through a bound name). A named ``getLogger(__name__)`` and a named
    logger's own ``addHandler`` are clean.
    """
    violations: list[GlobalLoggingViolation] = []
    for raw_path in source_paths:
        path = Path(raw_path)
        tree = _safe_parse(path.read_text(encoding="utf-8"))
        if tree is None:
            continue
        visitor = _G3Visitor(path.name)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


# ---------------------------------------------------------------------------
# Logger-convention detector: reads the target, never imposes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoggerConvention:
    """The target's own detected logging conventions."""

    factory: str | None
    levels: frozenset[str]
    format: str | None
    correlation_key: str | None


_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})

# The complete standard LogRecord attribute set — never a correlation key.
_STANDARD_LOGRECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "asctime",
        "message",
        "taskName",
    }
)


class _ConventionVisitor(ast.NodeVisitor):
    """Detect the target's logger factory, the exact levels called, formats, from one file."""

    def __init__(self) -> None:
        self.imports: dict[str, str] = {}
        self.str_consts: dict[str, str] = {}
        self.logger_names: set[str] = set()
        self.factory_found = False
        self.levels: set[str] = set()
        self.formats: list[tuple[int, str]] = []  # (lineno, format)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            local = alias.asname or root
            self.imports[local] = alias.name if alias.asname else root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                self.imports[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def _canon(self, node: ast.AST) -> str | None:
        attrs: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        head = self.imports.get(cur.id, cur.id)
        attrs.reverse()
        return ".".join([head, *attrs])

    def _is_getlogger_call(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and self._canon(node.func) == "logging.getLogger"

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.str_consts[target.id] = node.value.value
        if self._is_getlogger_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.logger_names.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        canonical = self._canon(node.func)
        if canonical == "logging.getLogger":
            self.factory_found = True
        if canonical == "logging.Formatter":
            fmt = self._formatter_format(node)
            if fmt is not None:
                self.formats.append((node.lineno, fmt))
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_LEVELS:
            receiver = node.func.value
            on_logger = (
                isinstance(receiver, ast.Name) and receiver.id in self.logger_names
            ) or self._is_getlogger_call(receiver)
            if on_logger:
                self.levels.add(node.func.attr)
        self.generic_visit(node)

    def _formatter_format(self, node: ast.Call) -> str | None:
        candidate: ast.AST | None = None
        if node.args:
            candidate = node.args[0]
        for keyword in node.keywords:
            if keyword.arg == "fmt":
                candidate = keyword.value
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
        if isinstance(candidate, ast.Name) and candidate.id in self.str_consts:
            return self.str_consts[candidate.id]
        return None


def detect_logger_convention(repo_root: Path) -> LoggerConvention:
    """Detect the target's logging conventions from its source: factory, levels, format, correlation."""
    repo_root = Path(repo_root)
    factory: str | None = None
    levels: set[str] = set()
    formats: list[tuple[int, int, str]] = []  # (file_index, lineno, format)

    for file_index, path in enumerate(sorted(repo_root.rglob("*.py"))):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        tree = _safe_parse(source)
        if tree is None:
            continue
        visitor = _ConventionVisitor()
        visitor.visit(tree)
        if visitor.factory_found and factory is None:
            factory = "logging.getLogger"
        levels |= visitor.levels
        for lineno, fmt in visitor.formats:
            formats.append((file_index, lineno, fmt))

    chosen_format: str | None = None
    if formats:
        chosen_format = min(formats, key=lambda item: (item[0], item[1]))[2]

    correlation_key: str | None = None
    if chosen_format:
        for field_name in re.findall(r"%\((\w+)\)[sdr]", chosen_format):
            if field_name not in _STANDARD_LOGRECORD_FIELDS:
                correlation_key = field_name
                break

    return LoggerConvention(
        factory=factory,
        levels=frozenset(levels),
        format=chosen_format,
        correlation_key=correlation_key,
    )


# ---------------------------------------------------------------------------
# Runtime log capture + sensitive predicate + evidence collector
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture(fn) -> tuple[object, list[logging.LogRecord], BaseException | None]:
    """Run ``fn`` with a temporary root handler; NEVER leave a lasting handler and NEVER raise.

    Returns ``(result, records, exception)``. A raising ``fn`` still yields the records captured
    before the exception, with the exception returned (not propagated) for the caller to decide.
    """
    root = logging.getLogger()
    previous_level = root.level
    handler = _ListHandler()
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    result: object = None
    exception: BaseException | None = None
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - captured, not swallowed silently
        exception = exc
    finally:
        records = list(handler.records)
        root.removeHandler(handler)
        root.setLevel(previous_level)
    return result, records, exception


def capture_logs(fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` once, returning ``(result, records)``; installs no lasting handler."""
    result, records, exception = _capture(lambda: fn(*args, **kwargs))
    if exception is not None:
        raise exception
    return result, records


def sensitive_values_present(records: list[logging.LogRecord], sensitive: set[str]) -> set[str]:
    """Return the subset of ``sensitive`` present in any record's fully-formatted message."""
    found: set[str] = set()
    for record in records:
        message = record.getMessage()
        for value in sensitive:
            if value in message:
                found.add(value)
    return found


def _event_emitted(event: str, messages: list[str]) -> bool:
    """True when ``event`` appears as a whole token (not a sub-identifier) in any message."""
    pattern = re.compile(r"(?<!\w)" + re.escape(event) + r"(?!\w)")
    return any(pattern.search(message) for message in messages)


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
    _, success_records, _ = _capture(success_path)
    # The failure path MAY raise after logging; _capture keeps its pre-exception records and does
    # not propagate the exception.
    _, failure_records, _ = _capture(failure_path)

    all_records = success_records + failure_records
    all_messages = [record.getMessage() for record in all_records]

    events_emitted = frozenset(
        event for event in required_events if _event_emitted(event, all_messages)
    )
    leaked = frozenset(sensitive_values_present(all_records, sensitive_fields | canaries))

    return LogEvidence(events_emitted=events_emitted, leaked=leaked)
