# IssueForge — Claude project instructions

IssueForge is a human-gated TDD issue runner: a Python workflow engine (Typer CLI + Textual TUI) that turns a
registered GitHub issue into one human-approved, TDD-built, green PR. It is being **extracted from MARVIN's
build harness** as a standalone, decoupled product.

Global rules in `~/.claude/CLAUDE.md` still apply. This file adds only what is specific to IssueForge.

## Canonical documents — read these, do not restate them

- `docs/prd.md` — the PRD (issue #1). 59 acceptance criteria, decisions **D1–D6**. The authority.
- `docs/architecture.md` — the v1 architecture and the MARVIN extraction rule.
- `docs/planning/decomposition.md` — the current decomposition (25 v1 slices + 2 deferred-v2 issues; 59/59
  criteria owned), identified immutably by the rename-provenance commit recorded in the filed issue footers.
  `docs/planning/reviews/` holds the historical drafts, review transcripts, and blocked failure reports; Git,
  not a filename version suffix, supplies their revision history.
- `docs/provenance/marvin/` — read-only migration evidence copied from MARVIN.

Before claiming what a requirement says, quote the PRD line. A prior run deleted a correct criterion by
believing a reviewer's unverified claim about the PRD; do not repeat it.

## The MARVIN boundary — the point of this project

MARVIN at `/Users/matthewdruhl/marvin` is **read-only migration provenance**, nothing more.

- **Never modify MARVIN** — no file, state, skill, ledger, configuration, generated artifact, or GitHub issue.
- **IssueForge has no runtime dependency on a MARVIN checkout** (US-11.5). It reads MARVIN only while
  extracting; that access is transitional scaffolding (`.claude/settings.local.json`), removed when extraction
  completes.
- MARVIN is two things and only one is extracted: the **build harness** (the pipeline — `merged_runner`,
  `agent_runs_lib`, `build_recovery`, the `check_acceptance_*` / `validate_*` family, `ci_acceptance_gate`,
  `schedule_waves`, and the `spec-up` / `spec-dev` / `spec-wave` / `merged` / `tdd` / `prd-to-issues` skills)
  is what IssueForge extracts. The **chief-of-staff workspace** (`marvin_start`, `generate_current`, `gmail_*`,
  quiz/resume/TWC/habits) is NOT extracted; it stays a consumer that pulls from IssueForge's interface (US-11.7).
  MARVIN does not draw that line itself — cutting the seams where the harness reaches into workspace state is a
  primary v1 goal (see decision D6).

## Development

- Python 3.12+. Use `uv` (`uv run pytest`, `uv run ruff check`) — never `pip`.
- Never commit directly to `main`; branch → PR → merge. A `no-main-commit` hook enforces this.
- Never merge PRs; merging is Matt's.

## Process rules that repeatedly mattered (carried from MARVIN's hard-won lessons)

- **Guarded AI launch.** When invoking a review CLI (`codex exec`): stdin closed (`< /dev/null`), stderr
  captured to a file (never `2>/dev/null`), a wall-clock timeout (`timeout(1)` does not exist on macOS — use
  `perl -e 'alarm N; exec @ARGV'`), full output persisted, and **empty output or a non-zero exit = FAILED,
  never a pass**. The reviewer has **no network** — materialize every input to local disk first.
- **Verify at the boundary.** A push "succeeded" only when `git rev-parse origin/<branch>` shows the sha. A PR
  is merged only when `gh pr view` says so. A failed read is never negative evidence.
- **Stacked PRs merge bottom-up**, and a branch delete with open stacked PRs based on it closes them — retarget
  to `main` and verify before deleting.
