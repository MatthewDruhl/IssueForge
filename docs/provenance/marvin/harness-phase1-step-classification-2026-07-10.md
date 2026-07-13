# Merged Runner — Per-Step Determinism Classification

Source: `skills/merged/SKILL.md` (repo copy, updated 2026-07-10). Classes:

- **(a)** fully deterministic today (pure gh/git/python; existing script named)
- **(b)** deterministic but code doesn't exist yet (missing piece described)
- **(c)** genuine judgment (LLM call or human)

| Step | What it does | Class | Detail |
|---|---|---|---|
| Scope/discovery (L26-29) | No-arg mode finds "recently-merged-but-uncleaned PRs" across all repos in `state/projects.md`; repo arg narrows | (b) | `gh pr list --state merged` is trivial; the **uncleaned predicate** (recency window + branch-still-exists / run-still-needs-review / issue-still-open) is undefined as code. Registry reader exists: `scripts/list_projects.py` (#536). |
| 1. Verify merge state (L35) | `gh pr view <n> --json state,mergedAt,baseRefName`; skip non-merged | (a) | Pure gh. No script needed. |
| 1. Stale-base check (L37-44) | Base must be default branch | (a) | `scripts/check_build_pr_base.py` (#673) — pure core `check_build_pr_base(base_ref, default_branch)`, CLI exit 0/1. |
| 1. Reachability confirmation (L44) | "Confirm the change is actually reachable from origin/main" when base was stale | (b) | Deterministic (`git fetch` + `git merge-base --is-ancestor <mergeCommit> origin/main`, or content check for squash) but no script exists. |
| 1. Stranding recovery (L44) | Cherry-pick stranded squash onto fresh main, open new PR, hold cleanup on branches carrying the stranded commit | (c) | Detection is (b); deciding to recover, resolving conflicts, and composing the recovery PR is judgment. Runner should detect + halt + report. |
| 2. Sync main (L48) | `git -C <path> checkout main && git -C <path> pull` | (a) | Pure git. Edge (b): behavior on a dirty main checkout or detached HEAD is unspecified — needs a codified fail-and-flag path. |
| 3. Delete merged branches (L52) | Local + remote delete; never force-delete unmerged | (b) | `git branch -d` self-guards for true merges, but **squash merges make every branch "unmerged" by ancestry** — the runner needs squash-merge content verification (e.g. headRefOid == PR's merged head, or `git cherry`/diff-empty check) before a `-D`/remote delete. No script exists. |
| 3. Suite-branch guard (L54, #647) | Before deleting a merged suite branch: `gh pr list --base <branch>`, retarget each open PR to main (`gh pr edit --base main`), verify, then delete; recovery = restore-by-sha, reopen, retarget, delete again | (b) | Fully mechanical retarget-then-verify-then-delete order, but it exists only as SKILL.md prose (#647 was a skill edit, not a script). Prime candidate for the runner's first hard-won codified rule. |
| 4. Worktree cleanup (L58) | Find worktrees on merged branches; clean → `git worktree remove` + `prune`; dirty → flag and skip | (b) mechanics, (c) disposition | `git worktree list --porcelain` + `git status --porcelain` + remove/prune is deterministic, no script exists. The flag-and-skip policy IS deterministic; only the eventual fate of a dirty worktree's contents is judgment, and the skill already assigns that to the report/human. |
| 5. Close linked issues (L62) | Close each GitHub-linked issue not auto-closed, with a comment naming the PR; nothing beyond them | (b) | `gh pr view --json closingIssuesReferences` + `gh issue close --comment` is fully deterministic **if** scope is restricted to formal closing linkage. Prose-referenced ("relates to #N") issues would be (c) — the skill's "nothing beyond them" rule suggests dropping those to the report. |
| 5.5. Agent-run close-out (L66-68, #690) | Flip `needs-review` → `merged` for the run whose `output` == PR URL | (a) | `close_run_for_pr(store, pr_url)` in `scripts/agent_runs_lib.py` — idempotent, guarded, never promotes `running`. Small (b): it is an in-memory seam; a thin locked load→flip→atomic-write CLI driver (reusing `_repo_lock`/`_atomic_write_log`/`resolve_logs_dir`/`repo_slug`) doesn't exist yet. |
| 6. Conditional test re-run (L72) | Re-run suite on fresh main only when merged diff touched code; note skips | (b) data + (c) triage | Diff file list (`gh pr view --json files`) is (a); the code-vs-docs classifier rules and the **per-repo test command** are undefined (registry has no testCommand field — resolveProject gap). Interpreting a red main is (c): runner reports pass/fail, triage stays human/LLM. |
| 7. What's-next report (L76-80) | Open PRs in stack order (bases verified), unblocked issues, PENDING suites ready for /spec-dev | (b) data, (c) composition | PR list + base verification + topological stack order: deterministic, no script. Pending-suite detection: deterministic scan for literal `xfail(strict=True)` markers on main (the #491 guard convention) mapped to issues — no runner-facing script. "Issues the merge unblocked" is (b) where a blocked-by/label convention exists (wave:/serialize: labels), (c) otherwise. Composing prose is (c) or a fixed template — decision for the PRD. |
| HB: never merge/approve/close a PR (L84) | — | (a) | Enforced by construction: the runner simply contains no merge/approve/close-PR call. Stronger than the skill's prose. |
| HB: never force-delete unmerged (L85) | — | (b) | Depends on the Step-3 squash-verification piece. |
| HB: never watches/polls (L86) | Matt invokes; no background loop | (a) by construction / open question | Trivially satisfied by a manual CLI; in tension with #691's scheduled sweep — trigger model is a PRD decision. |
| HB: verify before acting (L87) | Merge state from gh, never from invocation wording | (a) | Same `gh pr view` call as Step 1. |

## Rough determinism budget

Matches #404's "~90% deterministic" claim: everything except (1) stranding/dirty-worktree/red-main disposition and (2) report prose is pure gh/git/python. The two existing scripts (`check_build_pr_base.py`, `agent_runs_lib.close_run_for_pr`) cover Steps 1-guard and 5.5; the biggest missing deterministic pieces are the **#647 suite-branch delete order** and **squash-merge content verification**, both currently encoded only as SKILL.md prose learned from real incidents (#633, #640-#646).
