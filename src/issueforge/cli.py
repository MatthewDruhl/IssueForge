"""IssueForge command-line entry point."""

import sys
from pathlib import Path

import typer

app = typer.Typer(help="Turn GitHub issues into human-approved, TDD-built pull requests.")


def _isatty() -> bool:
    """Is a human actually watching this process's stdin? (#140)

    A module-level seam rather than an inline ``sys.stdin.isatty()`` call: under Typer's
    ``CliRunner`` — which every CLI test uses — stdin is never a tty, so an inline call would make
    the interactive branch untestable. The DEFAULT reports the process's real stdin.
    """
    return sys.stdin.isatty()


audit_app = typer.Typer(help="Build-time source-audit gates (provenance completeness).")
app.add_typer(audit_app, name="audit")


@audit_app.command("check")
def audit_check(
    stage: str,
    manifest: Path = typer.Option(None, help="Path to the extraction manifest."),
    stages_dir: Path = typer.Option(None, help="Directory holding per-stage records."),
    source_root: Path = typer.Option(
        None, help="MARVIN checkout to discover unmanifested candidates (mode 5)."
    ),
) -> None:
    """Validate a stage's provenance record against the extraction manifest."""
    from issueforge.audit import DEFAULT_MANIFEST, DEFAULT_STAGES_DIR, check_stage

    violations = check_stage(
        stage,
        manifest or DEFAULT_MANIFEST,
        stages_dir or DEFAULT_STAGES_DIR,
        source_root,
    )
    if violations:
        for violation in violations:
            typer.echo(f"ERROR: {violation}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


lint_app = typer.Typer(help="Structural boundary lints (build-time gates).")
app.add_typer(lint_app, name="lint")


@lint_app.command("boundary")
def lint_boundary(
    root: Path = typer.Option(None, help="Package root to scan (defaults to issueforge)."),
) -> None:
    """Fail on any code that could write outside IssueForge or reach a sibling checkout."""
    from issueforge.boundary import check_tree, declared_deps, find_pyproject
    from issueforge.paths import package_root

    scan_root = root or package_root()
    pyproject = find_pyproject(scan_root) or find_pyproject(Path.cwd())
    deps = declared_deps(pyproject) if pyproject else frozenset()

    violations = check_tree(scan_root, deps=deps)
    if violations:
        for violation in violations:
            typer.echo(f"ERROR: {violation}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


config_app = typer.Typer(help="Inspect a repository's committed .issueforge.toml.")
app.add_typer(config_app, name="config")


@config_app.command("check")
def config_check(path: Path) -> None:
    """Resolve and print a repo's build plan, or fail loudly naming the offending field."""
    from issueforge.adapters.pytest_adapter import PytestAdapter
    from issueforge.config import ConfigError, load_config

    try:
        config = load_config(path)
    except ConfigError as error:
        for violation in error.violations:
            typer.echo(str(violation), err=True)
        raise typer.Exit(1)

    adapter = PytestAdapter()
    probe = adapter.probe()

    typer.echo(f"adapter: {adapter.framework} (reporter={adapter.reporter})")
    typer.echo(f"reporter_version: {probe.reporter_version}")
    typer.echo(f"capabilities: {probe.capabilities}")
    typer.echo(f"baseline: {config.baseline}")
    typer.echo(f"acceptance: {config.acceptance}")
    typer.echo(f"lint: {config.lint}")
    typer.echo(f"build: {config.build}")
    typer.echo(f"contract_paths: {config.contract_paths}")
    typer.echo(f"sensitive_fields: {config.sensitive_fields}")


provider_app = typer.Typer(help="Inspect and verify AI provider configuration.")
app.add_typer(provider_app, name="provider")


@provider_app.command("check")
def provider_check(
    config: Path = typer.Option(..., "--config", help="Path to a providers TOML file."),
) -> None:
    """Verify the primary provider's CLI is authenticated; fail closed with no fallback launch."""
    import tomllib

    from issueforge import providers
    from issueforge.config import ConfigError, load_roles

    try:
        data = tomllib.loads(config.read_text())
        roles = load_roles(data)
    except ConfigError as error:
        for violation in error.violations:
            typer.echo(str(violation), err=True)
        raise typer.Exit(1) from None

    typer.echo(f"role primary -> provider {roles.primary.name}")
    if roles.secondary is not None:
        typer.echo(f"role secondary -> provider {roles.secondary.name}")

    if not providers.authenticated(roles.primary):
        typer.echo(f"provider {roles.primary.name!r} is not authenticated", err=True)
        raise typer.Exit(1)

    typer.echo("authenticated: primary")


repo_app = typer.Typer(help="Register and list verified local clones.")
app.add_typer(repo_app, name="repo")


@repo_app.command("add")
def repo_add(spec: str = typer.Argument(..., help="ALIAS:PATH of the clone to register.")) -> None:
    """Register a verified existing clone under ALIAS, or refuse loudly on stderr."""
    from issueforge.registry import RegistryError, register

    alias, sep, path_token = spec.partition(":")
    if not sep or not alias or not path_token:
        typer.echo(f"invalid ALIAS:PATH argument: {spec!r}", err=True)
        raise typer.Exit(1)

    try:
        register(alias, path_token)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None

    typer.echo(f"registered {alias}")


@repo_app.command("list")
def repo_list() -> None:
    """Print each registered clone: alias, absolute path, slug, default branch, baseline, adapter."""
    from issueforge.registry import Registry, RegistryError

    try:
        entries = Registry.load().entries()
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None

    for entry in entries:
        baseline = " ".join(entry.baseline)
        typer.echo(
            f"{entry.alias}\t{entry.path}\t{entry.slug}\t{entry.default_branch}\t"
            f"{baseline}\t{entry.adapter}"
        )


@app.command()
def run(
    spec: str = typer.Argument(..., help="ALIAS#N of the issue to run."),
    scope: list[str] = typer.Option(
        None,
        "--scope",
        help="Approved write-scope path (repeat per path). Answers the pre-authoring scope gate.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Pre-approve the authored acceptance contract instead of being asked.",
    ),
) -> None:
    """Run an issue end-to-end: resolve ALIAS#N, verify it is open, then drive the composed PoC-D
    default stage (candidate -> readiness -> deliver one PR, landing waiting-for-merge; #115).

    Two human gates run over stdin. From a terminal they are asked interactively; without a TTY they
    would hit EOF and auto-reject, so a keyboard-less run must answer BOTH up front with ``--scope``
    and ``--yes`` (#140). Missing either is a LOUD refusal here, before any run is admitted.
    """
    from issueforge import engine

    approved_scope = list(scope) if scope else None
    if not _isatty():
        missing = [
            flag
            for flag, given in (("--scope", approved_scope is not None), ("--yes", yes))
            if not given
        ]
        if missing:
            typer.echo(
                f"run needs answers to its human gates but stdin is not a terminal; missing "
                f"{' and '.join(missing)}. Pass --scope <path> (repeat per path) and --yes to run "
                f"headless, or run it in a terminal to be asked interactively.",
                err=True,
            )
            raise typer.Exit(2)

    engine.run(spec, approved_scope=approved_scope, auto_approve_contract=yes)


@app.command()
def queue() -> None:
    """List the active run, then the queued runs in FIFO order."""
    from issueforge import store

    state = store.RunStore().read_queue()
    if state.get("active"):
        typer.echo(state["active"])
    for run_id in state.get("queue", []):
        typer.echo(run_id)


@app.command()
def pause(run_id: str = typer.Argument(..., help="Run id to pause.")) -> None:
    """Pause the active running run (keeps the worker slot)."""
    from issueforge import engine

    engine.pause(run_id)


@app.command()
def park(run_id: str = typer.Argument(..., help="Run id to park.")) -> None:
    """Park a run (releases the worker and advances the FIFO)."""
    from issueforge import engine

    engine.park(run_id)


@app.command()
def cancel(run_id: str = typer.Argument(..., help="Run id to cancel.")) -> None:
    """Cancel a queued run or the current paused run."""
    from issueforge import engine

    engine.cancel(run_id)


@app.command("continue")
def continue_(run_id: str = typer.Argument(..., help="Run id to resume.")) -> None:
    """Resume a paused, parked, or crash-orphaned run."""
    from issueforge import engine

    engine.continue_run(run_id)


@app.command()
def reorder(
    run_id: str = typer.Argument(..., help="Queued run id to move."),
    index: int = typer.Argument(..., help="Absolute 0-based target position in the FIFO."),
) -> None:
    """Move a queued run to an absolute 0-based index in the FIFO."""
    from issueforge import engine

    engine.reorder(run_id, index)


@app.command()
def version() -> None:
    """Show the installed IssueForge version."""
    from issueforge import __version__

    typer.echo(__version__)


@app.command()
def tui() -> None:
    """Open the IssueForge terminal interface."""
    from issueforge.tui import IssueForgeApp

    IssueForgeApp().run()


if __name__ == "__main__":
    app()
