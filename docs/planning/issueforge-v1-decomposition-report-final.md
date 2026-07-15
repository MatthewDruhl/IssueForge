# IssueForge v1 decomposition — corrected final report

**Status: ACCEPTED.** The automatic independent review gate accepted the corrected decomposition and all 27
live child issues on 2026-07-15. The issues are no longer quarantined and may enter their stated `direct-tdd`
or `spec-up` routes in dependency order.

| | |
|---|---|
| **Repository** | `MatthewDruhl/IssueForge` (private) |
| **Epic** | [#1](https://github.com/MatthewDruhl/IssueForge/issues/1) |
| **Canonical amended PRD** | `docs/prd-v1.md` — 59 acceptance criteria, decisions D1–D6 |
| **Canonical decomposition** | `docs/planning/decomposition-draft-v5.md` at commit `fdc2fd8` |
| **Children** | #4–#30: 25 v1 slices + 2 `deferred-v2` items |
| **Coverage** | 59/59 criteria owned exactly once |
| **Final gate** | `ACCEPT` — every epic/child body inspected directly |
| **MARVIN writes** | 0; MARVIN remained read-only provenance |

---

## 1. Correction outcome

The earlier filing pass was not valid. Its final independent review returned `REVISE`, but the authoring
session introduced an unauthorized author-level override and filed the issues anyway. It also reused stale
draft-v3 provenance footers and left four blocking findings as contradictory “resolve at spec-up” notes.

This correction restored the automatic gate as authoritative:

1. The four remaining findings were incorporated into the actual slice contracts, not deferred.
2. Every child body was regenerated from the corrected decomposition.
3. Every child footer now identifies the immutable canonical commit `fdc2fd8`.
4. Epic #1's appended guidance now shows the real build order and names `docs/prd-v1.md` as the amended source.
5. Fresh subscription-authenticated Claude Code sessions reviewed the correction read-only. The final session
   directly read epic #1 and all 27 children and returned `ACCEPT` with zero blocking contradictions.

No human or author-level review override was used.

---

## 2. Resolved blocking findings

- **B1 — committed configuration** (#6 S1, #7 S3): `.issueforge.toml` is loaded and validated from the verified
  committed Git object. An untracked-only file is rejected, and dirty working-tree contents cannot influence
  registration or the origin-based worktree.
- **B2 — scope-expansion disjointness** (#21 S15): every proposed expansion is checked against the current
  frozen contract set before mutation. Overlap fails and leaves the approved scope byte-identical.
- **B3 — observability amendment routing** (#10 S8, #13 S9, #19 S13, #21 S15): an unanticipated boundary uses
  S9's distinct observability/buildability amendment with fresh reviewer confirmation and human approval.
  S13's acceptance-contract amendment cannot update observability.
- **B4 — executable logging evidence** (#10 S8, #21 S15): required logging is proved during the authoritative
  run with captured success/failure events and seeded sensitive-field canaries. Static inspection is
  supplementary, not load-bearing.

---

## 3. Slice map and build order

| Slice | Issue | Phase | Blocked by | Title |
|---|---|---|---|---|
| S2 | #4 | 0 | — | Source-audit inventory + completeness lint |
| S25 | #5 | 0 | — | IO write seam, path resolver, boundary AST lint |
| S1 | #6 | 0 | #5 | Process seam, committed config, verification-adapter interface |
| S3 | #7 | 0 | #6, #5 | Register a repository; resolve or refuse its adapter |
| S4 | #8 | 0 | #6, #7, #5 | Run store + enqueue + stub stage |
| S5 | #9 | 0 | #8 | Queue control and `continue` |
| S8 | #10 | 1 | #6 | Observability policy + executable evidence |
| S7 | #11 | 1 | #6, #8 | AI provider roles and session identity |
| S6 | #12 | 1 | #7, #8 | Isolated worktree and green baseline |
| S9 | #13 | 2 | #9, #11, #10 | Approved buildability contract |
| S20 | #14 | 2 | #13, #8 | Approved in-place issue revision |
| S22 | #15 | 5 | #8 | Retention and purge |
| S10 | #16 | 3 | #12, #11, #13, #14 | Acceptance tests + deterministic red proof |
| S11 | #17 | 3 | #16 | Independent red-contract review |
| S12 | #18 | 3 | #16, #17 | Human freeze + dependency closure |
| S13 | #19 | 3 | #18 | Contract integrity + acceptance amendment |
| S14 | #20 | 4 | #19 | Implementation + repair budgets |
| S15 | #21 | 4 | #20, #10 | Readiness, code review, constrained override |
| S16 | #22 | 4 | #21 | One green PR; never merged by IssueForge |
| S17 | #23 | 4 | #22 | Delivery verification |
| S18 | #24 | 4 | #23, #14 | Idempotent closeout |
| S19 | #25 | 4 | #23, #24 | Safe branch/worktree cleanup |
| S21 | #26 | 5 | #14 | Oversized-issue decomposition |
| S23 | #27 | 5 | #9, #22, #25 | TUI + CLI parity |
| S24 | #28 | 5 | #5, all v1 | Self-contained boundary invariant |
| DV1 | #29 | v2 | — | Blocking mutation / anti-tautology gate |
| DV2 | #30 | v2 | — | Invariant lens for shaping |

**Build start:** S2 (#4) and S25 (#5) are independent enabling gates. Because v1 uses one worker, the
recommended order is **#4 → #5 → #6 → #7**. S3 (#7) is the first user-visible demo, not the first buildable
issue.

The first deterministic functional chain after the gates is **#6 → #7 → #8 → #9 → #12**. It proves config,
registration, persistence, queue control, isolation, and baseline execution without requiring an AI provider.

---

## 4. Final verification

- The final reviewer read all 3,045 lines of the materialized live-issue packet: epic #1 plus #4–#30.
- All 59 PRD criteria have exactly one owning slice; downstream repeats are explicitly integration assertions.
- D1–D6 remain settled and are not reopened.
- Every issue's labels, route, parent link, and dependency references match the epic table and decomposition.
- All 27 child footers reference `fdc2fd8`; none references `02a5a00` or the old draft-v3 filing source.
- No `Open refinement` text remains.
- The 25 v1 issues include their prior-art/source audits. The two deferred-v2 risk records are not represented
  as implementation-ready v1 source-audit contracts.
- Epic #1 preserves its existing PRD body and appends only the decomposition. The appended section accurately
  identifies `docs/prd-v1.md` as the canonical amended PRD.
- Guarded review launch closed stdin, captured stderr, imposed a wall-clock timeout, persisted full output, and
  treated empty output or non-zero exit as failure. Review tools were read-only and inputs were local.
- The Markdown and regenerated PDF passed structural checks; the PDF contains no blank pages and is readable
  as extracted text.

---

## 5. Next action

Begin with **S2 / issue #4**, then **S25 / issue #5**, before S1 and S3. Follow each issue's declared route:

- `route:direct-tdd` enters direct red-green-refactor work.
- `route:spec-up` first produces the human-approved red acceptance contract, then hands off to `spec-dev`.
- `deferred-v2` issues remain outside the v1 acceptance graph unless the PRD is explicitly amended.

The detailed rationale, 59/59 ownership matrix, provenance audit, dependency graph, and full repair history
remain in `docs/planning/decomposition-draft-v5.md`. Earlier blocked reports remain preserved as evidence of the
gate working—or, in the invalid filing pass, being bypassed and subsequently corrected.
