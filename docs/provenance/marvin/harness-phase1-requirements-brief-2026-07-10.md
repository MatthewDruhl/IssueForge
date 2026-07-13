# Requirements Brief — Merged Runner (Harness Phase 1)

**Feeds:** /write-a-prd interview for roadmap #712 Phase 1 (first harness code per #404's 2026-07-09 direction update).
**Sources:** `skills/merged/SKILL.md` (repo copy, line refs below), issues #404 (direction comment), #712, #690, #691, #694, #647 (via SKILL.md changelog), #673, existing scripts (`scripts/check_build_pr_base.py`, `scripts/agent_runs_lib.py`, `scripts/list_projects.py`).
**Companion:** `/tmp/phase1-prep/step-classification.md` (per-step determinism table).

---

## Agreed Requirements

| ID | Requirement | Source |
|---|---|---|
| R1 | Verify each candidate PR's merge state via `gh pr view --json state,mergedAt,baseRefName` before any cleanup; report and skip PRs that are open or closed-unmerged. Never trust invocation wording. | SKILL.md L35, L87 |
| R2 | Run the stale-base guard on every merged PR (`scripts/check_build_pr_base.py`, exact-match base == default branch). On failure, confirm reachability from `origin/main`; if the squash is stranded, halt cleanup for every branch carrying it and surface a recovery report. | SKILL.md L37-44; #673 |
| R3 | Sync main in each affected repo (`git -C <abs-path> checkout main && git -C <abs-path> pull`) before later steps. All cross-repo commands use literal absolute paths. | SKILL.md L29, L48 |
| R4 | Delete each verified-merged PR's local and remote branch. Never force-delete a branch whose content is not verified merged; flag it in the report instead. (Squash merges require content verification, not ancestry — see G3.) | SKILL.md L52, L85 |
| R5 | Before deleting a merged suite branch: list open PRs based on it (`gh pr list --base`), retarget each to main (`gh pr edit --base main`), verify the new base reads main, then delete. Recovery on mistake: restore branch by sha, reopen PRs, retarget, delete again. | SKILL.md L54; #647 incident (#640-#646) |
| R6 | Remove and prune clean worktrees linked to merged branches; flag dirty worktrees in the report and skip them (never discard uncommitted changes). | SKILL.md L58 |
| R7 | Close each GitHub-linked issue that did not auto-close, with a comment naming the merging PR. Scope is exactly the issues linked to verified-merged PRs, nothing beyond. The `/merged` invocation is the consent. | SKILL.md L62 |
| R8 | Flip the merged PR's agent-run record `needs-review` → `merged` via `close_run_for_pr(store, pr_url)` (exact `output` == PR URL match; idempotent; never promotes `running`). Never use raw `log_run.py --update` for this — it lacks the `needs-review` guard. | SKILL.md L66-68; #690 |
| R9 | Re-run the repo's test suite on fresh main only when the merged diff touched code files; report results. For docs/state-only merges, skip and note the skip. | SKILL.md L72 |
| R10 | Finish with a what's-next report: remaining open PRs in stack order with each upper PR's base verified retargeted, issues the merge unblocked, and PENDING acceptance suites now ready for /spec-dev. | SKILL.md L76-80 |
| R11 | The runner never merges, approves, or closes a PR (permanent non-goal), and never watches or polls for merges — Matt invokes it. | SKILL.md L84-86, L91 |
| R12 | No-arg invocation sweeps all registered repos in one pass; optional args narrow to specific PR numbers and/or one repo. Registry source of truth: `state/projects.md` (machine-readable via `scripts/list_projects.py`; behind a resolveProject seam per #404). | SKILL.md L26-28; #404 |
| R13 | The runner is harness code: deterministic (non-LLM) wherever possible; any LLM step drives plan-authenticated CLIs (`claude -p` + OAuth token), never per-token API. | #404 2026-07-09 comment |
| R14 | Develop in place in the marvin repo with the `resolveProject`/`logRun` seams preserved; lift-out timing deferred. Build order: merged runner is the first runner, after wave-6 cleanup. | #404, #712 |
| R15 | The fates of #691 (scheduled reconciler) and #694 (CI base check) are settled at this spec. | #712 |

## Conflicts

| ID | Conflict | Positions |
|---|---|---|
| C1 | **R11 "never watches/polls" vs #691's scheduled sweep.** #691 wants a scheduled job that polls GitHub PR state to reconcile the agent-runs store — exactly what the /merged hard rule forbids for the cleanup ritual. | Resolve by splitting the boundary: the hard rule protects *mutating cleanup* (branch deletes, issue closes); a scheduled mode restricted to store-record flips + anomaly flags arguably doesn't violate its intent. Needs Matt's call (OQ1). |
| C2 | **R4 "never force-delete unmerged" vs squash-merge mechanics.** Under squash merges (the pipeline default), no feature branch is ever ancestry-merged, so a literal reading of R4 forbids deleting any branch, while current practice deletes them daily. The skill resolves this with human judgment; the runner needs a codified equivalence rule. | Content-verification predicate (G3) replaces ancestry as the definition of "merged" for delete-safety. |

## Gaps

| ID | Gap | Notes |
|---|---|---|
| G1 | "Recently-merged-but-uncleaned" predicate undefined | Recency window? Definition of uncleaned (remote branch exists / run still needs-review / linked issue open / worktree present)? Drives no-arg discovery. |
| G2 | #647 suite-branch safe-delete order exists only as prose | Retarget-then-verify-then-delete must become code; it encodes a real incident (#640-#646 mass-closed 2026-07-08). |
| G3 | Squash-merge content verification before delete | No script decides "this branch's content landed on main" (headRefOid vs merged PR head, `git cherry`, or empty-diff check). Gates R4/C2. |
| G4 | Reachable-from-main check for stale-base detection | `git merge-base --is-ancestor` wrapper (or squash-aware equivalent) doesn't exist; needed for R2's recovery detection. |
| G5 | resolveProject seam is underspecified | `list_projects.py` yields name/path/repo/status/type; the runner also needs per-repo default branch, test command, worktree-root convention, and logs-dir slug. Extend `state/projects-registry.json` or a runner config? (OQ5) |
| G6 | Code-vs-docs diff classifier rules undefined | Per-repo path/extension rules for R9's "touched code" test. |
| G7 | Pending-suite detection for the report unnamed | Deterministic scan for literal `xfail(strict=True)` markers on main mapped to issues (the #491 convention); no runner-facing script exists. |
| G8 | No locked CLI driver around `close_run_for_pr` | The seam is in-memory; the runner needs load-under-lock → flip → atomic-write plumbing (reuse `_repo_lock`, `_atomic_write_log`, `resolve_logs_dir`, `repo_slug`). |
| G9 | Dirty-main / detached-HEAD handling during sync | R3 assumes a clean checkout; failure path unspecified (fail-and-flag proposed). |
| G10 | Runner behavior on detected stranding | Detection (G4) is deterministic; whether the runner attempts scripted recovery (restore-by-sha is mechanical) or strictly halts-and-reports is unspecified. |

## Open Questions (for the PRD interview)

1. **Trigger model and #691's fate.** Does the runner stay manual-invoke only, with a carved-out `--reconcile` sub-mode (store flips + anomaly flags, zero git/branch/issue mutations) that the launchd schedule may call — absorbing and closing #691? Or does #691 remain a separate `reconcile_agent_runs.py` script?
2. **Report surface.** Where does the what's-next report land: stdout consumed by the invoking Claude session (skill becomes a thin wrapper over the runner), a written markdown artifact, or the future approval queue? And is the report a fixed template (fully deterministic) or an LLM composition step?
3. **Judgment escalation.** When the runner hits a genuine-judgment case (dirty worktree, stranded squash, red tests on fresh main), does it strictly halt-and-report for the next interactive session, or may it spawn a headless plan-authenticated `claude -p` triage?
4. **resolveProject shape.** Extend `state/projects-registry.json` (and `list_projects.py`) with default-branch + test-command + worktree-root fields, or give the runner its own config file? (Decides where the seam boundary sits for later runners.)
5. **Issue-close scope.** Strictly `closingIssuesReferences` (formal GitHub linkage) with prose-referenced issues demoted to the report — confirming the "nothing beyond them" rule as code?
6. **Language/runtime + home.** Confirm Python + uv under `marvin/scripts/` (develop-in-place per #404) as a single CLI entry point (e.g. `scripts/merged_runner.py` with subcommands), with lift-out deferred to the harness repo decision.

*(#694 is proposed as coexist-and-share-core, not a runner decision — see companion recommendation — so it carries no open question unless Matt disagrees.)*
