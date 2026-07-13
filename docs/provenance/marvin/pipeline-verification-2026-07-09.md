# Pipeline Verification Report — 2026-07-09

Multi-agent verification of the project-build pipeline artifacts against the skills as ground truth.

- **Ground truth:** the 11 pipeline skill definitions under `skills/` (write-a-prd, extract-specs, prd-to-issues, findings-to-issues, grill-me, spec-up, spec-wave, spec-dev, tdd, dispatch, merged, wave-status)
- **Artifacts under test:** `context/project-build-pipeline.md` (routing playbook), `content/diagrams/marvin-build-pipeline.svg/.png`, related context docs, plus cross-skill consistency
- **Method:** 47 agents. 7 parallel extractors (4 skill groups, docs, diagram, recent practice), 4 cross-checkers (doc drift, diagram drift, contradictions, improvement lens), then one adversarial skeptic per finding instructed to refute it against direct file reads.
- **Outcome:** 33 findings confirmed, 3 refuted.

**Severity:** 7 high, 16 medium, 10 low.

## Confirmed findings

### 1. [HIGH] [contradiction] write-a-prd Step 7.5 sanctions a 'skip marker' pending convention that spec-up forbids and the #491 guard treats as weakening

**Artifact:** `skills/write-a-prd/SKILL.md`

**Evidence:** write-a-prd/SKILL.md:273 tells the test-author agent to commit the approved suite 'marked PENDING (project convention: pytest xfail, skip marker, or tagged suite)'. spec-up/SKILL.md:47 (ground truth for the same shared procedure; spec-up Step 4 says to run write-a-prd Step 7.5 'exactly') states the convention 'must keep a committed-red suite from both failure modes: silently skipping (a false-green that proves nothing) and reddening sibling PRs'. spec-dev/SKILL.md:305 lists the marker downgrade 'xfail->skip' as a blocking WEAKENING. Practice codified the same rule in docs commit 3898b33 ('pending-suite convention must avoid false-green skip'). A skip marker is exactly the silently-skipping false-green spec-up forbids.

**Verifier correction:** Nuance: spec-up:47 does not categorically forbid skip-shaped markers (it lists vitest `it.skip`/`test.fails` as examples); it requires any convention to avoid both failure modes, with skip-style conventions needing an explicit opt-in gate. The contradiction is that write-a-prd:273 sanctions a bare "skip marker" unconditionally, omitting the constraint spec-up:47 attaches to the same shared procedure. Also, spec-dev's WEAKENING list is at spec-dev/SKILL.md:304-305 and governs downgrades to already-committed suites, so it is corroborating rather than direct evidence; the load-bearing conflict is write-a-prd:272-273 vs spec-up:47 plus commit 3898b33.

**Proposed fix:** Edit write-a-prd/SKILL.md:273 to match spec-up:47: name pytest xfail(strict=True) as the canonical Python convention and require any alternative (tagged suite, vitest test.fails) to avoid both silent-skip false-greens and sibling-red; drop 'skip marker' from the sanctioned list.

### 2. [HIGH] [contradiction] spec-dev's unconditional isolation:"worktree" spawn contradicts agent-contract, dispatch, and spec-wave for cross-repo targets

**Artifact:** `skills/spec-dev/SKILL.md`

**Evidence:** spec-dev/SKILL.md:275-284 hardcodes Agent(isolation: "worktree") for every spawn and claims 'Worktree isolation is required by the agent contract'. But agent-contract.md:7-10 restricts worktree isolation to same-repo work (target IS the session's repo) and defines a different cross-repo mode (live checkout via git -C, or agent-created /tmp linked worktrees). dispatch/SKILL.md:105 says isolation "worktree" 'ONLY when target == marvin; omit for cross-repo' and lines 87-90 explain the Agent tool worktrees the session repo (marvin), not the target. spec-wave/SKILL.md:130-131 says do NOT rely on the Agent tool's worktree isolation for a multi-repo wave ('it has bound agents to the wrong repo's worktree'). Following spec-dev literally against rdv-expenses or DandD gives the implementer a marvin worktree, not the target repo (also memory reference_subagent_cross_repo_writes).

**Verifier correction:** Exact lines: spec-dev/SKILL.md:277 (`isolation: "worktree"` in the spawn block spanning ~274-281), spec-dev/SKILL.md:283-284 ("Worktree isolation is required by the agent contract"), plus spec-dev/SKILL.md:132 ("spawn ONE implementer agent with worktree isolation"). Contradicted by context/agent-contract.md:7-10 (Isolation section: worktree isolation = same-repo only; cross-repo = git -C live checkout or agent-created linked /tmp worktrees), skills/dispatch/SKILL.md:105 ("ONLY when target == marvin; omit for cross-repo") and the surrounding Step 3 isolation bullets, and skills/spec-wave/SKILL.md:130-133 (do not rely on Agent-tool worktree isolation for multi-repo waves).

**Proposed fix:** Change spec-dev's Spawning the Agent section to the same conditional as dispatch: isolation:"worktree" only when the target project is marvin itself; for cross-repo targets spawn without isolation and have the implementer create its own linked worktree per agent-contract.md's parallel cross-repo pattern (as spec-wave Step 2/6 already does). Fix line 283's claim that the contract 'requires' worktree isolation.

### 3. [HIGH] [coverage-gap] /findings-to-issues is missing from the routing playbook entirely; the /harden route dead-ends at "findings" and the wave route names no label producer

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** skills/findings-to-issues/SKILL.md:3-9,22-44 defines the labeling step that converts review findings (report mode) or existing open issues (existing-issue mode) into a conflict-grouped wave with a hard human-approval gate, then hands to /spec-wave or /spec-up. skills/spec-wave/SKILL.md:27-28 says explicitly "This is the execution half of what the labeling step (/findings-to-issues) produces: that step emits the conflict-grouping labels, spec-wave consumes them." The playbook never mentions /findings-to-issues anywhere: the routing table row for audits (project-build-pipeline.md:23) ends at "→ findings" with no next step, the wave row (line 19) and wave chain (line 33) say the wave "Consumes the wave:N / conflict-grouping labels" without naming what produces them, and the canonical-skills list (lines 49-58) omits it. A registered /findings-to-issues skill and slash command exist.

**Proposed fix:** Add a routing-table row: "A review report (/harden, /marvin-review, /ai-app-review) or a pile of unlabeled open issues to wave-group | either | /findings-to-issues | → /spec-wave (or /spec-up for a single issue)". Extend the /harden row's Then column to "→ findings → /findings-to-issues". Add /findings-to-issues to the canonical-skills list (skills/findings-to-issues/SKILL.md, labeling step; emits wave:N / route:* / serialize:<file> labels that /spec-wave consumes).

### 4. [HIGH] [coverage-gap] Entire brownfield half of the pipeline is missing (spec-up, spec-wave, findings-to-issues, dispatch)

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** Diagram (titled 'MARVIN Project-Build Pipeline', svg line 10) depicts only the greenfield path: inputs -> extract-specs -> write-a-prd -> prd-to-issues -> spec-dev. Ground truth: context/project-build-pipeline.md lines 18-19, 32-34 define /spec-up as the brownfield single-issue entry ('/spec-up <issue> -> /spec-dev'), /spec-wave as 'the default for a qualifying batch' of 3+ parallel-safe issues, /findings-to-issues as the wave-labeling producer, and /dispatch as the trivia/non-contract route. None of these skills, nor their handoff edges into spec-dev, appear anywhere in the SVG.

**Verifier correction:** One correction: /findings-to-issues is NOT mentioned anywhere in context/project-build-pipeline.md (grep returns nothing); it exists only as skills/findings-to-issues/SKILL.md and the wave:N/conflict-grouping labels it produces are consumed per line 33 ("Consumes the wave:N / conflict-grouping labels"). Accurate citations: spec-up at md lines 18, 32, 41, 54; spec-wave at lines 19, 33, 55; dispatch at lines 21-22, 26, 34, 58. The severity and proposed fix stand; only the claim that the md "defines /findings-to-issues" should be softened to "the skill exists at skills/findings-to-issues/SKILL.md and feeds the wave labels the md's spec-wave route consumes".

**Proposed fix:** Either retitle the diagram 'Greenfield Project-Build Pipeline' with a note pointing to the brownfield routes, or add a brownfield entry lane: existing issue -> /spec-up -> /spec-dev; review findings/labeled issues -> /findings-to-issues -> /spec-wave (fan-out spec-up + spec-dev); non-testable work -> /dispatch.

### 5. [HIGH] [improvement] Diagram omits the entire brownfield half of the pipeline (no spec-up, spec-wave, findings-to-issues, dispatch) and shows no entry decision points

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** grep confirms zero occurrences of 'spec-up', 'spec-wave', 'dispatch', or 'findings-to-issues' in the SVG. The router makes these first-class entries: context/project-build-pipeline.md:18 (/spec-up for one issue with no contract), :19 (/spec-wave the DEFAULT for 3+ file-disjoint issues), :21-22 (/dispatch for spec-less work and the #507 trivia batch), and :36 (spec-up triage back to /write-a-prd). Recent practice is dominated by these routes (wave-1/wave-2 commits 1f65687, #653-#662; skills/spec-wave/SKILL.md:301-305). The diagram depicts only the greenfield chain, so someone running it from the diagram would force existing issues through write-a-prd/prd-to-issues.

**Proposed fix:** Add an entry-routing decision diamond at the top of the diagram mirroring the router table (project-build-pipeline.md:13-24): raw notes -> extract-specs; feature idea/epic -> write-a-prd; one contract-less issue -> spec-up; 3+ disjoint issues -> spec-wave; already-contracted issue -> spec-dev; spec-less task/trivia -> dispatch; audit -> harden. Show spec-up joining the flow at the acceptance-suite container (it reuses Step 7.5) and spec-wave as a fan-out wrapper around spec-up + spec-dev.

### 6. [HIGH] [improvement] Diagram contradicts the #618 stacked-build model and omits the #647 safe post-merge order that has already destroyed PRs

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** Diagram banner (svg lines 47-48) states 'Approved tests land on project main as PENDING' before any build, i.e. merged-to-main-first. The router explicitly says ancestor-ordering, not merged-to-main-first, is the guarantee: build may start from the approved suite branch with the build PR stacked on the suite PR (project-build-pipeline.md:9, #618; spec-dev/SKILL.md:32-43; spec-wave/SKILL.md:211-247). Neither the diagram nor the router documents the #647 hazard: deleting a merged suite branch before retargeting stacked children CLOSES them (closed PRs #640-#646 on 2026-07-08, spec-wave/SKILL.md:226-239; merged/SKILL.md:47). grep shows 'stacked' and 'retarget' appear nowhere in the SVG.

**Verifier correction:** Minor citation corrections: the diagram banner text is svg line 48 (its rect is line 47), and a second contradicting line is svg line 62 ("confirm targeted acceptance tests are PENDING on main"). The spec-dev stacked-mode text is at skills/spec-dev/SKILL.md:33-41 (not 32-43). The #647 procedure is at skills/spec-wave/SKILL.md:226-236 and skills/merged/SKILL.md:47 as cited.

**Proposed fix:** Update the banner to 'suite committed PENDING via PR; suite commit is an ancestor of every build commit (builds may stack on the suite branch, human approval releases the build leg)'. Add a merge-order sub-flow near FINAL: suite PR merges first -> retarget every stacked child to main -> verify base reads main -> only then delete the suite branch. Add one sentence to project-build-pipeline.md:9 pointing to the #647 procedure in skills/spec-wave/SKILL.md and skills/merged/SKILL.md.

### 7. [HIGH] [improvement] No failure or recovery path anywhere in diagram or router, despite recovery being 9 of the last 40 commits

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** The diagram is happy-path only; 'recovery' does not appear in the SVG (grep) or in context/project-build-pipeline.md. Ground truth defines a full recovery machine: scripts/build_recovery.py with record_attempt/next_action(cap=2)/reset_worktree/build_retry_prompt/escalate_run (spec-dev/SKILL.md:170-190, #675; spec-wave/SKILL.md:256-269, wave-2 needed 6 recoveries on 2026-07-09), plus verify-push-at-origin ('agents report pushed when the push silently failed', spec-wave/SKILL.md:249-254, #621). Git log shows 9 '(recovery)' feat commits (8c2dccc, 89b0ac4, 9149ebf, 1366f6c, etc.). Someone running the pipeline hits these failures routinely with no documented route.

**Verifier correction:** Two evidence corrections: (1) commit count is 7 '(recovery)'-tagged commits in the last 40 (11 lines mentioning 'recovery'), not 9; (2) the spec-dev recovery section is SKILL.md:170-183 (the finding's cited 170-190 range bleeds into the unrelated background-agent prompt template starting ~line 185), and the spec-wave citations are verify-push at 249-254 and build recovery at 256-269 (finding's 256-269 is correct).

**Proposed fix:** Add a failure branch off Phase 4b: 'build strands (agent dies / suite still red / Codex rejects) -> build_recovery.py: fresh agent from frozen contract + trace, cap 2 retries -> escalate to needs-review'. Add a small annotation on the 'Open PR' box: 'verify sha landed at origin before review'. Add one 'When a build fails' line to the router pointing at spec-dev's recovery section.

### 8. [MEDIUM] [accuracy-drift] Prerequisite 3 names plain "pytest xfail", omitting the strictness requirement the skills make load-bearing

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** project-build-pipeline.md:47 says the convention is "pytest xfail, vitest it.skip / test.fails, a tagged suite" with no qualifier. skills/spec-up/SKILL.md:43-47 requires the convention "must keep a committed-red suite from both failure modes: silently skipping (a false-green that proves nothing) and reddening sibling PRs", and says "xfail(strict) gives this for free". Plain non-strict pytest xfail is exactly the silent false-green failure mode (an xpass never reddens anything), and per memory feedback_acceptance_literal_xfail / commit 29afc72 ("test(#604): use literal xfail(strict=True) markers so the #491 guard..."), non-literal or non-strict markers block or evade the deterministic guard: check_acceptance_integrity.py treats strict=True→strict=False as a weakening (spec-dev/SKILL.md weaken-check section, #491).

**Verifier correction:** context/project-build-pipeline.md:47 (plain "pytest `xfail`") vs skills/spec-up/SKILL.md:47 (convention must avoid silent-skip false-green; "`xfail(strict)` gives this for free") and skills/spec-dev/SKILL.md:295-307 (#491 weaken-check treats `strict=True`->`strict=False` as a blocking marker downgrade).

**Proposed fix:** Change line 47 to name the strict form: "pytest xfail(strict=True) with a literal marker (canonical for Python), vitest it.skip / test.fails, a tagged suite — the convention must neither silently skip (false-green) nor redden sibling PRs; see skills/spec-up/SKILL.md Requirements."

### 9. [MEDIUM] [accuracy-drift] Doc implies the #604 CI acceptance-integrity gate enforces no-weakening universally; the gate is a per-repo workflow, and the always-on layer is the skill-run #491 check the doc never mentions

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** project-build-pipeline.md:9 says "the #604 CI acceptance-integrity gate machine-enforces no-weakening at merge time regardless of branch topology." The gate is a GitHub Actions workflow in the marvin repo only (.github/workflows/acceptance-gate.yml, running scripts/ci_acceptance_gate.py, issue #604); spec-wave explicitly supports multi-repo waves (#621, spec-wave/SKILL.md Requirements) across repos that may not have that workflow installed. The enforcement layer the skills define as always-on everywhere is the orchestrator-run deterministic weaken-check, check_acceptance_integrity.py --old/--new (#491), which spec-dev/SKILL.md (weaken-check section, ~lines 293-314) says "Runs ALWAYS, even when Codex is skipped." The playbook mentions neither #491 nor the per-repo scope of #604.

**Verifier correction:** Minor citation tightening only: the exact "always" quotes are skills/spec-dev/SKILL.md:309 "This guard runs ALWAYS, not just when Codex is up" (weaken-check before Cross-Review Gate, section starting at line 293) and skills/spec-dev/SKILL.md:71 "runs ALWAYS, independent of Codex" (Step 1 entry gate, --entry mode). The workflow's per-repo scope is further narrowed by its path filter (acceptance-gate.yml triggers only on PRs touching tests/** or scripts/** in the marvin repo). DandD, the pipeline test bed, has only ci.yml in .github/workflows.

**Proposed fix:** Amend line 9: "...the #604 CI acceptance-integrity gate (installed per repo; marvin's .github/workflows/acceptance-gate.yml) machine-enforces no-weakening at merge time where present; the always-on layer in every build is /spec-dev's deterministic weaken-check (scripts/check_acceptance_integrity.py, #491), which runs regardless of CI or Codex availability."

### 10. [MEDIUM] [accuracy-drift] 2.5(c) user approval labeled 'one at a time' but the skill requires ONE batched pass

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG line 46: '(c) User approval ... approve / adjust / reject, one at a time (Codex findings advisory)'. Ground truth skills/write-a-prd/SKILL.md Step 7.5 gate 3 (verified in file, ~lines 264-271): 'Present the full annotated suite in one batch ... take approve / adjust / reject per issue in that single pass.' Batched suite-level approval was a deliberate update (memory feedback_batched_approval_plain_english, marvin#516). 'One at a time' describes the superseded per-issue serial gate.

**Verifier correction:** SVG line 46: '<text x="206" y="620" ...>approve / adjust / reject, one at a time (Codex findings advisory)</text>' vs skills/write-a-prd/SKILL.md:264-268 ('Present the full annotated suite in one batch ... per issue in that single pass ... plain-English-first dual-layer form'). The original citation's line numbers were accurate.

**Proposed fix:** Change the 2.5(c) sublabel to 'ONE batched pass, plain-English-first · approve / adjust / reject per issue (Codex findings advisory)'.

### 11. [MEDIUM] [accuracy-drift] PRD 'submitted as a GitHub issue' shown BEFORE acceptance-suite authoring; skill order is the reverse

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 30-32 (stage 2): 'write-a-prd ... submitted as a GitHub issue', then arrow down to container 2.5 (Step 7.5 acceptance suite, lines 34-48). Ground truth skills/write-a-prd/SKILL.md: Step 7.5 (acceptance-suite authoring) precedes Step 8 'Save and Submit' ('Ready to submit this PRD as a GitHub issue?' then gh issue create, verified lines under '### Step 8'). The diagram implies the PRD issue is filed before the suite is authored/committed.

**Verifier correction:** SVG lines 29-33 (stage 2 box text "submitted as a GitHub issue" at line 32, arrow at line 33) preceding the 2.5 container starting at line 34; SKILL.md Step 7.5 at line 194 vs Step 8 "Save and Submit" at lines 282-288 ("Ready to submit this PRD as a GitHub issue?" line 286, gh issue create line 287).

**Proposed fix:** Move the 'submitted as a GitHub issue' sublabel out of stage 2, or add a small 'Step 8: PRD filed as GitHub issue' node after the 2.5 container and before stage 3, and relabel stage 2 to 'PRD drafted w/ testable acceptance criteria'.

### 12. [MEDIUM] [accuracy-drift] Stage 6 'harden / code-review / verify' depicted as a mandatory per-PR pipeline stage; no skill defines it as one

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 90-93 insert '6 — harden / code-review / verify: Security + quality + test-coverage audit pass' between the Codex gate and Matt's merge on the main flow. Ground truth: skills/spec-dev/SKILL.md defines the post-build gates as weaken-check + Codex cross-review then Matt's merge (lines 293-360); context/project-build-pipeline.md line 23 routes /harden as an on-demand audit ('An audit or hardening pass | either | /harden | -> findings') whose findings feed /findings-to-issues, not a required pre-merge stage on every slice. No skill sequences harden/code-review/verify between cross-review and merge.

**Verifier correction:** SVG stage-6 box and its arrows are at content/diagrams/marvin-build-pipeline.svg lines 89-95 (not 90-93): line 89 arrow from stage 5, lines 90-93 the box and text, line 95 arrow into the FINAL Matt-merges box. spec-dev gate definitions: skills/spec-dev/SKILL.md sections 'Deterministic acceptance-integrity guard' and 'Cross-Review Gate (second-model critic)' (approx lines 293-365). Routing table row: context/project-build-pipeline.md line 23.

**Proposed fix:** Either remove stage 6 from the mandatory linear flow, or restyle it as an optional/periodic side loop ('/harden audit -> findings -> /findings-to-issues -> new wave') rather than a step every PR passes through.

### 13. [MEDIUM] [contradiction] spec-up invokes its lint gates with marvin-relative paths while prd-to-issues and spec-dev mandate absolute ~/marvin paths for cross-repo cwd safety

**Artifact:** `skills/spec-up/SKILL.md`

**Evidence:** spec-up/SKILL.md:64-65 runs 'uv run python scripts/validate_spec_up_issue.py <body-file>' and line 104 runs 'scripts/validate_accept_body.py' (relative). prd-to-issues/SKILL.md:80 runs 'uv run python ~/marvin/scripts/validate_slice_issue.py' with the explicit rationale '(absolute path: runs from any target repo's cwd)', and spec-dev/SKILL.md:67,299 uses '~/marvin/scripts/check_acceptance_integrity.py'. The two conventions conflict, and the observed effect is real: skills-audit-2026-07-07.md notes the guards only run correctly when cwd is ~/marvin, so a cross-repo spec-up/wave silently loses the deterministic lint layer.

**Verifier correction:** spec-up/SKILL.md:64-65 ('uv run python scripts/validate_spec_up_issue.py', relative) and :104 ('scripts/validate_accept_body.py', relative) vs prd-to-issues/SKILL.md:80 ('~/marvin/scripts/validate_slice_issue.py ... (absolute path: runs from any target repo's cwd)') and spec-dev/SKILL.md:67,299 ('~/marvin/scripts/check_acceptance_integrity.py'). Minor correction: content/notes/skills-audit-2026-07-07.md:23-24 flagged the relative-path cwd bug in prd-to-issues and spec-dev (since fixed to absolute), not in spec-up; spec-up's relative paths are the unfixed remainder of that same audit finding.

**Proposed fix:** Update spec-up Step 0 and Step 4 (and audit spec-wave for the same pattern) to invoke every marvin validator with the absolute ~/marvin/scripts/ path, matching the prd-to-issues convention and its stated rationale.

### 14. [MEDIUM] [contradiction] Suite-amendment path: spec-dev routes all contract amendments through /write-a-prd, but spec-wave sanctions in-wave iteration of committed suites, and practice (PR #592) amended in-slice

**Artifact:** `skills/spec-dev/SKILL.md`

**Evidence:** spec-dev/SKILL.md:112-115: new acceptance tests 'are authored and approved through /write-a-prd (the contract is amended there, with a logged reason), not invented inside the build'; lines 118-119 forbid planning around editing a wrong test. spec-wave/SKILL.md:238-239: 'If the human gate iterates a suite after builds started, rebase that issue's build branch onto the amended suite tip' — sanctioning amendment of an already-committed approved suite inside the wave with no /write-a-prd pass. Practice agrees with spec-wave, not spec-dev: pipeline-eval-2026-07-07.md finding 5 records PR #592 amending two committed #577-era suites in-slice (disclosed, Codex-cleared) because the /write-a-prd path is too heavy; no lightweight AMEND convention exists.

**Verifier correction:** Minor corrections only: spec-dev citation is lines 113-115 (not 112-115) plus 118-119; the pipeline-eval reference is section 1, bypass point 4, at content/notes/pipeline-eval-2026-07-07.md:20 (not "finding 5"). spec-wave/SKILL.md:238-239 quote is verbatim. Note also this is a skill-vs-skill contradiction; context/project-build-pipeline.md contains no amendment-path language at all (grep for amend/write-a-prd shows only routing rows), so the artifact under test is silent rather than contradictory.

**Proposed fix:** Define one amendment path and state it identically in both skills: e.g. a lightweight AMEND convention (disclosed suite edit + re-run of the plain-English human gate + Codex/integrity-guard pass) for in-wave/in-slice amendments, reserving /write-a-prd for net-new scope. File it as an issue per the follow-ups-become-issues rule.

### 15. [MEDIUM] [coverage-gap] Post-merge and status stages (/merged, /wave-status) are absent from the playbook

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** skills/wave-status/SKILL.md:24 names the pipeline as "/spec-wave, /spec-up, /spec-dev, /merged" creating state in three places, and skills/spec-wave/SKILL.md (Step 6, lines ~211-247) defers post-merge retarget verification to "/merged Step 7". skills/merged/SKILL.md:3-9 defines the post-merge protocol (verify merge state, stale-base check, suite-branch guard #647 that retargets stacked PRs before branch deletion, close linked issues, report what's next), and skills/wave-status/SKILL.md:3-11 defines the read-only stacked-work view. The playbook mentions neither skill; its chains end at "PR" (lines 20, 30) with no route for the "Matt just merged PR(s)" or "where does the stacked work stand" problem shapes. The #647 hazard (deleting a merged suite branch closes every stacked build PR, which closed PRs #640-#646 on 2026-07-08 per merged/SKILL.md:47) is only avoided if the operator knows to run /merged.

**Verifier correction:** Minor citation correction: the /merged suite-branch guard (#647, PRs #640-#646 closed 2026-07-08) is at skills/merged/SKILL.md:47 (Step 3, "Suite branches (stacked PRs)"), not a "Step 7"; the "/merged Step 7" reference comes from skills/spec-wave/SKILL.md:218. Also the spec-wave Step 6 material spans lines ~211-247 as cited (safe post-merge order at lines 227-237). All other citations hold as written.

**Proposed fix:** Add two routing-table rows: "Matt just merged PR(s) in the browser | either | /merged | → sync, cleanup, suite-branch guard, what's-next report" and "Need to see where stacked work stands | either | /wave-status | → read-only report (which PR merges next, which suite is /spec-dev-ready)". Add both to the canonical-skills list. Note in the #618 paragraph (line 9) that /merged Step 7 / the #647 safe post-merge order handles retarget-before-delete.

### 16. [MEDIUM] [coverage-gap] Deterministic model-free acceptance-integrity checks (#491) are missing; weaken-detection attributed solely to Codex

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 86-88 (stage 5): 'Codex cross-review ... confirms no acceptance test weakened'. Ground truth skills/spec-dev/SKILL.md line 67 (entry gate: check_acceptance_integrity.py --entry, hard refusal) and lines 296-314 (pre-review weaken-check --old/--new, 'Runs ALWAYS, even when Codex is skipped', non-zero is blocking). The diagram has no node for either deterministic gate, so a reader would believe weakening detection depends entirely on Codex availability.

**Proposed fix:** Add a small verification-gate box before stage 5 labeled 'deterministic weaken-check (check_acceptance_integrity.py, always-on, model-free)' and note the --entry gate inside Phase 4a's 'confirm targeted tests are PENDING' step.

### 17. [MEDIUM] [coverage-gap] Build recovery loop (#675) missing from Phase 4b

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 67-83 show Phase 4b as a straight 4-step chain (TDD loop -> flip -> verify -> PR) with no failure path. Ground truth skills/spec-dev/SKILL.md lines 170-190: stranded builds (agent dies, suite still red after 'done', Codex gate rejects) route through scripts/build_recovery.py — fresh-agent retry from frozen contract + trace, cap 2, then escalate to needs-review/human. The diagram has no failure/retry/escalate edge at all.

**Proposed fix:** Add a dashed failure edge from the 4b verify/gate steps to a small 'build recovery: retry fresh agent (cap 2) -> escalate needs-review (scripts/build_recovery.py, #675)' node looping back into 4b.

### 18. [MEDIUM] [coverage-gap] Post-merge protocol (/merged) and status view (/wave-status) absent after FINAL

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 95-100: FINAL 'Matt reviews & merges' flows straight to 'Ship-ready product'. Ground truth skills/merged/SKILL.md (lines 3-9, 61-67): after Matt merges, /merged verifies merge state, syncs main, deletes branches (suite-branch retarget guard #647), closes linked issues, re-runs tests, and reports the next PENDING suites ready for /spec-dev — i.e. the edge that feeds the 'next unblocked slice' loop. skills/wave-status/SKILL.md reads the same pipeline state. Neither appears in the diagram.

**Verifier correction:** SVG: FINAL node at line 96 ('FINAL ◆ Matt reviews & merges'), arrow line 99, 'Ship-ready product' line 100; no occurrence of '/merged' or 'wave-status' anywhere in the file, while stages 1-6 name their skills (lines 25, 30, 51, 59, 91). skills/merged/SKILL.md lines 3-9 (description) and lines 61-67 (Step 7: Report what's next, including 'PENDING acceptance suites the merge unblocked, now ready for /spec-dev'). skills/wave-status/SKILL.md lines 3-11 and 24 (reads PRs in stack order, wave states, PENDING suites created by /spec-wave, /spec-up, /spec-dev, /merged).

**Proposed fix:** Add a '/merged post-merge protocol (verify · sync · cleanup · close issues · report what's next)' node after FINAL, with an edge from it back to the per-slice loop (it is what surfaces the next PENDING suite for /spec-dev).

### 19. [MEDIUM] [coverage-gap] wave-status Step 5 detects pending suites by grepping for a 'PENDING (#' marker string that no authoring skill mandates

**Artifact:** `skills/wave-status/SKILL.md`

**Evidence:** wave-status/SKILL.md:58: 'Grep each repo's test tree for PENDING (# xfail markers to find committed acceptance suites still pending'. Neither write-a-prd Step 7.5 (SKILL.md:271-277, which specifies docstring content but no marker reason format) nor spec-up Step 4 requires the xfail reason string to contain 'PENDING (#'. Grep across skills/ and context/ shows the string appears only in wave-status/SKILL.md:58. A suite committed with a different reason string (or a vitest test.fails suite, which has no xfail marker at all) is invisible to the status view, and Matt could be told nothing is awaiting /spec-dev when suites are.

**Proposed fix:** Make the marker-reason format a stated authoring requirement in write-a-prd Step 7.5 (e.g. reason='PENDING (#<issue>)') so spec-up inherits it, and extend wave-status Step 5 to also detect the non-pytest conventions (test.fails / tagged suites) the other skills allow.

### 20. [MEDIUM] [improvement] Diagram shows only Codex as the verification layer, omitting the deterministic model-free integrity gates that run always

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** Diagram verification gates are 2.5(b) Codex review, 4b full-suite regression, and stage 5 Codex cross-review. Ground truth layers deterministic checks under Codex: check_acceptance_integrity.py --entry hard-refuses spec-dev if a targeted test is absent or not pending (spec-dev/SKILL.md:49-71), the --old/--new weaken-check runs ALWAYS, even when Codex is skipped, and blocks on any non-marker change (spec-dev/SKILL.md:293-314, #491), plus validate_accept_body.py / validate_spec_up_issue.py / validate_slice_issue.py lint gates (write-a-prd Step 7.5; spec-up Step 0g; prd-to-issues Step 5). The 2026-07-07 pipeline eval (content/notes/pipeline-eval-2026-07-07.md:13-21) names this layered order of defense as the design. A reader of the diagram would think enforcement rests entirely on a second model.

**Verifier correction:** Minor line-number corrections: the --entry gate is described in skills/spec-dev/SKILL.md at roughly lines 55-71 (Step 1, item 6 and its sub-bullet), and the always-on --old/--new weaken-check section is at roughly lines 293-317. All other citations (write-a-prd validate_accept_body.py at 209/226/246, spec-up validate_spec_up_issue.py at 64-65, prd-to-issues validate_slice_issue.py at 80, pipeline-eval-2026-07-07.md "layered defense" in the lens-1 verdict) verify as cited.

**Proposed fix:** Add a fourth legend color or badge for 'deterministic script gate' and place three small nodes: validator lint before 2.5(b), the --entry gate at the start of 4a ('confirm targeted tests PENDING' already exists there, name the script), and the AST weaken-check between 4b and stage 5 with the note 'runs even when Codex is skipped'.

### 21. [MEDIUM] [improvement] Router has no routes for the operational skills that bracket a build: /findings-to-issues in, /merged and /wave-status out

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** The file calls itself 'the router: given the shape of the problem... it says where to enter' (line 3) but its table (lines 13-24) and canonical-skill list (lines 49-58) omit: /findings-to-issues, which produces the wave:N/serialize labels /spec-wave consumes (skills/findings-to-issues/SKILL.md:3-9,126-127; spec-wave/SKILL.md:26-28 'the execution half of what the labeling step produces'); /merged, the post-merge protocol with the suite-branch guard (skills/merged/SKILL.md:3-9,47); and /wave-status, built specifically because merge-order questions recurred ~13x/month (session-history-analysis-2026-07-07.md:17-26, commit c739baf). Problem shapes 'I have a review report', 'I just merged PRs', and 'where does the stacked work stand' are unroutable from the router.

**Verifier correction:** Minor citation nits only: the session-history counts are at session-history-analysis-2026-07-07.md:12-26 (pattern 1, "118 merged prompts... largest uncovered pattern"; pattern 4, ~20 merge-order/status prompts), and the spec-wave quote "the execution half of what the labeling step (`/findings-to-issues`) produces" sits at skills/spec-wave/SKILL.md:27-28. All other citations are accurate as given.

**Proposed fix:** Add three rows to the routing table: 'A review report or a pile of already-filed issues to wave-group -> /findings-to-issues -> /spec-wave or /spec-up'; 'PR(s) just merged in the browser -> /merged -> cleanup + what's-next'; 'Where does the stacked work stand? -> /wave-status'. Add all three to the canonical-skills list. Optionally add /merged and /wave-status as post-FINAL loop nodes in the diagram.

### 22. [MEDIUM] [improvement] Diagram stage 6 'harden / code-review / verify' depicts a mandatory automated per-PR stage that no skill or router prescribes

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 90-93 place '6 — harden / code-review / verify: Security + quality + test-coverage audit pass' as a blue (automated) stage every PR passes through before FINAL. Ground truth: /harden is a distinct entry point for audit passes ('An audit or hardening pass -> /harden -> findings', project-build-pipeline.md:23,26), not a per-PR pipeline stage; spec-dev's post-build gates are the deterministic weaken-check plus the Codex cross-review run IN PARALLEL with Matt's review, with the merge decision waiting for both (spec-dev/SKILL.md:316-360), and no spec-dev step invokes /harden or /verify per PR. The sequential stage-5-then-stage-6-then-FINAL layout also hides the parallelism.

**Verifier correction:** SVG stage-6 block is at content/diagrams/marvin-build-pipeline.svg lines 90-93 (rect + three text elements), with serial arrows at lines 89 and 95 and the "Automated step (AFK)" legend swatch for #dbeafe at lines 120-121. The /harden router row is in the "Route by problem shape" table of context/project-build-pipeline.md ("An audit or hardening pass | either | /harden | → findings", followed by "Specialized skills win"). Parallel-review ground truth: skills/spec-dev/SKILL.md lines 317-322 and changelog line 388; deterministic weaken-check at lines 295-315.

**Proposed fix:** Replace stage 6 with what actually runs per PR (deterministic weaken-check already proposed above), draw stage 5 Codex cross-review and Matt's review as parallel lanes converging on FINAL ('merge waits for BOTH'), and move /harden out of the main line into a side annotation: 'periodic audit entry point -> findings -> /findings-to-issues'.

### 23. [MEDIUM] [improvement] Router prerequisite 3 omits the pending-convention safety rule (no false-green skip, no sibling-red) and the canonical xfail(strict=True) form

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** Line 47 lists conventions generically ('pytest xfail, vitest it.skip / test.fails, a tagged suite') with no quality bar. Ground truth: the convention 'must keep a committed-red suite from both failure modes: silently skipping (a false-green that proves nothing) and reddening sibling PRs'; xfail(strict) gives this for free (spec-up/SKILL.md:47, codified in docs commit 3898b33). Non-strict conventions run red in CI for the suite->build window and need explicit acceptance at the gate (spec-wave/SKILL.md:61-67). Practice adds that markers must be literal @pytest.mark.xfail(strict=True) or the #491 guard misreads them (MEMORY feedback_acceptance_literal_xfail, #604/PR #605). A new project choosing plain it.skip per the router's list would silently defeat the invariant the same file calls 'the whole point of the pipeline' (line 7). The diagram banner similarly says 'xfail / skip-with-reason' without the strict qualifier.

**Proposed fix:** Expand prerequisite 3 with the two failure modes and the canonical forms: 'pytest: literal @pytest.mark.xfail(strict=True) (aliases defeat the #491 guard); non-Python conventions must either have true expected-fail semantics or an explicit opt-in run flag, else main runs red for the suite->build window'. Update the diagram banner to 'PENDING (xfail(strict=True) or equivalent)'.

### 24. [LOW] [accuracy-drift] Stage 5 Codex cross-review drawn as strictly serial before merge; skill runs it in parallel with Matt's review

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 84-98 show a single linear chain: 4b PR -> 5 Codex cross-review -> 6 -> FINAL Matt merges. Ground truth skills/spec-dev/SKILL.md line 320: kick off the cross-review as soon as the run is needs-review 'so it runs in parallel with Matt's own review rather than gating' (changelog line 388: '2026-07-01: Cross-Review Gate runs in parallel with Matt's review (merge waits for both)'). The merge decision waits for both, but the depicted sequencing (Matt only sees the PR after Codex and stage 6 finish) is not the actual flow.

**Verifier correction:** SVG serial chain is at lines 83-97 of /Users/matthewdruhl/marvin/content/diagrams/marvin-build-pipeline.svg (stage 5 header at line 86, stage 6 at line 91, FINAL at line 96), slightly wider than the cited 84-98 but substantively the same. Skill citations are exact: skills/spec-dev/SKILL.md line 320 (parallel-with-Matt's-review instruction, sentence spans lines 318-321) and line 388 (changelog entry).

**Proposed fix:** Annotate the 5 -> FINAL region: 'Codex review runs in parallel with Matt's review; merge waits for both', or draw stage 5 and Matt's review as parallel branches joining at FINAL.

### 25. [LOW] [accuracy-drift] 'PENDING on main' wording omits stacked mode (#618): suite may live on an approved suite branch

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG line 62 (Phase 4a): 'confirm targeted acceptance tests are PENDING on main'; line 48 banner: 'Approved tests land on project main as PENDING'. Ground truth skills/spec-dev/SKILL.md lines 36-37 and 59: tests may be 'on the project's main branch, or on the approved suite branch when the build stacks on the suite PR (#618)', with the suite commit an ancestor of the build branch; context/project-build-pipeline.md line 9 confirms ancestor-ordering, not merged-to-main-first, is the guarantee. Also the banner omits that tests land via PR (never directly on main).

**Verifier correction:** SVG text elements are at cat -n lines 62 ('confirm targeted acceptance tests are PENDING on main', Phase 4a) and line 48 (banner 'Approved tests land on project main as PENDING'), matching the finding. skills/spec-dev/SKILL.md: the stacked-mode language appears in the Requirements bullet ('either on the project's main branch, or on the approved suite branch when the build stacks on the suite PR (#618)') and again in Step 1 item 6 ('on the suite ref: the project's main branch, or the approved suite branch tip in stacked mode (#618)'). context/project-build-pipeline.md line 9: 'Ancestor-ordering (not merged-to-main-first) is the guarantee (#618)'.

**Proposed fix:** Change 4a text to 'PENDING on the suite ref (main, or approved suite branch in stacked mode #618)' and the banner to '...land on project main via PR as PENDING'.

### 26. [LOW] [accuracy-drift] Loop-back 'next unblocked slice' points at stage 3 (prd-to-issues) instead of the per-slice dispatch point

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG line 111: purple dashed arrow from the spec-dev container (y=1200) back to the stage 3 box (y=764). Ground truth: skills/prd-to-issues/SKILL.md creates ALL slice issues once (Step 5, dependency order); iterating slices means running /spec-dev again on the next unblocked issue, not re-entering prd-to-issues. The correct loop target is the 'per slice (in dep order)' edge below stage 3, not the stage 3 node itself.

**Proposed fix:** Terminate the loop-back arrow at the 3->4 edge / 'per slice' label (or a small 'next unblocked issue' junction) rather than at the prd-to-issues box.

### 27. [LOW] [accuracy-drift] agent-contract names a spec-dev PR section ('Acceptance Criteria Verification') that spec-dev does not use

**Artifact:** `context/agent-contract.md`

**Evidence:** agent-contract.md:50: 'Skills may extend this (e.g., spec-dev adds Acceptance Criteria Verification) but never drop the Summary or Test Plan sections.' spec-dev/SKILL.md:252-266 actually extends the PR format with 'Acceptance Tests Flipped' and 'Full Suite' sections; no section named 'Acceptance Criteria Verification' exists in spec-dev.

**Proposed fix:** Update agent-contract.md:50's example to 'spec-dev adds Acceptance Tests Flipped and Full Suite'.

### 28. [LOW] [improvement] No route for a stranded/failed build (recovery loop #675)

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** skills/spec-dev/SKILL.md (Build recovery loop, ~lines 170-190) and skills/spec-wave/SKILL.md (Step 6 recovery, ~lines 256-269) define a load-bearing failure path: scripts/build_recovery.py, record_attempt/next_action with cap=2, reset_worktree + fresh agent from frozen contract + trace (never the prior transcript), then escalate_run to needs-review. Wave-2 on 2026-07-09 needed 6 recoveries. The routing playbook has no row or pointer for the "a fan-out build stranded / suite still red after 'done' / Codex gate rejected" problem shape, so an operator hitting it has no router-level answer.

**Proposed fix:** Add one routing-table row or a sentence under the chains: "A stranded or Codex-rejected build → the recovery loop in /spec-dev (scripts/build_recovery.py, #675): retry with a fresh agent from the frozen contract, cap 2, then escalate to needs-review. Never hand-author a recovery PR."

### 29. [LOW] [improvement] Canonical-skills list omits /harden even though the routing table routes to it, and omits /grill-me as an adjacent shape

**Artifact:** `context/project-build-pipeline.md`

**Evidence:** project-build-pipeline.md:23 routes "an audit or hardening pass" to /harden, but the "Where each step is documented" list (lines 49-58) does not include skills/harden/SKILL.md, breaking the doc's own pattern of naming every routed skill's canonical location. Separately, skills/grill-me/SKILL.md:3 defines an adjacent problem shape ("pressure-test a plan/design the user already has, OUTSIDE the PRD pipeline") that can feed decisions into /write-a-prd; the router has no row for it, so a plan-pressure-test request has no documented route or non-route.

**Proposed fix:** Add /harden: skills/harden/SKILL.md to the canonical list (audit entry; findings feed /findings-to-issues). Optionally add a table row: "An existing plan/design to pressure-test | either | /grill-me (outside the pipeline) | → decisions.md, or feed /write-a-prd".

### 30. [LOW] [improvement] Stage 5 gives no Codex-unavailable fallback; reader could treat a failed Codex run as a pass or a hard block

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG lines 86-88 present stage 5 as an unconditional blocking gate. Ground truth skills/spec-dev/SKILL.md lines ~340-358: fallback chain plugin -> codex exec -> skip-with-recorded-reason ('do not block on it'), while a non-zero exit or empty output is a FAILED review, never a pass, recorded in the run's cross_review field. The diagram carries neither nuance.

**Verifier correction:** SVG lines 86-88 (stage 5 gate text, no fallback note); skills/spec-dev/SKILL.md lines 345-355 (fallback chain, FAILED-review rule, "do not block on it" skip semantics).

**Proposed fix:** Add a one-line footnote to stage 5: 'fallback: codex exec; unavailable = skip w/ recorded reason; failed/empty run = FAILED review, not a pass'.

### 31. [LOW] [improvement] Lint-gate scripts (validate_accept_body.py, validate_slice_issue.py) not shown at their gates

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** SVG 2.5(a) (lines 37-38) and stage 3 (lines 51-54) show no mechanical lint gates. Ground truth: write-a-prd Step 7.5 gate 1 requires scripts/validate_accept_body.py exit 0 before the Codex batch (SKILL.md ~lines 244-247); prd-to-issues Step 5 requires scripts/validate_slice_issue.py exit 0 (with the mandatory 'Targets acceptance tests' section) before each gh issue create (SKILL.md lines 68-115, 'If the script ... exits non-zero, do not file the slice').

**Verifier correction:** SVG evidence is at different line numbers than cited: the 2.5 sub-steps (a)/(b) are at marvin-build-pipeline.svg lines 37-44 (no lint between authoring and Codex gate), and stage 3 is at lines 51-57. Skill citations: write-a-prd/SKILL.md lines 245-248 (not 244-247); prd-to-issues/SKILL.md lines 74-88 for the lint requirement (68-115 also covers the template).

**Proposed fix:** Add small sublabels: 'lint: validate_accept_body.py must exit 0' under 2.5(a), and 'lint: validate_slice_issue.py — Targets acceptance tests required' inside stage 3.

### 32. [LOW] [improvement] Three different retry/escalation caps (3 attempts, 2 reviewer rounds, cap=2 recoveries) are never distinguished, inviting conflation

**Artifact:** `skills/spec-dev/SKILL.md`

**Evidence:** agent-contract.md:56: failing tests get 'at most 3 distinct attempts' (agent-internal). spec-dev/SKILL.md:166-168: 'After 2 failed rounds on the same slice, escalate/take over' (reviewer side, #676). spec-dev/SKILL.md:170-190 and spec-wave:256-269: build_recovery next_action(attempt, cap=2) (orchestrator re-dispatches). spec-dev/SKILL.md:370-371 then cites the 3-attempt rule again. The numbers apply at different layers but no artifact says so; an operator or agent reading two of the three can reasonably conclude the caps contradict each other.

**Verifier correction:** The 3-attempt rule lives at /Users/matthewdruhl/marvin/context/agent-contract.md:56 (context/, not a spec-dev sub-file). spec-wave citation is lines 256-268, not 256-269. Also note spec-dev partially labels each cap's layer locally (SKILL.md:166 "(reviewer side)", :170 "Build recovery", :370-371 "Contract verification rules"), so the gap is the missing statement of how the three caps nest/interact, not the absence of any layer labels.

**Proposed fix:** Add one clarifying sentence in spec-dev (near the Handoff-Prompt Contract) naming the three layers explicitly: 3 in-agent attempts per failing test (contract), 2 reviewer fix-rounds per slice (#676), 2 fresh-agent recovery re-dispatches (#675), and that they nest rather than conflict.

### 33. [LOW] [improvement] Diagram is undiscoverable and unversioned: nothing in the router, CLAUDE.md, or skills links to it, and it has drifted a month behind the skills

**Artifact:** `content/diagrams/marvin-build-pipeline.svg`

**Evidence:** grep -rl 'marvin-build-pipeline' over *.md finds only June session archives (sessions/archive/2026-06/2026-06-16.md, 2026-06-18.md); context/project-build-pipeline.md, CLAUDE.md, and no SKILL.md reference it. The diagram therefore predates spec-up (created 2026-06-27, spec-up/SKILL.md:133), spec-wave (2026-07-03, spec-wave/SKILL.md:301), stacked mode (#618, 2026-07-08), and recovery (#675, 2026-07-09), which explains every drift above. The practice extract notes docs lag practice by design (docs commits 3898b33, d605518, 65d64aa), so an unlinked, undated artifact will keep drifting silently.

**Proposed fix:** Add a 'Diagram: content/diagrams/marvin-build-pipeline.svg (regenerate when routes change)' pointer to project-build-pipeline.md, and stamp the SVG subtitle with a last-updated date + the router as its source of truth. Consider adding 'regenerate the pipeline diagram' to the instruction-ownership change checklist (context/instruction-ownership.md:14-28) so docs commits that change routing also refresh the picture.

## Refuted findings (recorded for the audit trail)

- **Playbook declares greenfield waves valid for /spec-wave; the canonical skill describes itself strictly as brownfield** — refuted: The finding hinges on the claim that "nowhere does the skill state that /prd-to-issues-fed greenfield waves are in scope," but /Users/matthewdruhl/marvin/skills/spec-wave/SKILL.md:45-47 states it explicitly: "**No issues exist yet / an epic**: use `/write-a-prd` then `/prd-to-issues` first; those produce the disjoint slices a wave then builds." That directly endorses waves built from greenfield-origin /prd-to-issues slices, matching context/project-build-pipeline.md:19 and :33 ("greenfield waves fed by /prd-to-issues slices are valid"). The "Brownfield wave orchestrator" self-description (SKILL.md:4) and its "NOT for ... /write-a-prd for greenfield" clause route away only pre-issue/epic work, the same carve-out the playbook makes ("1-2 issues or heavy ambiguity route to plain sequential /spec-up"). The files agree; no contradiction exists.

- **Router's bolded invariant ('suite lands on main first' before any implementation exists) is contradicted two lines later by the #618 ancestor-ordering paragraph** — refuted: No actual disagreement. project-build-pipeline.md:7 states two properties: "the suite commit is an ancestor of every implementation commit, and the suite lands on `main` first". Both match the ground-truth skills exactly: spec-dev/SKILL.md:37-38 says "In stacked mode the suite commit MUST be an ancestor of the build branch, and the suite PR merges to main first", and spec-wave/SKILL.md Step 6 says "merge order: suite first, build second, both the human's". "Lands on main first" is a merge-order claim (suite PR before build PR), which #618 stacked mode preserves, and project-build-pipeline.md:9 explicitly qualifies line 7 in the very next paragraph ("Ancestor-ordering (not merged-to-main-first) is the guarantee... The suite PR still merges first, the build PR second"), so the document itself forecloses the park-until-merge misreading the finding hinges on. At most this is a phrasing-clarity nitpick, not a contradiction between the artifact and the skills or within the artifact.

- **Router lists vitest it.skip as a valid pending-test convention, which is the silent-skip false-green spec-up's convention rule forbids** — refuted: No artifact/skill disagreement exists. The router's convention list at context/project-build-pipeline.md:47 ("pytest `xfail`, vitest `it.skip` / `test.fails`, a tagged suite") is a verbatim mirror of the ground-truth skill's own example list at skills/spec-up/SKILL.md:47, which reads "(pytest `xfail`, vitest `it.skip` / `test.fails`, a tagged suite, etc.)". The skill itself lists it.skip as an acceptable convention example, so the router cannot be contradicting the skill by repeating it. Further, spec-up:47's rule does not categorically forbid skip-style conventions: it requires the convention avoid both failure modes and explicitly blesses the gated-skip pattern ("needs an explicit gate so pending suites only run when opted in — check the repo's testing doc (e.g. rdv-expenses `docs/testing.md`)"), meaning it.skip behind an opt-in gate is compliant, not "the forbidden false-green". The alleged tension (listing it.skip next to an anti-silent-skip rule) is internal to spec-up/SKILL.md itself, which is the ground truth here, not drift in the artifact. The proposed fix of duplicating the gate rule into the router would also conflict with CLAUDE.md's instruction-ownership rule against duplicating skill content. spec-wave/SKILL.md:61-67 covering only the run-red mode is a fact about a different skill, not about this artifact.
