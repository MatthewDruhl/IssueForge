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

## "Done" is the contract, not your judgment

A prior `/tdd` run on #4 looked done without being done: it (1) narrowed the acceptance criteria on its
own authority — one line ("out of scope") used as self-granted license to drop the manifest, `stages/`, and
CI; (2) wrote a Mode-3 test hand-fed inputs so it went green without ever exercising the discovery it claimed
to prove; and (3) skipped fresh-review on its own #4 code because running it would surface the gaps. All three
are the same move: producing a plausible, mergeable artifact instead of satisfying the requirement, then
dressing the shortcut up as a principled "human-judgment follow-up."

Non-negotiable, before the word "done":
- **The acceptance criteria are the contract.** You may not drop, narrow, or defer a deliverable by calling it
  out of scope. If scope is genuinely wrong, stop and ask — you do not get to decide.
- **Every acceptance test must exercise the real path end-to-end.** A test fed hand-crafted inputs so it passes
  without running the behavior under test is not a test; it is a fake checkmark. If a behavior can't be
  exercised, say so — do not simulate the green.
- **Run the adversarial gate on your OWN output, hardest where you least want to.** Skipping the review that
  would expose your gaps is avoidance, not oversight. The gate before "done" is not optional and not yours to
  waive.

**Build spec'd issues through `/spec-up`→`/spec-dev`, not raw `/tdd`.** The committed PENDING acceptance suite
(authored before the build) and the orchestrator-run gate are what make the three rules above enforced instead
of honor-system. Raw `/tdd` has neither, which is how the #4 run had the discretion to skip them.

## Gate-finding triage — PoC before hardening (Matt, 2026-07-23)

The milestone order is **M1 walking skeleton (#18–#22) → M2 full loop + TUI (#23–#25, #27) → M3 v1
completion (#15, #26, #28)**, then batched hardening rounds. To keep the critical path moving, every
review-gate or cross-review finding gets exactly one of two dispositions:

1. **Fix inline before merge** — happy-path correctness: wrong output, a fake or unearned green, data
   loss, security.
2. **File with the `post-v1` label and do NOT schedule** — robustness: crash recovery, concurrency
   edges, adversarial inputs, portability. It waits for a hardening round after v1 is usable.

There is no third bucket; a finding never becomes a new scheduled v1 peer of the feature slices. This
triages **findings about code already satisfying its contract** — it does not touch the rules above:
the acceptance criteria of the issue under build are still never dropped, narrowed, or deferred.
Deferral here is explicit and labeled, never silent.

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
