# IssueForge v1 decomposition, attempt 3 — FAILURE REPORT (no issues created)

**Status: BLOCKED.** The independent review gate rejected the decomposition on both of its two rounds. Per the
run protocol, **no GitHub issues were created and no resolution was invented.** This report records what the two
rounds confirmed as resolved, the three findings that remain, their resolution direction, and the checked-in
draft the next run should build on.

| | |
|---|---|
| **Date** | 2026-07-15 |
| **Repository** | `MatthewDruhl/IssueForge` (private) |
| **Source PRD** | [#1](https://github.com/MatthewDruhl/IssueForge/issues/1) — amended with D1–D6 |
| **PRD scale** | **59** acceptance criteria across 11 user stories |
| **Starting point** | `docs/planning/decomposition-draft-v3.md` (59/59 owned) |
| **This attempt applied** | D5, D6, and the three mechanical fixes from the v2 report §2 |
| **Round 1 (fresh session, gpt-5.6-sol)** | **REVISE** — 4 blocking findings; all 4 repaired |
| **Round 2 (fresh session, gpt-5.6-sol)** | **REVISE** — 3 blocking findings remain |
| **Child issues created** | **0** |
| **Issues modified** | **0** (PRD #1 untouched) |
| **MARVIN files/issues modified** | **0** |
| **Carry-forward draft** | `docs/planning/decomposition-draft-v4.md` (this attempt's repaired draft) |

---

## Executive summary

The gate held, and it was right to. This attempt moved the work forward measurably and then stopped exactly
where the protocol requires.

**What this attempt did.** It applied the two PRD amendments the prior attempt surfaced — **D5** (file roles are
two disjoint scopes: an approved implementation write scope and a frozen contract set) across S9/S12/S13/S15,
and **D6** (the source-audit inventory unit is a test, discovered against a versioned checked-in extraction
manifest) in S2 — plus the three mechanical fixes the v2 report specified (per-producer redaction canaries in
S7/S10/S11/S15; S8's obsolete heuristic language removed and the US-6.9 evidence defined; S25 made a hard
blocker of S3 and S4 with a CI-order assertion). The 59-criterion coverage matrix stayed complete and singly
owned throughout.

**Round 1** returned REVISE with four blocking findings. Each was verified against the PRD before being
accepted, and all four were repaired: an S10 red-evidence contradiction with US-10.3's "never retained"; an S25
write-seam claim that collided with `git worktree`'s unavoidable metadata writes; an S9 observability
justification that named a "reviewer-confirmed" step it never invoked; and an S1/S12/S13 adapter API that could
not produce the external dependency inventory S12 and S13 relied on.

**Round 2** — the second and final review — confirmed those four repairs as coherent in the slice bodies but
returned REVISE with **three** blocking findings. Two are pre-existing gaps that survived draft v3 and both
prior attempts; one was introduced by the round-1 repair itself. All three were verified against the PRD. Per
the protocol's two-round cap, this attempt created no issues, invented no resolution, and produced this report.

**Why the discipline matters here.** The temptation after a second REVISE is "just one more round." The protocol
forbids it precisely because that rationalization is how a decomposer talks itself into shipping a plan the gate
already rejected. The two prior attempts honored the cap; so does this one.

---

## 1. The three blocking findings that remain

Each was verified by quoting the PRD line, per the run's hard rule. None was accepted on the reviewer's word.

### F1 — S9 lets a human override the required observability confirmation *(introduced by the round-1 repair)*

**The defect.** Round 1 correctly found that S9 named a "reviewer-confirmed" observability justification without
invoking any reviewer. The round-1 repair added a fresh secondary-role confirmation before human approval — but
it also added, by analogy to US-5.4, that "a reviewer failure here is recorded and may be explicitly overridden
by a fresh same-provider session or a human." **That override defeats the very requirement the repair was meant
to satisfy.**

**The PRD.** US-6.7 (`prd-v1.md:82`): *"Every shaped issue records `required`, `existing coverage sufficient`,
or `not applicable` for observability, with reviewer-confirmed justification."* Unlike US-5.4 (test-contract
review) and US-6.5 (implementation review), **US-6.7 grants no override.** "Every shaped issue" is unconditional.
Importing US-5.4's override into US-6.7 creates a path to ship a shaped issue whose observability justification
was never confirmed.

**Verdict: real, and self-inflicted.** The gate caught an over-reach in the round-1 repair. The fix must not
weaken US-6.7's "every."

### F2 — No slice owns the hermetic, separately-provisioned verification environment *(pre-existing in draft v3)*

**The defect.** The plan repeatedly depends on an authoritative, hermetic verification environment — S7 assumes
dependencies are "already provisioned by the engine," S13 re-resolves external identities "in the authoritative
verification environment," S6 claims gap G14 — but **no slice or adapter operation creates or owns that
environment as a testable deliverable.**

**The PRD.** The adapter decision (`prd-v1.md:157`) names it as part of the portable seam: *"the verification
adapter interface — **prepare a hermetic environment**, enumerate approved tests, run a selection, report
structured execution and failure phases, normalize behavioral evidence, and detect zero/skipped/deselected
tests."* And `:158`: the residual test-context risk is carried *"by hermetic, separately provisioned
verification runs."* And US-6.1 (`:76`): identities are *"re-resolved and compared in the authoritative
verification environment."* S1's adapter Protocol defines only five functions — `probe`, `canonical_collect`,
`classify`, `discover_contract_dependencies`, `validate_invocation` — **none of which prepares or owns the
environment.** S13's re-resolution therefore has no defined place to run.

**Verdict: real, and load-bearing.** Environment preparation is explicitly in the PRD's adapter seam and is a
precondition for the integrity and residual-risk guarantees. It cannot be an unowned assumption.

### F3 — S5 contains contradictory cancellation criteria *(pre-existing in draft v3)*

**The defect.** S5 says reorder and cancel are "available only before a run starts," then immediately lists
cancel as an exit from the paused (already-started) state. **Both cannot hold literally.**

**The PRD.** US-2.2 (`prd-v1.md:32`): *"Additional issues enter a persistent FIFO queue and may be **reordered
or cancelled before starting**."* US-2.3 (`:33`): *"A **paused** run blocks the worker until explicitly resumed,
**cancelled**, or parked."* The PRD distinguishes two cancellations: a queued (not-started) run, and a paused
(started) run. S5 collapsed them into "only before a run starts," which contradicts US-2.3.

**Verdict: real.** A textual contradiction in the acceptance criteria of a foundational slice.

---

## 2. What both review rounds confirmed as resolved — do not re-litigate

Recorded so the next attempt does not redo settled work. The gate explicitly accepted all of the following.

- **D5 is consistently applied** across S9/S12/S13/S15 — two disjoint scopes (implementation write scope vs
  frozen contract set), acceptance tests never in the write scope, the two clean readiness questions, and a
  path proposed as both failing the freeze — **including US-6.1's re-resolution of external identity/version.**
- **D6 is correctly test-granular** in S2 — inventory unit is a test, discovery is scoped to a versioned,
  checked-in extraction manifest, and a human approves both manifest membership and each stage audit.
- **The three mechanical fixes landed** as testable acceptance criteria: per-producer redaction canaries
  (S7/S10/S11/S15) exercising success and failure paths; S8's two separately-testable analyses with the US-6.9
  evidence named; S25 hard-blocking S3 and S4 with a CI-order assertion.
- **Round-1 B1, B2, B4 are coherent** in the slice bodies: S10's re-derivable redacted red evidence; S25's IO
  seam scoped to IssueForge's own writes with git as a separate boundary; and the external dependency closure
  unified under `discover_contract_dependencies`.
- **The 59-criterion matrix is complete and singly owned**, the dependency graph is acyclic and coherent, and
  **no `deferred-v2` work or MARVIN write-back appears in the v1 acceptance graph.**

---

## 3. Resolution direction for the next run — specified, NOT applied

The protocol forbids inventing a resolution and proceeding to create issues. These are the reviewer's and this
report's recommended directions for the next attempt; they are **not** applied here.

- **F1 (S9).** Remove the US-5.4-style override from the observability confirmation. A replacement fresh
  secondary-role reviewer may confirm the category and justification; a human may serve as the reviewer **only
  by explicitly reviewing and recording a confirmation**, never by waiving confirmation. US-6.7's "every shaped
  issue … reviewer-confirmed" admits no unconfirmed exit.
- **F2 (environment).** Add environment preparation to the S1 adapter contract as a sixth operation (for
  example `provision_environment(worktree) -> authoritative env handle`), implement the pytest environment
  lifecycle in S6 with a testable provisioning criterion for G14, and require S10/S13/S14/S15 to run their
  authoritative verification and dependency re-resolution in that provisioned environment. Add a test proving a
  candidate or implementer cannot mutate the authoritative environment to control results.
- **F3 (S5).** Split the two operations explicitly: reordering applies to queued, not-started runs only;
  cancellation applies to **both** a queued run and the current paused run, each with its own transition test
  and worker-slot-release assertion.

All three are contained, local edits to `decomposition-draft-v4.md`. None reopens D1–D6 or disturbs the
coverage matrix.

---

## 4. The carry-forward draft

`docs/planning/decomposition-draft-v4.md` is this attempt's repaired draft. It carries D5, D6, the three
mechanical fixes, and the four confirmed round-1 repairs — everything the gate accepted — so the next run does
not re-derive them. The next run should reuse it as its starting point, apply the three F-fixes above, then
re-run the two-round gate.

---

## 5. Verification

- **No GitHub issues were created, edited, labeled, commented on, or closed** in `MatthewDruhl/IssueForge`.
  **PRD #1 is unmodified.** The only open issue is #1, unchanged.
- The `epic` / `v1` / `deferred-v2` / `phase:0`–`phase:5` / `route:*` labels created by an earlier attempt still
  exist and remain **unused**.
- **No MARVIN file, state, skill, ledger, configuration, generated artifact, or GitHub issue was modified.**
  MARVIN was read-only provenance throughout.
- No IssueForge source code or tests were changed. No implementation branches or PRs were created.
- **Both review rounds ran under the guarded-launch contract:** stdin closed (`< /dev/null`), stderr captured to
  a file (never `2>/dev/null`), a wall-clock timeout via `perl -e 'alarm N; exec @ARGV'` (`timeout(1)` does not
  exist on macOS), full output persisted, and empty-output-or-non-zero treated as FAILED. Each round was a
  fresh, independent `codex exec` session (gpt-5.6-sol, high reasoning effort), read-only sandbox, with every
  input materialized to local disk first. Both rounds returned a schema-valid structured verdict with exit 0
  and non-empty output.

**PDF verification.** `issueforge-v1-decomposition-report-v3.pdf` was rendered from this markdown via
HTML plus Chrome headless (`--headless --disable-gpu --no-pdf-header-footer --print-to-pdf`). Every page was
inspected: exactly one `<h1>`, zero blank pages, no paragraph beginning `#` immediately followed by a digit
(the lazy-`<h1>` hazard), and a stated page count matching the actual one. **Result: 5 pages, 0 blank pages,
1 `<h1>` — verification PASS.**

---

## 6. The exact next command

Once the three findings in §1 are resolved in `decomposition-draft-v4.md`:

    /prd-to-issues for MatthewDruhl/IssueForge#1

Re-run against the amended PRD, **reusing `docs/planning/decomposition-draft-v4.md`** — it is three contained
edits away from a clean gate, D1–D6 are settled, and 59/59 criteria are already owned with a single owner each.
Do not start from zero, and do not re-derive the source audit or the D5/D6 application: this attempt paid for
them and the gate confirmed them.

---

*Attempt 3 of the IssueForge v1 decomposition. BLOCKED after two review rounds. 0 issues created. Preserved as
failure evidence alongside `issueforge-v1-decomposition-report.md` (attempt 1) and
`issueforge-v1-decomposition-report-v2.md` (attempt 2).*
