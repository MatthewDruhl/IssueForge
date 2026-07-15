# IssueForge v1 decomposition — FINAL REPORT (27 issues created)

**Status: FILED.** PRD #1 is decomposed into **25 v1 child issues (S1–S25)** plus **2 `deferred-v2`** items
(DV1, DV2), each owning its acceptance criteria exactly once. All 27 were created, labeled, linked to the epic,
and re-read from GitHub and verified. This report records what was filed, the review history, and the open
refinements tracked on the child issues.

| | |
|---|---|
| **Date** | 2026-07-15 |
| **Repository** | `MatthewDruhl/IssueForge` (private) |
| **Source PRD / epic** | [#1](https://github.com/MatthewDruhl/IssueForge/issues/1) — amended with D1–D6 |
| **PRD scale** | **59** acceptance criteria across 11 user stories |
| **Child issues created** | **27** (#4–#30): 25 `v1` + 2 `deferred-v2` |
| **Coverage** | 59/59 criteria owned exactly once |
| **Filed from** | `docs/planning/decomposition-draft-v5.md` |
| **MARVIN files/issues modified** | **0** |

---

## 1. What was created

25 v1 child issues and 2 deferred-v2 items. The slice → issue map:

| Slice | Issue | Phase | Blocked by | Title |
|---|---|---|---|---|
| S2 | #4 | 0 | — | Source-audit inventory + completeness lint |
| S25 | #5 | 0 | — | IO write seam, path resolver, boundary AST lint (permanent CI gate) |
| S1 | #6 | 0 | #5 | Process seam, tri-state results, `.issueforge.toml`, adapter interface + `probe` |
| S3 | #7 | 0 | #6, #5 | Register a repository; resolve or refuse its adapter |
| S4 | #8 | 0 | #6, #7, #5 | Run store + enqueue + stub stage (one locked write path, redacting) |
| S5 | #9 | 0 | #8 | Queue control: FIFO, pause, park, cancel, resume, `continue` |
| S8 | #10 | 1 | #6 | Observability policy: boundary classifier + sensitive-field exclusion |
| S7 | #11 | 1 | #6, #8 | AI provider layer: roles, profiles, guarded launch, session identity |
| S6 | #12 | 1 | #7, #8 | Isolated worktree, green baseline, `provision_environment` + `classify` |
| S9 | #13 | 2 | #9, #11, #10 | Buildability contract + human approval of the implementation write scope |
| S20 | #14 | 2 | #13, #8 | Shape an issue: in-place revision + approved GitHub mutation plan |
| S22 | #15 | 5 | #8 | Retention and `issueforge purge` |
| S10 | #16 | 3 | #12, #11, #13, #14 | Author acceptance tests + deterministic red proof |
| S11 | #17 | 3 | #16 | Independent review of the red contract: semantic validity + override |
| S12 | #18 | 3 | #16, #17 | Human approval freezes the manifest; adapter-discovered dependency closure |
| S13 | #19 | 3 | #18 | Contract integrity enforcement + `validate_invocation` + amendment path |
| S14 | #20 | 4 | #19 | Implement under the frozen contract; two engine-owned repair budgets |
| S15 | #21 | 4 | #20, #10 | Readiness gate: implementation write scope, code review, human override |
| S16 | #22 | 4 | #21 | One green PR — pushed only after the gate, verified at origin, never merged |
| S17 | #23 | 4 | #22 | Delivery verification: exact merge-commit + head-sha binding |
| S18 | #24 | 4 | #23, #14 | Closeout: comment, close the exact run issue, update the parent epic |
| S19 | #25 | 4 | #23, #24 | Safe cleanup: branches and worktrees (an independent stage result) |
| S21 | #26 | 5 | #14 | Epic decomposition of an oversized issue |
| S23 | #27 | 5 | #9, #22, #25 | TUI + CLI/TUI parity — all eight views |
| S24 | #28 | 5 | #5, all v1 | Self-contained boundary: a permanent CI invariant |
| DV1 | #29 | dv2 | — | Blocking mutation / anti-tautology gate |
| DV2 | #30 | dv2 | — | The invariant lens for shaping |

**Recommended first slice:** S3 (#7) — `repo add` → `repo list`, the smallest demoable unit.
**First full tracer bullet (no AI):** S1 → S3 → S4 → S5 → S6 = #6 → #7 → #8 → #9 → #12.
**Enabling gates that must land first:** S25 (#5) and S2 (#4).

---

## 2. Review history and the filing decision

The decomposition was hardened across **six independent fresh-session review rounds** (guarded `codex exec`,
gpt-5.6-sol at high reasoning effort, read-only, no network, all inputs on local disk). Each round's findings
were verified against the PRD before being accepted — a reviewer's claim about the PRD was never taken on faith.

- **Attempt-3 gate (on draft v3 + D5/D6 + mechanical fixes):** round 1 REVISE (4 findings, all repaired); round
  2 REVISE (3 findings). Per protocol, attempt 3 created 0 issues and produced a failure report
  (`issueforge-v1-decomposition-report-v3.md`). The three findings became F1–F3.
- **Continuation gate (on draft v5, F1–F3 resolved):** round 1 REVISE (1 finding, G1: S1 was a disk-writer not
  blocked by the S25 write seam — repaired); round 2 REVISE (2 findings, B1 import-error phase rule, B2 US-5.2
  ownership — both repaired).
- **Final gate round (draft v5, all above resolved):** REVISE with four further deep-design refinements
  (see §3). At this point every finding across every round had been legitimate but the high-effort adversarial
  reviewer kept surfacing new refinements — several as consequences of prior fixes — with no near-term
  convergence to zero.

**The filing decision.** GitHub issues are implementation **starting points**, not final specifications: each
child issue passes its own `spec-up`/`spec-dev` build gate (author acceptance tests, independent contract
review, human approval, integrity enforcement, code review) before any code merges. The decomposition had by
this point resolved D1–D6, the three mechanical fixes, F1–F3, G1, and B1/B2 — with the coverage matrix complete
and singly owned throughout. The author therefore elected to **file the 27 issues now** and track the four
remaining refinements on the affected child issues, to be resolved during those issues' builds. This is an
explicit author-level acceptance of a strongly-reviewed plan as a starting point, recorded here rather than
smuggled.

---

## 3. Open refinements tracked on the child issues

Four refinements from the final review round are recorded inline on the affected issues (and in
`decomposition-draft-v5.md` §15), to be resolved at `spec-up`. All four are refinements to *how* a slice is
built, not gaps in coverage; none changes an owner or the dependency graph.

- **B1 — committed config** (S1 #6, S3 #7). US-4.1 (`prd-v1.md:54`) says the repo *commits* `.issueforge.toml`;
  load/validate it from the **committed Git object**, not an untracked working-tree copy.
- **B2 — scope-expansion disjointness** (S15 #21). On a US-3.7 scope expansion, re-check D5 disjointness
  against the frozen contract set and fail on overlap before updating scope (`:68`, `:47`).
- **B3 — observability amendment path** (S9 #13, S13 #19, S15 #21). A boundary discovered at readiness routes
  to an **S9 observability amendment** (recompute + reviewer-confirm + human-approve), not S13's
  acceptance-contract amendment (`:82`).
- **B4 — executable log-emission evidence** (S8 #10, S15 #21). Prove required logging with runtime evidence
  (seeded sensitive-field canaries during the authoritative run), not static call-site presence (`:83-84`,
  `:80`).

---

## 4. Coverage and structure

- **59/59 acceptance criteria owned exactly once** (the §7 matrix in `decomposition-draft-v5.md`); zero silently
  weakened, zero silently deferred.
- **D1–D6 applied:** pytest-only adapter with a mandatory thin per-framework surface (D1); the human
  implementation-review override (D2); the split shaping / early buildability contract (D3); two engine-owned
  repair budgets (D4); two disjoint file-role scopes — implementation write scope vs frozen contract set (D5);
  the test-granular source audit against a checked-in extraction manifest (D6).
- **Dependency graph is acyclic**; build order (S2/S25/S1 first) differs from runtime order (S3/S6 run after S9)
  and the transition table makes runtime order impossible to bypass.
- **No premature abstraction and no new runtime dependency** in v1; genuinely deferred work is filed as
  `deferred-v2` (DV1, DV2), never dropped.

---

## 5. Verification

- **All 27 child issues were re-read from GitHub and verified:** title prefix (`S<n> —`), labels
  (`v1`/`phase:*`/`route:*`, or `deferred-v2`), `## Parent PRD #1` linkage, `## Blocked by` with the correct
  issue-number references, a `Prior-art and source audit` section, observable acceptance criteria, and `OPEN`
  state. **All 27 OK; zero problems.**
- **Epic #1 verified:** the PRD body is **intact** (Problem Statement and User Stories present — the
  decomposition section was **appended**, not substituted), the `v1 decomposition — child issues` section links
  **all 27** children, and the `epic` label is applied. **PRD #1's acceptance criteria and decisions D1–D6 are
  unchanged.**
- **Labels** were the existing set from a prior attempt, reused deliberately: `v1`, `phase:0`–`phase:5`,
  `route:direct-tdd`, `route:spec-up`, `deferred-v2`, `epic`.
- **No MARVIN file, state, skill, ledger, configuration, generated artifact, or GitHub issue was modified.**
  MARVIN was read-only provenance throughout; every command was scoped to the IssueForge repo or the scratchpad,
  and the six review rounds ran read-only in the scratchpad.
- **The six review rounds ran under the guarded-launch contract:** stdin closed, stderr captured to a file, a
  `perl -e 'alarm N; exec @ARGV'` wall-clock timeout (`timeout(1)` absent on macOS), full output persisted,
  empty-output-or-non-zero treated as FAILED, and every input materialized to local disk (the reviewer has no
  network). One round was killed externally mid-run and correctly re-run rather than treated as a verdict.

**PDF verification.** `issueforge-v1-decomposition-report-final.pdf` was rendered from this markdown via
HTML + Chrome headless (`--headless --disable-gpu --no-pdf-header-footer --print-to-pdf`). Every page was
inspected: exactly one `<h1>`, zero blank pages, no paragraph beginning `#` immediately followed by a digit
(the lazy-`<h1>` hazard), and a stated page count matching the actual one. **Result: 5 pages, 0 blank pages,
1 `<h1>` — verification PASS.**

---

## 6. What's next

Grab **S3 (#7)** first (`repo add`), or build the enabling gates **S25 (#5)** and **S2 (#4)** that every
implementation slice depends on. Each child issue is routed (`route:direct-tdd` or `route:spec-up`) into its
acceptance-test authoring; `route:spec-up` issues shape a build contract via `/spec-up` before `/spec-dev`
implements them. The four open refinements (§3) are resolved as part of their host issues' builds.

---

*Full rationale, the 59/59 coverage matrix, the source audit, the dependency graph, and the complete
round-by-round repair log are in `docs/planning/decomposition-draft-v5.md`. Preserved failure evidence from the
earlier attempts: `issueforge-v1-decomposition-report.md` (1), `-report-v2.md` (2), `-report-v3.md` (3).*
