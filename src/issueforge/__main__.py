"""Enable ``python -m issueforge`` to run the CLI (the acceptance suite invokes it this way)."""

from issueforge.cli import app

if __name__ == "__main__":
    app()
