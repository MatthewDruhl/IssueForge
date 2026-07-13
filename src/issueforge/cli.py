"""IssueForge command-line entry point."""

import typer

app = typer.Typer(help="Turn GitHub issues into human-approved, TDD-built pull requests.")


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
