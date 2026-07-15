"""Six-class AST boundary lint (issue #5, slice S25).

Statically forbids the MARVIN-coupling shapes a naive extraction reintroduces:
foreign imports, hardcoded executables, MARVIN env reads, checkout-relative
defaults and path literals, and raw filesystem writes outside the IO seam. Reports
every violation (``file:line: rule``); no fail-fast.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

_STDLIB = set(sys.stdlib_module_names)


def _is_forbidden_env(key: object) -> bool:
    return isinstance(key, str) and (key.startswith("MARVIN_") or key == "AGENT_LOGS_DIR")


def _string_literal(node: ast.AST | None) -> str | None:
    """The string value of a bare ``"..."`` or a ``Path("...")`` literal, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    return None


def _bad_default(value: str) -> bool:
    return value.startswith(("/", "~", "../")) or "marvin" in value.lower() or "$HOME/" in value


# The one module allowed raw writes (the seam) and the one allowed Path(__file__).
_SEAM_MODULE = "io.py"
_RESOLVER_MODULE = "paths.py"
_LINT_MODULE = "boundary.py"  # holds the denylists below as its own data

# Narrowed to MARVIN's private persistence coupling (issue #5 lists skills//context//
# SKILL.md too, but those are artifact-type patterns the source-audit legitimately uses).
_PATH_DENYLIST = (
    "state/",
    "agentLogs",
    "projects.md",
    "model-rates.json",
    "agent-runs.json",
    "/Users/",
)

# Only these executables may appear as a literal argv[0]; anything else must be
# read from configuration or the frozen manifest (provenance, not identity).
_ENGINE_EXECUTABLES = {"git", "gh"}
_ARGV_BAD_TOKENS = ("marvin", "MARVIN_", "$HOME/", "~")

# Path-object write methods (names not shared with str/dict, so no false positives).
_WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "touch", "unlink"}
_OS_WRITE_FUNCS = {"remove", "rename", "replace", "makedirs", "mkdir"}


def check_source(path: Path, *, deps: frozenset[str] = frozenset()) -> list[str]:
    """Return boundary violations for a single module (empty list = clean)."""
    path = Path(path)
    return _check(path, path.name, deps)


def check_tree(root: Path, *, deps: frozenset[str] = frozenset()) -> list[str]:
    """Return boundary violations across every ``*.py`` under ``root``."""
    root = Path(root)
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        violations.extend(_check(path, str(path.relative_to(root)), deps))
    return violations


def declared_deps(pyproject: Path) -> frozenset[str]:
    """Top-level import roots of the declared dependencies in a pyproject.toml."""
    data = tomllib.loads(Path(pyproject).read_text())
    roots: set[str] = set()
    for spec in data.get("project", {}).get("dependencies", []):
        name = ""
        for char in spec:
            if not (char.isalnum() or char in "-_."):
                break
            name += char
        if name:
            roots.add(name.replace("-", "_"))
    return frozenset(roots)


def find_pyproject(start: Path) -> Path | None:
    """Walk up from ``start`` to locate the nearest pyproject.toml."""
    for parent in [Path(start), *Path(start).parents]:
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _check(path: Path, display: str, deps: frozenset[str]) -> list[str]:
    allowed_imports = _STDLIB | set(deps) | {"issueforge"}
    linter = _Linter(display, allowed_imports)
    linter.visit(ast.parse(path.read_text()))
    return linter.violations


class _Linter(ast.NodeVisitor):
    def __init__(self, display: str, allowed_imports: set[str]) -> None:
        self.filename = display
        self.basename = Path(display).name
        self.allowed_imports = allowed_imports
        self.violations: list[str] = []

    def _add(self, node: ast.AST, rule: str, detail: str) -> None:
        self.violations.append(f"{self.filename}:{node.lineno}: {rule}: {detail}")

    # --- class 1: imports ---------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in self.allowed_imports:
                self._add(node, "import", f"foreign module '{alias.name}'")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            root = node.module.split(".")[0]
            if root not in self.allowed_imports:
                self._add(node, "import", f"foreign module '{node.module}'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        dotted = ast.unparse(node.func)
        if dotted.endswith(("sys.path.insert", "sys.path.append")):
            self._add(node, "import", f"{dotted} manipulates the import path")
        elif dotted.endswith("spec_from_file_location"):
            self._add(node, "import", "spec_from_file_location loads a sibling by path")
        elif dotted == "__import__" and not (node.args and isinstance(node.args[0], ast.Constant)):
            self._add(node, "import", "__import__ with a non-literal name")

        if dotted.startswith("subprocess.") or dotted.split(".")[-1] == "Popen":
            self._check_argv(node)

        if dotted in ("os.environ.get", "os.getenv") and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and _is_forbidden_env(key.value):
                self._add(node, "env", f"reads forbidden environment '{key.value}'")

        for kw in node.keywords:
            if kw.arg == "default":
                self._check_default(node, kw.value)

        self._check_write_surface(node, dotted)
        self.generic_visit(node)

    # --- class 6: write surface (structural boundary) -----------------------
    def _check_write_surface(self, node: ast.Call, dotted: str) -> None:
        if self.basename == _SEAM_MODULE:
            return
        if dotted == "open":
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if isinstance(mode, str) and any(flag in mode for flag in "wax"):
                self._add(node, "write-surface", f"open(mode={mode!r}) outside the IO seam")
        elif dotted.startswith(("shutil.", "tempfile.")):
            self._add(node, "write-surface", f"{dotted}() outside the IO seam")
        elif isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            base = ast.unparse(node.func.value)
            if attr in _WRITE_METHODS:
                self._add(node, "write-surface", f".{attr}() outside the IO seam")
            elif base == "os" and attr in _OS_WRITE_FUNCS:
                self._add(node, "write-surface", f"os.{attr}() outside the IO seam")

    # --- class 0: Path(__file__) is paths.py's alone ------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "__file__" and self.basename != _RESOLVER_MODULE:
            self._add(node, "dunder-file", "__file__ is permitted only in paths.py")
        self.generic_visit(node)

    # --- class 3: environment -----------------------------------------------
    def visit_Subscript(self, node: ast.Subscript) -> None:
        if ast.unparse(node.value) == "os.environ" and isinstance(node.slice, ast.Constant):
            if _is_forbidden_env(node.slice.value):
                self._add(node, "env", f"reads forbidden environment '{node.slice.value}'")
        self.generic_visit(node)

    # --- class 5: path-literal denylist -------------------------------------
    def visit_Constant(self, node: ast.Constant) -> None:
        if self.basename == _LINT_MODULE or not isinstance(node.value, str):
            return
        for token in _PATH_DENYLIST:
            if token in node.value:
                self._add(node, "path-literal", f"'{node.value}' names a MARVIN path")
                break

    # --- class 4: defaults and module constants -----------------------------
    def _check_default(self, node: ast.AST, value_node: ast.AST | None) -> None:
        value = _string_literal(value_node)
        if value is not None and _bad_default(value):
            self._add(node, "default", f"'{value}' is checkout-relative or names marvin")

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_default(node, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_default(node, node.value)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            self._check_default(node, default)
        self.generic_visit(node)

    # --- class 2: executable argv -------------------------------------------
    def _check_argv(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self._add(node, "argv", "shell=True is forbidden")
        argv = node.args[0] if node.args else None
        if isinstance(argv, ast.Constant) and isinstance(argv.value, str):
            self._add(node, "argv", "argv must be a list literal, not a shell string")
            return
        if not isinstance(argv, ast.List):
            return  # a Name/Attribute/Subscript is a config value or typed Command
        for index, element in enumerate(argv.elts):
            if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                continue
            value = element.value
            if index == 0 and value not in _ENGINE_EXECUTABLES:
                self._add(
                    node,
                    "argv",
                    f"hardcoded executable '{value}' (only git/gh may be a literal argv[0])",
                )
            if any(token in value for token in _ARGV_BAD_TOKENS):
                self._add(node, "argv", f"argv element '{value}' reaches outside the sandbox")
