# Requirements Brief (skeleton): Wave Orchestrator — Roadmap #712 Phase 3

Shape follows /extract-specs output: context, agreed requirements, constraints,
out of scope, open questions. Sources: skills/spec-wave/SKILL.md (v2 + #673
un-stack), skills/merged/SKILL.md, sessions/2026-07-08.md, sessions/2026-07-09.md,
scripts/check_build_pr_base.py, scripts/build_recovery.py, scripts/agent_runs_lib.py,
scripts/schedule_waves.py, context/agent-run-logging.md, issue #404 last comment
(harness definition / #712 sequencing). Companion: /tmp/phase3-prep/state-machine.md.

## 1. Context / problem

- spec-wave is prose executed by a model. The stranding / merge-order / verify-push
  failure class (#633, #640-#646/#647, wave-2 x6, wave-3 x3, #673) recurred because
  ordering rules lived in prose, not code. Per #404's harness definition, Phase 3
  builds a state machine that REPLACES skills/spec-wave/SKILL.md (not wraps it) and
  encodes those rules as enforced invariants.
- Wave state today lives only in the session transcript: no crash/resume, and the
  agent-runs store drifted to 44 phantom needs-review before manual reconciliation
  (2026-07-09; #690-#693).
- Sequencing (#712): merged runner and spec-dev runner land BEFORE this; the wave
  orchestrator composes them. Phase 4 (spec-up gating/queue) later absorbs the
  human gate into a queue surface.

## 2. Requirements (extracted, to confirm in interview)

R1. Encode the per-issue and wave-level state machine in state-machine.md as code:
    explicit states, guarded transitions, events (recon verdict, Step-1.5 answers,
    author done, Codex verdict rounds, batched human gate, suite PR merge, build
    done, weaken-check, cross-review verdict, build PR merge, close-out).
R2. Enforce INV-1..INV-15 (state-machine.md section 3) as blocking transition
    guards, not advisories. Minimum bar = the four incident classes:
    base==main (create + merge-recommend), suite-on-main before build start,
    landed==reachable-from-main, no branch delete with open child PRs,
    origin-sha verification after every push report, sha-bound gate verdicts.
R3. Durable per-wave state record (shape in state-machine.md section 4):
    crash/resume; GitHub authoritative for PR/branch facts, record authoritative
    for gate artifacts; divergence surfaced, not silently healed.
R4. Preserve exactly ONE batched plain-English-first human approval (per-test
    verdicts) before any suite commit, behind a pluggable gate seam so Phase 4's
    queue can replace the transport. Step-1.5 question batch stays a single
    pre-fan-out round with recommended defaults.
R5. Dispatch LLM work (recon, authors, Codex reviews, spec-dev builds, recovery
    retries) and verify every result mechanically; never trust agent self-reports.
R6. Compose, don't reimplement: use check_build_pr_base.py, build_recovery.py
    (attempt cap + escalate), agent_runs_lib (log_run/update_run/apply_run/
    close_run_for_pr), schedule_waves.py conflict logic, and the Phase 1/2
    merged + spec-dev runners.
R7. Keep policy boundaries: never merge/approve/close a PR; never force-delete
    unmerged branches or remove dirty worktrees; multi-repo waves with per-repo
    conventions and one cross-repo gate.
R8. Per-issue pipelining downstream of the Step-4 gate (an iterating suite must
    not block sibling issues' commits/builds); recovery loops independent per build.

## 3. Constraints

- Python + uv, in-repo scripts pattern; storage tech deliberately undecided.
- Codex inputs must be local files (no network in codex exec).
- Worktrees: agent-created linked worktrees, not Agent-tool isolation (multi-repo).
- Non-Python pending conventions may run a red window on main (disclose at gate).
- The orchestrator's own build goes through the pipeline (spec-up contract first).

## 4. Out of scope (Phase 3)

- The queue / gating surface (Phase 4) — but the gate seam must anticipate it.
- Producing conflict labels (/findings-to-issues) and epic decomposition.
- Auto-merge of any kind (permanent non-goal).
- CI-side required check for base guard (#694, separate).

## 5. Open questions for Matt (feeds /write-a-prd)

Q1. **Merge detection:** the orchestrator can't merge, but must observe merges to
    advance (suite-merged, build-merged, close-out). Poll `gh` on an interval,
    or stay event-driven (Matt runs a "merged" command that feeds the machine,
    i.e. the Phase-1 merged runner becomes an orchestrator event source)?
Q2. **Wave-record home:** the agent-runs store is bucketed per-repo, but a wave
    spans repos. New per-wave file in the central agentLogs store, a waves/
    sibling directory, or somewhere in the marvin repo? (Privacy: PR URLs +
    issue refs, probably fine outside the repo like agent-runs.)
Q3. **Resume conflict policy:** on resume, when the record and GitHub disagree
    (e.g. record says merge-ready, GitHub shows merged with wrong base), does the
    machine auto-enter recovery states, or halt and surface for a human decision?
    Where's the line between auto-recover (INV-3 cherry-pick flow ran by hand 9
    times) and escalate?
Q4. **Gate transport now:** do Step-1.5 and Step-4 stay AskUserQuestion inside a
    Claude session for Phase 3, with the queue arriving only in Phase 4? If so,
    is a wave resumable across sessions while parked at the human gate?
Q5. **Interface to the spec-dev runner:** does the wave orchestrator invoke the
    Phase-2 spec-dev runner as a library/state-machine (shared persistence), or
    as a subprocess/agent with its own record the wave record references by
    run_id? Same question for the merged runner.
Q6. **Partial-wave semantics:** when one issue escalates (recovery cap hit) or a
    suite is rejected at the gate, does the wave complete around it and close
    with an explicit remainder (follow-up issues auto-filed per the
    followups-become-issues rule), or hold open until every issue is terminal?
