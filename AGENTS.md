# IssueForge — Codex / agent instructions

IssueForge is a human-gated TDD issue runner (Python workflow engine, Typer CLI + Textual TUI) being extracted
from MARVIN's build harness as a standalone, decoupled product.

The full project rules live in `CLAUDE.md`; read it. This file is the Codex-side mirror and repeats only the
load-bearing constraints.

## Authority

- `docs/prd-v1.md` is the specification (59 acceptance criteria, decisions D1–D6). Quote its lines rather than
  paraphrasing them.
- `docs/architecture.md` is the architecture and extraction rule.
- `docs/provenance/marvin/` is read-only evidence.

## Hard constraints

- **Never modify MARVIN** (`/Users/matthewdruhl/marvin`) — it is read-only provenance.
- **IssueForge stays self-contained** — no runtime dependency on a MARVIN checkout (US-11.5), no writes into
  MARVIN's state or files (US-11.6).
- **Never commit to `main`** (a `no-main-commit` hook enforces it); branch → PR → merge.
- **Never merge PRs.**
- Python 3.12+, `uv`, `pytest`, `ruff`.

## When reviewing

You are the independent review gate. Empty output or a non-zero exit is a FAILED review, never a pass. You run
without network — everything you need is materialized to local disk before you are invoked. Report an explicit
`ACCEPT` or `REVISE` with blocking findings; do not reopen settled decisions (D1–D6).
