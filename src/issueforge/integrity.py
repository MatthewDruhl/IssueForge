"""Deterministic acceptance-integrity AST backstop (#19, S13).

Ported from MARVIN's ``scripts/check_acceptance_integrity.py`` (issues #491/#588/#610/#627/#666),
DEMOTED here to defense-in-depth behind ``contract.verify_contract_integrity``'s absolute
diff/hash/collection/ancestry gates. Where MARVIN's version shipped a CLI, IssueForge exposes only
the pure functions the S13 contract locks:

    integrity.classify_acceptance_change(old_src: str, new_src: str) -> str   # "allowed" | "weakened"
    integrity.is_pending(src: str, test_name: str) -> bool

A model-free check that distinguishes the ONE edit an implementer legitimately makes to activate a
pending test (removing its ``xfail(strict=True)`` marker) from any WEAKENING of a committed
acceptance test: an assertion value/operator change, an ``or True`` short-circuit, an ``assert``
no-op, a whole-test deletion, a rename-with-same-body, a marker downgrade (xfail->skip,
strict=True->strict=False), a change to a directly-referenced module constant, or a deeper
data-flow weakening (a fixture/helper/param-default/computed value the assertion transitively
depends on).

CLASS-BASED TESTS (#610): ``test_`` methods inside a top-level class are first-class citizens, keyed
by the qualified ``ClassName::method`` so a same-named method in another class can never shadow a
weakened one. PENDING-MARKER ALIASES are resolved single-level (an alias-of-alias or IMPORTED alias
stays opaque). DEEPER DATA FLOW (#588) resolves the transitive module-level definitions an assertion
depends on. SCOPE LIMIT: only module-level definitions are resolved; imported/conftest values and
nested classes/functions remain opaque.

The pure functions call ``ast.parse`` with no try/except of their own, so a malformed source
propagates ``SyntaxError`` unmodified to the caller (S13 wraps them where a swallowed verdict would
be unsafe).
"""

from __future__ import annotations

import ast

# --- AST helpers -----------------------------------------------------------


def _test_functions(module: ast.Module) -> dict[str, ast.FunctionDef]:
    """Module-level functions named test_* keyed by name (last wins on dup)."""
    out: dict[str, ast.FunctionDef] = {}
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            out[node.name] = node
    return out


_QUALSEP = "::"


def _class_test_methods(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    """Methods named test_* directly in the class body, keyed by bare name (last wins on dup)."""
    out: dict[str, ast.FunctionDef] = {}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            out[node.name] = node
    return out


def _test_classes(module: ast.Module) -> dict[str, ast.ClassDef]:
    """Top-level classes containing at least one test_* method, keyed by name."""
    return {
        node.name: node
        for node in module.body
        if isinstance(node, ast.ClassDef) and _class_test_methods(node)
    }


def _collect_tests(module: ast.Module) -> dict[str, ast.FunctionDef]:
    """ALL tests the guard protects: module-level test_* functions keyed by bare name, plus test_*
    methods of top-level classes keyed by the qualified ``ClassName::method`` (#610)."""
    out: dict[str, ast.FunctionDef] = dict(_test_functions(module))
    for cname, cls in _test_classes(module).items():
        for mname, func in _class_test_methods(cls).items():
            out[f"{cname}{_QUALSEP}{mname}"] = func
    return out


def _resolve_tests(module: ast.Module, test: str) -> list[tuple[str, ast.FunctionDef]]:
    """Resolve a NAME to [(qualified_name, func), ...]: an exact module-level name, a qualified
    ``Class::method`` / ``Class.method``, or a bare method name (every class defining it)."""
    tests = _collect_tests(module)
    if test in tests:  # module-level bare name or exact Class::method
        return [(test, tests[test])]
    if "." in test:  # Class.method — #610 leaves the separator open
        cls_name, _, meth = test.rpartition(".")
        key = f"{cls_name}{_QUALSEP}{meth}"
        if key in tests:
            return [(key, tests[key])]
    suffix = f"{_QUALSEP}{test}"
    return [(name, func) for name, func in tests.items() if name.endswith(suffix)]


def _module_constants(module: ast.Module) -> dict[str, str]:
    """Module-level simple assignments (NAME = ...) keyed by name -> value dump."""
    out: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = ast.dump(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                out[node.target.id] = ast.dump(node.value)
    return out


def _decorator_mark(dec: ast.expr) -> str | None:
    """The marker's LEAF name (``skip`` / ``xfail`` / ``parametrize``) regardless of how it is
    referenced: ``pytest.mark.skip``, ``mark.skip``, or a bare ``@skip``."""
    node = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_pending_marker_decorator(dec: ast.expr, aliases: frozenset[str] = frozenset()) -> bool:
    """True iff dec is an xfail(strict=True) marker (literal or via a module-level pending alias)."""
    if isinstance(dec, ast.Name) and dec.id in aliases:
        return True
    if _decorator_mark(dec) != "xfail" or not isinstance(dec, ast.Call):
        return False
    return any(
        kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in dec.keywords
    )


def _pending_alias_names(module: ast.Module) -> frozenset[str]:
    """Names of module-level assignments whose value is a literal xfail(strict=True) marker call.
    Single-level on purpose: an alias-of-an-alias is not resolved."""
    out: set[str] = set()
    for node in module.body:
        value: ast.expr | None = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if value is not None and _is_pending_marker_decorator(value):
            out.update(t.id for t in targets if isinstance(t, ast.Name))
    return frozenset(out)


def _is_pending_func(func: ast.FunctionDef, aliases: frozenset[str] = frozenset()) -> bool:
    """True iff the function carries an xfail(strict=True) marker (literal or in-file alias)."""
    return any(_is_pending_marker_decorator(dec, aliases) for dec in func.decorator_list)


def _is_pending_class(cls: ast.ClassDef, aliases: frozenset[str] = frozenset()) -> bool:
    """True iff the class carries an xfail(strict=True) marker anywhere in its decorator list."""
    return any(_is_pending_marker_decorator(dec, aliases) for dec in cls.decorator_list)


def _owning_classes(module: ast.Module) -> dict[str, ast.ClassDef]:
    """Qualified ``ClassName::method`` key -> the ClassDef owning that method."""
    return {
        f"{cname}{_QUALSEP}{mname}": cls
        for cname, cls in _test_classes(module).items()
        for mname in _class_test_methods(cls)
    }


def _unmarked_matches(module: ast.Module, test_name: str) -> tuple[bool, list[str]]:
    """Entry-mode pending resolution: (found, unmarked_qualified_names). A match counts as pending
    if the function/method itself carries the marker OR (for a method) its owning class does (#627)."""
    aliases = _pending_alias_names(module)
    owners = _owning_classes(module)
    matches = _resolve_tests(module, test_name)
    unmarked = [
        name
        for name, func in matches
        if not _is_pending_func(func, aliases)
        and not (name in owners and _is_pending_class(owners[name], aliases))
    ]
    return bool(matches), unmarked


_PENDING_TOKEN = ("PENDING", "")


def _norm_decorators(
    func: ast.FunctionDef | ast.ClassDef, aliases: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """ORDERED list of decorators (order is semantic). Each pending marker collapses to one PENDING
    token; every other decorator carries its exact AST dump."""
    out: list[tuple[str, str]] = []
    for dec in func.decorator_list:
        if _is_pending_marker_decorator(dec, aliases):
            out.append(_PENDING_TOKEN)
        else:
            out.append(("DEC", ast.dump(dec)))
    return out


def _decorator_change_allowed(
    old_func: ast.FunctionDef | ast.ClassDef,
    new_func: ast.FunctionDef | ast.ClassDef,
    old_aliases: frozenset[str] = frozenset(),
    new_aliases: frozenset[str] = frozenset(),
) -> bool:
    """True iff NEW's decorators are obtainable from OLD's by removing zero or more pending markers,
    preserving order. Asymmetric: keeping/removing a pending marker is allowed; ADDING one, or
    adding/removing/reordering/downgrading any other decorator, is a weakening."""
    old_norm = _norm_decorators(old_func, old_aliases)
    new_norm = _norm_decorators(new_func, new_aliases)
    i = 0
    for item in new_norm:
        while i < len(old_norm) and old_norm[i] != item:
            if old_norm[i] != _PENDING_TOKEN:
                return False  # a non-pending decorator would be dropped/changed
            i += 1
        if i >= len(old_norm):
            return False  # NEW has a decorator not present (in order) in OLD
        i += 1
    while i < len(old_norm):
        if old_norm[i] != _PENDING_TOKEN:
            return False  # a trailing non-pending decorator would be dropped
        i += 1
    return True


def _module_binding_env(module: ast.Module) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Module-level name -> (binding fingerprint, referenced names). Assignments fingerprint as the
    value's AST dump; imports as an import-origin token, so an import swap is detectable."""
    env: dict[str, tuple[str, tuple[str, ...]]] = {}

    def _refs(value: ast.expr) -> tuple[str, ...]:
        return tuple(sorted({n.id for n in ast.walk(value) if isinstance(n, ast.Name)}))

    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    env[target.id] = (ast.dump(node.value), _refs(node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                env[node.target.id] = (ast.dump(node.value), _refs(node.value))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                env[alias.asname or alias.name] = (
                    f"import-from:{node.module}:{alias.name}",
                    (),
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                env[bound] = (f"import:{alias.name}", ())
    return env


def _binding_closure(
    names: set[str], env: dict[str, tuple[str, tuple[str, ...]]]
) -> dict[str, str | None]:
    """Fingerprints of every module-level binding transitively reachable from ``names`` (cycle-safe).
    Unbound names map to None so a DELETED import/assignment registers as a change."""
    seen: dict[str, str | None] = {}
    stack = list(names)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        binding = env.get(name)
        if binding is None:
            seen[name] = None
            continue
        seen[name] = binding[0]
        stack.extend(binding[1])
    return seen


def _kept_decorator_bindings_changed(
    new_func: ast.FunctionDef | ast.ClassDef,
    old_env: dict[str, tuple[str, tuple[str, ...]]],
    new_env: dict[str, tuple[str, tuple[str, ...]]],
    new_aliases: frozenset[str],
) -> bool:
    """True iff a module binding feeding a KEPT non-pending decorator changed between OLD and NEW
    (#627): a chain-root redefinition, or an import swap/replacement/deletion under a kept decorator."""
    for dec in new_func.decorator_list:
        if _is_pending_marker_decorator(dec, new_aliases):
            continue
        names = {n.id for n in ast.walk(dec) if isinstance(n, ast.Name)}
        if _binding_closure(names, old_env) != _binding_closure(names, new_env):
            return True
    return False


def _body_dump(func: ast.FunctionDef) -> str:
    """AST dump of the function body (decorators excluded)."""
    return "".join(ast.dump(stmt) for stmt in func.body)


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _free_names(node: ast.AST) -> set[str]:
    """Names referenced in ``node`` respecting comprehension scope (Py3). A comprehension has its own
    scope; a name bound by one of its generator targets is private to it. Comprehension scope is
    SEQUENTIAL (#666): each generator's ``iter`` sees only PRIOR targets, and its ``if`` clauses see
    only the targets bound UP TO AND INCLUDING that generator."""
    free: set[str] = set()

    def _walk(n: ast.AST, bound: frozenset[str]) -> None:
        if isinstance(n, _COMPREHENSIONS):
            inner = set(bound)
            for gen in n.generators:
                _walk(gen.iter, frozenset(inner))
                for t in ast.walk(gen.target):
                    if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                        inner.add(t.id)
                for cond in gen.ifs:
                    _walk(cond, frozenset(inner))
            frozen_inner = frozenset(inner)
            elt_nodes = (n.key, n.value) if isinstance(n, ast.DictComp) else (n.elt,)
            for elt in elt_nodes:
                _walk(elt, frozen_inner)
            return
        if isinstance(n, ast.Name):
            if n.id not in bound:
                free.add(n.id)
            return
        for child in ast.iter_child_nodes(n):
            _walk(child, bound)

    _walk(node, frozenset())
    return free


def _referenced_names(func: ast.FunctionDef) -> set[str]:
    return _free_names(func)


# --- deeper data-flow resolver (#588) --------------------------------------


def _names_in(node: ast.expr) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _module_defs(module: ast.Module) -> dict[str, ast.AST]:
    """Module-level name -> defining node (an assignment value, or a FunctionDef helper/fixture).
    Later definitions win. Only module-level definitions are resolved."""
    defs: dict[str, ast.AST] = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                defs[node.target.id] = node.value
        elif isinstance(node, ast.FunctionDef):
            defs[node.name] = node
    return defs


def _def_fingerprint(node: ast.AST) -> str:
    """Stable fingerprint of a module-level definition. A FunctionDef fingerprints BODY + SIGNATURE
    (defaults) + DECORATORS, so a value shifting via a default or a fixture-param decorator is caught."""
    if isinstance(node, ast.FunctionDef):
        return (
            "fn:"
            + ast.dump(node.args)
            + "|dec:"
            + "".join(ast.dump(d) for d in node.decorator_list)
            + "|body:"
            + "".join(ast.dump(stmt) for stmt in node.body)
        )
    return "val:" + ast.dump(node)


def _def_refs(node: ast.AST) -> set[str]:
    """Names a definition references so the closure follows the chain. For a FunctionDef only its FREE
    references are followed (a locally-bound/parameter name is scoped to the helper); signature
    defaults and decorator expressions evaluate in module scope so their names are always followed."""
    if isinstance(node, ast.FunctionDef):
        local = _locally_bound_names(node)
        params = {a.arg for a in (*node.args.args, *node.args.posonlyargs, *node.args.kwonlyargs)}
        if node.args.vararg is not None:
            params.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            params.add(node.args.kwarg.arg)
        refs: set[str] = set()
        for stmt in node.body:
            refs |= _free_names(stmt)
        refs -= local | params
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                refs |= _names_in(default)
        for dec in node.decorator_list:
            refs |= _names_in(dec)
        return refs
    return _names_in(node)


def _locally_bound_names(func: ast.FunctionDef) -> set[str]:
    """Names BOUND locally inside the function body (assignment/for/with/except/walrus targets).
    Comprehension generator targets are EXCLUDED (their own scope). A ``global``/``nonlocal`` name is
    NOT a local bind (it names an outer/module binding, #666), so it stays a followed ref."""
    bound: set[str] = set()
    declared_outer: set[str] = set()

    def _targets(tgt: ast.expr) -> None:
        for n in ast.walk(tgt):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                bound.add(n.id)

    def _visit(node: ast.AST) -> None:
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared_outer.update(node.names)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                _targets(t)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _targets(node.target)
        elif isinstance(node, ast.NamedExpr):
            _targets(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                _targets(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bound.add(node.name)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            _visit(child)

    for stmt in func.body:
        _visit(stmt)
    return bound - declared_outer


def _assertion_dep_roots(func: ast.FunctionDef) -> set[str]:
    """Root names an assertion transitively depends on: body names PLUS parameter-default names PLUS
    genuinely-injected (not locally reassigned) parameters. Locally-bound names are excluded."""
    local = _locally_bound_names(func)
    roots = _referenced_names(func) - local
    for default in (*func.args.defaults, *func.args.kw_defaults):
        if default is not None:
            roots |= _names_in(default)
    for arg in (*func.args.args, *func.args.posonlyargs, *func.args.kwonlyargs):
        if arg.arg not in local:
            roots.add(arg.arg)
    return roots


def _dep_closure(roots: set[str], defs: dict[str, ast.AST]) -> dict[str, str]:
    """Fingerprints of every module-level definition transitively reachable from ``roots`` (cycle-safe).
    A root with no module-level definition is simply absent (never recorded as a change)."""
    seen: dict[str, str] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        node = defs.get(name)
        if node is None:
            continue
        seen[name] = _def_fingerprint(node)
        stack.extend(_def_refs(node))
    return seen


def _dep_closure_changed(
    new_func: ast.FunctionDef,
    old_defs: dict[str, ast.AST],
    new_defs: dict[str, ast.AST],
) -> str | None:
    """The name of a module-level definition an assertion in NEW depends on whose fingerprint changed
    from OLD, or None if the whole dependency closure is identical."""
    roots = _assertion_dep_roots(new_func)
    old_closure = _dep_closure(roots, old_defs)
    new_closure = _dep_closure(roots, new_defs)
    for name in sorted(new_closure.keys() | old_closure.keys()):
        if old_closure.get(name) != new_closure.get(name):
            return name
    return None


# --- pure core -------------------------------------------------------------


def is_pending(src: str, test_name: str) -> bool:
    """The named test exists in ``src`` AND carries xfail(strict=True).

    Accepts a module-level name, ``Class::method`` / ``Class.method``, or a bare method name; a bare
    name matching several classes is pending only if EVERY match is (fail-closed). A method under a
    class-level pending marker counts as pending (#627). Propagates ``SyntaxError`` on malformed src.
    """
    found, unmarked = _unmarked_matches(ast.parse(src), test_name)
    return found and not unmarked


def _compare_test(
    name: str,
    old_func: ast.FunctionDef,
    new_func: ast.FunctionDef | None,
    old_consts: dict[str, str],
    new_consts: dict[str, str],
    old_aliases: frozenset[str],
    new_aliases: frozenset[str],
    old_env: dict[str, tuple[str, tuple[str, ...]]],
    new_env: dict[str, tuple[str, tuple[str, ...]]],
    old_defs: dict[str, ast.AST],
    new_defs: dict[str, ast.AST],
) -> tuple[str, str] | None:
    """Per-test weakening checks, shared by module-level functions and class methods. Returns
    (offending_name, reason) or None if clean."""
    if new_func is None:
        return name, "the committed test was deleted or renamed"

    if not _decorator_change_allowed(old_func, new_func, old_aliases, new_aliases):
        return (
            name,
            "a decorator was added, removed, reordered, or downgraded "
            "(only removing the pending xfail marker is allowed)",
        )

    if _kept_decorator_bindings_changed(new_func, old_env, new_env, new_aliases):
        return (
            name,
            "a module-level binding or import feeding a kept decorator was changed or removed",
        )

    if ast.dump(old_func.args) != ast.dump(new_func.args):
        return name, "the test signature changed"

    if _body_dump(old_func) != _body_dump(new_func):
        return name, "an assertion or test body was changed"

    for ref in _referenced_names(new_func):
        if ref in old_consts and old_consts.get(ref) != new_consts.get(ref):
            return name, f"referenced module constant '{ref}' changed value"

    changed = _dep_closure_changed(new_func, old_defs, new_defs)
    if changed is not None:
        return (
            name,
            f"a value an assertion depends on changed through deeper data flow ('{changed}')",
        )

    return None


def _analyze(old_src: str, new_src: str) -> tuple[str, str | None, str]:
    """Return (verdict, offending_test_name, reason); verdict is "allowed" or "weakened"."""
    old_mod = ast.parse(old_src)
    new_mod = ast.parse(new_src)

    old_tests = _collect_tests(old_mod)
    new_tests = _collect_tests(new_mod)
    old_consts = _module_constants(old_mod)
    new_consts = _module_constants(new_mod)
    old_aliases = _pending_alias_names(old_mod)
    new_aliases = _pending_alias_names(new_mod)
    old_env = _module_binding_env(old_mod)
    new_env = _module_binding_env(new_mod)
    old_defs = _module_defs(old_mod)
    new_defs = _module_defs(new_mod)

    for name, old_func in old_tests.items():
        finding = _compare_test(
            name,
            old_func,
            new_tests.get(name),
            old_consts,
            new_consts,
            old_aliases,
            new_aliases,
            old_env,
            new_env,
            old_defs,
            new_defs,
        )
        if finding is not None:
            return "weakened", finding[0], finding[1]

    new_classes = {node.name: node for node in new_mod.body if isinstance(node, ast.ClassDef)}
    for cname, old_cls in _test_classes(old_mod).items():
        new_cls = new_classes.get(cname)
        if new_cls is None:
            continue
        if not _decorator_change_allowed(old_cls, new_cls, old_aliases, new_aliases):
            return (
                "weakened",
                cname,
                "a class-level decorator was added, removed, reordered, or downgraded",
            )
        if _kept_decorator_bindings_changed(new_cls, old_env, new_env, new_aliases):
            return (
                "weakened",
                cname,
                "a module-level binding or import feeding a kept class decorator changed",
            )

    return "allowed", None, "only marker removal / comment / whitespace changes"


def classify_acceptance_change(old_src: str, new_src: str) -> str:
    """Pure classifier: "allowed" iff the only change is pending-marker removal or comment/whitespace
    edits; "weakened" for any assertion/marker/constant tampering, deletion, or rename. Propagates
    ``SyntaxError`` on malformed input (no swallowed verdict)."""
    return _analyze(old_src, new_src)[0]
