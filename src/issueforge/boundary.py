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


class Command(list[str]):
    """Configured argv paired with its seam-constrained working directory."""

    def __init__(self, argv: list[str], *, cwd: Path) -> None:
        super().__init__(argv)
        self.cwd = cwd

    @classmethod
    def from_config(cls, argv: list[str], *, cwd: Path) -> Command:
        return cls(argv, cwd=cwd)

    @classmethod
    def from_manifest(cls, argv: list[str], *, cwd: Path) -> Command:
        return cls(argv, cwd=cwd)


_STDLIB = set(sys.stdlib_module_names)


def _is_forbidden_env(key: object) -> bool:
    return isinstance(key, str) and (key.startswith("MARVIN_") or key == "AGENT_LOGS_DIR")


def _bad_default(value: str) -> bool:
    return value.startswith(("/", "~", "../")) or value.lower() == "marvin" or "$HOME/" in value


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
# shutil/tempfile members that only read; everything else from those modules writes.
_TEMPFILE_SHUTIL_READS = {
    "which",
    "disk_usage",
    "get_terminal_size",
    "gettempdir",
    "gettempprefix",
    "get_archive_formats",
    "get_unpack_formats",
}


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
        self.binding_scopes: list[dict[str, str | None]] = [{}]
        self.path_names = {"Path"}
        self.command_scopes: list[dict[str, bool]] = [{}]
        # class 6 (#40): names provably bound to WriteSeam()/io.WriteSeam() in each scope.
        self.seam_scopes: list[dict[str, bool]] = [{}]
        # Parallel marker: True only for a real function / async-function body, where a
        # local `seam = WriteSeam()` may confer exemption. False for the module scope (so a
        # module-level seam never exempts) and for lambda / comprehension scopes (so a seam
        # shadowed by a lambda param or comprehension target is never exempt inside it) (#40).
        self.scope_is_function: list[bool] = [False]
        # Control-flow nesting depth within the current scope: 0 only at a scope's top level.
        # A `seam = WriteSeam()` under an `if`/`try`/`for`/`while`/`with` body is CONDITIONAL —
        # not provably the value at the write site — so trust is granted only at depth 0 (#40).
        self.control_depth: list[int] = [0]
        # Names declared `global`/`nonlocal` in the current scope: such a name is not a provable
        # function-LOCAL binding, so it can never be a trusted seam (#40).
        self.global_nonlocal_names: list[set[str]] = [set()]

    def _add(self, node: ast.AST, rule: str, detail: str) -> None:
        self.violations.append(f"{self.filename}:{node.lineno}: {rule}: {detail}")

    # --- class 1: imports ---------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in self.allowed_imports:
                self._add(node, "import", f"foreign module '{alias.name}'")
            local = alias.asname or root
            self.binding_scopes[-1][local] = alias.name if alias.asname else root
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            root = node.module.split(".")[0]
            if root not in self.allowed_imports:
                self._add(node, "import", f"foreign module '{node.module}'")
            self._record_import_aliases(node.module, node.names)
        self.generic_visit(node)

    def _record_import_aliases(self, module: str, names: list[ast.alias]) -> None:
        for alias in names:
            local = alias.asname or alias.name
            canonical = f"{module}.{alias.name}"
            if module == "pathlib" and alias.name == "Path":
                self.path_names.add(local)
            self.binding_scopes[-1][local] = canonical

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
        if canonical in ("sys.path.insert", "sys.path.append"):
            self._add(node, "import", f"{canonical} manipulates the import path")
        elif canonical.endswith("spec_from_file_location"):
            self._add(node, "import", "spec_from_file_location loads a sibling by path")
        elif canonical in ("__import__", "builtins.__import__") and not (
            node.args and isinstance(node.args[0], ast.Constant)
        ):
            self._add(node, "import", "__import__ with a non-literal name")

        if canonical.startswith("subprocess."):
            self._check_argv(node)

        if canonical in ("os.environ.get", "os.getenv") and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and _is_forbidden_env(key.value):
                self._add(node, "env", f"reads forbidden environment '{key.value}'")

        for kw in node.keywords:
            if kw.arg == "default":
                self._check_default(node, kw.value)

        self._check_write_surface(node, dotted, canonical)
        self.generic_visit(node)

    # --- class 6: write surface (structural boundary) -----------------------
    def _check_write_surface(self, node: ast.Call, dotted: str, canonical: str) -> None:
        if self.basename == _SEAM_MODULE:
            return
        path_open = (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "open"
                and canonical not in ("builtins.open", "io.open")
            )
            or canonical.rsplit(".", 1)[-1] == "open"
            and canonical
            not in (
                "open",
                "builtins.open",
                "io.open",
            )
        )
        if canonical in ("open", "builtins.open", "io.open") or path_open:
            mode_node = (
                node.args[0]
                if path_open and node.args
                else node.args[1]
                if len(node.args) >= 2
                else None
            )
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode_node = kw.value
                elif kw.arg is None:
                    self._add(node, "write-surface", "open(**kwargs) has an unresolved mode")
            if mode_node is not None:
                mode = mode_node.value if isinstance(mode_node, ast.Constant) else None
                if mode not in {"r", "rt", "tr", "rb", "br"}:
                    detail = repr(mode) if isinstance(mode, str) else "unresolved"
                    self._add(node, "write-surface", f"open(mode={detail}) outside the IO seam")
        elif isinstance(node.func, ast.Name) and (
            canonical.rsplit(".", 1)[-1] in _WRITE_METHODS | {"rename", "replace"}
            or canonical.startswith("os.")
            and canonical.rsplit(".", 1)[-1] in _OS_WRITE_FUNCS
        ):
            self._add(node, "write-surface", f"{dotted}() outside the IO seam")
        elif canonical.startswith(("shutil.", "tempfile.")):
            if canonical.rsplit(".", 1)[-1] not in _TEMPFILE_SHUTIL_READS:
                self._add(node, "write-surface", f"{canonical}() outside the IO seam")
        elif isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _WRITE_METHODS:
                if attr == "write_text" and self._is_trusted_seam(node.func.value):
                    return  # #40: sanctioned WriteSeam.write_text is exempt
                self._add(node, "write-surface", f".{attr}() outside the IO seam")
            elif attr in ("rename", "replace") and len(node.args) + len(node.keywords) == 1:
                # Path.rename(target) / Path.replace(target); str.replace needs >= 2 args.
                self._add(node, "write-surface", f".{attr}() outside the IO seam")
            elif canonical.startswith("os.") and attr in _OS_WRITE_FUNCS:
                self._add(node, "write-surface", f"{canonical}() outside the IO seam")

    # --- class 0: Path(__file__) is paths.py's alone ------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "__file__" and self.basename != _RESOLVER_MODULE:
            self._add(node, "dunder-file", "__file__ is permitted only in paths.py")
        self.generic_visit(node)

    # --- class 3: environment -----------------------------------------------
    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = ast.unparse(node.value)
        if self._canonical(value) == "os.environ" and isinstance(node.slice, ast.Constant):
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
        if self.basename == _LINT_MODULE:
            return
        for value in self._literal_strings(value_node):
            if _bad_default(value):
                self._add(node, "default", f"'{value}' is checkout-relative or names marvin")

    def _literal_strings(self, node: ast.AST | None):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                yield from self._literal_strings(element)
        elif isinstance(node, ast.Dict):
            for element in (*node.keys, *node.values):
                yield from self._literal_strings(element)
        elif (
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id in self.path_names
                or self._canonical(ast.unparse(node.func)) == "pathlib.Path"
            )
            and node.args
        ):
            yield from self._literal_strings(node.args[0])

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_default(node, node.value)
        for target in node.targets:
            self._record_assignment(target, node.value)
            self._apply_seam_binding(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_default(node, node.value)
        if isinstance(node.target, ast.Name):
            is_command = self._is_command_annotation(node.annotation) and self._is_command_source(
                node.value
            )
            self._record_rebinding(node.target.id, node.value)
            self.command_scopes[-1][node.target.id] = is_command
            self._apply_seam_binding(node.target, node.value)
        self.generic_visit(node)

    # --- class 6 (#40): seam-trust granting (one place) vs clearing (every other binder) ---
    def _apply_seam_binding(self, target: ast.AST, value: ast.AST | None) -> None:
        """Grant seam trust ONLY for a simple ``name = WriteSeam()``; clear it for anything else.

        A destructuring/tuple/list target, or any binder whose value is not a provable
        WriteSeam()/io.WriteSeam() construction, CLEARS trust for every name it binds — the lint
        fails toward flagging, so a name bound by anything but a proven construction is untrusted.
        Trust is granted ONLY for an UNCONDITIONAL binding (``control_depth == 0`` — not nested in
        an ``if``/``try``/``for``/``while``/``with`` body, where the WriteSeam value is not
        guaranteed at the write site) whose name is not declared ``global``/``nonlocal`` (#40).
        """
        if isinstance(target, ast.Name):
            provable = (
                self._is_writeseam_construction(value)
                and self.control_depth[-1] == 0
                and target.id not in self.global_nonlocal_names[-1]
            )
            self.seam_scopes[-1][target.id] = provable
        else:
            self._clear_seam(target)

    def _clear_seam(self, target: ast.AST | None) -> None:
        for name in self._bound_names(target):
            self.seam_scopes[-1][name] = False

    def _bound_names(self, target: ast.AST | None):
        """Yield every name a binder target binds (Name, Starred, and nested Tuple/List)."""
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, ast.Starred):
            yield from self._bound_names(target.value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                yield from self._bound_names(element)

    def _record_assignment(self, target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, ast.Name):
            self.command_scopes[-1][target.id] = False
            self._record_rebinding(target.id, value)
        elif (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for child_target, child_value in zip(target.elts, value.elts, strict=True):
                self._record_assignment(child_target, child_value)

    def _record_rebinding(self, target: str, node: ast.AST | None) -> None:
        if not isinstance(node, (ast.Name, ast.Attribute)):
            self.binding_scopes[-1].setdefault(target, None)
            return
        value = self._canonical(ast.unparse(node))
        protected = (
            value in ("os.environ", "os.environ.get", "os.getenv", "builtins.__import__")
            or value in ("subprocess", "os", "shutil", "tempfile", "builtins", "io", "sys")
            or value.startswith("subprocess.")
            or value in ("open", "builtins.open", "io.open")
            or value.rsplit(".", 1)[-1] == "open"
            or value.startswith("os.")
            and value.rsplit(".", 1)[-1] in _OS_WRITE_FUNCS
            or value.startswith(("shutil.", "tempfile."))
            and value.rsplit(".", 1)[-1] not in _TEMPFILE_SHUTIL_READS
            or value.rsplit(".", 1)[-1] in _WRITE_METHODS | {"rename", "replace"}
        )
        if not protected:
            self.binding_scopes[-1].setdefault(target, None)
            return
        self.binding_scopes[-1][target] = value

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        # An aug-assign (`seam += ...`) clobbers the binding: trust is cleared, never granted.
        self._record_assignment(node.target, None)
        self._clear_seam(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        # A walrus binding is treated as non-simple: it always clears seam trust for its name.
        self._record_assignment(node.target, node.value)
        self._clear_seam(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._record_assignment(node.target, None)
        self._clear_seam(node.target)
        # The loop body is conditional (may run zero times): a WriteSeam bound inside it is not
        # provably the value afterward, so raise control depth for the whole node (#40).
        self.control_depth[-1] += 1
        self.generic_visit(node)
        self.control_depth[-1] -= 1

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._record_assignment(item.optional_vars, None)
                self._clear_seam(item.optional_vars)
        self.control_depth[-1] += 1
        self.generic_visit(node)
        self.control_depth[-1] -= 1

    visit_AsyncWith = visit_With

    def visit_If(self, node: ast.If) -> None:
        # A binding inside either branch is conditional: raise control depth so a WriteSeam bound
        # under `if`/`else` is not trusted at the write site (#40).
        self.control_depth[-1] += 1
        self.generic_visit(node)
        self.control_depth[-1] -= 1

    def visit_Try(self, node: ast.Try) -> None:
        self.control_depth[-1] += 1
        self.generic_visit(node)
        self.control_depth[-1] -= 1

    visit_TryStar = visit_Try

    def visit_While(self, node: ast.While) -> None:
        self.control_depth[-1] += 1
        self.generic_visit(node)
        self.control_depth[-1] -= 1

    def visit_Match(self, node: ast.Match) -> None:
        # Each `case` body is a conditional branch: a WriteSeam bound inside one is not provably
        # the value at a later write, so raise control depth for the whole match (#40). A case
        # capture pattern (`case ... as seam`, `case [*seam]`, `case {**seam}`) REBINDS the name to
        # a matched value, so clear seam trust for every captured name.
        for case in node.cases:
            for name in self._match_capture_names(case.pattern):
                self.seam_scopes[-1][name] = False
        self.control_depth[-1] += 1
        self.generic_visit(node)
        self.control_depth[-1] -= 1

    @staticmethod
    def _match_capture_names(pattern: ast.AST | None):
        """Yield every name a match-case pattern captures (MatchAs/MatchStar names, MatchMapping rest)."""
        if pattern is None:
            return
        for sub in ast.walk(pattern):
            name = (
                getattr(sub, "name", None)
                if isinstance(sub, (ast.MatchAs, ast.MatchStar))
                else None
            )
            if name is None and isinstance(sub, ast.MatchMapping):
                name = sub.rest
            if isinstance(name, str):
                yield name

    def visit_Global(self, node: ast.Global) -> None:
        # A `global name` makes the name refer to the module global, not a function-local: never
        # trustable as a local seam. Record it and clear any trust (#40).
        for name in node.names:
            self.global_nonlocal_names[-1].add(name)
            self.seam_scopes[-1][name] = False
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.global_nonlocal_names[-1].add(name)
            self.seam_scopes[-1][name] = False
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._record_rebinding(node.name, None)
            self.command_scopes[-1][node.name] = False
            # `except ... as seam` binds an exception, not a WriteSeam: clear trust.
            self.seam_scopes[-1][node.name] = False
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            self._check_default(node, default)
        parameter_names = {
            arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        }
        if node.args.vararg:
            parameter_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            parameter_names.add(node.args.kwarg.arg)
        self.command_scopes.append(dict.fromkeys(parameter_names, False))
        self.binding_scopes.append(dict.fromkeys(parameter_names))
        self.seam_scopes.append(dict.fromkeys(parameter_names, False))
        self.scope_is_function.append(True)
        self.control_depth.append(0)
        self.global_nonlocal_names.append(set())
        self.generic_visit(node)
        self.global_nonlocal_names.pop()
        self.control_depth.pop()
        self.scope_is_function.pop()
        self.seam_scopes.pop()
        self.binding_scopes.pop()
        self.command_scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # A class body is its own scope (like a function/lambda/comprehension): open fresh binding
        # stacks so a class-body binding never mutates — or leaks into — the enclosing function's
        # seam trust. scope_is_function is False, so a write in the class body is never exempt (#40).
        self.command_scopes.append({})
        self.binding_scopes.append({})
        self.seam_scopes.append({})
        self.scope_is_function.append(False)
        self.control_depth.append(0)
        self.global_nonlocal_names.append(set())
        self.generic_visit(node)
        self.global_nonlocal_names.pop()
        self.control_depth.pop()
        self.scope_is_function.pop()
        self.seam_scopes.pop()
        self.binding_scopes.pop()
        self.command_scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda opens its own scope: its parameters shadow any outer trusted seam, and the
        # scope is marked non-function so no write inside the lambda body is ever exempt (#40).
        parameter_names = {
            arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        }
        if node.args.vararg:
            parameter_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            parameter_names.add(node.args.kwarg.arg)
        self.seam_scopes.append(dict.fromkeys(parameter_names, False))
        self.scope_is_function.append(False)
        self.control_depth.append(0)
        self.global_nonlocal_names.append(set())
        self.generic_visit(node)
        self.global_nonlocal_names.pop()
        self.control_depth.pop()
        self.scope_is_function.pop()
        self.seam_scopes.pop()

    def _visit_comprehension(self, node: ast.AST) -> None:
        # A comprehension opens its own scope: its ``for`` targets shadow any outer trusted seam,
        # and the scope is non-function so a shadowed seam is never exempt in the body (#40).
        targets: set[str] = set()
        for generator in node.generators:
            targets.update(self._bound_names(generator.target))
        self.seam_scopes.append(dict.fromkeys(targets, False))
        self.scope_is_function.append(False)
        self.control_depth.append(0)
        self.global_nonlocal_names.append(set())
        self.generic_visit(node)
        self.global_nonlocal_names.pop()
        self.control_depth.pop()
        self.scope_is_function.pop()
        self.seam_scopes.pop()

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def _is_command_annotation(self, node: ast.AST | None) -> bool:
        return (
            node is not None and self._canonical(ast.unparse(node)) == "issueforge.boundary.Command"
        )

    def _is_command_source(self, node: ast.AST | None) -> bool:
        if not isinstance(node, ast.Call) or self._canonical(ast.unparse(node.func)) not in (
            "issueforge.boundary.Command.from_config",
            "issueforge.boundary.Command.from_manifest",
        ):
            return False
        cwd = next((kw.value for kw in node.keywords if kw.arg == "cwd"), None)
        return (
            bool(node.args)
            and isinstance(node.args[0], (ast.Attribute, ast.Subscript))
            and isinstance(cwd, (ast.Attribute, ast.Subscript))
        )

    # --- class 2: executable argv -------------------------------------------
    def _check_argv(self, node: ast.Call) -> None:
        if self._canonical(ast.unparse(node.func)) == "subprocess.Popen" and len(node.args) > 8:
            shell = node.args[8]
            if not (isinstance(shell, ast.Constant) and shell.value is False):
                self._add(node, "argv", "positional shell must be literal False")
        for kw in node.keywords:
            if kw.arg == "shell" and not (
                isinstance(kw.value, ast.Constant) and kw.value.value is False
            ):
                self._add(node, "argv", "shell must be literal False")
            elif kw.arg is None:
                self._add(node, "argv", "subprocess **kwargs cannot prove shell=False")
        argv = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == "args"), None)
        )
        if isinstance(argv, ast.Constant) and isinstance(argv.value, str):
            self._add(node, "argv", "argv must be a list literal, not a shell string")
            return
        if not isinstance(argv, ast.List):
            typed_command = isinstance(argv, ast.Name) and self._is_typed_command(argv.id)
            if not typed_command:
                self._add(node, "argv", "argv must be a list literal or typed Command value")
            else:
                cwd = next((kw.value for kw in node.keywords if kw.arg == "cwd"), None)
                if not (
                    isinstance(cwd, ast.Attribute)
                    and isinstance(cwd.value, ast.Name)
                    and cwd.value.id == argv.id
                    and cwd.attr == "cwd"
                ):
                    self._add(node, "argv", "typed Command requires cwd=command.cwd")
                for kw in node.keywords:
                    if kw.arg in ("stdout", "stderr") and not (
                        isinstance(kw.value, ast.Constant)
                        and kw.value.value is None
                        or self._canonical(ast.unparse(kw.value))
                        in ("subprocess.PIPE", "subprocess.DEVNULL")
                    ):
                        self._add(node, "argv", f"{kw.arg} is not constrained to captured output")
            return
        for index, element in enumerate(argv.elts):
            if index == 0 and not (
                isinstance(element, ast.Constant) and isinstance(element.value, str)
            ):
                self._add(node, "argv", "list literal argv[0] must be an engine-owned literal")
                continue
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

    def _is_typed_command(self, name: str) -> bool:
        for scope in reversed(self.command_scopes):
            if name in scope:
                return scope[name]
        return False

    # --- class 6 (#40): receiver-aware WriteSeam.write_text exemption ---------
    def _is_writeseam_construction(self, node: ast.AST | None) -> bool:
        """True only for a literal WriteSeam()/io.WriteSeam() call (a provable seam)."""
        return (
            isinstance(node, ast.Call)
            and self._canonical(ast.unparse(node.func)) == "issueforge.io.WriteSeam"
        )

    def _is_trusted_seam(self, node: ast.AST) -> bool:
        """True when ``node`` is a function-local name provably bound to a WriteSeam here.

        Exemption is granted only inside a real function body (``scope_is_function``) and only
        for a name trusted in the CURRENT scope — never a module-global, and never a name a
        lambda/comprehension has shadowed.
        """
        return (
            isinstance(node, ast.Name)
            and self.scope_is_function[-1]
            and self.seam_scopes[-1].get(node.id, False)
        )
