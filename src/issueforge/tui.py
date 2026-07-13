"""Basic Textual interface for IssueForge."""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class IssueForgeApp(App[None]):
    """Initial IssueForge status shell."""

    TITLE = "IssueForge"
    SUB_TITLE = "Human-gated TDD workflow"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("No active run. The workflow engine is not implemented yet.")
        yield Footer()
