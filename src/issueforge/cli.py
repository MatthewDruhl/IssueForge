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
