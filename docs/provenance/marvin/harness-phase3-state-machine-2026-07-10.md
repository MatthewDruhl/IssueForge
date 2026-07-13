# Phase 3 Prep: Wave Orchestrator State Machine

Extracted from `skills/spec-wave/SKILL.md` (v2, 2026-07-08 revisions incl. #673 un-stack),
`skills/merged/SKILL.md`, `scripts/check_build_pr_base.py`, `scripts/build_recovery.py`
(per SKILL references), and the incident record in `sessions/2026-07-08.md` /
`sessions/2026-07-09.md`. Feeds a future /write-a-prd interview. Analysis, not code.

Framing (issue #404 last comment / roadmap #712): the orchestrator REPLACES
spec-wave's prose with a state machine; the stranding / merge-order / verify-push
failure class (#633, #640-#646, #647, #673, 3x recurrence week of 2026-07-09) was
prose-orchestration failure and becomes enforced invariants. Build order per #712:
merged runner -> spec-dev runner -> wave orchestrator -> spec-up gating/queue.

---

## 1. Per-issue states

| # | State | Meaning | Entered by (event) | Exits to |
|---|-------|---------|--------------------|----------|
| I0 | `candidate` | Named in the wave (`wave:N` label or user list) | Wave selection | I1 |
| I1 | `recon` | Real footprint being recomputed (read-only Explore agent per repo) | Recon dispatch | I2, `serialized`, `dropped` |
| I2 | `questions-pending` | Recon surfaced author-blocking ambiguity / locked-file auth / undecided semantic | Recon verdict | I3 (answers received) or `dropped` (semantic left unresolved) |
| I3 | `ready-to-author` | Disjointness confirmed, questions answered with defaults accepted/overridden | Step-1.5 batch answered | I4 |
| I4 | `authoring` | Background author drafting the PENDING suite (draft under /tmp subdir, no commit) | Author spawn | I5, I4q (new ambiguity -> back to human, not guessed), I-green |
| I-green | `reclassified-green` | Draft is green against current tree and that is correct: green suite-only contract amendment; no build leg | Author red-verification result | I6 (presented at gate labeled as such) -> I8 -> `closed` on suite merge |
| I5 | `codex-suite-review` | ONE exhaustive adversarial pass -> fix ALL findings in batch -> ONE confirmation round; reopen only on NEW blocking finding from confirmation (#617) | Draft returned + red-verified | I6 (confirmation clean) |
| I6 | `awaiting-human-gate` | Draft + Codex annotations queued for the batched approval | Confirmation round clean | I7 (approved), I5/I4 (adjust/iterate per test), `dropped` (rejected) |
| I7 | `approved` | Human approved this issue's tests plain-English-first | Step-4 gate verdict per test | I8 |
| I8 | `suite-committed` | Suite committed PENDING on branch, suite PR open (per-issue commit; batched-per-repo PR ok); issue body updated to name the committed tests | Orchestrator commit | I9 |
| I9 | `suite-merged` | Suite PR merged to `main` (human merge event); PENDING tests now on main | Merge observed + verified reachable from origin/main | I10 (or `closed` for I-green) |
| I10 | `building` | spec-dev implementer in own linked worktree, branch FORKED FROM MAIN, PR base = `main` (#673 un-stack); run logged per agent-run-logging | Build dispatch (precondition: I9) | I11, I-rec |
| I11 | `build-pushed-verified` | Agent reported done AND origin/<branch> verified to contain the sha (never trust "pushed") | Origin sha check passes | I12 |
| I-rec | `build-recovery` | Stranded build (agent died / suite still red after "done" / Codex gate rejects): `build_recovery.py` loop — `record_attempt` -> `next_action(cap=2)` -> `retry` (reset worktree to base sha, fresh agent, frozen contract+trace) or `escalate` (mark run needs-review with notes, hand to human) | Strand detected | I10 (retry) or human (escalate) |
| I12 | `codex-build-gate` | Cross-review on the PR diff + weaken-check (no acceptance test weakened, unit tests assert real behavior); gate-breaking strengthenings -> follow-up issue, not in-slice patch | Run reaches needs-review | I13 (clean), I10-fix (fix round -> back through I11 origin verification) |
| I13 | `merge-ready` | Gate clean on the CURRENT head sha AND base guard passes (`check_build_pr_base.py` exit 0: baseRefName == `main`, exact equality) | Base guard + gate verdict | I14 |
| I14 | `merged` | Human merged in browser; merge verified (`gh pr view`: MERGED + base was main) and squash reachable from origin/main | Merge event observed | I15 |
| I15 | `closing` | /merged protocol: sync main, delete branch (after retarget check), remove clean worktree, close linked issue, flip run record via `close_run_for_pr`, re-run tests if code touched | Close-out runner | `closed` |
| — | `closed` | Issue closed, flipped tests required and green on main | | terminal |
| — | `serialized` | Real footprints intersect / ordered by shared live gate: dropped to a serialized follow-up, reported | Recon verdict | out of wave |
| — | `dropped` | Mis-scoped (live-clean fail -> split), semantic unresolved, or rejected at gate | | out of wave |

## 2. Wave-level states and barriers

| # | Wave state | Barrier? | Notes |
|---|-----------|----------|-------|
| W0 | `selecting` | — | Gather candidates; labels are advisory, never trusted (re-verify at dispatch) |
| W1 | `recon` | **BARRIER (inherent)** | Conflict graph + shared-live-gate + live-clean checks need ALL footprints before the wave membership verdict. Parallel per repo internally. |
| W1.5 | `question-gate` | **BARRIER (human)** | ONE AskUserQuestion batch: every ambiguity with recommended default + locked-file auths. No author spawns before answers. |
| W2 | `authoring` | pipelines | Fan-out; each draft flows to Codex review as it returns ("do not wait for the whole batch"). |
| W3 | `codex-suite-review` | pipelines | Per-draft, parallel. |
| W4 | `human-gate` | **BARRIER (human, THE gate)** | ONE batched plain-English-first approval across the whole wave, per-test verdicts. Nothing commits before it. |
| W5 | `committing` | per-issue | Each suite commits independently; an iterating suite does not block the others. >=1 suite branch+PR per repo. |
| W5.5 | `suite-merge` | per-issue (human event) | Un-stack (#673): suite PR merges to main FIRST. Deliberate parallelism trade: a build cannot start until ITS suite merged. |
| W6 | `building` | per-issue | File-disjoint builds do not collide; independent recovery loops. |
| W7 | `codex-build-gate` | pipelines | "As soon as the run is needs-review, in parallel with the human's own review." |
| W8 | `merging` | per-PR (human events) | Never auto-merge; base guard before recommending each. Disjoint => any order. |
| W9 | `closing` | per-PR or swept | /merged protocol per merge or batched sweep. |
| — | `done` / `aborted` | | Terminal. |

**Barriers today vs pipelineable:** Only W1 (inherent: conflict graph), W1.5 and W4
(deliberate: batch human round-trips) are true barriers. Everything downstream of W4 is
already per-issue in the prose and should be modeled per-issue: issue A can be building
while issue B's suite is still iterating post-gate (SKILL Step 5 says exactly this).
The orchestrator could additionally pipeline W4 partially (approve-as-ready), but that
reverses a deliberate design (ONE gate saves round-trips) — a PRD question, not a default.

## 3. Invariants (machine-checkable rules from the failure history)

Sources: 2026-07-08 session (wave-1 stranding #633 -> recovery #634; #648 stale-base
incident #2 -> #652; branch-delete closure of #640-#646 -> #647), 2026-07-09 session
(wave-2 stranding x6 -> #667-#672; wave-3 stranding x3 -> #687-#689; store
reconciliation -> #690-#693; #673 shipped), `check_build_pr_base.py`, `/merged` Steps 1/3/5.5.

| ID | Invariant (machine-checkable) | Checked when | Incident |
|----|-------------------------------|--------------|----------|
| INV-1 | A build PR may not be created with, nor recommended for merge with, `baseRefName != <default branch>` (exact string equality; `check_build_pr_base.py` exit 0 required). | PR open; before every merge recommendation; re-check whenever base could have drifted | #633; wave-2 x6 (2026-07-09); wave-3 x3; #648/#651 |
| INV-2 | A build may not START until its suite PR's merge commit is reachable from `origin/main` (un-stack precondition; build branch forks from fresh main). | Build dispatch | #673 structural fix |
| INV-3 | A merged PR does not count as LANDED until its squash sha is reachable from `origin/main`. Not reachable => recovery state (cherry-pick/recover from feat tip onto FRESH origin/main, new PR), and every branch holding the stranded commit is marked not-cleanup-safe until the recovery PR merges. | Post-merge verify | #633; #667-#672; #687-#689 (first recovery pass conflicted: cut from stale local main) |
| INV-4 | A PR is not merge-ready while a fix round is in flight: the recorded gate verdict must be bound to the current head sha (verdict.sha == head sha) at recommendation time. | Merge-ready transition | wave-3: two PRs merged before their fix agents landed |
| INV-5 | A branch may not be deleted while any open PR bases on it (`gh pr list --base <branch>` must be empty); retarget each child to main and VERIFY the new base before delete. Recovery: restore by sha, `gh pr reopen`, retarget, delete again. | Branch cleanup | #647: plain ref-delete CLOSED open stacked PRs #640-#646 |
| INV-6 | A build is not DONE on the agent's word: `origin/<branch>` must contain the reported sha (`gh pr view --json commits` / `git log origin/<branch>`), after the initial push AND after every fix round. Missing push recovers from the agent's worktree. | Every push report | #621 silent-push failures (wave 1) |
| INV-7 | A suite may not be committed unless (a) verified red against the current tree, or (b) explicitly reclassified green suite-only and presented as such at the gate. | Pre-commit | anti-tautology contract rule |
| INV-8 | No suite commit before per-test human approval is recorded for every test in it (plain-English-first). | W5 entry | wave-1 manual-run gap (suites committed pre-summary) |
| INV-9 | The build diff may not weaken any committed acceptance test (weaken-check / acceptance-integrity gate passes). | Build gate | pipeline contract (#610 guard etc.) |
| INV-10 | On merge observed: the run record flips `needs-review -> merged` via `close_run_for_pr` (exact `output`==PR URL; a `running` record is NEVER promoted straight to merged). No merged PR leaves a phantom needs-review. | Close-out | 2026-07-09 reconciliation: 44 phantom needs-review + 7 stuck running; #690 |
| INV-11 | The orchestrator never merges, approves, or closes a PR; merges are human events it observes and verifies (`gh pr view`, never invocation wording). | Always | policy (never-auto-merge) |
| INV-12 | Live-clean precondition: an issue wiring a NEW strict check into a live gate enters the wave only if the proposed check passes on the live tree at dispatch. | Recon | Step 1.5 rule; #588 flag |
| INV-13 | Shared-live-gate independence: each wave issue must leave any aggregating live gate green ON ITS OWN regardless of merge order, or the pair is ordered, not parallel. | Recon | Step 1.4 rule |
| INV-14 | Never force-delete a branch with unmerged commits; never remove a dirty worktree (flag instead). | Cleanup | /merged hard boundary |
| INV-15 | Wave membership requires pairwise-disjoint REAL footprints recomputed at dispatch (labels never trusted). | W1 verdict | "real footprint != declared" lesson |

## 4. Persistence analysis

**What exists today:**
- **Agent-runs store** (`context/agent-run-logging.md`, `scripts/agent_runs_lib.py`,
  `context/agent-runs.example.json`): per-repo JSON at
  `<AGENT_LOGS_DIR|~/Projects/agentLogs>/<owner>-<repo>/agent-runs.json`; wrapper
  `{version, last_updated, runs[]}`; run schema: id (`run-YYYY-MM-DD-NNN`, gap-filling,
  fresh-read), project, skill, task, branch, output (PR URL), status
  (`running|needs-review|merged|abandoned`), launched/completed, notes, cost tuple
  (tokens/duration_ms/model/usd or unmeasurable), cross_review (required at terminal).
  Concurrency: per-repo `fcntl.flock` spanning the whole read-modify-write; atomic
  temp-file+`os.replace` writes; `update_run` (validate-under-lock), `apply_run`
  (atomic transform, used by the recovery counter), `close_run_for_pr` (guarded flip).
  This is per-RUN state only — no wave grouping, no gate artifacts, no branch/PR graph.
- **Wave labels** (`scripts/schedule_waves.py`): `wave:N` + `route:*` +
  `serialize:<hotfile>` GitHub labels computed by greedy first-fit over footprints.
  Advisory and stale-able by design (Step 1 re-verifies).
- **Nothing else.** Wave state currently lives in the session transcript, which is why
  crash/resume is impossible and why the store drifted (44 phantom needs-review found
  2026-07-09).

**Proposed wave-state record shape** (storage-agnostic; the agent_runs_lib pattern —
lock + atomic replace + validate-under-lock — already proves the write discipline):

```
wave_id: wave-YYYY-MM-DD-N          # wave spans repos, so NOT bucketed per-repo
repos: [owner/repo, ...]
state: selecting|recon|question-gate|authoring|human-gate|committing|building|merging|closing|done|aborted
created / updated (timestamps)
gates:
  questions:                        # Step 1.5 artifact
    [{qid, issue, question, recommended_default, answer, answered_at}]
  approval:                         # Step 4 artifact — the audit trail INV-8 checks
    {presented_at, tests: [{issue, test_id, plain_english, codex_annotation, verdict, iterated}]}
issues:
  - ref: {repo, number}
    state: <per-issue enum, section 1>
    footprint: {declared: [...], derived: [...], verified_at}
    conflict_edges: [issue refs]     # serialize graph
    reclassified_green: bool
    suite:
      draft_path, red_verified: {sha, at},
      codex_rounds: [{findings_count, blocking_fixed, confirmation}],
      branch, pr, merged: {at, sha_on_main}
    build:
      worktree, branch, base_sha, pr,
      head_sha, origin_verified_sha    # INV-6: these two must match to be "done"
      run_id                           # FK into agent-runs store; don't duplicate cost
      recovery: {attempts, cap, escalated, notes}
    gates:
      codex_build: {verdict, sha}      # INV-4: sha-bound
      weaken_check: {ok, at}
      base_guard: {base_ref, ok, at}   # INV-1
    close_out:
      merged: {at, squash_sha, reachable_from_main}   # INV-3
      branch_deleted, worktree_removed, issue_closed, store_flipped   # INV-10
```

Resume semantics: GitHub is authoritative for PR/branch/merge facts; the record is
authoritative for gate artifacts (approvals, verdicts, attempts). On resume, reconcile
record against `gh` truth and re-derive each issue's state; a divergence is surfaced,
never silently overwritten (same posture as #693's read-only divergence detector).

## 5. LLM-work vs orchestrator-owned code

**Dispatched LLM work (the orchestrator prompts, collects, verifies):**
- Recon/Explore agents (real footprint, conflict inputs, ambiguity list) — one per repo
- Suite authors (draft PENDING suites, live-tree adversarial fixtures, per-test summaries)
- Codex suite reviews (exhaustive pass + confirmation round) and Codex build gates
- spec-dev build implementers (TDD loop in worktree)
- Recovery re-dispatch (`build_retry_prompt`: frozen contract + trace)
- Plain-English test descriptions for the gate presentation (drafted by authors/orchestrating model)

**Orchestrator-owned code (deterministic, no model in the loop):**
- Wave selection + conflict graph + serialize verdict (schedule_waves.py logic, applied to recon-verified footprints)
- Live-clean check execution (run the proposed check over the live tree)
- Red-verification (run the draft suite, assert failure)
- Commit/branch/PR mechanics; issue-body test-name updates
- ALL invariant checks: base guard (check_build_pr_base.py), origin-sha verification, reachable-from-main, branch-delete safety, weaken-check invocation, sha-bound verdicts
- Merge observation + /merged close-out (sync, retarget-check, delete, worktree cleanup, issue close, `close_run_for_pr`)
- Recovery counter/cap (`build_recovery.py`: record_attempt/next_action/reset_worktree/escalate_run)
- Run logging + wave-state persistence

**The ONE human gate:** Step 4, the batched plain-English-first per-test approval
(W4 barrier). Step 1.5's question batch is a second human touchpoint but a pre-gate
input round, not an approval; PR merges remain human EVENTS by policy (never
auto-merge) but sit outside the orchestrator. Per #404/#712 Phase 4 (spec-up
gating/queue), the Step-4 gate later converges into the queue surface; Phase 3 should
keep it as a pluggable seam (present -> collect per-test verdicts) so the queue can
replace the AskUserQuestion transport without touching the state machine.
