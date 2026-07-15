# IssueForge v1 decomposition: final report

**Status: ACCEPTED.** The v1 decomposition (25 slices + 2 `deferred-v2` items, 59/59 criteria owned) was
accepted by the independent review gate and filed as live child issues #4–#30 under epic #1. This report also
records the **documentation-naming cleanup** that re-based those documents onto stable, git-versioned canonical
filenames without changing a single requirement or contract. A fresh read-only independent review of the
renamed state returned `REVISE` on two consistency findings (both repaired below), then `ACCEPT` on the second
round after directly inspecting the PRD, the decomposition, epic #1, and all 27 child bodies.

| | |
|---|---|
| **Repository** | `MatthewDruhl/IssueForge` (private) |
| **Epic** | [#1](https://github.com/MatthewDruhl/IssueForge/issues/1) |
| **Canonical amended PRD** | `docs/prd.md`, 59 acceptance criteria, decisions D1–D6 |
| **Canonical decomposition** | `docs/planning/decomposition.md` |
| **Immutable provenance commit** | `8d5f0882e0ecb2ba0352cf66bf3a4698fabc6ad3` (cited by all 27 child footers) |
| **Decomposition-content lineage** | content accepted at `fdc2fd8`; renamed at `8d2490b`; deferred-slice IDs made consistent at the provenance commit |
| **Children** | #4–#30: 25 v1 slices + 2 `deferred-v2` items, all OPEN |
| **Coverage** | 59/59 criteria owned exactly once |
| **MARVIN writes** | 0; MARVIN remained read-only provenance |

---

## 1. Why Git, not numbered filenames, carries revision history

The planning directory had accumulated version-suffixed filenames: `prd-v1.md`, four `decomposition-draft-v2..v5`
files, and four `issueforge-v1-decomposition-report{,-v2,-v3,-final}` reports. Those suffixes duplicated
information Git already tracks. A file's revision history is `git log --follow <path>`; encoding "v5" or "final"
into the name adds a second, manually-maintained version axis that drifts out of sync (there is no stable name
to link to, and "final" is only final until the next edit).

The fix is to give each **living** document one stable, purpose-based name and let Git supply its history. A
reader who wants the previous state runs `git log` or `git show <sha>:<path>`; a reader who wants the accepted
snapshot uses the immutable commit recorded below. Version terms are retained only where they name **actual
product scope**: the v1 release, the `v1` issue label, and the `deferred-v2` items, never to mark successive
edits of the same file.

## 2. Files renamed (all via `git mv`, history preserved)

**Canonical, living documents:**

| Old path | New path |
|---|---|
| `docs/prd-v1.md` | `docs/prd.md` |
| `docs/planning/decomposition-draft-v5.md` | `docs/planning/decomposition.md` |
| `docs/planning/issueforge-v1-decomposition-report-final.md` | `docs/planning/decomposition-report.md` |
| `docs/planning/issueforge-v1-decomposition-report-final.pdf` | `docs/planning/decomposition-report.pdf` |

`git` recorded `docs/prd-v1.md → docs/prd.md` as a 100%-identical rename: the PRD body did not change.

## 3. How the historical attempts are organized

Three decomposition attempts were **BLOCKED** by the review gate before the accepted correction. Each attempt
produced a draft plus a failure report. All of that evidence now lives under `docs/planning/reviews/` with
purpose-based, attempt-numbered names (attempt numbers denote genuinely separate executions, confirmed by their
shared authoring commits, not successive edits of one file):

| Attempt | Draft (was) | → | Failure report (was) | → |
|---|---|---|---|---|
| 01 | `decomposition-draft-v2-SUPERSEDED.md` | `reviews/decomposition-attempt-01-draft.md` | `issueforge-v1-decomposition-report.md` (+`.pdf`) | `reviews/decomposition-attempt-01-blocked.md` (+`.pdf`) |
| 02 | `decomposition-draft-v3.md` | `reviews/decomposition-attempt-02-draft.md` | `issueforge-v1-decomposition-report-v2.md` (+`.pdf`) | `reviews/decomposition-attempt-02-blocked.md` (+`.pdf`) |
| 03 | `decomposition-draft-v4.md` | `reviews/decomposition-attempt-03-draft.md` | `issueforge-v1-decomposition-report-v3.md` (+`.pdf`) | `reviews/decomposition-attempt-03-blocked.md` (+`.pdf`) |

The six adversarial review transcripts (`reviews/01-*` … `reviews/08-*`) and the now-spent operating prompt
(`NEXT-RUN-PROMPT.md` → `reviews/decomposition-next-run-prompt-historical.md`) are also under `reviews/`. No
historical evidence was deleted. Those files still narrate past events using the original draft names; that is
correct: they describe the state as it was, not a current path.

## 4. Immutable provenance commit

The work landed as two dedicated commits on branch `docs/decomposition-v1-issues-filed`, each pushed and
verified to resolve at origin to its exact SHA:

- **`8d2490b`**: the renames and every in-repo reference update.
- **`8d5f0882e0ecb2ba0352cf66bf3a4698fabc6ad3`**: one consistency fix the independent gate required: the
  one-line `deferred-v2` summary in `decomposition.md` called the two slices `D1`/`D2`, colliding with settled
  decisions D1–D6; renamed to the canonical `DV1`/`DV2` used everywhere else. Names only; no requirement,
  ownership, dependency, or route changed.

Every child footer (#4–#30) and epic #1's appended section identify `docs/planning/decomposition.md` at
**`8d5f088`**. The decomposition **content** is byte-unchanged from the version accepted at `fdc2fd8` apart
from that one deferred-slice ID relabel and the path-reference updates.

## 5. Verification of epic #1 and issues #4–#30

Each issue was edited and then **read back from GitHub** and compared byte-for-byte to the intended body:

- All **27** child footers cite `docs/planning/decomposition.md` at `8d5f088…`, with a direct commit link and
  the correct slice id (S1–S25, DV1, DV2).
- **Zero** child bodies retain any old canonical filename (`prd-v1.md`, `decomposition-draft-v*`,
  `issueforge-v1-decomposition-report*`). In-body PRD line citations were updated `prd-v1.md:N → prd.md:N`;
  because the PRD rename is byte-identical, every line number still resolves.
- All 27 issues remain **OPEN** with their existing labels, routes (`route:direct-tdd` / `route:spec-up` /
  `deferred-v2`), dependencies, and slice ids unchanged from the pre-pass baseline.
- Epic #1 names `docs/prd.md` (canonical amended PRD) and `docs/planning/decomposition.md` at the provenance
  commit. The PRD body above the `<!-- prd-to-issues:v1-decomposition -->` marker is byte-for-byte identical to
  before; the accepted gate status and the build order are preserved.
- The independent gate also flagged one epic-table transcription gap: the S24 (#28) "Blocked by" column read
  only `#5`, while child #28 and `decomposition.md` require `S25` **and all v1 implementation issues**. The epic
  table was corrected to `#5, all v1` to match the child contract; no child issue changed.

## 6. What did not change

- **No PRD requirement, no acceptance criterion, and no decision (D1–D6) was altered.** The 59/59 ownership
  matrix is unchanged; each criterion still has exactly one owning slice.
- **No issue contract changed beyond paths and provenance.** Titles, acceptance criteria, scope, routes,
  dependencies, and slice ids are identical to the accepted decomposition.
- **Product-scope version terms were preserved on purpose:** the v1 release framing, the `v1` label on the 25
  slices, and the two `deferred-v2` items (#29, #30). Only redundant file-name version suffixes were removed.
- **MARVIN was untouched.** No file, issue, or state under `/Users/matthewdruhl/marvin` was read-for-write or
  modified; it remained read-only migration provenance.

## 7. Accepted decomposition: slice map and resolved findings (record)

The build order and blocking findings below are the accepted decomposition, carried forward unchanged.

**Build start:** S2 (#4) and S25 (#5) are the independent enabling gates. With one worker, use
**#4 → #5 → #6 → #7**; S3 (#7) is the first user-visible demo, not the first buildable issue. The first
deterministic functional chain after the gates is **#6 → #7 → #8 → #9 → #12**: config, registration,
persistence, queue control, isolation, and baseline execution without an AI provider.

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

**Resolved blocking findings** (incorporated into the accepted decomposition):

- **B1, committed configuration** (#6 S1, #7 S3): `.issueforge.toml` is loaded and validated from the verified
  committed Git object; dirty working-tree contents cannot influence registration or the origin-based worktree.
- **B2, scope-expansion disjointness** (#21 S15): every proposed expansion is checked against the frozen
  contract set before mutation; overlap fails and leaves the approved scope byte-identical.
- **B3, observability amendment routing** (#10 S8, #13 S9, #19 S13, #21 S15): an unanticipated boundary uses
  S9's distinct observability/buildability amendment with fresh reviewer confirmation and human approval; S13's
  acceptance-contract amendment cannot update observability.
- **B4, executable logging evidence** (#10 S8, #21 S15): required logging is proved during the authoritative
  run with captured success/failure events and seeded sensitive-field canaries; static inspection is
  supplementary.

---

## 8. Next action

Begin with **S2 / issue #4**, then **S25 / issue #5**, before S1 and S3. Follow each issue's declared route:
`route:direct-tdd` enters direct red-green-refactor; `route:spec-up` first produces the human-approved red
acceptance contract, then hands off to `spec-dev`; `deferred-v2` issues stay outside the v1 acceptance graph
unless the PRD is explicitly amended.

The full rationale, the 59/59 ownership matrix, the source audit, the dependency graph, and the repair history
remain in `docs/planning/decomposition.md`. The blocked attempts remain preserved under
`docs/planning/reviews/` as evidence of the gate working, and, in the one invalid filing pass, being bypassed
and then corrected.
