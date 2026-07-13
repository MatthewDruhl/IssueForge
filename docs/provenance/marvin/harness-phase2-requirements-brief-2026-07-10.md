# Requirements Brief — spec-dev Runner (Roadmap #712 Phase 2)

**Sources analyzed:**
- `skills/spec-dev/SKILL.md` (skill prose, rev 2026-07-10 / #699)
- `scripts/build_recovery.py` (#675 seam, read 2026-07-10)
- `scripts/check_acceptance_integrity.py` (#491/#610/#627/#588, docstring + CLI)
- Issue #404, 2026-07-09 direction-update comment (harness definition, billing constraint)
- Issue #712 body (Phase 2 scope: outer loop in harness code; suite physically outside implementer write scope)
- Issue #713 (both output streams logged per run — Fix item 2 names harness runners)
- `context/agent-contract.md`, `context/agent-run-logging.md`
- Memory: `feedback_verify_agent_push` (#604/#607), `feedback_never_auto_merge`, `feedback_cross_repo_commit_hook`

## Agreed Requirements
| # | Requirement | Who | Source | Type | Confidence |
|---|-------------|-----|--------|------|------------|
| 1 | Runner is harness code orchestrating plan-authenticated CLIs; headless `claude -p` with `CLAUDE_CODE_OAUTH_TOKEN`, never raw API; full CLI, not Agent-SDK `--bare` | Matt | #404 comment 2026-07-09 | constraint | explicit |
| 2 | Harness owns the outer loop: #491 entry + weaken checks, #675 recovery retries, worktree lifecycle, Codex cross-review invocation | Matt | #712 Phase 2 | feature | explicit |
| 3 | Headless session does ONLY the TDD implementation; acceptance suite physically outside its write scope | Matt | #404 comment + #712 Phase 2 | constraint | explicit |
| 4 | #491 weaken-check runs always (model-free), against the suite ref, before the Codex gate; independent backstop regardless of prevention mechanism | Matt | skills/spec-dev/SKILL.md + #491 | constraint | explicit |
| 5 | Entry gate: `check_acceptance_integrity.py --entry` per targeted test on the suite ref; non-zero = hard refusal | Matt | SKILL.md Step 1.6 | feature | explicit |
| 6 | Recovery loop reuses `build_recovery.py` as-is: `record_attempt` -> `next_action(cap=2)` -> `reset_worktree` + `build_retry_prompt` (no transcript), or `escalate_run` (validator-clean needs-review) | Matt | #675 + SKILL.md "Build recovery" | feature | explicit |
| 7 | Three retry caps stay distinct and nested: 3 in-agent attempts (prompt), 2 reviewer fix-rounds (runner loop), 2 recovery re-dispatches (`next_action`) | Matt | SKILL.md #699 prose | constraint | explicit |
| 8 | Handoff prompt must render all six #676 sections (Goal, repo+paths, Constraints, Non-goals, verbatim proof command, Output shape); runner refuses to launch if any is empty | Matt | SKILL.md #676 | constraint | explicit |
| 9 | Runner-side verification runs the EXACT proof command (verbatim, not paraphrased) plus the full-suite regression run | Matt | SKILL.md #676 reviewer rule + agent step 5 | feature | explicit |
| 10 | Push verification before cross-review: `origin/<branch>` must contain the sha; recover from the worktree if the push silently failed | Matt | memory feedback_verify_agent_push (#604/#607) | constraint | explicit |
| 11 | Codex gate: batched contract (#617) — one exhaustive pass, batch fix, one confirmation round; guarded invocation (`</dev/null`, timeout, empty output = FAILED not pass); verdict recorded in `cross_review` via `log_run.py --update` | Matt | SKILL.md Cross-Review Gate + #575/#617 | feature | explicit |
| 12 | Both output streams of every background CLI launch logged per run; `2>/dev/null` forbidden | Matt | #713 (Fix item 2 names harness runners) | constraint | explicit |
| 13 | Run-store lifecycle: `log_run.py` at launch, terminal `--update` with cost tuple (`--tokens/--duration-ms/--model --auto-usd` or `--unmeasurable`) and `cross_review`; `validate_agent_runs.py`-clean | Matt | context/agent-run-logging.md (#438/#497/#577) | feature | explicit |
| 14 | Runner never merges PRs or closes issues; terminal state is PR open + run `needs-review`; merge is a human gate | Matt | agent-contract + feedback_never_auto_merge | constraint | explicit |
| 15 | Stacked mode (#618): entry gate and weaken-check compare against the suite ref (main or suite-branch tip); suite commit must be an ancestor of the build branch | Matt | SKILL.md Requirements + 2026-07-08 changelog | feature | explicit |
| 16 | Script invocations go through the #700 resolver seam (no literal absolute paths) — Phase 0 exit criterion feeds this runner | Matt | #712 Phase 0 | constraint | explicit |
| 17 | Retry prompts carry frozen contract + compact trace, never the prior transcript (context-rot avoidance) | Matt | build_recovery.py / #675 | constraint | explicit |
| 18 | Worktree lifecycle owned by the runner (create, serialize add/remove per target, teardown); implementer never manages worktrees | inferred from #712 "worktree lifecycle" + agent-contract pattern | #712 Phase 2 | feature | inferred |

## Conflicts
- **The pending -> required flip vs. suite write scope.** SKILL.md agent step 4 has the IMPLEMENTER edit the acceptance file to remove the pending marker; #712 Phase 2 makes the suite physically unwritable by the implementer. Both cannot hold. Resolution direction: the runner applies the flip (scripted marker-only removal, verified by #491, which sanctions exactly that edit) — but this is a spec change to the skill's contract and needs Matt's sign-off (Open Question 1).
- **Reviewer fix-rounds vs. headless execution.** SKILL.md's cap (b) assumes a reviewer who can send the implementer back for fixes mid-slice; a fire-and-forget `claude -p` session has ended by the time Codex findings arrive. Mechanism unresolved (Open Question 3).
- **Plan phase placement.** SKILL.md Phase 1 is interactive (explore, plan, approve); #404 scope split says the conversational front stays in sessions, but doesn't state whether spec-dev's plan step is "front" (session produces the approved plan, runner consumes it) or harness-hosted via the Phase-4 queue (Open Question 2).

## Gaps
- No defined machine-readable output contract for the headless session (how the runner parses "done", files changed, test output — `--output-format` choice, sentinel, or exit-code convention).
- No coded enumeration of build-failure signals (SKILL.md lists them in prose: implementer errors/dies, suite red after "done", Codex rejects); detectors are unwritten.
- No trace-capture tool: the "compact error trace" (failing-test id + assertion diff) that feeds `build_retry_prompt` has no parser; today it is operator judgment.
- No pending-flip script exists (the legal marker-only edit #491 verifies has no writer).
- Plan rate-limit/concurrency ceiling for `claude -p` under the OAuth token is undocumented (#712 open question, first-PRD scope; affects per-attempt timeout and parallelism defaults).
- Worktree retention policy on escalation is unstated (forensics value per feedback_verify_agent_push recovery vs. disk hygiene of auto-clean).
- Non-pytest projects: proof command is framework-neutral in prose, but #491 entry/weaken checks are pytest/Python-AST-specific; runner behavior on a non-Python target repo is undefined.

## Open Questions
1. **Who performs the pending -> required flip?** Runner applies a scripted marker-only removal after the suite-boundary diff gate (recommended: #491 already defines the legal diff), or the suite gets a narrow write exception for exactly this edit? This changes the skill contract either way.
2. **Where does the plan phase live?** Does the runner REQUIRE a pre-approved plan artifact from an interactive session (`.plans/plan-issue-<n>.md` as a hard input), or should it headlessly draft the plan and route approval through the future Phase-4 queue? Determines the runner's entry precondition and how much of Steps 2-3 moves into harness scope now.
3. **How do reviewer fix-rounds execute headlessly?** After a blocking Codex finding: resume the same session (`claude -p --resume`, preserving context), dispatch a fresh session with the finding as trace (folding cap b into cap c's machinery but keeping separate counters), or escalate straight to human? The skill keeps caps (b) and (c) distinct; the runner needs one coded answer.
4. **Trace capture: deterministic parser or LLM summary?** A pytest-output parser is testable and free but per-framework; a cheap headless summarization call is universal but adds a model dependency inside the recovery loop. Which default (and is a parser-with-LLM-fallback acceptable complexity)?
5. **Worktree + branch disposition on escalation:** keep the worktree and unpushed commits for human forensics (matches the push-recovery memory) or auto-clean with the branch pushed as-is? What does the escalation record need to point at?
6. **Headless output contract + timeout:** which `claude -p` output format does the runner parse for completion/report-back fields, and what per-attempt wall-clock timeout is acceptable before the attempt counts as died (interacts with the unknown plan rate-limit ceiling)?

## Handoff
This brief feeds `/write-a-prd` for the Phase 2 runner. Companion analysis:
`/tmp/phase2-prep/step-classification.md` (full step classification, build_recovery contract,
write-scope enforcement options ranked, runner external surface).
