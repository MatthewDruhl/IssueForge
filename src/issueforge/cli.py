"""IssueForge command-line entry point."""

from pathlib import Path

import typer

app = typer.Typer(help="Turn GitHub issues into human-approved, TDD-built pull requests.")

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
    """Print each registered alias and its normalized origin slug."""
    from issueforge.registry import Registry

    for entry in Registry.load().entries():
        typer.echo(f"{entry.alias}\t{entry.slug}")


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
