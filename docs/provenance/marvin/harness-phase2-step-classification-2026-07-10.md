# Phase 2 Prep: spec-dev Runner — Step Classification

Source: `skills/spec-dev/SKILL.md` (read 2026-07-10, post-#699 revision), `scripts/build_recovery.py`,
`scripts/check_acceptance_integrity.py`, `context/agent-contract.md`, `context/agent-run-logging.md`,
issue #404 (2026-07-09 direction comment), #712 (Phase 2 definition), #713 (stderr capture).

Classification legend:
- **(a)** deterministic, existing script (named)
- **(b)** deterministic but unwritten (runner code to build)
- **(c)** LLM work that stays in the headless implementer session
- **(d)** human gate

## Classification Table

| # | Skill step / phase (SKILL.md location) | Class | Existing script / notes |
|---|---|---|---|
| 1 | Repo/project lookup (Step 1.1-1.2, `state/projects.md`) | (b) | Becomes explicit runner input (issue + owner/repo + path); `scripts/list_projects.py` + the #700 resolver seam cover registry resolution. |
| 2 | Fetch issue + comments (Step 1.3-1.4) | (a) | `gh issue view <n> --repo <slug> --comments`; trivial wrapper. |
| 3 | Slice names its acceptance tests (Requirements section) | (a) | `scripts/validate_slice_issue.py` — lints the "Targets acceptance tests" section for a parseable committed-test reference. |
| 4 | Acceptance criteria exist / not vague (Step 1.5) | (d)+(b) | Presence is lintable; "vague" is a judgment. In the harness this collapses into the upstream gate (contracted issue = precondition); a runner refusal message replaces the interactive stop. |
| 5 | Entry gate per targeted test (Step 1.6) | (a) | `scripts/check_acceptance_integrity.py --entry --base <file@suite-ref> --test NAME`. Exit != 0 is a hard refusal. Runner must resolve the suite ref (main vs approved suite branch tip, stacked mode #618) and verify the suite commit is an ancestor of the build base (`git merge-base --is-ancestor` — (b)). |
| 6 | Explore codebase / footprint recon (Step 2) | (c) or upstream | Today feeds the plan and the handoff prompt's "exact target paths." If the runner takes an approved plan as input, this moved upstream (interactive front); the implementer still explores inside its session. |
| 7 | Plan authoring (Step 3) | (c) upstream | Stays in the conversational front per #404 scope split, OR becomes a headless draft routed through the Phase-4 queue — open question. |
| 8 | Plan approval (Step 3, "Wait for explicit user approval") | (d) | Precondition for the runner. |
| 9 | Save plan to `.plans/plan-issue-<n>.md` (Step 4) | (b) | Trivial file write; runner reads it back as an input artifact. |
| 10 | Handoff prompt assembly, six sections (#676) | (b) | Deterministic template render from inputs (goal, repo+paths, constraints, non-goals, verbatim proof command, output shape). Runner should REFUSE to launch if any section is empty — the skill says "fill it in before spawning." |
| 11 | Worktree lifecycle (Spawning the Agent + agent-contract cross-repo pattern) | (b) | Prose today (conditional isolation: Agent-tool worktree for marvin, linked `git worktree add /tmp/<repo>-wt-<ts>-<pid>` for cross-repo). Runner owns create/serialize-add-remove/teardown; the implementer never runs `git worktree`. |
| 12 | Spawn implementer | (b) wrapper around (c) | Replaces the Agent tool with `claude -p` + `CLAUDE_CODE_OAUTH_TOKEN` (plan billing, #404). Both output streams captured per run (#713). |
| 13 | Inner TDD loop: unit tests, minimal impl, red-green (agent steps 2-3, /tdd) | (c) | Headless session. Keeps the 3 in-agent attempts rule and test-run economy prose in its prompt. |
| 14 | Flip pending -> required (agent step 4) | **conflict** | Today the IMPLEMENTER edits the acceptance file to remove the marker. Under "suite physically out of write scope" this is impossible. Runner must own the flip (scripted marker removal, (b), verified by #491 which sanctions exactly marker-only removal) — or a narrow write exception is carved. Open question #1. |
| 15 | Verify vs spec + full-suite regression run (agent step 5) | (c) inside + (a/b) outside | Implementer runs tests as feedback; the AUTHORITATIVE run is the runner executing the verbatim proof command + full suite after the session ends (the reviewer-side "exact proof command" rule, #676). Command execution is (b); `scripts/ci_acceptance_gate.py` exists for the CI side. |
| 16 | Commit + push + PR (agent step 6) | split (c)/(b) | Commits can stay in-session; push + PR creation should move to the runner so push verification is structural. |
| 17 | Push verification (memory `feedback_verify_agent_push`, #604/#607) | (b) | Prose/memory today: agents report "pushed" when the push silently failed. Runner: `git fetch` + assert `origin/<branch>` contains the worktree sha BEFORE cross-review; recover from the worktree if missing. Must be code. |
| 18 | Log the run (agent-run-logging.md) | (a) | `scripts/log_run.py` at launch (`--skill spec-dev --branch ...`), `--update` at terminal with cost tuple + `--auto-usd` (or `--unmeasurable`); `agent_runs_lib` under the hood; `scripts/validate_agent_runs.py` enforces terminal shape. |
| 19 | Weaken-check (always-on, before cross-review) | (a) | `check_acceptance_integrity.py --old <file@suite-ref> --new <file@branch>` per targeted acceptance file in the diff. Runner owns the TIMING (after implementer done + push verified, before Codex) and the suite-ref resolution. Non-zero = blocking; independent of Codex availability. |
| 20 | Codex cross-review gate (#617 batched contract) | (b) wrapper around external LLM | Runner invokes plugin path or guarded raw `codex exec --skip-git-repo-check -C "$ROOT" "$PROMPT" </dev/null > out.txt 2>&1` with wall-clock timeout; non-zero exit OR empty output = FAILED review (`skipped:codex-exec-failed`), never a pass. `2>/dev/null` forbidden (#713). One exhaustive pass -> batch fix -> one confirmation round; reopen only on a NEW blocking finding. Verdict recorded via `log_run.py --update` `cross_review` field. |
| 21 | Reviewer fix-rounds (cap b: 2 rounds, #676) | (b) loop, (c) fixes | Runner counts rounds and escalates after 2; WHO applies fixes headlessly (resumed session vs fresh dispatch) is open question #3. |
| 22 | Build recovery (#675) | (a) core + (b) glue | `scripts/build_recovery.py`: `record_attempt` -> `next_action(cap=2)` -> `reset_worktree` + fresh dispatch with `build_retry_prompt` OR `escalate_run`. See below. |
| 23 | Trace capture ("compact error trace OUTSIDE the worktree") | (b) or cheap (c) | Failing-test id + assertion diff parseable from pytest output deterministically; open question #4. |
| 24 | Merge decision | (d) | Runner never merges (contract + `feedback_never_auto_merge`). Terminal runner state = PR open, run `needs-review`, cross_review recorded. |

## Prose-encoded failure recovery the runner must own

1. **Three nested retry caps, kept distinct** (#699 prose): (a) 3 in-agent attempts per failing test — stays in the implementer prompt; (b) 2 reviewer fix-rounds per slice — runner loop counter; (c) 2 fresh-agent recovery re-dispatches — `next_action(attempt, cap=2)`. Each outer cap contains many of the inner. The runner must not conflate (b) and (c).
2. **Build-failure signal enumeration** (currently operator judgment): implementer process errors/dies (non-zero exit / timeout of `claude -p`); committed suite still red after "done" (runner's proof-command run); Codex gate rejects with a concrete failure scenario. Each triggers the #675 loop.
3. **Silent-push verification** (`feedback_verify_agent_push`): verify `origin/<branch>` has the sha before cross-review; recover the commit from the worktree if the push silently failed. Currently only memory + prose.
4. **Weaken-check timing**: runs on EVERY targeted acceptance file in the diff, against the suite ref (main, or suite-branch tip in stacked mode), after implementation and before the Codex gate; blocking regardless of Codex availability.
5. **Codex silent-failure guards**: stdin block without TTY, untrusted-dir error, empty output read as clean review; guarded invocation + empty-output-is-failure is a runner invariant, plus stderr capture (#713) so hung vs failed is distinguishable.
6. **Stacked-mode ref discipline** (#618): entry gate and weaken-check compare against the suite ref; suite commit must be an ancestor of the build branch; suite PR merges first.
7. **Context-rot avoidance**: retry prompts carry frozen contract + compact trace, NEVER the prior transcript (`build_retry_prompt` enforces; runner must not "helpfully" attach logs).

## build_recovery.py (#675) — loop contract

- **Attempt ledger**: `record_attempt(logs_dir, slug, run_id)` increments `recovery_attempts` INSIDE the store lock via `agent_runs_lib.apply_run` (read-modify-write closure), so concurrent drivers cannot under-count and under-enforce the cap. 1-based; non-int (incl. bool) persisted values raise instead of coercing.
- **Decision**: `next_action(attempt, cap=2)` — "retry" while `attempt <= cap`, else "escalate"; cap is a parameter (a wave can tune it).
- **Reset**: `reset_worktree(worktree, base_sha)` verifies `base_sha` is a real commit (`rev-parse --verify ^{commit}`) before `reset --hard` + `clean -fd`, all `git -C`-scoped so the failure notes outside the worktree survive.
- **Retry prompt**: `build_retry_prompt(trace, contract, transcript=None)` — full frozen contract + full compact trace; transcript deliberately dropped.
- **Escalation**: `escalate_run(...)` writes a validator-clean terminal record via `apply_run`: status `needs-review` (store has no "blocked"), notes MERGED after existing notes inside the lock, `completed` date, `unmeasurable: True` cost waiver, `cross_review: "skipped:build recovery cap exhausted; escalated for human review"`.
- **apply_run atomicity**: single locked read-modify-write primitive; the runner should route ALL store mutations through it (or `update_run`) rather than hand-editing JSON.

**Runner reuses**: all five functions as-is — the module was built as exactly this seam ("the Claude-loop re-dispatch action itself stays prose in skills/…; this module is only the testable seam those skills call"). **Runner replaces**: the prose around it — failure-signal detection (coded detectors), trace capture (parser or summarizer), and the re-dispatch action (Agent tool -> `claude -p` subprocess with fresh worktree state).

## Write-scope enforcement: acceptance suite physically out of scope

The #491 weaken-check remains the independent backstop in ALL options.

| Option | What it blocks | What it can't block | Operational cost |
|---|---|---|---|
| **A. settings deny rules + PreToolUse hook in the worktree** (runner writes `.claude/settings.json` into the worktree before launch, or passes `--settings`; deny Edit/Write/NotebookEdit on suite path globs, hook string-checks Bash commands touching suite paths) | The normal paths: every file-tool edit; Bash commands the hook's matcher catches (`sed -i tests/...`, redirects). Known-good mechanism (no-main-commit hook precedent). | Arbitrary shell writes the string matcher misses (`python -c 'open(...,"w")'`, `git checkout -- tests/...`, var-indirected paths — same class as `feedback_cross_repo_commit_hook`). | Low. Runner materializes one settings file per worktree; no infra. Note: SUBAGENTS are hard-denied from writing settings.json (memory), but the runner is harness code writing before launch — not affected. |
| **B. Runner-owned diff boundary gate** (after session ends: `git diff --name-only <base>..HEAD` in the worktree; ANY change under suite paths = automatic build-failure -> #675 recovery; the sanctioned pending-flip is applied by the RUNNER after this gate, then verified with #491) | Everything at the boundary that matters: no suite modification can reach the commit/PR regardless of HOW it was written (tool, shell, git). Deterministic, unforgeable from inside the session. | The write happening in-worktree mid-session (detect-at-boundary, not prevent). An implementer that wastes an attempt weakening then gets reset. | Low. ~10 lines of runner code + the flip script (new, small; #491 already defines "marker-only removal" as the legal diff). |
| **C. chmod a-w on suite files** | Casual/accidental writes through any tool. | Same-uid process can `chmod +w`; git checkout/unlink+rewrite bypasses file perms (directory write is what matters); making the DIRECTORY read-only breaks adding unit tests when they share a tree. | Low to apply, brittle guarantees. Belt-and-suspenders only. |
| **D. Sparse checkout excluding suite paths** (suite lives only in the runner's checkout) | Strongest prevention: the file isn't present to edit; a commit can't casually include it. | Implementer must READ the targeted tests (contract) and RUN them for red-green feedback — excluding them breaks the TDD inner loop unless the runner proxies every test run or injects a read-only copy (which reopens the write question). `git sparse-checkout disable` is same-uid reachable anyway. | High. Restructures the TDD loop; slow feedback via runner proxy. |
| **E. #491 weaken-check post-hoc** | Any semantic weakening that reaches the diff (assertion/operator/constant/marker-downgrade/deletion/rename, #588 transitive data flow). | Out-of-file indirection (imported helpers, conftest — documented scope limit, follow-up class); doesn't prevent, detects. | Zero — exists, already mandated always-on. |

**Recommendation (layered, in this order):** **B + A + E, with C optional and D rejected for v1.**
B is the load-bearing layer: with the runner owning commit-boundary + push + the pending-flip, "physically unable to touch the acceptance suite" is true where it counts — nothing the session writes to suite paths can land, and the flip itself is a runner-applied, #491-verified marker removal. A makes the common paths fail fast inside the session (cheap, good signal: a denied Edit on a suite file logged in the run output is an early adversarial/confusion indicator). E stays as the independent semantic backstop on the final diff. C is a free extra if desired. D costs the TDD feedback loop for a guarantee B already provides at the boundary.

## Runner external surface

**Inputs**
- Issue number + `owner/repo` slug + target repo path (or resolver via registry / #700 seam)
- Contract: targeted acceptance test names + file paths + verbatim proof command + suite ref (main | suite-branch tip, stacked #618)
- Approved plan artifact (`.plans/plan-issue-<n>.md`) — precondition
- Branch name, base sha
- Config: recovery cap (default 2), reviewer-round cap (2), Codex mode (plugin | guarded exec | unavailable), logs dir (`resolve_logs_dir()`), per-attempt wall-clock timeout

**Headless invocation**
- `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) — plan billing, never API key; full CLI, not Agent-SDK `--bare` (API-key-only) per #404 comment
- `claude -p "<six-section handoff prompt (#676) + agent-contract subset>"` with cwd = worktree, deny-rules settings applied
- stdout AND stderr captured to per-run log files (#713 design requirement); `2>/dev/null` forbidden
- Wall-clock timeout per attempt; non-zero exit / timeout = build-failure signal

**Gate order**
1. Preconditions: contracted issue (`validate_slice_issue.py`), approved plan exists, suite ref resolved, stacked ancestor check
2. Entry gate per test: `check_acceptance_integrity.py --entry` (hard refusal)
3. Worktree create + deny-settings materialize + `log_run.py` (status running)
4. Headless implementer attempt (in-agent 3-attempt rule inside the prompt)
5. Runner verification: verbatim proof command + full-suite regression
6. Suite-boundary diff gate (`git diff --name-only`), then runner applies pending-flip, then weaken-check `--old/--new` per targeted file
7. Push + push-verification (`origin/<branch>` contains sha)
8. Codex cross-review (batched #617; ≤2 fix-rounds; guarded invocation; verdict recorded)
9. Any failure at 4-8 -> #675 loop: `record_attempt` -> `next_action(cap=2)` -> `reset_worktree` + fresh dispatch (`build_retry_prompt`) | `escalate_run`
10. Terminal: PR open, run `needs-review` with cost tuple (+`--auto-usd`) and `cross_review` verdict; Matt merges (human gate)

**Outputs**
- PR (extended format: Acceptance Tests Flipped + Full Suite sections)
- Run-store record (per-repo `agent-runs.json`): terminal status, output URL, notes, `recovery_attempts` ledger, cost tuple `tokens/duration_ms/model/usd` or `unmeasurable`, `cross_review` verdict — `validate_agent_runs.py`-clean
- Per-run artifact dir: both output streams per attempt, traces, weaken-check/entry-gate outputs
- On cap exhaustion: `needs-review` escalation record with accumulated notes (worktree retained? — open question #5)
