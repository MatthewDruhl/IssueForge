# IssueForge v1 — PRD #1 decomposition (draft v5, corrected gate candidate)

**Repo:** MatthewDruhl/IssueForge · **Source PRD:** #1 · **Date:** 2026-07-15
**Supersedes:** `docs/planning/reviews/decomposition-attempt-03-draft.md` (attempt 3, BLOCKED after two review rounds).

This draft carries everything the attempt-3 gate **confirmed as resolved** — D5, D6, the three mechanical
fixes, and the four round-1 repairs — and additionally resolves the **three blocking findings** that survived
attempt 3's second review round (documented in `reviews/decomposition-attempt-03-blocked.md` §1):

- **F1 — S9 observability confirmation is non-overridable.** US-6.7 (`prd.md:82`) grants no override, unlike
  US-5.4/US-6.5. The US-5.4-style override the round-1 repair had added is removed; a human may serve as the
  reviewer only by explicitly recording a confirmation, never by waiving it.
- **F2 — a sixth adapter operation, `provision_environment`, owns the hermetic verification environment.**
  `prd.md:157` makes "prepare a hermetic environment" part of the adapter seam; **S1** now declares it,
  **S6** implements and owns it with a testable G14 criterion, and **S10/S13/S14/S15** run their authoritative
  verification and dependency re-resolution inside it.
- **F3 — S5 splits reorder from cancel.** Reorder is queued-only (US-2.2); cancel applies to **both** a queued
  run and the current paused run (US-2.3), each with its own transition test and worker-slot-release assertion.

59/59 criteria remain owned once each. D1–D6 are settled and not reopened.

`docs/prd.md` is the canonical current PRD and includes the approved **D1–D6** amendments. Epic #1 retains
the original PRD text plus an appended decomposition section; it is intentionally not rewritten or claimed to
be byte-identical to the amended file. Every child contract cites the canonical file's requirement lines. D5
and D6 were the two blocking decisions the second decomposition attempt surfaced
(`reviews/decomposition-attempt-02-blocked.md`); they are settled and are **not reopened here**.

**59 acceptance criteria / 11 user stories. 25 v1 child issues + 2 `deferred-v2`.**

**This draft applies, on top of draft v3:**
- **D5** (`prd.md:170`) — file roles are **two disjoint scopes**: an approved **implementation write scope**
  (the paths the implementer may modify, governing the implementation commit range only) and a **frozen contract
  set** (the acceptance-test files plus their adapter-discovered dependency closure). The acceptance tests are
  delivered in the contract commit and are **never** inside the implementation write scope. Applied to **S9,
  S12, S13, S15** (and US-6.1's external identity/version re-resolution).
- **D6** (`prd.md:171`) — the source-audit inventory unit is a **test**, discovered against a **versioned,
  checked-in extraction manifest** that declares which MARVIN artifacts are build-harness (as distinct from the
  chief-of-staff workspace). A human approves each stage audit. Applied to **S2**.
- The **three mechanical fixes** the v2 report specified (§2 there): per-producer redaction canaries
  (**S7/S10/S11/S15**); delete S8's contradictory heuristic/hint language and define the US-6.9 evidence
  (**S8/S15**); make **S25 a hard blocker of S3 and S4** with a CI-order assertion.

The pre-amendment draft counted **51** criteria; the D1–D6 amendment reached **59**. A decomposition reusing an
old 51-row matrix silently drops eight; §1 lists them.

---

## Governing rules for this draft

1. **The PRD is the authority.** It has been amended; D1–D4 are settled and are not reopened here.
2. **A reviewer's claim about the PRD is not evidence about the PRD.** Review 02's finding #3 asserted the
   PRD granted no implementation-review override; `prd.md:153` granted one, and the prior run deleted a
   correct criterion on that false claim. Every claim in this draft that cites the PRD was re-read against
   the PRD. Every claim that cites MARVIN was re-read against MARVIN's source.
3. **A false "nothing to port" is as damaging as a false "port this."** The prior draft's source audits were
   verified against the real files for this draft. Five of its "net-new" claims were wrong (§3).
4. **Every criterion has exactly one owning issue.** Downstream repeats are labelled **integration
   assertions**, never second implementations.

---

## 1. The eight criteria the old matrix would have dropped

| New ID | PRD line | Requirement |
|---|---|---|
| **US-1.5** | 24 | Registration **resolves a verification adapter** and **refuses a repo whose framework has no installed adapter**, naming it. |
| **US-3.5** | 45 | Shaping emits a **buildability contract** before any acceptance test is authored, carrying a proposed **implementation write scope**. The acceptance-contract files are **not** in this scope; they are protected by the US-5 freeze (D5). |
| **US-3.6** | 46 | **A human approves the buildability contract — including the implementation write scope — before contract authoring.** That scope governs the **implementation commit range only** and is the one enforced at readiness, **never derived from the diff** (a diff-derived scope approves itself). |
| **US-3.7** | 47 | **Expanding the approved implementation write scope requires new human authorization** (prior approval preserved in the audit trail); an out-of-scope write pauses the run. |
| **US-5.6** | 69 | The frozen dependency set is **discovered by the adapter, not declared by configuration**, transitively — **including the immutable identity and pinned version of every external plugin/package**, not only in-repo files. A user path list may **add** to the boundary, never **shrink** it. |
| **US-6.3** | 78 | Both repair counters are **persisted run state incremented inside the store lock**; the engine gates the transition on them. |
| **US-6.5** | 80 | The implementation-review **human override**: human-only, per-finding, sha-bound, after one fresh replacement review; never waives deterministic evidence; reported in the PR. |
| **US-9.5** | 115 | With **no secondary provider configured**, review runs on the **primary provider in a brand-new session**. A review whose session identity equals the authoring session's is **rejected**. |

**Materially amended (mapped, but the old issue no longer covers the text):** US-5.5 (freeze now carries the
approved **implementation write scope**, disjoint from the frozen contract set — D5), US-6.1 (integrity now
**re-resolves and compares the identity/version of every frozen external plugin/package** in the authoritative
verification environment, not only file hashes), US-6.2 (one budget → **two**), US-6.4 (readiness asks the two
D5 questions — every change inside the approved write scope, no frozen contract path or pinned external identity
changed — plus the override carve-out), US-7.2 (PR reports overrides), US-9.4 (roles not vendors; no provider
name hardcoded), US-11.1/US-11.2 (inventory unit is a **test** discovered against a **checked-in extraction
manifest**; human approves each stage audit — D6).

---

## 2. Defects carried forward from review 02 that the amendment did NOT fix

Three of review 02's seven blocking findings were PRD conflicts (now amended away). **Four were engineering
defects and remain live**, and one half-finding survives that the failure report did not carry forward.

**A. Closeout does not close the *exact run issue*** (US-8.2). Draft v2 closed only formal
`closingIssuesReferences`. A PR with **no** closing reference leaves the run issue **open**; a PR with
**multiple** can close issues **other than** the run issue. → **S18** keys closeout on the **persisted,
repository-qualified run-issue identity**; closing references are verified and reported, never substituted.

**B. The source-audit lint cannot establish completeness** (US-11.1–11.4) — **settled by D6.** It checked
against `architecture.md`'s *"**Initial** source map"* and the ledger's *"including **at minimum**"* list —
**both expressly non-exhaustive**. → **S2** now inventories at the granularity of a **test**, discovered against
a **versioned, checked-in extraction manifest** (harness vs workspace, human-curated); its failure modes
include a **manifested artifact absent from the record** and a **candidate harness artifact discovered under a
declared root but absent from the manifest** (flagged for human classification). This draft is itself the proof
the defect is real: the verification pass found five canonical MARVIN artifacts the prior draft's audit had
declared not to exist (§3).

**C. "The preexisting baseline stays green in the same run" is impossible as written.** Running the repo's
ordinary baseline command at the test commit **includes the new, intentionally-failing acceptance tests** and
is therefore red by construction. Reusing the pre-authoring baseline result cannot detect the conftest/config
breakage the check exists to catch. → **S10** defines the adapter operation: execute
`preexisting_ids_at_base − newly_authored_acceptance_ids` at the contract candidate. **IDs that vanished
between base and candidate are a contract-integrity signal, not a green baseline.**

**D. The frozen dependency boundary is incomplete — the `helpers.py` bypass.** `conftest.py` imports a
fixture from `tests/helpers.py`; the implementer edits **only `helpers.py`**. Conftest hash, test hashes,
command, and collected IDs are all unchanged, and the helper is in *conftest's* import closure, not the *test
modules'*. → **S12**, via `discover_contract_dependencies` (US-5.6).

**E (the half-finding the failure report dropped): draft v2 pushed before the readiness gate.** Its #13 ran
`diff → commit → push → verify-at-origin` *inside* the implementation stage. **US-7.1 (`prd.md:91`):
"IssueForge pushes and opens a PR automatically only after all readiness gates pass."** Review 02 called this
a direct contradiction and it is. → **S14 never pushes.** Code review and the readiness gate run against a
**local immutable candidate sha**. **S16** is the only slice that pushes, and only after S15 passes.

---

## 3. Source-audit corrections — claims in draft v2 that are FALSE

Each was verified against the real file. Shipping any of these into a GitHub issue would have handed the
review gate a free kill.

| Draft v2 claim | Verdict |
|---|---|
| `_parse_pytest_summary` **"cannot see pytest exit 5"** | **FALSE.** `merged_runner.py:677` is `if failed > 0 or result.returncode != 0` — exit 5 is non-zero, so it **is** caught and it **does** halt. It fails **closed**. The real defect is a **mislabel**: exit 5 / 2 / 3 / 4 all report as `red-main` with `passed: 0, failed: 0`. |
| (not claimed at all) | **NEW — and worse: a real FALSE GREEN.** A fully-skipped suite exits **0** with summary `"12 skipped"` → `passed=0, failed=0, returncode=0` → **no anomaly** → main is treated as **green**, and `_process_pr` proceeds to delete branches, remove worktrees, and close issues (`:841-862`). A suite whose module-level `pytest.skip` fired verifies **nothing** and reports green. **Exit 0 is not green.** |
| `check_build_pr_base.py` is **~100 lines** | **FALSE.** 68 lines. And `default_branch` **has a default of `"main"`** — the port must make it required. |
| The golden-value arrow proxy is in **`validate_accept_body.py`** | **FALSE.** That file checks **arrow presence only** (`:116`); `... → ...` and `TBD -> TBD` pass it. The both-sides non-placeholder proxy is in **`validate_spec_up_issue.py`** (`_has_real_token` :66-74, `_line_has_golden` :77-85). Port **that** one. |
| `check_acceptance_integrity.py` **"excludes conftest.py from its guard"** | **Mis-framed.** `:79-82` is a **value-resolution scope limit** (only module-level defs *in the file being diffed* are resolved; a conftest fixture is an example of an opaque outside value), not a file-scope exclusion. State it correctly or a reviewer will. |
| **"five inversions"** in `merged_runner.py` | **Undercounted.** There are **six-plus**: `_pr_view` :163-181, `_reachability` :291-311, `_branches_containing` :313-333, `cleanup_worktree` :456-469, `_remote_branch_present` :735-761, `_worktree_for_branch` :763-781, **plus** `_retarget_stacked_prs` :400-409 and the sync gate :665-671. |
| `:828-836` is a **"blanket halt-on-red-main"** | **Imprecise.** It halts on **any** gate anomaly and it is **per-PR**, not global. |
| **"MARVIN has no workflow/state, no queue, no retention, no epic, no decomposition — all net-new, nothing to port"** | **FIVE OF SEVEN ARE FALSE.** See below. Only **TUI** is cleanly net-new. |

**The five false "nothing to port" claims:**

- **Retention.** `scripts/prune_plan_files.py` **is** a 30-day retention sweep, with `DEFAULT_MAX_AGE_DAYS = 30`
  and an **injectable `now`** so the age logic is testable without the wall clock. → **direct port target for S22.**
- **Guarded status transition.** `validate_agent_runs.py:26,32` (`VALID_STATUSES`, `TERMINAL_STATUSES`) plus
  `agent_runs_lib.close_run_for_pr:423-453`, which enforces **exactly one legal transition**
  (`needs-review → merged`) and **never** promotes `running` straight to merged. → prior art for **S4/S5**.
- **Footprint extraction.** `issues_to_findings.py` parses `## Files affected`, requires exactly one `route:*`
  label, and **forbids an empty footprint**. → prior art for **S9**.
- **Conflict scheduling.** `schedule_waves.py` is a real, tested, deterministic conflict-detecting scheduler.
  v1 is single-run (concurrency is Out of Scope), so it is **not needed** — but "nothing to port" was wrong.
- **Decomposition + epic routing.** `skills/prd-to-issues/SKILL.md` **is** the decomposition procedure;
  `skills/spec-up/SKILL.md:34,38,81` routes epics explicitly. → prior art for **S20/S21**.

**And one the draft assumed exists but does not: there is NO epic prior art in the closeout chain.**
`grep -i "epic\|parent"` over `merged_runner.py` and `skills/merged/SKILL.md` returns **zero** matches.
US-8.2's parent-epic update is **new engine policy**, and S18's audit must say so.

**Two more real MARVIN defects found (extract with the fix, never as-is):**

- **`_Closeout.close_issues` (:509-515) never checks `res.returncode`.** A transient `gh` failure yields
  `info = {}` → `refs = []` → `action: "clean"`, exit 0, **after** the branch and worktree were already
  deleted (:841-858). A failed read silently becomes *"there were no issues to close."*
- **Cross-repo issue identity is lost.** `close_issues` reads only `ref["number"]` (:523-525) and closes with
  `--repo project["repo"]` (:534-546). `closingIssuesReferences` can name an issue in a **different** repo;
  this closes the same *number* in the PR's own repo. Carry `(owner, repo, number)` end to end.

**MARVIN closes issues LAST, after every destructive step.** IssueForge deliberately **inverts** this
(S17 → S18 → S19): closing an issue is reversible; deleting a branch is not. The audit records it as a
deliberate reordering, not an extraction.

---

## 4. The six cross-cutting rules (incorporated by reference into every issue's Preserve)

1. **A failed read is NEVER negative evidence.**
2. **Honor every return code.**
3. **Verify at the boundary; do not trust the report.** The authoritative test run is the **engine's**.
4. **Content, not ancestry** (squash merges) — via a tri-state predicate whose only trustworthy negative is exit 1.
5. **The contract is enforced by the harness or CI, NEVER by the session being policed.**
6. **Every gate needs a legitimate escape hatch, or people route around it.**

---

## 5. PRD gaps — prose requirements with no acceptance criterion, each given a home

| Gap | Home |
|---|---|
| G1 argv-array/no-shell beyond the baseline command | **S1** |
| G2 subprocess timeouts (`timeout(1)` **does not exist on macOS** — never shell out for it) | **S1** |
| G3 libraries never install global logging configuration | **S8** |
| G5 store locking / atomicity beyond the two counters | **S4** |
| G6 crash recovery / event replay | **S4** |
| G7 `issueforge continue` semantics outside `waiting-for-merge` | **S5** |
| G8 optional acceptance/lint/build commands | **S1** |
| G9 named CLI verbs for queue reorder/cancel | **S5** |
| G10 PR body reports logging added / reused / intentionally unnecessary | **S16** |
| G11 a **not-testable** exit (refactor/docs/research issues that cannot carry a TDD contract) | **S9** — US-3.5 fixes the classification to exactly `buildable`/`oversized`/`blocked`, so this lands as **`blocked` with `blocked_reason: not_testable`**. PRD-conformant; no amendment needed. |
| G12 the six adapter function names are **nowhere in the PRD**, not even in prose | **S1** — they name the capability list at `prd.md:157` (which includes *"prepare a hermetic environment"* → `provision_environment`). Recorded as an **implementation-level naming decision**, not a PRD quotation. |
| G13 zero-collected / skipped / deselected detection | **S6** |
| G14 hermetic, separately-provisioned verification runs | **S6** |
| G15 the code review must be **instructed to look for test-context-dependent behavior** (`prd.md:158`) | **S15** |
| G16 one branch: contract commit **then** implementation commit (`prd.md:161`) | **S12** (commit) / **S14** (ordering) |
| G4 author/reviewer session separation | **CLOSED by the amendment** — now US-9.5. |

---

## 6. The 25 v1 issues

| S | Title | Phase | Criteria owned | Blocked by |
|---|---|---|---|---|
| S1 | Process seam, tri-state results, `.issueforge.toml`, verification-adapter interface + `probe` | 0 | US-4.1, G1, G2, G8, G12 | **S25** |
| S2 | Source-audit inventory + completeness lint | 0 | US-11.1–11.4 | — |
| S25 | IO write seam, path resolver, boundary AST lint (permanent CI gate) | 0 | *(enabling gate)* | — |
| S3 | Register a repository; resolve or refuse its adapter | 0 | US-1.1–1.5 | S1, **S25** |
| S4 | Run store + enqueue + stub stage (one locked write path, redacting) | 0 | US-2.1, US-10.1, US-10.3, G5, G6 | S1, S3, **S25** |
| S5 | Queue control: FIFO, pause, park, cancel, resume, `continue` | 0 | US-2.2–2.4, US-9.3, G7, G9 | S4 |
| S6 | Isolated worktree, green baseline, and `classify` — red vs **broken** | 1 | US-4.2–4.4, G13, G14 | S3, S4 |
| S7 | AI provider layer: roles, profiles, guarded launch, session identity | 1 | US-6.6, US-9.4, US-9.5 | S1, S4 |
| S8 | Observability policy: boundary classifier + sensitive-field exclusion | 1 | US-6.8, US-6.9, G3 | S1 |
| S9 | **Buildability contract** + human approval of the implementation write scope | 2 | US-3.4, US-3.5, US-3.6, US-6.7, G11 | S5, S7, S8 |
| S10 | Author acceptance tests + deterministic red proof | 3 | US-5.1 | S6, S7, S9, S20 |
| S11 | Independent review of the red contract: semantic validity + recorded override | 3 | US-5.2, US-5.3, US-5.4 | S10 |
| S12 | Human approval freezes the manifest; adapter-discovered dependency closure | 3 | US-5.5, US-5.6, G16 | S10, S11 |
| S13 | Contract integrity enforcement + `validate_invocation` + amendment path | 3 | US-6.1 | S12 |
| S14 | Implement under the frozen contract; two engine-owned repair budgets | 4 | US-6.2, US-6.3 | S13 |
| S15 | Readiness gate: implementation write scope, code review, human override | 4 | US-3.7, US-6.4, US-6.5, G15 | S14, S8 |
| S16 | One green PR — pushed only after the gate, verified at origin, never merged | 4 | US-7.1–7.4, G10 | S15 |
| S17 | Delivery verification: exact merge-commit + head-sha binding | 4 | US-8.1 | S16 |
| S18 | Closeout: comment, close the **exact run issue**, update the parent epic; idempotent | 4 | US-8.2, US-8.4 | S17, S20 |
| S19 | Safe cleanup: branches + worktrees (an independent stage result) | 4 | US-8.3 | S17, S18 |
| S20 | Shape an issue: in-place revision + approved GitHub mutation plan | 2 | US-3.1 | S9, S4 |
| S21 | Epic decomposition of an oversized issue | 5 | US-3.2, US-3.3 | S20 *(mutation machinery only)* |
| S22 | Retention and `issueforge purge` | 5 | US-10.2, US-10.4 | S4 |
| S23 | TUI + CLI/TUI parity — all eight views | 5 | US-9.1, US-9.2 | S5, S16, S19 |
| S24 | Self-contained boundary: a permanent CI invariant | 5 | US-11.5–11.7 | S25 + ALL |

Plus **`deferred-v2`**: **D1** blocking mutation / anti-tautology gate · **D2** the invariant lens for shaping.

**Recommended build start:** S2 and S25 are the two independent enabling gates; with one worker, build **S2
then S25**, followed by S1 and only then S3. S3 (`repo add` → `repo list`) is the first user-visible demo, not
the first buildable issue.
**First deterministic functional chain after the S2/S25 gates: S1 → S3 → S4 → S5 → S6** —
`repo add` → enqueue → fetch → isolated worktree → run baseline → pause. Fully deterministic tests, zero
provider dependency, and it de-risks every seam the rest of the system sits on. Per review 02's fair caveat,
this is **infrastructure** validation; the first complete *product-lifecycle* tracer needs **S9**.

## 7. PRD coverage matrix — 59/59, single owner each

| Criterion | Owner | Criterion | Owner | Criterion | Owner |
|---|---|---|---|---|---|
| US-1.1 | S3 | US-5.1 | S10 | US-8.1 | S17 |
| US-1.2 | S3 | US-5.2 | **S11** | US-8.2 | **S18** |
| US-1.3 | S3 | US-5.3 | S11 | US-8.3 | S19 |
| US-1.4 | S3 | US-5.4 | S11 | US-8.4 | S18 |
| **US-1.5** | **S3** | US-5.5 | S12 | US-9.1 | S23 |
| US-2.1 | S4 | **US-5.6** | **S12** | US-9.2 | S23 |
| US-2.2 | S5 | US-6.1 | S13 | US-9.3 | S5 |
| US-2.3 | S5 | US-6.2 | S14 | US-9.4 | S7 |
| US-2.4 | S5 | **US-6.3** | **S14** | **US-9.5** | **S7** |
| US-3.1 | S20 | US-6.4 | S15 | US-10.1 | S4 |
| US-3.2 | S21 | **US-6.5** | **S15** | US-10.2 | S22 |
| US-3.3 | S21 | US-6.6 | S7 | US-10.3 | S4 |
| US-3.4 | S9 | US-6.7 | S9 | US-10.4 | S22 |
| **US-3.5** | **S9** | US-6.8 | S8 | US-11.1 | S2 |
| **US-3.6** | **S9** | US-6.9 | S8 | US-11.2 | S2 |
| **US-3.7** | **S15** | US-7.1 | S16 | US-11.3 | S2 |
| US-4.1 | S1 | US-7.2 | S16 | US-11.4 | S2 |
| US-4.2 | S6 | US-7.3 | S16 | US-11.5 | S24 |
| US-4.3 | S6 | US-7.4 | S16 | US-11.6 | S24 |
| US-4.4 | S6 | | | US-11.7 | S24 |

**59/59 owned. Zero silently weakened. Zero silently deferred.**

**Integration assertions** (a criterion re-asserted downstream, never re-implemented): US-4.1 in S3
(registration refuses a repo with no baseline command); US-6.4's approved-file-scope in S16 (the PR opens only
after S15 passed); US-6.7/6.8/6.9 in S15 (the readiness gate *invokes* S8's predicates and refuses a shaped
issue with no observability verdict — S8 owns the predicates, S9 owns the verdict, S15 enforces); US-9.1 is an
architectural invariant on every slice from S4 onward (commands route through the engine API; every stage
emits structured events), and S23 builds only the *rendering*; US-10.1 in S22 (`purge` never removes a
permanent manifest).

**On US-6.8/6.9 ownership.** D3's review assigned "enforcement" of the boundary-logging criteria to
implementation and review. This draft gives S8 the **deterministic predicates** (the boundary classifier and
the sensitive-field exclusion check) because a predicate needs one owner and one test suite, and gives S15 the
**enforcement call site** as an integration assertion. The AI reviewer still judges diagnosability everywhere
a deterministic rule does not fire (`architecture.md:91`). This is a decomposition choice, stated rather than
smuggled.

**On US-5.2 ownership** (*v5-round-2 fix B2*). US-5.2 — *"the new tests fail for a recorded expected behavioral
reason while the preexisting baseline remains green"* — spans a deterministic half (baseline-still-green,
call-phase red evidence) and a semantic half (the red matches the **named** expected reason). This draft assigns
the **single owner to S11**, the gate that can assert both: it consumes S10's deterministic evidence and adds
the semantic correspondence judgment. **S10 contributes the deterministic half as an enabling integration**, not
as a second owner — the same pattern as US-6.8/6.9. S10 cannot own US-5.2 alone because it explicitly cannot
prove semantic correspondence.

## 8. Dependency graph

```
S2  (source-audit lint) ─── gates every implementation issue, blocks none
S25 (IO write seam + paths + boundary AST lint) ─── gates every issue that writes to disk (S1 onward)

S1 (process + config + adapter interface + probe)     [needs S25 — S1 creates per-invocation artifact dirs via the seam]
├── S3 (registry; adapter resolve-or-refuse)         [needs S1, S25 — S3 persists the registry to disk]
├── S8 (observability policy: prospective + diff reconciliation)
└── S4 (run store + enqueue + stub stage)          [needs S1, S3, S25]
    ├── S22 (retention)
    ├── S5 (queue control)
    ├── S7 (providers)
    └── S6 (worktree + baseline + classify)         [needs S3, S4]

S5 + S7 + S8 ── S9 (BUILDABILITY CONTRACT — human approves the implementation write scope)
                 │
                 ├── buildable ─→ S20 (in-place revision; BUILDS the gateway write side)  [needs S4]
                 │                 └── S10 (author + DETERMINISTIC red proof)  [needs S6, S7, S9, S20]
                 │                      └── S11 (independent SEMANTIC review; own round budget)
                 │                           └── S12 (approval freezes manifest + dep closure)
                 │                                └── S13 (integrity + validate_invocation + amendment)
                 │                                     └── S14 (implement + 2 budgets; NEVER pushes)
                 │                                          └── S15 (readiness + scope + review + override)
                 │                                               └── S16 (push, then ONE green PR)
                 │                                                    └── S17 (DELIVERY VERIFICATION)
                 │                                                         └── S18 (closeout)  [needs S20]
                 │                                                              └── S19 (safe cleanup)
                 ├── oversized ─→ S21 (epic decomposition)  [needs S20's mutation machinery]
                 │                 └── children enter the queue; THE PARENT RUN STOPS
                 └── blocked ────→ PAUSE  (incl. not_testable)

S5 + S16 + S19 ── S23 (TUI, all 8 views)
S25 + ALL ── S24 (boundary invariant + exhaustive redaction canary — then permanent in CI)
```

**Runtime order ≠ build order.** S3/S6 are built before S9 but run *after* it. The final transition table and
S24's lifecycle test must make the runtime order **impossible to bypass** regardless of the order things were
built in.

---

# Issue bodies

The six cross-cutting rules (§4) are incorporated by reference into every **Preserve**; each issue lists only
its *additional* preserves.

---

## S1 — Process seam, tri-state results, `.issueforge.toml`, and the verification-adapter interface
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem.** Every module shells out. MARVIN re-derived "a failed read is not a negative answer" by hand at
six-plus call sites, each comment marking a shipped bug — and at the one place it forgot (`_parse_pytest_summary`),
it shipped a false green. And the PRD's portable seam is **the verification adapter interface, not raw process
output** (`prd.md:157`): an exit code cannot distinguish a behavioral failure from a compile error, a
collection error, zero tests collected, a skipped suite, or a timeout.

**User-visible outcome.** `issueforge config check <path>` loads and validates a repository's
`.issueforge.toml`, prints the resolved argv arrays and the selected verification adapter with its probed
capabilities and pinned reporter version — or fails loudly, naming the offending field.

**PRD criteria covered.** **US-4.1** (owner). Gaps G1, G2, G8, G12.

**Observable acceptance criteria**
- `CommandResult` frozen dataclass: `argv, returncode, stdout, stderr, duration_ms, timed_out`.
  `run(argv, *, cwd, timeout, env)` **never raises on a non-zero exit** — callers inspect the result.
- **Timeout is a state DISTINCT from failure** (a typed `timed_out` flag), not a non-zero returncode.
  Implemented with **`subprocess.run(..., timeout=N)` in-process**. **`timeout(1)` does not exist on macOS**
  (verified: `which timeout gtimeout` → not found) — the engine must never shell out for it. On expiry the
  **process group** is killed (`start_new_session=True` + `os.killpg`), or the CLI's children survive.
- **`shell=False` always. Commands are argument arrays.** A shell string where argv is required is **rejected
  at load time**, naming the field. No pipeline can exist, so the whole `tee`/`pipefail` exit-masking class is
  unreachable by construction.
- **An error result cannot coerce to `False`.** A predicate handed an error **raises**. Tested against every
  MARVIN inversion.
- `.issueforge.toml` is loaded and validated from the **verified committed Git object**, never from an
  untracked or dirty working-tree copy. An untracked-only file is rejected, and a dirty working-tree edit cannot
  change the configuration used by the origin-based worktree. `baseline` is **required** as an argv array
  (US-4.1); `acceptance`/`lint`/`build` are optional (G8), alongside `contract_paths` and `sensitive_fields`.
  Every command is argv-validated, not just the baseline (G1).
- **`VerificationAdapter` Protocol — six functions, mandatory, keyed on (framework, reporter):**
  `probe(toolchain)` → capabilities + **pinned reporter version**;
  `provision_environment(worktree, frozen_deps=None)` → **a hermetic, separately-provisioned authoritative
  environment handle** (US decision, `prd.md:157`: the adapter must *"prepare a hermetic environment"*);
  `canonical_collect(invocation)` → canonical IDs + selection metadata; `classify(native events)` →
  **phase-aware** outcomes; `discover_contract_dependencies(collection)` → the protected closure — **in-repo
  file paths AND the immutable identity + pinned version of every external plugin/package in the import
  closure** (US-5.6), resolved against the installed distributions in the authoritative environment;
  `validate_invocation(command/config)` → a frozen, safe execution plan. *(Round-1 fix: the external
  identity/version inventory is `discover_contract_dependencies`'s output — `probe` supplies only the reporter
  version, which cannot enumerate the closure. S12 freezes this operation's output; S13 re-runs the SAME
  operation and compares.)* *(Attempt-3 fix F2: `provision_environment` is the seam's environment-preparation
  operation the PRD names at `:157` and that US-6.1 / `:158` rely on for "authoritative" and "hermetic,
  separately provisioned" runs — previously assumed, now owned.)*
  **This issue ships the Protocol, the `Outcome` enum, and pytest's `probe` only.** The other five land in the
  slices that first need them (**`provision_environment`, `canonical_collect`, `classify` in S6**;
  `discover_contract_dependencies` in S12; `validate_invocation` in S13) — a "build the whole adapter" issue
  would be a horizontal layer.
- **The registry keys on (framework, reporter), NOT language.** `pytest` and `unittest` are both Python and
  differ. A repo running `unittest` is **not** covered by the pytest adapter.
- **The core owns, and no adapter duplicates:** subprocess isolation, a **fresh artifact directory outside the
  repository per invocation** (never a configured static path — a stale report destroys the "no report" vs "no
  tests" distinction), report parsing, count reconciliation, exit/timeout/signal capture. **That per-invocation
  directory is created THROUGH S25's IO/path seam** (`io.py`/`paths.py`), so even this earliest slice's disk
  write is guarded and resolves under an IssueForge-owned root — *the reason S25 blocks S1* (see Dependencies).
- CLI discipline: success → exit 0, payload on stdout. Failure → exit 1, message on **stderr, stdout empty**,
  no traceback. Lints report **every** violation; no fail-fast.
- The test `FakeRunner` enforces a **read-only allowlist**: any command that is not a known read-only prefix
  and not explicitly registered **raises AssertionError**. An unforeseen destructive command (`git reset`,
  `clean`, `checkout`, `rm`, `update-ref`, `gh api`) is caught **by construction, not by a denylist**.

**Expected footprint.** `src/issueforge/process.py`, `config.py`, `adapters/base.py`, `adapters/pytest_adapter.py`,
`cli.py`; `tests/test_process.py`, `tests/test_config.py`, `tests/test_adapters.py`, `tests/conftest.py`.

**Dependencies.** Blocked by **S25** — S1 creates a per-invocation artifact directory (a disk write), and
S25's IO/path seam and boundary lint must exist before any module writes to disk (*v5-round-1 fix: S1 is a
disk-writer the earlier "S25 blocks S3/S4" fix missed*). **Unblocks: everything else.**

**Deterministic / AI / Human.** All deterministic. No AI. No runtime approval.

**Human approval points.** None.

**Failure & recovery.** A malformed config fails at load, naming the field. A timeout returns a typed result,
never an exception the caller can mistake for a failure exit.

**Logging & observability.** **Required — this module IS the subprocess boundary.** Every invocation emits
argv, cwd, duration, exit, `timed_out`. **stderr is always captured; `2>/dev/null` is forbidden.** Raw output
is persisted only through S4's redacting writer.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:80-101` (`RunResult`/`run` — verified: **no `check=True`**, docstring `:93-94`
  says *"Never raises on a non-zero exit"*, and **no `timeout=`**); the six-plus inversions (`:163-181`,
  `:291-311`, `:313-333`, `:456-469`, `:735-761`, `:763-781`); `:677` + `_parse_pytest_summary:682-693`;
  `tests/test_merged_runner.py` (the FakeRunner allowlist); `list_projects.py:94` (`test_command` shell-split
  from a Markdown bullet).
- *Preserve:* cross-cutting rules 1 & 2. `run()` never raises on non-zero. stderr always captured.
- *Refactor/extract:* `merged_runner.RunResult`/`run` port near-verbatim, **extended with `timeout=` and
  `duration_ms`, which the original lacks**. The FakeRunner allowlist **pattern**, not merely its tests.
- *Replace:* **`_parse_pytest_summary` — DO NOT PORT.** Summary-line scraping is the concrete false-green
  (§3). Structured reporting behind the adapter replaces it. Also replace the tri-state discipline itself:
  MARVIN hand-codes it at six-plus sites; here it is a **type**, so a seventh site cannot forget it.
- *Discard:* commands as shell strings; `_DOCS_ONLY_PREFIXES = ("state/",)` (`merged_runner.py:623`) — a
  literal MARVIN path convention inside a code-vs-docs classifier.
- *Test provenance:* `tests/test_merged_runner.py` FakeRunner cases port with the seam.
- *New engine policy (no MARVIN prior art):* the adapter Protocol; the `Outcome` enum; per-invocation artifact
  directories.

**Out of scope.** Retry policy (S14). Artifact persistence/redaction (S4). Git/gh semantics (S3, S6).
`provision_environment` / `canonical_collect` / `classify` (S6), `discover_contract_dependencies` (S12),
`validate_invocation` (S13).

**Route into acceptance-test authoring.** `route:direct-tdd` — the contract is mechanical.
Planned: `tests/test_process.py`, `tests/test_config.py`, `tests/test_adapters.py`.

---

## S2 — Source-audit inventory + completeness lint
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem (review 02 #5 → resolved by D6).** US-11.1–11.4 demand a design record inventorying **the
corresponding** MARVIN build-harness skills, scripts, tests, and failure-driven updates, with **every**
inventoried behavior classified **with a reason**. Draft v2's lint checked against `architecture.md`'s
*"**Initial** source map"* and the ledger's *"including **at minimum**"* list — **both expressly
non-exhaustive**. A record can therefore look complete while omitting an unlisted harness artifact. **This is
not hypothetical: the verification pass found five canonical MARVIN artifacts the prior audit had declared not
to exist** (`prune_plan_files.py`, `schedule_waves.py`, `issues_to_findings.py`, `close_run_for_pr`'s guarded
transition, and `prd-to-issues/SKILL.md`). **D6 settles the two questions a curated map could not answer:** the
countable inventory unit is a **test** (not a public symbol — one symbol can carry several independently-learned
safeguards, some private, that a symbol scan would pass while classifying only one), and the discovery scope is
a **versioned, checked-in extraction manifest** that declares which MARVIN artifacts are the build harness, as
distinct from the chief-of-staff workspace IssueForge does not extract. MARVIN does not draw that line itself —
its harness reaches into workspace state at several seams (registry `state/projects.md`, store under
`~/Projects/agentLogs`, the launch contract enforced by linting `skills/*/SKILL.md` prose) — so the line is a
**human-authored, checked-in decision**, and cutting those seams is a primary v1 goal.

**User-visible outcome.** `issueforge audit check <stage>` validates a stage's provenance record against the
checked-in extraction manifest and exits non-zero naming the specific gap — including **a test in a manifested
harness artifact with no disposition**, or **a candidate harness artifact discovered under a declared root but
absent from the manifest** (flagged for human harness-vs-workspace classification).

**PRD criteria covered.** **US-11.1, US-11.2, US-11.3, US-11.4** (owner — enforced, not aspirational). D6.

**Observable acceptance criteria**
- **A versioned, checked-in EXTRACTION MANIFEST** (`docs/provenance/extraction-manifest.json`) is the
  authoritative discovery root (US-11.1, D6). It lists **exactly the MARVIN build-harness artifacts** — skills,
  scripts, and their test files — each tagged `harness`, with the chief-of-staff **workspace** artifacts either
  absent or tagged `workspace, not extracted`. It is a **build-time artifact**; IssueForge has **no runtime
  dependency** on a MARVIN checkout (US-11.5). The manifest is **produced by discovery over declared roots
  against a checkout supplied at audit time and then human-curated** — discovery proposes candidates; a human
  decides harness vs workspace.
- **The inventory unit is a TEST** (US-11.1/US-11.2, D6). For each manifested harness artifact, discovery
  enumerates **every test in that artifact's test files**, and the per-stage record
  (`docs/provenance/stages/<stage>.md`) must carry a **disposition for each test**: **ported** /
  **replaced-with-reason** / **discarded-with-reason**, where the reason names the class — *deterministic engine
  policy* / *AI judgment* / *human approval* / *MARVIN-specific behavior to discard*. A symbol-level scan is
  explicitly **not** sufficient: one symbol (`merged_runner.py`'s six-plus failed-read inversions) carries
  several independently-learned safeguards, some in **private** functions, that such a scan would pass while
  classifying one. Counting **tests** catches them, and is exactly the unit US-11.4 already speaks in.
- **A behavior with NO test is an explicit author-supplied entry** (US-11.2), classified with a reason — never
  an omission. A stage may legitimately record **"new engine policy — no MARVIN prior art"** (e.g. the
  parent-epic update, `merged_runner`'s missing epic step), but only as such an entry.
- **Five failure modes, all tested:** (1) an **inventoried test with no disposition**; (2) a **manifested
  harness artifact absent from the stage record entirely**; (3) a **discovered test inside a manifested
  artifact's test files with no disposition** — test-granular, so a half-audited file (one safeguard classified,
  five ignored) fails; (4) a **reused safeguard (a `ported` test) with no mapped source test** (US-11.4);
  (5) a **candidate harness artifact discovered under a declared root but absent from the manifest** — flagged
  for human harness-vs-workspace classification, the mode that catches a false "nothing to port" without the
  lint pretending to judge the line itself.
- **Declared discovery roots are versioned** (`scripts/`, `skills/`, `tests/`, `context/`, and the provenance
  ledger), so "where to look for candidates" is a checked-in decision, not a reviewer's guess. Discovery
  proposes; the manifest (human-curated) decides.
- **A stage audit is HUMAN-APPROVED before that stage is implementation-ready** (US-11.2), and **manifest
  membership is a human judgment too.** The lint proves the record is *complete against the manifest*; it cannot
  prove a `discard` is *correct*, nor that "this file is workspace, not harness" — **exactly the two judgments
  D6 assigns to the human.**
- **US-11.3:** a rewrite with **no documented reason why extraction was unsuitable** fails the lint.
- Reports **every** violation; no fail-fast. Runs in CI. **No implementation issue is ready while its audit is
  incomplete.**

**Expected footprint.** `src/issueforge/audit.py`, `docs/provenance/stages/`,
`docs/provenance/extraction-manifest.json`, `tests/test_audit.py`, `cli.py`, CI workflow.

**Dependencies.** Blocked by: none. **Gates every implementation issue; blocks none.**

**Deterministic / AI / Human.** Deterministic lint (completeness against the manifest). No AI. **Human:
curating the extraction manifest (harness vs workspace), authoring each test disposition, and APPROVING the
stage audit.**

**Human approval points.** **Two, both D6 judgments a lint cannot make:** (1) **manifest membership** — whether
a discovered artifact is build-harness or chief-of-staff workspace; (2) **the stage audit itself**, before that
stage is implementation-ready — whether each `discard` is correct. The lint proves completeness against the
manifest; only a human judges correctness.

**Failure & recovery.** A failing lint blocks issue readiness, naming the missing source or classification.

**Logging & observability.** N/A — a build-time gate, not a runtime boundary.

**Prior-art and source audit**
- *Sources:* `docs/provenance/marvin/open-issue-transfer-2026-07-12.md` (*"No implementation issue is ready
  until its source audit and provenance entry exist"*); `architecture.md:60` (literally *"**Initial** source
  map:"*); MARVIN's validator house style (`validate_*.py`: exit 0 + `OK`; exit 1 + one `ERROR:` per violation
  on stderr, stdout empty, **no fail-fast**).
- *Preserve:* the validator CLI convention and the report-every-violation rule.
- *Refactor/extract:* the `validate_*.py` structural pattern (file-path positional or `-` for stdin).
- *Replace:* the artifact schema, the **checked-in extraction manifest** (harness vs workspace), and the
  **test-granular discovery** — net-new (D6). The manifest exists because MARVIN does not distinguish its build
  harness from its workspace; discovery proposes candidates against declared roots, a human curates.
- *Discard:* **`check_validator_invocation.py` entirely.** It lints SKILL.md files to enforce a byte-exact
  `"${MARVIN_PIPELINE_ROOT:-$HOME/marvin}"/scripts/<v>.py` invocation — the purest expression of the coupling
  IssueForge exists to remove. Once IssueForge is a package with entry points, there is nothing to lint.
- *Test provenance:* none ported; the lint is net-new.

**Out of scope.** The audits themselves (each issue authors its own).

**Route into acceptance-test authoring.** `route:direct-tdd`. Planned: `tests/test_audit.py`.

---

## S3 — Register a repository; resolve or refuse its adapter
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem.** Resolve a friendly alias to a verified local clone — never cloning, never auto-discovering — and
**refuse at registration a repository whose test framework has no installed adapter** (US-1.5), where the user
can act on it, rather than after a run has already been shaped and worktreed.

**User-visible outcome.** `issueforge repo add DandD:~/Projects/DandD` → `issueforge repo list` prints the
alias, absolute path, normalized `owner/repo` slug, default branch, baseline command, and the resolved adapter.
A Go or unittest repository is **rejected at `repo add`**, naming the unsupported framework.
**This is the smallest user-visible demo in the system, reached after the S2/S25/S1 enabling work.**

**PRD criteria covered.** **US-1.1, US-1.2, US-1.3, US-1.4, US-1.5** (owner). Integration assertion: US-4.1.

**Observable acceptance criteria**
- `repo add` expands `~`; records alias, absolute path, normalized origin slug, and default branch.
- Alias lookup is **case-insensitive**; the entered spelling is preserved for display.
- Rejected **without changing the registry** (assert the registry file is **byte-identical** after each):
  missing path, non-Git path, duplicate alias, mismatched remote.
- **Never clones, never auto-registers.** Assert **no `git clone` invocation exists anywhere in the source**.
- **US-1.5:** registration runs the adapter `probe`. **No installed adapter for the detected (framework,
  reporter) → registration is REFUSED**, naming the framework. **It never degrades to a weaker contract.** A
  probe that cannot prove canonical identity, selection completeness, trustworthy phase information, and
  dependency protection **fails** — capability is **mandatory, not optional**.
- Registration resolves `.issueforge.toml` from the repository's **verified committed Git object**. A file
  present only as untracked working-tree content is rejected; dirty working-tree contents cannot influence
  registration. A missing baseline command in the committed configuration is a registration-time rejection
  (integration assertion on US-4.1), not a runtime surprise.
- **`default_branch` is a recorded fact, never assumed. No `or "main"` fallback exists anywhere in the source.**
- The registry is IssueForge-owned (`~/.issueforge/`, one `ISSUEFORGE_HOME` override). **Never a Markdown file
  parsed by regex; never resolved relative to `__file__`.**

**Expected footprint.** `src/issueforge/registry.py`, `cli.py`; `tests/test_registry.py`, `tests/conftest.py`
(a temp-git-repo factory).

**Dependencies.** Blocked by **S1** and **S25** (S3 persists the registry to disk, so the IO write seam and
boundary lint must exist first — *v2-report mechanical fix 3*). Unblocks **S4**, **S6**.

**Deterministic / AI / Human.** All deterministic. No AI.

**Human approval points.** None — registration is itself the explicit human act.

**Failure & recovery.** Every rejection leaves the registry byte-unchanged.

**Logging & observability.** Required (filesystem + subprocess boundaries).

**Prior-art and source audit**
- *Sources:* `agent_runs_lib.py:455-486` (`repo_slug`); `list_projects.py:45,66,88,94,123,135,181-182`;
  `merged_runner.py:200` (`project.get("default_branch") or "main"` — **verified, recurs at :412 and :665**),
  `:653-660` (missing `test_command` → blocked, never silently skipped), `:702-723` + `:714`
  (`_SCRIPTS_DIR.parent / "state" / "projects.md"`), `:921` (`--project` **defaults to `"marvin"`**).
- *Preserve:* **a missing baseline command BLOCKS the gate, never silently skips it** (`:648-652`: *"with no
  command we cannot prove main is green, so block the gate with a clear anomaly instead of… silently skipping
  the safety gate"*). **This fired for real on 2026-07-12** — four rdv `/merged` runs silently fell back to
  manual because the registry carried no `test_command`. It is *why* the PRD makes the baseline mandatory.
- *Refactor/extract:* **`repo_slug(url)` — extract here.** Normalizes ssh/https, is idempotent on a bare
  `owner/repo`, tolerates `.git` and trailing slashes, takes the first two path segments, and **raises rather
  than silently producing a garbage bucket.** Exactly US-1.1's "normalized origin slug" and US-1.3's
  "mismatched remotes rejected".
- *Replace:* the registry **format** — `state/projects.md` is generated Markdown parsed by regex, carrying
  MARVIN-only fields, resolved **cwd-relative** and **script-relative**.
- *Discard:* `merged_runner.py:714` (the MARVIN-checkout assumption in its purest form); `:200`'s
  `or "main"` (**do not copy**); `:921`'s `--project marvin` default; `generate_projects.py` entirely.
- *Test provenance:* none ported directly; `repo_slug`'s tests port with it.
- *New engine policy:* adapter resolution and refusal at registration (US-1.5) — **no MARVIN prior art**.

**Out of scope.** Worktree creation and baseline execution (S6).

**Route into acceptance-test authoring.** `route:direct-tdd`. Planned: `tests/test_registry.py`.

---

## S4 — Run store + enqueue + stub stage (one locked write path, redacting)
**Labels:** `v1` `phase:0` `route:spec-up`

**Problem.** MARVIN's store drifted to **44 phantom `needs-review` and 7 stuck `running` records** precisely
because writes were not funnelled through one primitive. A decomposition giving the engine, the gate, and
closeout each their own writes has already lost atomicity.

**This is NOT a storage layer.** It ships `issueforge run <alias>#<n>` **end to end through a stub stage that
actually completes**. If you cannot demo `run` → state persisted → completes, the slice went horizontal.

**User-visible outcome.** `issueforge run DandD#148` enqueues a valid open issue and completes through a stub
stage. Run state survives `kill -9`. Two terminals cannot corrupt a record. No secret lands in an artifact.

**PRD criteria covered.** **US-2.1, US-10.1, US-10.3** (owner). Gaps G5, G6.

**Observable acceptance criteria**
- Store root is IssueForge-owned: `~/.issueforge/` (one `ISSUEFORGE_HOME` override) →
  `runs/<run-id>/manifest.json` + `events.jsonl`, `queue.json`, one lock.
- **ONE `apply(run_id, fn)` primitive. Every mutation routes through it. No module opens the JSON.**
- Atomic write: temp file in the **same directory** + `os.replace`. A crash mid-write leaves the previous file
  intact.
- **The lock is an OS advisory lock (`fcntl.flock`), not a bare lockfile** — the kernel releases it when the
  process dies. (Test: hold it in a child, `kill -9` the child, assert it is re-acquirable.) A PID lockfile
  strands, which needs stale-lock reaping, which needs liveness detection — the classic footgun.
- **The lock spans the WHOLE read-modify-write.** A lock-free pre-read then update is a lost-update race.
- **Validation runs UNDER the lock, on the merged record about to land.** A raising validator leaves the file
  byte-unchanged.
- **Existence checks INSIDE the lock** — a pre-lock `exists()` is a TOCTOU that mints a phantom record.
- **Non-int persisted values (including `bool`) RAISE rather than coerce.** `int(True) == 1` would mint a
  valid-looking record the validator can no longer catch.
- **The same validator runs on BOTH sides of the boundary** (write-time and read-time). MARVIN enforced
  `cross_review` at read time but not write time, so two records wrote fine, later failed validation, and **an
  entire repository was dropped from the rollup.**
- **Torn-final-event recovery:** a process death mid-append can leave a truncated final JSONL line. Replay
  **must** detect and discard a torn trailing record **without losing the preceding history**, and a test must
  simulate it. Atomic manifest replacement does **not** cover the append-only log.
- Events are append-only and **permanent** (US-10.1): transitions, approvals, overrides, commit/PR ids,
  contract manifests, verification summaries, cleanup outcomes.
- **ONE redacting writer owns ALL artifact persistence, and bypassing it is MECHANICALLY IMPOSSIBLE, not a
  convention** (US-10.3). It is the **only** caller of S25's write seam that may persist an artifact, and
  **S25's AST lint fails any module that writes an artifact by another path.** *A "please use the redacting
  writer" rule is not a control.*
- **Redaction is proven per-producer, at the producer, as each one lands.** **This slice can only test the
  capture paths that EXIST NOW** (stdout, stderr, event stream, error trace). **Prompts, AI responses, diffs,
  and review packets do not exist until S7, S10, S11, and S15** — a canary run against a fake early path
  **proves nothing about a module that has not been written.** Each later producer therefore carries its **own**
  canary assertion through this API as part of its own acceptance criteria, and **the exhaustive
  all-real-paths canary is an acceptance criterion of S24**, where a complete lifecycle actually exists.
- **Hidden model reasoning is dropped AT INGEST, not at display.** The provider's auth file is never read and
  the harness never dumps its environment.
- **US-2.1 — the single-active-worker invariant is asserted directly:** at most **one** run occupies the active
  (non-paused, non-terminal) worker slot at a time; a second `issueforge run` while one is active **enqueues**
  rather than starting concurrently. A test starts one run and confirms a second lands in the FIFO queue behind
  it, and the slot admission is decided **inside the store lock** (a lock-free check-then-start would admit two).

**Expected footprint.** `src/issueforge/store.py`, `engine.py` (minimal states + a stub stage), `github.py`
(read side: validate the issue is open), `cli.py`; `tests/test_store.py`, `test_engine.py`, `test_github.py`,
`conftest.py`.

**Dependencies.** Blocked by **S1**, **S3**, and **S25** (the redacting writer is the only caller of S25's
write seam that may persist an artifact, and S25's AST lint fails any other write path — *v2-report mechanical
fix 3*). Unblocks **S5, S6, S7, S12, S22**. **Must land before any other stage.**

**Deterministic / AI / Human.** All deterministic. No AI. No approval.

**Human approval points.** None.

**Failure & recovery.** Crash mid-write → the previous manifest is intact. A dead lock holder → the kernel
releases the lock. A raising validator → byte-unchanged. A torn final event → discarded; history preserved.

**Logging & observability.** Required — this module **is** the filesystem boundary, the event stream, and the
redaction owner.

**Prior-art and source audit**
- *Sources:* `agent_runs_lib.py` `_require_int`(:68-79 — **verified: rejects `bool` explicitly at :77**),
  `_repo_lock`(:184-205 — **verified `fcntl.flock` at :199, released in a `finally`**), `resolve_logs_dir`(:208-217),
  `_atomic_write_log`(:269-290 — `mkstemp` in the same dir + `os.replace`), `update_run`(:292-316),
  **`apply_run`(:319-345)**, `_update_run_unlocked`(:348-421), `close_run_for_pr`(:423-453);
  `validate_agent_runs.py:26,32` (`VALID_STATUSES`, `TERMINAL_STATUSES`); `merged_runner.py:585-613`
  (`flip_run_record_for_pr` — the existence check **inside** the lock, a TOCTOU fix).
- *Preserve:* the lock spanning the **read** is what makes a second writer see the first's record instead of a
  stale snapshot. Fail loud rather than coerce. `close_run_for_pr`'s guard semantics: an **exact identity
  match**; **only one status flips**; every other case (no match, already merged, **still `running`**) is a
  **byte-unchanged no-op** — *a `running` record is NEVER promoted straight to merged.* **This is a guarded
  state transition and it is prior art — draft v2 wrongly called the state machine "net-new, nothing to port."**
- *Refactor/extract:* **`_require_int` + `_repo_lock` + `_atomic_write_log` + `update_run` + `apply_run` +
  `_update_run_unlocked` + `close_run_for_pr` — the concurrency core. This is the single best thing to lift
  from MARVIN.** Correction to draft v2: it is **~220 non-contiguous lines inside a 517-line module**, coupled
  to the agent-runs JSON schema — **extract the functions, do not copy the file.** Port their tests
  (`tests/test_agent_runs_writepath_lock_unit.py`, `test_close_run_for_pr_690.py`) with a provenance comment (US-11.4).
- *Replace:* **`resolve_logs_dir()` (:208-217)** — `$AGENT_LOGS_DIR` / `~/Projects/agentLogs`. **This is the
  sharpest US-11.6 violation vector: a wholesale extraction of closeout would take `_repo_lock` on MARVIN's
  store and write MARVIN's `agent-runs.json`.** IssueForge owns its store root. Also the status vocabulary:
  MARVIN has **no `paused`/`parked`/`queued`**, which is *why* it had to overload `needs-review`. **And the
  queue: MARVIN has none.**
- *Discard:* `_DEFAULT_RATES_PATH` → `<marvin>/context/model-rates.json` (`:27`) — IssueForge does not meter
  model spend (US-6.6). `generate_agent_runs.py` and the seen-watermark rollup (a derived view that
  permanently hid four source records — the lesson survives, the code does not).
- *Test provenance:* the write-path lock and `close_run_for_pr` suites port with the code.

**Out of scope.** Queue control (S5). Retention (S22). Real stages (S6+).

**Route into acceptance-test authoring.** `route:spec-up` — the concurrency contract must be shaped before it
is built. Planned: `tests/test_store.py`, `tests/test_engine.py`.

---

## S5 — Queue control: FIFO, pause, park, cancel, resume, `continue`
**Labels:** `v1` `phase:0` `route:spec-up`

**Problem.** Queue and worker-slot semantics must be provable before any AI or Git mutation exists.

**User-visible outcome.** `issueforge queue | pause | park | cancel | continue` all work; closing the terminal
loses nothing. **Demo `run → queue → park → resume` from the CLI, or the slice went horizontal.**

**PRD criteria covered.** **US-2.2, US-2.3, US-2.4, US-9.3** (owner). Gaps G7, G9.

**Observable acceptance criteria**
- Additional issues enter a **persistent FIFO queue**; **reorder** and **cancel** have named CLI verbs (G9).
  **REORDER and CANCEL are two different operations with two different scopes** (*attempt-3 fix F3 — draft v4
  wrongly said both were "only before a run starts," which contradicts US-2.3's cancellable paused run*):
  - **Reorder** applies **only to queued, not-yet-started** runs (US-2.2: *"reordered … before starting"*).
  - **Cancel** applies to **both** a **queued** run (US-2.2: *"cancelled before starting"*) **and the current
    paused run** (US-2.3: *"a paused run blocks the worker until explicitly resumed, **cancelled**, or
    parked"*). Cancelling the paused run **releases the worker** to the next queued issue and writes a terminal
    record. **Two transition tests, one per path** (`queued → cancelled`; `paused → cancelled`), each asserting
    the worker-slot outcome (a queued cancel never held the slot; a paused cancel releases it).
- A **paused** run blocks the worker until explicitly resumed, cancelled, or parked.
- **Parking preserves exact run state AND releases the worker** to the next queued issue. Pause and park are
  two exits from one worker-slot state: **a park that does not release the worker is meaningless; a pause that
  does IS a park.**
- **US-9.3: closing either interface does not terminate or corrupt persisted state.** `kill -9` mid-run, then
  `continue` → resumes from persisted state; event replay works.
- **`continue` is ONE verb with a defined meaning** (G7): resume the run at its persisted state, whatever the
  pause reason. (US-7.4's watch-mode/`continue` parity on `waiting-for-merge` is owned by **S16**; this slice
  owns the verb.)
- **Resume RECONCILES, never silently heals.** **GitHub is authoritative for PR/branch/merge facts; the run
  record is authoritative for gate artifacts (approvals, verdicts, attempt counts); a divergence is SURFACED,
  never overwritten.** Decompose it any other way and you build a reconciler that overwrites a human approval.
- States are a `State` enum + `TRANSITIONS: dict[State, set[State]]`, with table-driven tests. **No
  state-machine library.**
- **Failures are TYPED STAGE RESULTS, not a copied string catalogue.** Each stage declares its own failure
  type. MARVIN's anomaly names are *provenance for the recovery procedures*, not a taxonomy to import — several
  (`no-test-command`, `red-main`) are stage-specific or made impossible by S3.

**Expected footprint.** `src/issueforge/engine.py`, `store.py` (queue), `cli.py`; `tests/test_queue.py`,
`tests/test_engine.py`.

**Dependencies.** Blocked by **S4**. Unblocks **S9**, **S23**.

**Deterministic / AI / Human.** All deterministic. No AI. Pause/park/cancel/reorder are human-initiated verbs.

**Human approval points.** None (these are commands, not gates).

**Failure & recovery.** Resume reconciles and surfaces divergence; it never auto-resolves.

**Logging & observability.** Required (queue + filesystem boundaries). Every transition is a persisted event.

**Prior-art and source audit**
- *Sources:* `validate_agent_runs.py:26,32` (the status vocabulary and terminal-status gate);
  `agent_runs_lib.close_run_for_pr:423-453` (the one guarded transition); `merged/SKILL.md:76-85` (the nine
  anomalies **with their recovery procedures** — mine these, do not copy the names);
  `docs/provenance/marvin/harness-phase3-state-machine-2026-07-10.md` §1, §3 (INV-1…INV-15 with their
  incidents), §4; `open-issue-transfer-2026-07-12.md` (*"v1 executes one issue at a time, avoiding ambiguous
  batch-halt semantics"*).
- *Preserve:* "Halt and surface, never auto-resolve." Resume reconciles; divergence is surfaced. The
  guarded-transition discipline (only declared transitions are legal; a terminal status is gated).
- *Refactor/extract:* the `VALID_STATUSES` / `TERMINAL_STATUSES` **pattern** and `close_run_for_pr`'s
  byte-unchanged-no-op posture.
- *Replace:* **the workflow engine itself. MARVIN has no FSM class and no queue** — verified: zero hits for a
  transitions table, and zero for FIFO/enqueue/park/cancel across `scripts/`. States I0–I15 exist only as
  *analysis* derived from skill prose. **Building this IS the point of IssueForge.** *(Correction to draft v2:
  "nothing to port" was too strong — the status set, the guarded transition, and the terminal gate are real
  prior art. The engine is net-new; the discipline is not.)*
- *Discard:* wave barriers (v1 is single-run, so they collapse); the wave-record shape built around MARVIN's
  multi-repo transport; **everything downstream of the Agent tool's `isolation:"worktree"` conditional — that
  exists only because the Agent tool worktrees *the session's* repo, which for MARVIN is MARVIN. IssueForge
  creates worktrees in the target explicitly; the trap cannot exist.** `schedule_waves.py` (a real,
  well-tested conflict scheduler) stays in MARVIN — **v1 is single-run and does not need it**; it becomes
  prior art if parallel execution is ever taken off the Out-of-Scope list.
- *Test provenance:* none ported directly; the transition table is net-new and table-driven.

**Out of scope.** Real stages. The TUI (S23). Watch mode on `waiting-for-merge` (S16).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_queue.py`.

---

## S6 — Isolated worktree, green baseline, and `classify` — red vs BROKEN
**Labels:** `v1` `phase:1` `route:spec-up`

**Problem.** A run must be based on fresh, isolated, **green** code or a new failure is not attributable to
the issue. And "green" cannot be read from an exit code: **exit 0 covers a fully-skipped and a fully-xfailed
suite.** MARVIN's merge gate has this exact false green today (§3).

**User-visible outcome.** Fetch `origin/<default>`, create a proven-isolated worktree, run the baseline, and
**pause on anything that is not provably green — before any AI touches a file.**

**PRD criteria covered.** **US-4.2, US-4.3, US-4.4** (owner). Gaps G13, G14.
Delivers the adapter's **`provision_environment`**, **`canonical_collect`**, and **`classify`** (from S1's Protocol).

**Observable acceptance criteria**
- Worktree HEAD equals the sha **just fetched** from `origin/<default>`, not a local ref.
- **Isolation proof — all three:** the worktree path is outside the normal checkout's working tree; the normal
  checkout's **HEAD** is byte-identical before and after; its **index** is byte-identical before and after.
- **A dirty normal checkout is permitted ONLY when all three hold** (US-4.3).
- Failed fetch → pause. Unprovable isolation → pause. Non-green baseline → pause. **All before AI changes files.**
- Baseline runs from the argv array, **no shell, with a timeout** (S1).
- **`classify` returns a closed enum, never a `(passed, failed)` pair:**
  `GREEN`, `BEHAVIORAL_RED`, `COLLECTION_ERROR`, `NO_TESTS_COLLECTED`, `ALL_SKIPPED`, `USAGE_ERROR`,
  `INTERNAL_ERROR`, `TIMEOUT`, `LAUNCH_FAILED` — plus `collected`, `executed`, the per-node
  `(nodeid, phase, outcome, longrepr)` records, and `report_present: bool`.
- **GREEN is a conjunction, never a residual:** exit 0 **AND** `collected > 0` **AND** `executed > 0` **AND**
  every expected node id present with outcome `passed` (**not** `skipped`, **not** `xfailed`).
  **Exit 0 alone is not green.** Test `ALL_SKIPPED` explicitly — it is the false-green trap MARVIN fell into.
- **Zero collected is a THIRD state — BROKEN, neither green nor red** — and pauses with its own reason
  (pytest exit 5). `COLLECTION_ERROR` (exit 2) and `INTERNAL_ERROR` (exit 3) are **distinct diagnostics**,
  never collapsed into one anomaly name.
- Evidence is fused from **(exit code) × (report present/absent/complete) × (report content) × (engine-side
  timeout flag)**. A timeout or kill leaves **no report at all** — absence is signal, and the engine must know
  to look for it. **Prefer pytest's `--report-log` (JSONL, one record per setup/call/teardown phase) over
  JUnit XML**, which cannot see the exit code, cannot distinguish zero-collected from a passing empty run, and
  folds xfail/xpass inconsistently.
- The report is written to a **fresh directory outside the repository per invocation** (S1). A configured
  static path can retain a stale report and destroy the "no report" vs "no tests" distinction.
- **Never run the suite against a stale tree and call it green** — a failed checkout/pull halts and the suite
  is **not** run.
- **G14 — `provision_environment` owns the hermetic, separately-provisioned AUTHORITATIVE environment**
  (*attempt-3 fix F2*; `prd.md:157-158`). Before the baseline runs, the adapter **provisions a hermetic
  environment separate from the normal checkout**: an isolated interpreter/venv under an **IssueForge-owned**
  root (never the target's site-packages), dependencies **installed by the engine** (the reviewer/implementer
  has no network and cannot install), **network off**, and a **fresh artifact directory per invocation**. The
  handle it returns is what **every authoritative run in the system uses** — the baseline here, the red proof
  (S10), the integrity re-collection and external re-resolution (S13), the authoritative post-session run
  (S14), and the readiness/full-baseline run (S15). **A testable criterion, not an assumption:** a test mutates
  the environment from a candidate/implementer position (edits the target's installed packages, sets a
  test-runner env var **inside** the worktree) and asserts the **authoritative result is unchanged**, because it
  runs in the provisioned environment, not the candidate's. *(Draft v4 relied on "already provisioned by the
  engine" with no owner; this makes provisioning a first-class, tested deliverable.)*

**Expected footprint.** `src/issueforge/workspace.py`, `verify.py`, `adapters/pytest_adapter.py`
(+`provision_environment`, +`canonical_collect`, +`classify`), `engine.py`; `tests/test_workspace.py`,
`test_verify.py`, `test_adapters.py`.

**Dependencies.** Blocked by **S3**, **S4**. Unblocks **S10**.

**Deterministic / AI / Human.** All deterministic. No AI. Human: resolves any of the four pause conditions.

**Human approval points.** None — the human resolves a pause; there is nothing to approve.

**Failure & recovery.** **Nothing uncommitted is ever destroyed.** No `reset --hard`, no `clean -fd`, no
`worktree remove --force` on an unverified tree. Dirty or unknown state is **preserved and reported**.

**Logging & observability.** Required (subprocess + filesystem). Structured pass/fail/timeout evidence per command.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:456-469` (a failed `git status` is **not** clean — *"that would remove a
  worktree whose real state is unknown, discarding possible uncommitted work"*), `:763-781`
  (`_worktree_for_branch` returns `(path, ok)` **precisely so absence and failure are distinguishable**),
  `:665-671` (sync-failed → the suite never runs), `:677`/`:682-693` (the false green, §3);
  `build_recovery.py:36-71` (`reset_worktree`); `context/agent-contract.md:10-18` (the linked-worktree pattern
  and its caveats); `content/dispatch-cross-repo-worktree-spike-2026-07-03.md`.
- *Preserve:* dirty/unknown worktrees **preserved** — never reset, cleaned, force-removed, or inferred safe.
  `reset_worktree` **verifies the base sha is a real commit** (`git rev-parse --verify --quiet <sha>^{commit}`)
  **before** `reset --hard` and **raises** if the ref is a branch/tag/tree, *"so a typo cannot silently reset
  to the wrong object"* — and it is `git -C`-scoped.
- *Refactor/extract:* `build_recovery.reset_worktree` behind the workspace seam, with its tests.
- *Replace:* **worktree CREATION — MARVIN has none in code**; the pattern is prose in `agent-contract.md`.
  **Correction to draft v2's "nothing to port": the *contract* is real prior art and is battle-tested.** Carry
  its caveats as requirements: suffix the worktree name with **timestamp PLUS PID** (*"a timestamp alone
  collides when two agents launch in the same second"*); serialize only the `worktree add`/`remove` bracketing
  (they mutate `.git/worktrees/`); **`worktree remove` does NOT delete the branch.** But base on **verified
  `origin/<default>`**, which is a *better* base than MARVIN's `HEAD` (MARVIN chose `HEAD` to avoid a hard
  dependency on a configured remote; IssueForge requires a verified origin anyway). **The isolation PROOF is
  net-new: MARVIN merely assumes isolation.**
- *Discard:* `worktree_root` conventions tied to `~/Projects/<repo>-worktrees` and `state/projects.md`.
- *Test provenance:* `tests/test_build_recovery.py`'s `reset_worktree` cases port with the function.
- *New engine policy:* the isolation proof; the `Outcome` enum and the GREEN/RED conjunctions.

**Out of scope.** The provider (S7). Authoring (S10).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** The red-vs-broken discrimination is
load-bearing and must be shaped before it is built. Planned: `tests/test_workspace.py`, `test_verify.py`.

---

## S7 — AI provider layer: roles, profiles, guarded launch, session identity
**Labels:** `v1` `phase:1` `route:spec-up`

**Problem.** The independent review is the only thing between an AI's work and a human's merge. **If an empty
or failed invocation reads as a PASS, the gate is decorative.** MARVIN observed exactly this: a bare CLI
blocking on stdin without a TTY, and **empty output silently read as a clean review.**

**User-visible outcome.** `issueforge provider check` verifies the configured CLI is authenticated on a
subscription plan and prints the resolved profile and role bindings.

**PRD criteria covered.** **US-6.6, US-9.4, US-9.5** (owner).

**Observable acceptance criteria**
- **Two ROLES, never two vendors** (US-9.4): a **primary AI** (authors, implements) and a **secondary AI**
  (independently reviews). Each binds to a **provider profile** whose executable, start, resume, and auth
  commands are **configuration variables**. **Assert no provider name appears anywhere in the engine source** —
  a test greps the package for vendor strings and fails on a hit.
- **US-9.5:** with **no secondary configured, the review role runs on the primary provider in a brand-new
  session** — never the authoring session, never a resumed one. **The role, provider, and session identity are
  recorded on every AI result, and a review whose session identity equals the authoring session's is REJECTED**
  (a test asserts the rejection, not merely the recording).
- **ONE `invoke(profile, prompt, *, cwd, timeout, runner) -> AIResult`. No provider ABC, no adapter registry.**
  Configuration is the polymorphism; a second provider is a second config table, not a second class.
- **The guarded-launch contract, as unit-tested properties of the subprocess — not a lint over prose:**
  `stdin=subprocess.DEVNULL`; **stderr captured, never discarded**; **full output persisted, never
  `tail`-truncated** (truncation drops finding lists); an explicit **wall-clock timeout via
  `subprocess.run(timeout=)`**, never the `timeout(1)` binary (**absent on macOS**); **empty output = FAILED,
  never a clean review**; **non-zero exit = FAILED, never a pass**. No shell ⇒ no pipeline ⇒ the
  `tee`/`pipefail` exit-masking class **cannot exist**.
- **The reviewer subprocess has NO NETWORK.** `gh` calls inside it **stall forever with an empty output file**.
  **Every input must be materialized to local disk before invoking:** the diff, the issue/buildability
  contract, the frozen manifest, the red evidence, the **literal proof command** (copied verbatim), the head
  sha the verdict binds to, and prior verdicts on a confirmation round.
- **The sandbox must permit execution.** A read-only sandbox **cannot run pytest** (it needs `.pytest_cache`,
  `__pycache__`, temp files). The review runs against the **real worktree with execution capability**, scoped
  to the worktree and `$TMPDIR`, **network off**, with dependencies **already provisioned by the engine** — the
  reviewer cannot install anything with no network.
- **No metered API, ever** (US-6.6). With no API key in the environment `invoke` still works. With the CLI
  unauthenticated it **FAILS rather than falling back**. **No `*_API_KEY` read exists anywhere in the source**
  (asserted by a test).
- **A rate-limit refusal is a distinct, recoverable engine state**, not a crash and not a review failure. Plan
  auth draws from a **rolling window plus a weekly cap shared across all of the vendor's surfaces**, so harness
  throughput competes with the human's own interactive use. Spurious limit errors are a known class, so a limit
  error gets a **bounded retry**, never an API-key fallback.
- Full output persists as an auditable artifact through S4's redacting writer. The provider's auth file is
  **never read and never logged**.
- **REDACTION CANARY (this producer's paths, S4 API).** A known token, a credential path, an env value, and a
  synthetic secret are seeded into the **prompt, the AI response, and captured stderr**, then persisted through
  S4's redacting writer, and must appear in **ZERO** persisted artifacts. **Both persistence paths are
  exercised:** the **success** path (a clean invocation) and the **failure/timeout** path (a non-zero exit and
  an expiry that leaves a partial output file) — a single happy-path lifecycle never hits the error branch, and
  that branch is where a raw stderr dump leaks. *(v2-report mechanical fix 1: each producer carries its own
  canary; S24's exhaustive all-paths canary is the backstop, not a substitute for this producer-local
  error-branch test.)*

**Expected footprint.** `src/issueforge/providers.py`, `config.py` (+profiles), `cli.py` (+`provider check`);
`tests/test_providers.py`, `conftest.py` (a fake provider subprocess).

**Dependencies.** Blocked by **S1**, **S4**. Unblocks **S9, S10, S11, S14, S15**.

**Deterministic / AI / Human.** **The guarded-launch wrapper is entirely deterministic. This issue BUILDS the
invocation seam; it exercises no AI judgment.** No runtime approval.

**Human approval points.** None.

**Failure & recovery.** Non-zero exit, empty output, or timeout = hard FAILED. **No silent fallback of any
kind.** A rate-limit refusal pauses with its own state and a bounded retry.

**Logging & observability.** Required (AI + subprocess). Prompt, response, stderr, exit, duration, role,
provider, and session id — redacted.

**Prior-art and source audit**
- *Sources:* `check_cli_launch_hygiene.py:36-44` (**`CONCEPTS` — verified: exactly six** — `stdin-close`,
  `stderr-capture`, `persistent-log`, `timeout`, `empty-output-retry`, `nonzero-exit-FAILED`), `:258-268`
  (the tee/pipefail offense token — **order-aware `set ±o pipefail` tracking, the most rigorous part of the
  checker**), `:387-404` (the bg-vs-fg stderr rule); `spec-dev/SKILL.md:413-435` (the Background Launch
  Contract), `:377-380` (empty output read as a clean review); `spec-wave/SKILL.md:177-181` (**no network**;
  *"piping through `tail` truncates finding lists"*); `docs/provenance/marvin/harness-codex-plan-auth-2026-07-10.md`
  (**read in full** — the empirical basis for subscription-only, the shared rolling-window + weekly cap, and
  the phantom-limit class); `content/research/codex-reliability-2026-07-06.md:17-32` (the stdin hang).
- *Preserve:* all six concepts. **Empty output OR non-zero exit = FAILED — the single highest-blast-radius
  weakening in the system.** Author/reviewer session separation. Inputs materialized to local disk.
- *Refactor/extract:* **Do NOT port `check_cli_launch_hygiene.py`. It is a LINT OVER SKILL.md PROSE** — it
  scans Markdown for launch commands violating the contract. **In IssueForge the six concepts become
  properties of the subprocess invocation itself.** That inversion — from *"lint the prose that tells the model
  how to launch"* to *"the code launches correctly"* — **is the entire win.** Mine
  `tests/test_cli_launch_hygiene.py` for the failure cases that become the fake-subprocess adapter's tests.
- *Replace:* **the timeout mechanism.** MARVIN's canonical documented example is `timeout 600 codex exec …` —
  **and `timeout(1)` does not exist on macOS**, so MARVIN's own documented command is not runnable on the
  machine it documents. Use `subprocess.run(timeout=)` and kill the process group. Also replace **how the
  launch happens** (MARVIN spawns via a Claude Code Agent tool from inside a session; IssueForge spawns a
  subprocess with captured streams — no code transfers, only the contract), and **provider configurability**
  (MARVIN hardcodes its reviewer).
- *Replace (structural weakness — do not inherit):* MARVIN checks the six concepts at **document scope** while
  checking offenses at **command scope**, so an individual launch missing `</dev/null` **passes** as long as
  the file mentions the concept elsewhere. In IssueForge the properties are per-invocation and unit-tested.
- *Discard:* the reviewer plugin invocation and its Claude-Code-plugin dependency; `PIPELINE_SKILLS`.
- *Test provenance:* `tests/test_cli_launch_hygiene.py`'s failure cases port as fake-subprocess tests.

**RISK (recorded, not a blocker).** Rate limits are **shared with interactive use**. Harness throughput
competes with the human's own use of the same plan. Budget and surface it; do not discover it.

**Out of scope.** What the AI is *asked* to do (S9, S10, S11, S14, S15). Additional provider profiles (v2 — a
second config table).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** Planned: `tests/test_providers.py`.

---

## S8 — Observability policy: boundary classifier + sensitive-field exclusion
**Labels:** `v1` `phase:1` `route:spec-up`

**Problem.** Changes crossing an external boundary need diagnostic logging, and no reviewer reliably asks for
it unless the contract does. The verdict is an **input** to shaping (S9), to the readiness gate (S15), and to
the PR body (S16) — ordering it after them is backwards, and **producing it at review time is too late:
"independent implementation review confirms compliance; it should not originate the requirement it is supposed
to review."**

**User-visible outcome.** A diff that introduces a call across an external boundary is deterministically
flagged as requiring logging, and a contract-listed sensitive field appearing in an emitted log fails the check.

**PRD criteria covered.** **US-6.8, US-6.9** (owner). Gap G3.

**Observable acceptance criteria**
- **The boundary TRIGGER is deterministic** — crossing **HTTP, database, subprocess, filesystem, queue,
  third-party service, or AI**. **It must NOT be an LLM judgment, because an LLM judgment cannot be
  regression-tested.** Diagnosability *elsewhere* remains the reviewer's judgment (`architecture.md:91`).
- **TWO analyses, because the verdict is needed BEFORE a diff exists.** A single diff-based classifier is
  incoherent here: S9 must produce the observability verdict **before any acceptance test is authored** and
  therefore **long before an implementation diff exists**.
  1. **`classify_prospective(issue, proposed_footprint, existing_code)` → the PRE-AUTHORING analysis.** It
     reasons over the **files and path patterns in the proposed footprint** and the boundary-crossing calls
     **already present in them**, plus the issue text. **This is what S9 consumes** to produce the US-6.7
     verdict, the required success/failure events, and the prohibited sensitive fields.
  2. **`classify_diff(diff)` → the POST-IMPLEMENTATION RECONCILIATION.** It runs at readiness (S15) over the
     **actual** diff and is compared against the approved verdict.
- **A boundary the diff crosses that the approved verdict did not anticipate is a DETERMINISTIC FAILURE, not
  an AI review finding.** This distinction is load-bearing: **US-6.5's override can waive an AI review finding,
  but it can NEVER waive "deterministically established observability and sensitive-data requirements"**
  (`prd.md:80`). If a newly-crossed boundary surfaced only as a reviewer's observation, **the override would
  become a legal path to ship an unlogged boundary crossing** — precisely what the PRD forbids. It therefore
  **halts the run and demands an observability/buildability amendment through S9** with renewed reviewer
  confirmation and human approval, not an S13 acceptance-contract amendment and not an override.
- **US-6.9:** required logging **reuses the target project's** logger, levels, formats, and correlation
  conventions — detected from the target, never imposed.
- **For every `required` verdict, the authoritative run supplies executable log evidence.** It captures emitted
  logs while seeded sensitive-field canaries traverse the relevant success and failure paths, proves every
  contract-required event actually emits using the target project's conventions, and proves no canary or
  contract-listed sensitive value appears. Static call-site analysis is supplementary only; it cannot satisfy
  the gate by itself. The sensitive-field list comes from the shaped contract (S9), and this slice owns the
  executable evidence collector and exclusion predicate.
- **G3 — libraries never install global logging configuration.** (In the PRD prose twice, in no criterion.)
  Same discipline as MARVIN's *"never widen repo-global config to satisfy a hook — fix the narrow cause"*,
  applied to a new domain.
- **`classify_prospective` and `classify_diff` are TWO SEPARATELY TESTABLE APIs, each returning a structured
  verdict — not a heuristic hint.** *(v2-report mechanical fix 2: the old "heuristic feeding a judgment call /
  one `classify(diff)` used as a HINT" language contradicted the two-analysis design above and is removed.)*
  Each takes concrete inputs (issue + proposed footprint + existing code; or a diff) and returns a
  boundary-crossing set the caller compares — S9 consumes the prospective verdict, S15 reconciles the diff
  verdict. **A module-level tuple of boundary markers backs both; the boundary list is NOT user-configurable in
  v1; it is not a rule engine.**
- **This slice OWNS the deterministic evidence APIs that S15 enforces without re-deriving them:** (1) a
  **logger-convention detector** that reports the target's logger factory and level/format/correlation-id call
  shape; (2) an **authoritative runtime log capture** that proves required success/failure events emit; and (3)
  a **sensitive-field canary predicate** over captured output. Static inspection confirms that changed code does
  not introduce a new root logger or `basicConfig`, but runtime evidence is load-bearing. **What remains
  irreducibly semantic — general diagnosability — is explicitly the AI reviewer's and is therefore overridable;
  it is named, not smuggled** (`architecture.md:91`).

**Expected footprint.** `src/issueforge/observability.py`; `tests/test_observability.py`.

**Dependencies.** Blocked by **S1**. Unblocks **S9**, **S15**, **S16**.

**Deterministic / AI / Human.** **Deterministic:** the boundary trigger, the sensitive-field exclusion.
**AI:** the diagnosability judgment everywhere the deterministic rule does not fire. **Human:** none here.

**Human approval points.** None (the verdict is approved as part of S9's buildability contract).

**Failure & recovery.** A sensitive field found in an emitted log fails the readiness gate (S15).

**Logging & observability.** This issue **is** the policy.

**Prior-art and source audit**
- *Sources:* `docs/architecture.md:87-91`; `context/agent-contract.md:52-58` (the verification-plan rule),
  `:19` (don't-widen-config), `:33` (never echo secrets); `validate_agent_runs.py:123-179`
  (`_validate_cross_review` — the three-valued classification **with enforcement**); `check_current_pii.py`.
- *Preserve:* **the verification-plan-up-front rule** (`agent-contract.md:54`): *"Before implementing, state a
  verification plan… **Design verification in up front; do not leave it to be remembered at the end.**"*
  **This is the structural precedent for "every shaped issue records an observability impact" — the same move
  applied to logging instead of testing.** And the three-valued classification **with enforcement**: a skip
  must carry a reason, checked at terminal status.
- *Refactor/extract:* the *pattern* of `check_current_pii.py` (a deterministic pre-write scan) for the
  sensitive-field check. The code itself is small and MARVIN-specific.
- *Replace:* **the entire boundary-classification engine — net-new; there is no MARVIN code OR prose for it.**
  Likewise "reuse the target project's logger/levels/formats".
- *Discard:* the `/harden` skill routing and `harden_recon_scan.py`. (The *lesson* — a deferred finding becomes
  a filed issue — informs S9; the skill does not transfer.)
- *Test provenance:* `_validate_cross_review`'s enforcement tests inform the required-verdict tests.

**Out of scope.** **The observability VERDICT itself (US-6.7) — that is S9's, recorded on the shaped issue.**
Redaction of IssueForge's *own* artifacts (S4 — a different concern, the same word). The enforcement call site
(S15).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_observability.py`.

---

## S9 — Buildability contract + human approval of the implementation write scope
**Labels:** `v1` `phase:2` `route:spec-up`

**Problem (D3 + D5).** At readiness the engine must evaluate the **approved implementation write scope**
(US-6.4). With no approved set there are only three possible behaviors and **all three are unacceptable**: fail
closed (every run pauses — the buildable path does not actually work), skip the check (ship a readiness gate
that **cannot enforce US-6.4**), or **approve after seeing the diff — and every diff would approve itself.** The
PRD now says so outright (`prd.md:46`). **A "pass-through shaper" is not a real thing.** The same failure
hits observability concretely: with no pre-implementation contract naming sensitive fields, US-6.9 has **no list
to enforce**. **D5 makes the scope precise:** what this slice approves is the **implementation write scope** —
the paths the implementer may modify — governing the **implementation commit range only**. The acceptance-test
files are **not** part of it; they are protected by the US-5 freeze (S12) instead, because a path is a protected
contract input or a permitted implementation target, **never both** (`prd.md:170`).

**This is the first product decision point.** S1→S6 validate infrastructure; this is where a run first becomes
a *shaped* run. The complete lifecycle continues through contract authoring, implementation, PR delivery,
merge verification, and cleanup in later slices.

**User-visible outcome.** Before any acceptance test is authored, a run emits a buildability contract, and a
human approves it — **including the implementation write scope** — or the run pauses.

**PRD criteria covered.** **US-3.4, US-3.5, US-3.6, US-6.7** (owner). Gap G11.

**Observable acceptance criteria**
- **The buildability contract (US-3.5) emits, before any test is authored:** a readiness classification
  (`buildable` / `oversized` / `blocked`); the **duplicate verdict + evidence**; the
  **unresolved-design-decision list**; the **proposed implementation write scope** (US-3.5, D5) as **allowed
  files and path patterns with justification** — the paths the implementer may modify, governing the
  implementation commit range only, **excluding the acceptance-contract files (those are frozen by S12)** — plus
  an explicit `footprint_known` verdict; the **observability verdict**
  (US-6.7: exactly one of `required` / `existing coverage sufficient` / `not applicable`, with
  reviewer-confirmed justification) and, when required, the important success/failure events and the
  **prohibited sensitive fields**; and an **immutable shape-artifact version**.
- **US-6.7: a missing verdict is a HARD REFUSAL, not a default. An unjustified `not applicable` FAILS
  validation.** (Port the enforcement, not just the vocabulary.)
- **US-6.7 requires the justification to be REVIEWER-CONFIRMED, not merely author-asserted** (`prd.md:82`).
  Before the human approves the buildability contract, a **fresh secondary-role review session** (the S7
  independent-review capability — session id differs from the authoring session and is **rejected** if equal,
  US-9.5) confirms the **observability category and its justification** (and the duplicate verdict), and that
  confirmation is **recorded session-bound** on the shape artifact. This mirrors the US-5.3 pattern (an
  independent review precedes human approval) and is what makes the field *reviewer-confirmed* rather than a
  self-signed label. *(Resolves the round-1 finding that S9 named "reviewer-confirmed" but invoked no reviewer.)*
- **The observability confirmation is NON-OVERRIDABLE — US-6.7 grants no override** (*resolves attempt-3
  finding F1*). Unlike US-5.4 (test-contract review) and US-6.5 (implementation review), US-6.7 says *"**Every**
  shaped issue records … with reviewer-confirmed justification"* (`prd.md:82`) — an unconditional
  requirement with no waiver clause. A reviewer failure is **recorded and retried with another fresh
  secondary-role session**; a **human may act as the reviewer only by explicitly reviewing and recording a
  confirmation** of the category and justification. **There is no path that proceeds with the justification
  unconfirmed** (a test asserts a run cannot leave S9 with an unconfirmed observability verdict). Importing
  US-5.4's override here would be a legal path to ship a shaped issue whose diagnostic coverage nobody confirmed.
- **The observability/buildability amendment is a first-class S9 transition distinct from S13's acceptance-
  contract amendment.** When S15 discovers an unanticipated boundary, the run returns here, recomputes the
  verdict with S8, obtains a fresh non-overridable secondary-role confirmation, records renewed human approval
  of the revised shape artifact, and then reruns readiness. The old artifact and approval remain in the audit
  trail. No path updates observability through S13 or proceeds on the stale verdict.
- **The footprint vocabulary is explicit path OPERATIONS — `add` / `modify` / `delete` / `rename` — not just
  "allowed paths".** A contract that cannot express a new file cannot approve legitimate work, and people will
  route around it. *(This closes a gap the PRD still leaves open; recorded as a design decision.)*
- **US-3.4 pause conditions:** duplicate open work; an unresolved design decision; an **unknown footprint**.
  **Dedup runs against open issues before anything is minted.** An **empty footprint is REJECTED** (no
  files-affected section, or one with zero paths).
- **`oversized` → STOP at `decomposition_required`.** This slice does **not** pretend it can process an
  oversized issue; S21 does that.
- **G11 — the not-testable exit.** A pure-refactor, docs, or research issue **cannot carry a TDD contract**.
  US-3.5 fixes the classification to exactly three values, so this lands as **`blocked` with
  `blocked_reason: not_testable`** — PRD-conformant, no amendment. Without it, such an issue is forced through
  a contract it cannot have.
- **The testability-seam pre-check:** if the code under test is not reachable in isolation, **the seam becomes
  part of the work and is NAMED in the contract.** Without it the acceptance author later discovers the code is
  untestable and **guesses**.
- **US-3.6 — the human approves the buildability contract, including the implementation write scope, BEFORE
  contract authoring begins.** That scope governs the **implementation commit range only**. The scope enforced
  at PR readiness (US-6.4) is **exactly this approved scope**; a test asserts it is **never** recomputed from
  the diff.
- **No GitHub write of any kind occurs in this slice.** (A test asserts `gh` is never invoked in write mode.)
  In-place revision and epic mutation are S20/S21.
- **THE RUNTIME BRANCH OUT OF THIS SLICE IS UNAVOIDABLE, AND THE TRANSITION TABLE ENFORCES IT.** Build order is
  not runtime order; the engine must make the runtime order **impossible to bypass**:
  - **`buildable` → S20** (propose the in-place revision → **human approves** → apply the GitHub mutation)
    **→ THEN** baseline + contract authoring (S10).
  - **`oversized` → S21** (propose the epic + children → **human approves** → apply → **each child enters the
    queue independently; the PARENT RUN STOPS HERE**). It does **not** proceed to authoring.
  - **`blocked` (including `not_testable`) → PAUSE.**
  - **`S10` IS NOT REACHABLE until the applicable shaping mutation has completed.** A transition test asserts
    that a `buildable` run cannot enter authoring with the revision unapplied — *otherwise US-3.1's "a buildable
    issue receives a proposed in-place revision" is satisfied by an issue nobody ever revised.*
  - **Until S21 lands, an `oversized` issue HALTS at `decomposition_required`.** That is honest fail-closed
    behavior, not a pass-through: the run stops and says why. **It is never silently treated as buildable.**

**Expected footprint.** `src/issueforge/shaper.py`, `contract.py` (the shape artifact), `engine.py`,
`observability.py` (consumer); `tests/test_shaper.py`.

**Dependencies.** Blocked by **S5**, **S7**, **S8**. Unblocks **S20**, **S21**.

**Deterministic / AI / Human.** **Deterministic:** the pause conditions, the empty-footprint rejection, the
required-verdict refusal, dedup mechanics, the immutable artifact version, the pre-approval write ban.
**AI:** the readiness assessment, the duplicate judgment, the proposed footprint, the observability
justification, **and a fresh secondary-role confirmation of the observability verdict (US-6.7, session-bound)**.
**Human: APPROVES the buildability contract, including the implementation write scope. This is the gate.**

**Human approval points.** **One, and it is load-bearing: approval of the buildability contract (implementation
write scope included) before contract authoring.** Also: resolving any pause.

**Failure & recovery.** Every pause condition halts and names its reason. `oversized` stops at
`decomposition_required`. Nothing is written to GitHub.

**Logging & observability.** Required (AI). The shape artifact is a **permanent** manifest record.

**Prior-art and source audit**
- *Sources:* `spec-up/SKILL.md:55-61` (the Step-0 readiness gates), `:80-82` (**three**-outcome triage:
  buildable → continue; oversized → decompose; **not-testable → route elsewhere**), `:34,38,81` (epic routing);
  `issues_to_findings.py:69-113` (footprint extraction); `validate_spec_up_issue.py` (`_has_real_token` :66-74,
  `_line_has_golden` :77-85); `validate_agent_runs.py:123-179` (three-valued verdict with enforcement).
- *Preserve:* the readiness gates; the testability-seam pre-check; dedup-before-minting; **three** triage
  outcomes; **an empty footprint is refused**; a three-valued verdict is **enforced at terminal status**, not
  merely recorded.
- *Refactor/extract:* **`issues_to_findings.py:69-113` → a `FootprintExtractor` that rejects an empty
  footprint** — real, tested code. *(Correction to draft v2, which listed decomposition/footprint prior art as
  nonexistent.)* Extract **`validate_spec_up_issue.py`'s golden-value proxy** (`_has_real_token` /
  `_line_has_golden`) — **not** `validate_accept_body.py`, whose arrow check is presence-only and is satisfied
  by `TBD -> TBD`. Keep its honest caveat: **it checks SHAPE only**, which is exactly why S11's semantic review
  sits above it.
- *Replace:* the readiness assessment (MARVIN's is model prose, not code) and the buildability-contract
  artifact itself.
- *Discard:* the `route:*` / `wave:N` / `serialize:<hotfile>` label taxonomy (wave scheduling — v1 is
  single-run); MARVIN's canonical section names, **including "Pending-test convention", which encodes the
  PENDING-on-main model IssueForge rejects**.
- *Test provenance:* `validate_spec_up_issue.py`'s tests port with the golden-value proxy.
- *New engine policy:* the buildability contract, its immutable version, and the path-operation vocabulary.

**Out of scope.** In-place revision and any GitHub mutation (S20). Epic decomposition (S21). **The invariant
lens (deferred-v2 — not in the PRD).**

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** Planned: `tests/test_shaper.py`.

---

## S10 — Author acceptance tests + deterministic red proof
**Labels:** `v1` `phase:3` `route:spec-up`

**Problem.** **The load-bearing control of the entire system, and the ONE contract in the PRD with no prior art
to extract.** `check_acceptance_integrity.py` imports only `argparse, ast, sys, pathlib` — it **never runs
pytest, never collects, never executes**; it diffs syntax trees. *"Verify it is red today"* exists as prose in
exactly one place. **The meaningful-red predicate is net-new.** The slice therefore **looks smaller than it
is**, and any plan that files "port MARVIN's guards" as its integrity slice ships a gate that accepts **any**
failure as red.

**If red-proof ships as a later slice, an approval flow exists that accepts any failure as red, and every
downstream gate is decorative. Authoring and red-proof CANNOT be separate deliverables.**

**Scope boundary.** This issue owns the **deterministic** half only: the test was collected, executed, and
failed **in the call phase**, on a healthy baseline, at a bound sha. **It does NOT — and cannot — prove the
failure was for the NAMED expected behavioral reason.** That correspondence is **semantic** and belongs to S11
(the AI reviewer) and S12 (the human approver). Claiming otherwise builds a gate that cannot do its job.

**User-visible outcome.** A run produces AI-authored acceptance tests plus machine-checked evidence that they
collected, executed, and failed in the call phase — and **refuses to proceed otherwise**.

**PRD criteria covered.** **US-5.1** (owner). **Contributes the DETERMINISTIC half of US-5.2** (the preexisting
baseline stays green, and the sha-bound call-phase red evidence) as an **enabling integration contribution**;
**US-5.2 is owned and finally asserted by S11** (*v5-round-2 fix B2*), which consumes this evidence and adds the
semantic "failed for the NAMED expected reason" half. S10 cannot own US-5.2 alone because it explicitly cannot
prove the semantic correspondence — see the scope boundary below.

**Observable acceptance criteria**
- **Collection is proven by IDENTITY, not count:** every targeted unit id appears in the collection report.
  **SET EQUALITY**, not `collected > 0`.
- **The failure occurs in the CALL phase.** The adapter's `errored` (collect/setup/teardown: import, fixture,
  config, environment) vs `failed` (call) distinction **is the mechanical discriminator**, and is exactly
  US-5.1's wording made checkable.
- **PHASE-BASED, never exception-type-whitelisted — AND import errors are INVALID at every phase** (*v5-round-2
  fix B1*). US-5.1 (`prd.md:64`) requires tests that *"collect and execute **without** syntax, **import**,
  fixture, configuration, or environment errors"* — so **an `ImportError` is invalid regardless of pytest
  phase**, not only at collection. The module under test **must import successfully**; the missing behavior is
  exercised **through the imported module** and surfaces as a **call-phase behavioral failure** — an
  `AttributeError`/absent-API on the imported module, a `NotImplementedError`, or an assertion. A rule of
  *"must be `AssertionError`"* is still **WRONG and breaks real TDD** (the genuine-TDD trap): the valid red is
  **any call-phase behavioral failure on a successfully-imported module**, not a whitelisted exception type.
  **Test THREE cases and require the checker to separate them:** a missing-symbol red reached via an imported
  module — an **`AttributeError` in the call phase** (**VALID**); a **collection-or-call-phase `ImportError`**
  (**INVALID** — the module under test is not importable, violating US-5.1); and a missing-fixture setup-phase
  error (**INVALID**). *(This reconciles the phase-based rule with US-5.1's prohibition on import errors: the
  earlier draft's "call-phase `ImportError` is VALID" overshot US-5.1.)*
- **US-5.2 — the preexisting baseline stays green.** *(This is review-02 defect C: the naive mechanism is
  impossible.)* Running the repo's **ordinary baseline command** at the test commit **includes the new,
  intentionally-failing tests** and is **red by construction**; and reusing S6's earlier baseline result cannot
  detect conftest/config breakage introduced **by the test author**, which is the entire point of the check.
  **The mechanism — a set operation anchored on the BASE snapshot, never a subtraction from the candidate:**
  - `BASE_IDS := canonical_collect(base)`, snapshotted at the verified base sha **before authoring**.
  - `CANDIDATE_IDS := canonical_collect(contract_candidate)`.
  - `ADDED := CANDIDATE_IDS − BASE_IDS` — the genuinely new acceptance IDs, **computed, never declared.**
  - **The baseline run executes EVERY id in `BASE_IDS`, at the candidate**, in the **S6-provisioned hermetic
    authoritative environment** (`provision_environment`, F2). It is
    **not** "the candidate set minus the new ones."
  - **`BASE_IDS ⊄ CANDIDATE_IDS` is a HARD FAILURE, not a green baseline.** A preexisting ID that
    **disappeared** — deleted, renamed, deselected, or collected away — is a **contract-integrity violation**.
  - **An authored test that REUSES a preexisting ID is a HARD FAILURE.** It is not "new". *A naive
    `collected(base) − new_acceptance_ids` subtraction would silently REMOVE that preexisting test from the very
    check that exists to protect it — the subtraction is unsound and must not be used.*
  - **`keep` / `revise` / `supersede` dispositions can never silently shrink `BASE_IDS`.** A `revise` or
    `supersede` that removes or redefines a preexisting ID requires the **human-authorized amendment path**
    (S13) and a **new manifest** — never an implicit exclusion.
- **Zero collected → REJECTED as BROKEN** (the third state). **`ALL_SKIPPED` → REJECTED.** **XPASS →
  REJECTED** (a pending-marked test that already passes). **Empty `parametrize` (collects to nothing) →
  REJECTED** — a real false-allow caught on MARVIN's own integrity build.
- The failure representation is a **canonical red-evidence record — exception type, unit id, assertion line,
  and a REDACTED message** — persisted through S4's redacting writer (US-10.3: secrets, env values, and
  credential paths are **never retained**, so a `longrepr` that happens to contain one is redacted **before** it
  lands, and the redaction canary above asserts it). **Fidelity comes from RE-DERIVABILITY, not from storing raw
  text verbatim:** the runner can check out the contract commit and reproduce the **same per-unit verdict**, and
  the manifest stores the canonical record plus the sha needed to reproduce it. Raw command output, if retained
  at all, is an **expiring (30-day), redacted** artifact (US-10.2), **never** the permanent manifest.
  **IssueForge has no PENDING-on-main self-reporting artifact, so this re-derivable record is the ONLY red
  evidence — it must be a runner capability, not a string someone wrote down once.** *(Resolves the round-1
  contradiction: "verbatim into the permanent manifest" could not coexist with US-10.3's "never retained.")*
- **Red evidence is SHA-BOUND**, and the build worktree forks from the **same verified `origin/<default>` sha**
  the red was proven against. (MARVIN's recovery passes conflicted because they cut from stale **local** main.)
- **Discover before authoring:** find existing contract tests and prior markers **first**; every existing test
  gets an explicit **keep / revise / supersede** disposition. Prevents stale XPASS and contradictory contracts.
- **Verbatim-example fixture rule:** when the source issue shows a concrete input/output example, one committed
  fixture reproduces it **verbatim** — not paraphrased, not re-shaped. (*A MARVIN suite tested the label and
  golden value on separate lines while the issue's canonical form was one line; the build passed locally and
  still rejected the issue's own example. Only the review gate caught it.*)
- **Suite-level anti-false-green discipline:** a "blocked" test asserts a non-zero exit **AND** a keyword
  **AND** the offending test name — *a test that only asserts "it failed" is satisfied by the script not
  existing.*
- **The lazily-satisfiable guard:** reject a criterion whose only proposed test is **trivially passable** (a
  bare "exit 0"; an assertion an empty implementation would satisfy). *Push for a stronger observable that a
  wrong implementation would fail.*
- **REDACTION CANARY (this producer's paths, S4 API).** A synthetic secret seeded into an **AI-authored test
  body** and into the **captured per-unit red evidence** (`longrepr`, assertion message, exception text) must
  appear in **ZERO** persisted artifacts after passing through S4's redacting writer. **Both paths:** the
  **success** path (a clean red proof persists its manifest) and the **failure** path (a rejected proof — zero
  collected, ALL_SKIPPED, or a torn run — still persists a diagnostic that must be redacted). *(v2-report
  mechanical fix 1.)*

**Expected footprint.** `src/issueforge/contract.py` (authoring + red proof), `verify.py` (per-unit reporting),
`adapters/pytest_adapter.py` (the baseline-selection operation), `engine.py`; `tests/test_contract.py`,
`conftest.py`.

**Dependencies.** Blocked by **S6**, **S7**, **S9**, **S20** (a `buildable` run must have its approved
in-place revision APPLIED before authoring begins — see S9's runtime branch). Unblocks **S11**, **S12**.

**Deterministic / AI / Human.** **Deterministic:** collection identity, call-phase discrimination,
baseline-still-green (by ID-set selection), zero-collected/ALL_SKIPPED/XPASS/empty-parametrize rejection,
sha-binding, re-derivation. **AI: authoring the test bodies ONLY.** **Human:** none here — approval is S12.

**Human approval points.** None (S12 owns the approval).

**Failure & recovery.** Any rejection pauses with the specific reason. **Red is never the default branch of an
`else`.**

**Logging & observability.** Required (AI + subprocess). The per-unit verdict report is a **permanent**
manifest artifact.

**Prior-art and source audit**
- *Sources:* `check_acceptance_integrity.py` — **read it to confirm what it does NOT do** (imports only
  `argparse, ast, sys, pathlib` at `:85-90`; 928 lines; never collects, never executes);
  `spec-wave/SKILL.md:137` (the **only** "verify it is red today", and it is prose);
  `spec-up/SKILL.md:55-61` (the lazily-satisfiable guard); `validate_pending_markers.py` (the **false-green
  catalogue**: skip in any form; non-strict / condition-bearing / extra-kwarg xfail; aliased / imported /
  module-level / class-level / parametrize-nested placement — **whitelist-shaped, fail-closed, walked at ANY
  depth**); `check_acceptance_mutation.py:150-186` (`_collect_nodeids`) and `:193` (the in-process
  `pytest_runtest_logreport` hook) — **structured collection, the anti-pattern's antidote**;
  `validate_spec_up_issue.py` (the golden-value proxy).
- *Preserve:* the lazily-satisfiable guard — **the shaping-time ancestor of meaningful red**. Discover-before-
  authoring. The verbatim-example fixture rule. The suite-level anti-false-green discipline.
  **`validate_pending_markers.py`'s CATALOGUE of false-green shapes — port the catalogue as what the
  red-verifier must REJECT, and drop the marker.**
- *Refactor/extract:* `check_acceptance_mutation._collect_nodeids` + the in-process report hook, behind the
  adapter — this is the **structured** collection that replaces summary-line scraping.
- *Replace:* **The red-proof predicate. NET-NEW. NOTHING TO PORT. This is the single most important line in
  this document.**
- *Discard:* **MARVIN's PENDING-on-main convention.** One branch, contract commit then implementation commit
  (`prd.md:161`). Dropping the marker kills the whole marker-downgrade attack class **and dissolves an
  unresolved MARVIN contradiction** (its implementer must remove the marker, which directly contradicts "the
  suite is physically outside the implementer's write scope" — both cannot hold). **IssueForge has no marker to
  flip; do not port the conflict.** Also discard the `ACCEPT:` satellite-issue pattern — **that is
  GitHub-as-database, a workaround for having no run store.** IssueForge has a manifest.
- *Test provenance:* `validate_pending_markers`' rejection cases port as red-verifier rejection tests.

**Out of scope.** Semantic validity (S11). The freeze (S12). Integrity enforcement (S13). Mutation
(deferred-v2).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** This issue's own contract must be
shaped before it is built. Planned: `tests/test_contract.py`.

---

## S11 — Independent review of the red contract: semantic validity + recorded override
**Labels:** `v1` `phase:3` `route:spec-up`

**Problem.** S10's predicate proves the test *executed and failed*. It **cannot** prove it failed **for the
named missing behavior**, nor that the tests actually **cover** the issue, nor that a shaped golden is
semantically weak — `run cmd -> exit 0` passes every syntactic check. **That judgment is irreducibly semantic**
and needs a fresh, independent session.

**User-visible outcome.** Before a human is asked to approve, an independent session has validated that the
observed red corresponds to the expected missing behavior, and its verdict is on the record.

**PRD criteria covered.** **US-5.2, US-5.3, US-5.4** (owner). US-5.2 is **finally asserted here** (*v5-round-2
fix B2*): this gate consumes S10's deterministic evidence (baseline-still-green + sha-bound call-phase red) and
adds the semantic half — that the red corresponds to the **recorded expected behavioral reason** — so the full
criterion "the new tests fail for a recorded expected behavioral reason while the preexisting baseline remains
green" is owned by one slice. S10 contributes the deterministic half as an enabling integration.

**Observable acceptance criteria**
- **The reviewer explicitly validates the OBSERVED red evidence against the EXPECTED behavioral reason.** This
  is the semantic half S10 cannot do, and it is this issue's core deliverable — **not a coverage rubber-stamp.**
- Runs in a **fresh session, separate from the authoring session** (session ids differ, are recorded, and an
  equal session id is **rejected** — US-9.5, enforced by S7).
- Runs **against the real branch worktree with execution capability**, given the **literal proof command**
  (copied verbatim, never paraphrased) and bounded time. *A reviewer that can only read a diff is a weaker gate
  than it appears.* All inputs are materialized to local disk first (S7 — **the reviewer has no network**).
- **Empty output OR non-zero exit = FAILED review, never a pass** (from S7; re-asserted here because this is
  the gate that matters).
- The verdict is **bound to the reviewed head sha**. A verdict whose sha ≠ head is **STALE and must not be
  reused.** (MARVIN merged two PRs before their fix agents landed.)
- **The batched adversarial contract:** ONE exhaustive pass enumerating **ALL** findings (not stopping at the
  first) → fix everything blocking in ONE batch → ONE confirmation round. **Reopen only if the confirmation
  round finds a NEW blocking finding.** Without this the review ping-pongs indefinitely.
- **US-5.4:** reviewer failure may be **explicitly overridden** by a fresh same-provider session or human
  review, and **the override is RECORDED** (who, why, when, which verdict). A skip carries an explicit one-line
  reason; the verdict is `done` / `blocking:<n>` / `skipped:<reason>`, **required at terminal status**.
- **This gate owns its OWN bounded round protocol — `contract_review_rounds` (default 2, configurable),
  persisted in the run record and incremented INSIDE THE STORE LOCK** (the S4 primitive).
  **It does NOT consume `review_rounds` or `repair_attempts`.** Those two counters are defined by US-6.2/US-6.3
  **within US-6 (implementation)**, and **S14 — which owns them — is DOWNSTREAM of this slice through S12 and
  S13.** A contract-review round consuming a counter its own producer has not built yet is an impossible
  dependency, and it conflates **test-contract review** with **implementation review**. They are different
  gates with different subjects.
- **Any change to a test or fixture RE-RUNS THE FULL S10 PREDICATE SET AND MINTS NEW SHA-BOUND RED EVIDENCE.**
  A fix round rewrites the contract, so the prior red proof is **stale by construction**. The reviewer then
  reviews **that new head**. **S12 may not freeze a manifest whose red evidence predates the last test change**
  (asserted by a test: mutate a test file after the proof, and the freeze must refuse).
- The full review packet is retained as an auditable artifact (30-day policy, S22). **MARVIN's own integrity
  review left only a one-token ledger field — its four rounds are reconstructible only from session-log prose.**
  Fixed here.
- **REDACTION CANARY (this producer's paths, S4 API).** A synthetic secret seeded into the **review packet**
  (the reviewer's response, the materialized inputs echoed back, and captured stderr) must appear in **ZERO**
  persisted artifacts after S4's redacting writer. **Both paths:** the **success** path (a completed verdict
  persists) and the **failure** path (an empty-output/non-zero/timeout review — a FAILED review still persists
  its packet, and the raw stderr on that branch is exactly where a leak hides). *(v2-report mechanical fix 1.)*

**Expected footprint.** `src/issueforge/contract.py` (+review), `providers.py`, `engine.py`;
`tests/test_contract.py`.

**Dependencies.** Blocked by **S10**. Unblocks **S12**.

**Deterministic / AI / Human.** **Deterministic:** session separation, sha-binding, the fail-loud posture,
override recording, the batched-round protocol. **AI: the semantic correspondence judgment.**
**Human:** overriding a failed review (recorded).

**Human approval points.** Overriding a reviewer failure (US-5.4) — explicit, never inferred.

**Failure & recovery.** A failed review pauses. An override is explicit and recorded, never a silent retry.

**Logging & observability.** Required (AI). The full review packet persists, redacted.

**Prior-art and source audit**
- *Sources:* `spec-dev/SKILL.md:347-391` (the Cross-Review Gate), `:352-354, 361-366, 387-390`;
  `spec-wave/SKILL.md:177-181` (local-file inputs; **no network**); `validate_agent_runs.py:123-179`
  (`_validate_cross_review` — the verdict vocabulary **enforced at terminal status**);
  `docs/provenance/marvin/harness-phase3-state-machine-2026-07-10.md` INV-4 (*two PRs merged before their fix
  agents landed*); `docs/provenance/marvin/open-issue-transfer-2026-07-12.md` (the reviewer needs execution
  capability and a literal proof command).
- *Preserve:* the reviewer executes in the **real worktree**; sha-bound verdicts; fail-loud on empty/non-zero;
  the batched contract; an override is a **first-class recorded event**, never a silent retry; a skip carries
  a reason, checked at terminal status.
- *Refactor/extract:* `_validate_cross_review`'s verdict-enforcement shape (`done` / `blocking:<n>` /
  `skipped:<reason>` required at terminal status) — the *rule*, not the cost-tuple coupling.
- *Replace:* the review invocation itself — **MARVIN's gate is a bash invocation inside SKILL.md prose.**
- *Discard:* MARVIN's `cross_review` verdict-string **format** and its cost-tuple coupling.
- *Test provenance:* `_validate_cross_review`'s tests port with the enforcement rule.

**Out of scope.** The human approval itself (S12). The implementation code review (S15 — a different gate, a
different override).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_contract.py`.

---

## S12 — Human approval freezes the manifest; adapter-discovered dependency closure
**Labels:** `v1` `phase:3` `route:spec-up`

**Problem (review-02 defect D — the `helpers.py` bypass).** Draft v2 froze the **test modules'** import closure
and hashed `conftest.py`. It did **not** hash **conftest's own transitive dependencies**, plugins, or other
adapter-loaded fixture modules. The bypass: `conftest.py` imports a fixture from `tests/helpers.py`; the
implementer edits **only `helpers.py`**. Conftest hash unchanged. Every test-file hash unchanged. Command
unchanged. Collected node-id set unchanged. `helpers.py` is in *conftest's* closure, not the *test modules'*.
**The fixture neutralizes the contract, undetected.**

**User-visible outcome.** A human approves an exact contract; the engine records **precisely** what was frozen,
and the boundary it froze was **discovered, not declared**.

**PRD criteria covered.** **US-5.5, US-5.6** (owner). Gap G16.
Delivers the adapter's **`discover_contract_dependencies`**.

**Observable acceptance criteria**
The manifest freezes, and the engine **DISCOVERS** (never merely accepts a configured glob):
- the **contract commit sha** — and **the contract is a SEPARATE commit, preceding implementation, on one
  branch** (`prd.md:161`, G16);
- a content hash of **every test file**;
- **US-5.6 — `discover_contract_dependencies` returns the protected closure. The ROOTS ARE SPECIFIED
  PRECISELY, because the closure is wrong in BOTH directions if they are not:**
  - **IN (the protected boundary):** the test modules; **every fixture provider and configuration file on the
    collection path** (`conftest.py` **and conftest's own transitive dependencies** — the `helpers.py` bypass);
    **plugins**; configuration loaders; helper modules that **influence an expected outcome**; and their
    **transitive** dependencies. **Adapter-supplied, never hardcoded, never configured.**
  - **IN, and easy to miss: EXTERNAL package identities.** US-5.6 says *"plugins, and their **transitive**
    dependencies."* A closure limited to *repository* files leaves **installed plugins and their external
    versions outside the frozen contract** — swap the plugin version and the contract's meaning changes with
    every hash intact. **`discover_contract_dependencies` supplies the external identities + pinned versions**
    (S1, resolved against the installed distributions) and the manifest freezes the **immutable identity +
    version** of every external package in the closure.
  - **OUT, and this is load-bearing: the SYSTEM UNDER TEST.** A test's import of the production module it
    exercises is **NOT** a protected dependency. **If it were, S13's absolute protected-path gate would freeze
    the implementation itself and the issue would be UNBUILDABLE.** The closure protects *the code that decides
    a test's outcome*, **not the code the test is about**. **D5 makes the discriminator the two disjoint
    scopes** (`prd.md:170`): a path inside the **approved implementation write scope** (US-3.6) is a
    permitted implementation target and is **expected to change**; a path in the **frozen contract set** (the
    test files plus this discovered closure) is a **protected contract input**. They are **disjoint by
    construction** — the acceptance tests are delivered in the contract commit and are **never** inside the
    implementation write scope. **A path proposed as BOTH is a contradiction and fails the freeze, naming it**
    — the human resolves it by amending the write scope or routing the issue to `blocked` when its real work is
    editing a shared fixture.
  - **Two fixtures prove both directions, and neither is optional:** (1) `conftest.py → helpers.py`, where only
    `helpers.py` is edited — **caught**; (2) an imported production module inside the approved implementation
    write scope, edited by the implementer — **still editable, build proceeds.**
- the **test configuration** (`[tool.pytest.ini_options]`, `pytest.ini`, `tox.ini`, `setup.cfg`) — adapter-supplied;
- the `.issueforge.toml` **command arrays** (US-5.5 freezes "the command", so the config file is **inside** the
  protected boundary, not outside it);
- the **collected unit-id set**;
- the **red evidence** (S10) and the **review verdict** (S11);
- **the APPROVED IMPLEMENTATION WRITE SCOPE carried forward from the buildability contract (US-3.6/US-5.5)** —
  the amendment's addition, and the reason the freeze can be checked against a scope at readiness at all. The
  frozen contract set (above) and this write scope are recorded as **two disjoint sets** (D5); a path proposed
  as both fails the freeze.
- **The discovered set is UNIONED into `contract_paths`, and the union is what is protected. A user-configured
  path list may ADD to the boundary but can never SHRINK it** (US-5.6, verbatim intent). *User-configured globs
  cannot be trusted to enumerate the boundary the engine claims to discover.*
- **Symlinks, renames, deletions, and generated files inside the boundary each have defined, tested behavior.**
  **A deleted contract file reads as an EMPTY MODULE, so every test in it reads as deleted — deletion is not an
  escape.**
- **Incomplete discovery fails CLOSED** — an unresolvable import means **refuse to freeze**, never freeze a
  partial boundary.
- **Human approval is THE gate.** Freezing is the human's act, recorded as an event carrying the approver's
  decision and the exact manifest hash.

**Expected footprint.** `src/issueforge/contract.py` (+manifest/freeze), `adapters/pytest_adapter.py`
(+`discover_contract_dependencies`), `engine.py`; `tests/test_contract.py`, `test_adapters.py`.

**Dependencies.** Blocked by **S10**, **S11**. Unblocks **S13**.

**Deterministic / AI / Human.** **Deterministic:** discovery, hashing, the fail-closed posture. **AI: none.**
**Human: THE APPROVAL.**

**Human approval points.** **The test-contract approval — the second of the two load-bearing gates** (the first
is S9's file-scope approval).

**Failure & recovery.** Incomplete discovery → refuse to freeze, naming the unresolvable import.

**Logging & observability.** Required. **The manifest is a permanent artifact** (US-10.1).

**Prior-art and source audit**
- *Sources:* `check_acceptance_integrity.py:79-82` (**the value-resolution scope limit** — only module-level
  defs *in the diffed file* are resolved; a conftest fixture is an example of an **opaque outside value**.
  *Note: this is a resolution limit, NOT a "conftest is excluded" file rule — draft v2 mis-framed it*),
  `_assertion_dep_roots` (:632-654), `_dep_closure` (:657-675), `_dep_closure_changed` (:677-694);
  `docs/provenance/marvin/pipeline-eval-2026-07-07.md` (**the conftest hole is filed as a LIVE OPEN issue in
  MARVIN**); `ci_acceptance_gate.py` (the tag is keyed strictly on the **base** revision: *"tag removal is not
  an escape hatch… silent un-designation would disable future protection"*).
- *Preserve:* **"Configuration and shared fixtures that can neutralize tests belong INSIDE the protected
  boundary."** **This is the most important thing IssueForge inherits AS A REQUIREMENT rather than as code —
  US-5.5/US-5.6's "dependent fixtures/configuration" is precisely MARVIN's known, still-open hole, fixed by
  specification.** And: **you cannot escape the contract by deleting your membership in it.**
- *Refactor/extract:* `_dep_closure` / `_assertion_dep_roots` as the import-closure computer — **with its real
  semantics stated:** it is a **RESOLVER, not fail-closed**. A root with no module-level definition (a
  parametrize value, a pytest-injected fixture, a builtin, an import) is simply **absent**, not recorded as a
  change; and **a name bound LOCALLY in the test body shadows a same-named module def and never enters the
  closure.** **IssueForge needs the OPPOSITE posture at the boundary: an unresolvable dependency must FAIL the
  freeze, not be silently dropped.** Extract the traversal; invert the failure mode.
- *Replace:* the freeze itself — **MARVIN has no manifest; it AST-compares two revisions.**
- *Discard:* the in-file `ACCEPTANCE_CONTRACT = True` tag **as the membership mechanism** (the frozen manifest —
  hashes + collected identifiers — is strictly stronger). **Keep the can't-un-designate rule.**
- *Test provenance:* `check_acceptance_integrity`'s dep-closure tests port with the traversal.

**Out of scope.** Enforcement (S13).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** Planned: `tests/test_contract.py`.

---

## S13 — Contract integrity enforcement + `validate_invocation` + the amendment path
**Labels:** `v1` `phase:3` `route:spec-up`

**Problem.** The invariant is **NOT "the test files are frozen." It is "the discovery boundary is frozen"** —
the exact units collected, the exact code deciding their outcome, and the exact command running them. Freezing
file hashes alone leaves ~16 channels open through which an implementing AI neuters an approved test **without
ever editing the test file**: autouse fixtures; `xfail_strict = false`; `addopts` gaining
`--deselect`/`--ignore`/`-k`/`-m`/`-p no:`; `collect_ignore`; `testpaths`/`norecursedirs`; an imported helper;
the command itself; renaming the test or its class; empty `parametrize`; marker downgrade; alias/import
indirection under a kept decorator; a changed fixture `params`; deleting the file; removing the contract
designation; `sitecustomize.py` or a shadowing stub package; amend/rebase/force-push.
**Enumerating 16 checks is whack-a-mole; channel 17 is the one nobody thought of.**

**User-visible outcome.** After approval, **any** change to the protected boundary fails the build regardless
of how it was written — and a **legitimate amendment has a real, auditable path**.

**PRD criteria covered.** **US-6.1** (owner). Delivers the adapter's **`validate_invocation`**.

**Observable acceptance criteria**
- **The protected-path diff gate is ABSOLUTE.** After the approved commit,
  `git diff --name-only <contract_sha>..HEAD` → **ANY** change under a protected path **fails the build. No
  sanctioned exception exists.** IssueForge has no PENDING marker and therefore no flip step, so — **unlike
  MARVIN — it needs no carve-out.** This layer is **complete rather than enumerative**: it is **unforgeable
  from inside the session** (file tool, shell redirect, `python -c 'open(...,"w")'`, `git checkout --` all fail
  identically).
- **EVERY frozen dependency hash from S12 is RECOMPUTED at the candidate head and COMPARED.** The diff gate
  alone is **not sufficient**: a file outside `contract_paths` but **inside the discovered closure** must still
  be caught. *Recollection is necessary but does not subsume semantic dependency integrity.*
- **EVERY frozen EXTERNAL identity/version is RE-RESOLVED and COMPARED in the AUTHORITATIVE VERIFICATION
  ENVIRONMENT** (US-6.1, D5). File hashes are blind to a plugin version swap: `helpers.py` unchanged,
  `conftest.py` unchanged, every in-repo hash intact, yet `pytest-randomly` or a fixture plugin bumped a major
  version and the contract's meaning changed. S12 froze the **immutable identity + pinned version** of every
  external package in the closure (from `discover_contract_dependencies`); this slice **re-runs
  `discover_contract_dependencies` in the **authoritative verification environment provisioned by S6's
  `provision_environment`** (F2 — a real, owned environment, not an assumption) and compares each external
  identity+version against the frozen set**, and a mismatch is a contract-integrity failure — *not assumed
  unchanged, and not read from the candidate's own declared metadata, which the session controls.*
- **Re-collection: the collected unit-id set reproduces EXACTLY (set equality).** The strongest single check,
  and **it does not exist in MARVIN** — MARVIN never collects; it only AST-compares. It subsumes most
  config/conftest/rename tricks in one predicate.
- **`validate_invocation`:** the command is taken **from the frozen manifest, never from candidate HEAD**;
  every invoked in-repository wrapper and configuration file is **hashed and frozen**; candidate-specified
  postprocessors are **rejected**; and **retries, sharding, bail, force-exit, pass-with-no-tests, and custom
  reporters are prohibited or explicitly modelled** — a candidate must not be able to silently enable a
  dangerous mode.
- `git merge-base --is-ancestor <contract_sha> <head>` — the approved commit **is still an ancestor** (defeats
  amend/rebase/force-push).
- The **AST weaken-check runs as DEFENSE-IN-DEPTH** on the final diff, **demoted from load-bearing**.
- **THE AMENDMENT PATH SHIPS WITH THE GATE, NOT AFTER IT** (cross-cutting rule 6). MARVIN's own evaluation is
  blunt: *"The amendment path is unrealistic, so amendments route around it."* A real MARVIN PR legitimately
  aligned seed args in two committed suites, and the guard merged two hours later *"would classify those exact
  edits as WEAKENED. The pipeline has no lightweight, auditable amend procedure, so legitimate amendments are
  indistinguishable in kind from the attack the guard exists to block."* **An amendment requires: an
  issue-linked reason, the exact diff, RENEWED human approval, and a NEW manifest.** Whole-body equality alone
  is insufficient. **Build the escape hatch with the gate, or the gate gets bypassed.**
- **This amendment changes only the frozen acceptance manifest**: contract files, dependency closure,
  collection, configuration, or command. It cannot update an observability verdict. A newly discovered boundary
  is routed to S9's separate observability/buildability amendment; a transition test rejects routing that state
  through S13.
- **US-6.1: implementation cannot proceed to PR readiness** when contract files, collection, configuration,
  command, **or the identity/pinned version of any frozen external plugin or package in the dependency closure**
  changed **without new human authorization** — re-resolved and compared in the authoritative verification
  environment, not assumed unchanged.
- **Enforcement is by the harness, NEVER by the session being policed** (cross-cutting rule 5).
- **The residual risk is carried, not claimed away** (`prd.md:158`): an implementation that **branches on a
  test-runner environment variable** defeats **every** static check — file hashing and import-closure analysis
  alike. It is carried by S15's code review (explicitly instructed to look for it) and by hermetic runs.
  **This slice must not claim to eliminate it.**

**Expected footprint.** `src/issueforge/integrity.py` (the AST backstop), `contract.py` (+verify),
`adapters/pytest_adapter.py` (+`validate_invocation`), `engine.py`; `tests/test_integrity.py`,
`test_contract.py`.

**Dependencies.** Blocked by **S12**. Unblocks **S14**.

**Deterministic / AI / Human.** **All deterministic. The AI is NEVER asked whether the contract is intact.**
**Human:** authorizing an amendment.

**Human approval points.** **Authorizing a contract amendment** (renewed approval + a new manifest).

**Failure & recovery.** Any violation halts **before** PR readiness, naming the violated predicate.

**Logging & observability.** Required. Every integrity verdict is **permanent**.

**Prior-art and source audit**
- *Sources:* `check_acceptance_integrity.py` — **read the 83-line docstring; it is a complete specification of
  every weakening vector, discovered one incident at a time**; `_kept_decorator_bindings_changed` (:379-400 —
  catches chain-root redefinition, import swap, and **import deletion**, because an unbound name maps to `None`
  rather than silently passing); `_dep_closure` (:657-675); class-keying `ClassName::method` (so *a same-named
  method in another class can never shadow a weakened one*, and **the class's own decorator list is part of the
  contract**); `docs/provenance/marvin/harness-phase2-step-classification-2026-07-10.md` (**the runner-owned
  diff boundary gate, "unforgeable from inside the session"**);
  `docs/provenance/marvin/harness-prior-art-research-2026-07-10.md` (**weak verifiers get gamed**);
  `docs/provenance/marvin/pipeline-eval-2026-07-07.md` (the amendment-path finding).
- *Preserve:* the layering (runner-owned diff gate + AST backstop + re-collection), with IssueForge's diff gate
  made **ABSOLUTE**. The four false-allows the AI reviewer caught during MARVIN's own integrity build (aliased
  `xfail→skip`; empty-`parametrize`; decorator reorder; re-marking an active test pending). **Deletion is not
  an escape.**
- *Refactor/extract:* `check_acceptance_integrity.py`'s AST machinery (~928 lines) into `integrity.py` **as
  defense-in-depth, not as the primary gate**, behind the framework-adapter boundary. **Port all of its test
  files** — they are the weakening-vector catalogue.
- *Replace:* **the primary gate.** MARVIN's is AST-diff (enumerative, with a documented and still-open conftest
  hole). IssueForge's is protected-path diff + dependency-hash comparison + re-collection (complete).
- *Discard:* **MARVIN's sanctioned marker-flip exception.** It exists **only** because MARVIN's implementer
  must remove the PENDING marker. **Carrying it across would import a hole IssueForge does not have.**
- *Test provenance:* all six of `check_acceptance_integrity`'s test files port with the AST backstop.

**Out of scope.** Mutation / anti-tautology (deferred-v2). The implementation (S14).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** Planned: `tests/test_integrity.py`.

---

## S14 — Implement under the frozen contract; two engine-owned repair budgets
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem.** Green must mean *"the approved behavior was delivered"*, not *"the tests stopped failing."* Two
things break that: an implementer that owns the Git boundary (making every file-level protection advisory), and
an **unverified self-report of green**.

**PRD criteria covered.** **US-6.2, US-6.3** (owner).

**User-visible outcome.** The AI implements against a frozen contract. **The engine — not the AI — owns diff,
commit, and the authoritative test run.** Exhausting either repair budget pauses with a schema-valid terminal
record.

**Observable acceptance criteria**
- **THIS SLICE NEVER PUSHES.** *(Review-02 defect E: draft v2 pushed here, which contradicts US-7.1 —
  "IssueForge pushes and opens a PR automatically **only after all readiness gates pass**.")* Implementation
  produces a **local immutable candidate sha**. Code review (S15) and the readiness gate (S15) run **against
  that local sha**. **S16 is the only slice that pushes.**
- **The implementing session NEVER runs `git worktree`, NEVER commits outside the engine's control, NEVER
  pushes, NEVER opens a PR.**
- **The AUTHORITATIVE test run is the ENGINE's, not the agent's.** The implementer runs tests as feedback; the
  authoritative run executes the **verbatim proof command** plus the full suite **after the session ends**, in
  the **S6-provisioned hermetic environment** (`provision_environment`, F2) — never the implementer's own
  session environment, which the session controls.
  **Never trust an agent's self-report of green.**
- **US-6.2 — TWO separate, independently configurable budgets, each defaulting to 2:**
  - **`review_rounds`** — the independent review raised blocking findings → the implementer **fixes them in
    place** and **the worktree is PRESERVED**.
  - **`repair_attempts`** — the implementer process **failed or died**, **or the acceptance suite is still red
    after the implementer reported done** → the attempt is a **write-off**: the worktree is **reset to the
    branch base**, a **fresh** implementer session is dispatched carrying the frozen contract and a **compact
    failure trace but NEVER the prior transcript**.
  - **Exhausting EITHER pauses the run with a schema-valid terminal record.**
  - *They recover from opposite failures; one counter would hide both, and collapsing them lets one transient
    implementer failure consume the budget intended for review iteration.*
- **US-6.3 — both counters are PERSISTED RUN STATE INCREMENTED INSIDE THE STORE LOCK, and the engine gates the
  transition on them.** A lock-free read-then-write **under-counts and under-enforces the cap**, and **an AI
  session cannot bypass a budget it does not own.** **Attempts an implementer makes INSIDE one session are not
  engine state and are not counted** — they are a prompt instruction, not a workflow budget.
- The repair prompt carries the **frozen contract + a compact trace, and NEVER the prior transcript**
  (context-rot). An engine that "helpfully" re-attaches logs **re-introduces the bug**.
- **Cap exhaustion writes a VALID terminal record, not a crash** — schema-valid, human-readable, never
  half-written.
- **Test-run economy:** run the full suite **once**; if it regresses, iterate on **just the failing tests**;
  then **one** confirming full run. *Never loop on the full suite: a failing full run dumps output the agent
  must re-read every cycle, which is where test tokens actually burn.*
- The contract commit and the implementation commit are **separate commits on one branch**, contract first
  (`prd.md:161`, G16).

**Expected footprint.** `src/issueforge/engine.py`, `repair.py`, `workspace.py` (+commit), `verify.py`,
`store.py` (counters); `tests/test_engine.py`, `test_repair.py`.

**Dependencies.** Blocked by **S13**. Unblocks **S15**.

**Deterministic / AI / Human.** **Deterministic:** the Git boundary, both repair budgets, the store writes, the
authoritative run, the terminal record. **AI:** writing the implementation. **Human:** pause on cap exhaustion.

**Human approval points.** None (S15 owns readiness; S12 owned the contract). A cap exhaustion **pauses for a
human**, which is not an approval.

**Failure & recovery.** `reset_worktree` **verifies the base sha is a real commit before any reset.** Cap
exhaustion → a valid terminal record + pause.

**Logging & observability.** Required (AI + subprocess + filesystem).

**Prior-art and source audit**
- *Sources:* `build_recovery.py` — **all five functions, verified**: `next_action(attempt, cap=2)` (:98-104,
  1-based, cap is a **parameter** not a constant), `build_retry_prompt` (:73-95 — **`:85` is literally
  `_ = transcript`; the arg is accepted and DELIBERATELY DROPPED**, with context-rot named as the reason),
  `escalate_run` (:115-153), `record_attempt` (:156-181), `reset_worktree` (:36-71 — **verifies the base sha
  with `rev-parse --verify --quiet <sha>^{commit}` at :50 and RAISES at :55-56 before `reset --hard`**);
  **both `escalate_run` (:153) and `record_attempt` (:181) write through `agent_runs_lib.apply_run` — i.e.
  INSIDE THE STORE LOCK. `apply_run` was ADDED to `agent_runs_lib` precisely because the AI review caught a
  raced retry-counter in this very module** (module docstring :19-20). That provenance is the direct ancestor
  of US-6.3 and must be cited in the audit.
- *Preserve:* context-rot avoidance (**the dropped transcript**); **the counter inside the lock**; cap
  exhaustion → a valid terminal record; the full-suite regression as a separate gate; the **verbatim**, never
  paraphrased, proof command; test-run economy.
- *Refactor/extract:* **`build_recovery.py`'s five functions behind a `RepairPolicy` seam. The module's own
  docstring says it was built as exactly this seam.** `next_action` and `build_retry_prompt` port near-verbatim;
  `record_attempt` / `escalate_run` re-point at IssueForge's store. **Port `tests/test_build_recovery.py`.**
- *Replace:* **one cap → two.** MARVIN has a single `cap=2` and its provenance **warns that one counter hides
  two different failures**. The PRD (D4) now **splits** them. **This is the one place where MARVIN's lesson was
  adopted INTO the PRD rather than followed from it** — record it as such.
- *Discard:* MARVIN's `needs-review`-as-escalation workaround (IssueForge has a real `paused` state) and the
  `unmeasurable` cost waiver `escalate_run` attaches.
- *Test provenance:* `tests/test_build_recovery.py` ports with the `RepairPolicy` seam.

**Out of scope.** **Pushing and opening the PR (S16).** The readiness gate and code review (S15).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** Planned: `tests/test_repair.py`.

---

## S15 — Readiness gate: implementation write scope, independent code review, human override
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem.** Without an override, **a probabilistic reviewer holds an unappealable veto over a deterministic
workflow**: a false-positive blocking finding consumes the repair budget failing to "fix" a non-problem, the
run pauses, resuming returns to the same unmet gate, US-7.1 forbids opening the PR — and **US-7.3's merge
authority is unreachable because the PR never opens.** The only remaining path is routing around the product.
**With** an override, the risk is cultural: it gets used whenever review is inconvenient. The friction is the
design.

**User-visible outcome.** A composite gate decides PR-worthiness. A blocking review finding can be overridden
by a human — per finding, on the record, and **still reported in the PR**.

**PRD criteria covered.** **US-3.7, US-6.4, US-6.5** (owner). Gap G15.

Integration assertions: US-6.7, US-6.8, US-6.9 (S8/S9's predicates and verdict, enforced here).

**Observable acceptance criteria**
- **US-6.4 — PR readiness requires ALL of:** green acceptance tests; **green full baseline (a SEPARATE gate —
  this slice may not regress any previously-passing test)** — both run in the **S6-provisioned hermetic
  environment** (`provision_environment`, F2), not the implementer's; configured quality gates; the **two D5 scope
  questions over the implementation commit range** — (a) **every change lies inside the approved implementation
  write scope**, and (b) **no frozen contract path, nor the pinned identity/version of any frozen external
  dependency, changed** (S13); and an independent code review with **no blocking findings except findings
  explicitly overridden under US-6.5**. Plus the **observability reconciliation** below.
- **The observability reconciliation is DETERMINISTIC and NON-OVERRIDABLE.** The gate runs S8's
  `classify_diff(diff)` over the **actual** diff and compares it against the **approved verdict** from S9
  (US-6.7). **A boundary crossing the diff introduces that the approved verdict did not anticipate HALTS the
  run and routes to S9's observability/buildability amendment**: recompute with S8, obtain a fresh
  non-overridable reviewer confirmation and renewed human approval, then rerun readiness. It never routes to
  S13's acceptance-contract amendment. It is **not** an AI review finding
  and **US-6.5's override cannot waive it** — `prd.md:80` says the override *"can never waive… **deterministically
  established observability and sensitive-data requirements**."* Routing this through the reviewer would make
  the override a legal path to ship an unlogged boundary crossing. **Also deterministically verified here — and
  the EVIDENCE for each is named, not left as "verified":**
  - **required success/failure events emitted** — evidence: S8's authoritative runtime capture observes every
    named event on the exercised success and failure paths. Static call-site presence is supplementary only;
  - **the target project's logging conventions followed (US-6.9)** — evidence: captured events match the
    detected level/format/correlation-id contract, while static inspection confirms the diff does not introduce
    a new `logging.getLogger` root or `basicConfig`;
  - **no contract-listed sensitive value appears in emitted output** — evidence: seeded sensitive-field
    canaries traverse the authoritative run and S8's runtime exclusion predicate finds zero canary or listed
    sensitive values in captured logs.

  **Anything that cannot be reduced to one of these deterministic checks — the qualitative "is this
  diagnosable?" judgment (`architecture.md:91`) — is explicitly the AI reviewer's, and is THEREFORE
  OVERRIDABLE under US-6.5.** The load-bearing split: the three checks above are deterministic and
  **non-overridable**; the residual diagnosability judgment is named as the reviewer's and may be overridden.
- **The approved implementation write scope is the one a human approved in S9. A test asserts it is NEVER
  derived from the diff** — *a scope derived from the diff approves itself.* The scope check runs over the
  **implementation commit range only** (D5), and the disjoint frozen-contract-path check is question (b) above.
- **US-3.7 — an implementation that writes OUTSIDE the approved implementation write scope PAUSES the run.**
  **Expanding the scope requires NEW human authorization**, and **the prior approval is preserved in the audit
  trail**. Before recording the expanded scope, resolve every added file or pattern against the **current frozen
  contract set** and fail on any overlap; the rejected expansion leaves the approved scope byte-identical.
  Defined, tested behavior for `add` / `modify` / `delete` / `rename` (S9's path-operation vocabulary).
- **The code review runs on a fresh session, against the local candidate sha** (never a pushed branch — S14
  does not push), with execution capability and all inputs materialized to disk (S7).
- **G15 — the reviewer is EXPLICITLY INSTRUCTED to look for test-context-dependent behavior** (`prd.md:158`):
  an implementation branching on a test-runner environment variable, a test-binary suffix, a worker variable,
  or a parent-process name **defeats every static check**, including file hashing and import-closure analysis.
  **This is the accepted residual risk, and this reviewer is the control that carries it. It is not eliminated.**
- **US-6.5 — the override:**
  - **human-only** (never the implementer AI, the reviewer AI, the engine, or the repair loop);
  - **only after ONE fresh independent same-provider review attempt** — so a recoverable session failure is
    distinguished from a conscious acceptance of risk;
  - **per finding. A blanket "ignore review" action DOES NOT EXIST** (asserted by a test);
  - **bound to the exact reviewed head sha; ANY subsequent code or contract change INVALIDATES it**;
  - **it can NEVER waive** contract integrity, acceptance tests, the full baseline, configured quality gates,
    the approved implementation write scope, or deterministic observability/sensitive-data requirements (each
    asserted separately);
  - the permanent audit trail records the **human identity, the commit, the reviewer sessions and verdicts,
    each overridden finding, the rationale, and the acknowledged risk**;
  - **override means the PR MAY OPEN, not that the finding is erased.** Every overridden finding **stays
    visible in the PR** for renewed human consideration before merge. **It does NOT authorize the merge**
    (US-7.3 is unchanged).
- A **reviewer execution failure** is overridable on the same terms (US-6.5) — but **empty output or a non-zero
  exit is a FAILED review, never a pass** (S7).
- **REDACTION CANARY (this producer's paths, S4 API).** A synthetic secret seeded into the **code-review packet**
  (the implementation diff handed to the reviewer, the reviewer response, and captured stderr) must appear in
  **ZERO** persisted artifacts after S4's redacting writer. **Both paths:** the **success** path (a clean review
  verdict persists) and the **failure** path (a FAILED/timed-out review, and an override record — which persists
  the finding text and rationale). *(v2-report mechanical fix 1.)*

**Expected footprint.** `src/issueforge/engine.py` (+readiness), `review.py`, `scope.py`, `verify.py`
(+quality gates); `tests/test_readiness.py`, `test_review.py`, `test_scope.py`.

**Dependencies.** Blocked by **S14**, **S8**. Unblocks **S16**.

**Deterministic / AI / Human.** **Deterministic:** every gate predicate, the scope check, the override's
structural constraints (human-only, per-finding, sha-binding, the non-waivable list), the audit record.
**AI:** the code review itself. **Human:** authorizing a scope expansion; issuing an override.

**Human approval points.** **Two:** authorizing a **scope expansion** (US-3.7); issuing a **per-finding
override** (US-6.5).

**Failure & recovery.** Any failed gate pauses, naming the predicate. An out-of-scope write pauses. An
overridden finding does not clear the gate silently — it is recorded and surfaced.

**Logging & observability.** Required (AI). Every verdict and override is **permanent**.

**Prior-art and source audit**
- *Sources:* `spec-dev/SKILL.md:347-391` (the cross-review gate); `validate_agent_runs.py:123-179`;
  `context/agent-contract.md`; `docs/provenance/marvin/harness-phase2-step-classification-2026-07-10.md`
  (*"prose-encoded failure recovery the runner must own"*); `docs/provenance/marvin/pipeline-eval-2026-07-07.md`
  (the amendment/escape-hatch finding).
- *Preserve:* fail-loud on empty/non-zero review output; a sha-bound verdict; an override as a **first-class
  recorded event**; the full-suite regression as a **separate** gate.
- *Refactor/extract:* nothing directly — **MARVIN's review gate is a bash invocation inside SKILL.md prose, and
  MARVIN has NO code-review override mechanism at all.**
- *Replace:* the composite readiness gate and the override — **net-new.** *(Note the provenance honestly:
  **the override is not a MARVIN safeguard. It is a PRD amendment (D2) forced by a deadlock analysis.** The
  PRD granted it at `prd.md:153` and the prior decomposition deleted it on a reviewer's false claim.)*
- *Discard:* MARVIN's `cross_review` cost-tuple coupling.
- *Test provenance:* none ported; the gate is net-new.
- **Risk, named:** *the failure mode is cultural. Once an override exists, it gets used whenever review is
  inconvenient.* The per-finding rationale, the sha binding, the permanent audit, and the PR disclosure add
  friction **deliberately**. They reduce that risk; **they do not eliminate it.**

**Out of scope.** Pushing and opening the PR (S16). The **test-contract** review and its override (S11 — a
different gate).

**Route into acceptance-test authoring.** `route:spec-up` — **mandatory.** Planned: `tests/test_readiness.py`.

---

## S16 — One green PR — pushed only after the gate, verified at origin, never merged
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem.** A PR is meaningful only if its head is actually **at origin** and its base is the **default
branch** — and only if it was **never pushed before the gate passed** (US-7.1).

**PRD criteria covered.** **US-7.1, US-7.2, US-7.3, US-7.4** (owner). Gap G10.

**Observable acceptance criteria**
- **Push and PR-open happen automatically ONLY after ALL readiness gates pass** (US-7.1). **This is the only
  slice in the system that pushes.**
- **The head is pushed and VERIFIED at origin** (`git rev-parse origin/<branch>` contains the sha) **before the
  PR is opened.** *Agents report "pushed" when the push silently failed.*
- **The base is the repository default branch**, enforced by a **reusable PURE predicate** suitable for local
  **and** required-CI enforcement: **EXACT, case-sensitive string equality** — no substring, no prefix, no
  strip. `gh baseRefName` yields the short branch name, so a qualified or near-miss ref is not the expected
  input and **rejects by default**.
- **US-7.2 — the PR body reports:** the approved contract commit, the integrity verdict, red/green evidence,
  the verification summary, **AI review verdicts, AND overrides** (each overridden finding + rationale, for
  renewed human consideration before merge), **and logging added / reused / intentionally unnecessary (G10)**.
  The body is **assembled from recorded evidence, never narrated by an AI**.
- **US-7.3 — IssueForge NEVER merges. No merge or approve call exists in the gateway — BY CONSTRUCTION.**
  (Test: assert the string `pr merge` appears **nowhere** in the source.) *This is stronger than a skill's prose.*
- **US-7.4 — the run persists `waiting-for-merge`; attached watch mode and a later `continue` observe the SAME
  persisted state.** **Watch mode is a READ-ONLY observation of merge state and performs NO mutation.**
- **The gateway takes `(owner, repo, number)` as an INDIVISIBLE identity. No API accepts a bare number** — a
  cross-repo reference can never be reduced to a number and resolved against the current repo.

**Expected footprint.** `src/issueforge/github.py` (+PR write side), `engine.py`, `cli.py` (+watch);
`tests/test_github.py`, `test_engine.py`.

**Dependencies.** Blocked by **S15**. Unblocks **S17**, **S23**.

**Deterministic / AI / Human.** All deterministic — the PR body is assembled from recorded evidence.
**Human: THE MERGE — absolute authority.**

**Human approval points.** **The merge** (outside IssueForge, on GitHub). IssueForge waits.

**Failure & recovery.** A failed push **halts before the PR is opened**. A non-default base is refused.

**Logging & observability.** Required (third-party boundary via `gh`).

**Prior-art and source audit**
- *Sources:* `check_build_pr_base.py` (**verified: 68 lines, not ~100**; `check_build_pr_base(base_ref,
  default_branch="main") -> tuple[bool, str|None]` at :27-45; **exact case-sensitive equality at :37**; imports
  only `argparse` + `sys`) and its tests; `merged_runner.py:13` (the by-construction no-merge boundary),
  `:163-181`; `docs/provenance/marvin/harness-phase1-step-classification-2026-07-10.md`.
- *Preserve:* **human merge authority is absolute.** No PR opened unless its head is **pushed and verified at
  origin** and its base is the default branch. **Merge state comes from `gh pr view`, never from the invocation
  wording — a human saying "I merged it" is not evidence.**
- *Refactor/extract:* **`check_build_pr_base` — extract verbatim; it is the cleanest port target in MARVIN.**
  ~20 lines of logic, pure, unit-testable with no live `gh`. **One change: `default_branch` currently DEFAULTS
  to `"main"` — make it REQUIRED**, consistent with S3's "no `or 'main'` fallback exists anywhere."
- *Replace:* PR-body assembly — net-new, from the manifest.
- *Discard:* MARVIN's stacked-PR / wave concepts — **v1 is single-run, so there IS no stack and the stranding
  cannot occur by construction.** Keep the base predicate anyway as a cheap regression guard.
  `_build_report_data` **as designed** — it emits data only *because MARVIN's runner is called by an AI
  session*. **IssueForge's CLI/TUI renders its own report.**
- *Test provenance:* `check_build_pr_base`'s tests port verbatim with the predicate.

**Out of scope.** Closeout (S17/S18/S19). Draft PRs (v2 — a `draft: bool` kwarg plus one state).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_github.py`.

---

## S17 — Delivery verification: exact merge-commit + head-sha binding
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem.** *"Content, not ancestry"* is a policy slogan, not an executable predicate, until the **exact**
GitHub merge commit and the **recorded PR head sha** are bound into it. **`state == MERGED` is NOT delivery
proof.**

**User-visible outcome.** A single auditable verdict: this run's work **is** (or **is not**) delivered on the
default branch — **with the SHAs that prove it**.

**PRD criteria covered.** **US-8.1** (owner). **This is the global destructive stop; S18 and S19 depend on it.**

**Observable acceptance criteria**
- The predicate is bound to **named, authoritative GitHub fields**: the PR's `mergeCommit.oid`, its
  `headRefOid` **as recorded in the run manifest at PR-open time** (not re-read later), `baseRefName`, and
  `state`. **Which fields are checked is written down, not implied.**
- **Delivery proof = the merge commit is reachable from `origin/<default>`**, via
  `git merge-base --is-ancestor <mergeCommit> origin/<default>`, which is **TRI-STATE: exit 0 = reachable;
  exit 1 is the ONLY trustworthy negative; anything else is an ERROR → HALT as `verification-failed`, never
  "not reachable".**
- **The freshly-merged squash may not be in the local object store yet** — `merge-base` errors **128 on a
  genuine clean merge** until you fetch. **Fetch ONCE, retry ONCE, on the ERROR path only, never on the clean
  exit-0 path. A failed fetch HALTS** (*a failed fetch must not be read as a successful sync*). **Without this
  the runner false-alarms on every fresh merge; with a naive `!= 0 → unreachable` it reads a clean merge as a
  STRANDING, which is worse.**
- **Missing or disagreeing facts are DEFINED, not undefined:** no `mergeCommit` on a MERGED PR; a `headRefOid`
  that no longer matches the manifest; a `baseRefName` that is not the default branch. **Each halts with its
  own named verdict; none is inferred as success or as failure.**
- **A failed `gh pr view` is `verification-failed`, NEVER "not merged"** — *that would let a transient network
  failure clear the way to delete a branch.*
- **Only a PROVEN delivery permits the destructive cleanup in S19.**

**Expected footprint.** `src/issueforge/github.py` (+verification), `workspace.py` (reachability);
`tests/test_github.py`.

**Dependencies.** Blocked by **S16**. Unblocks **S18**, **S19**.

**Deterministic / AI / Human.** All deterministic. No AI. **Human:** resolve any halt.

**Human approval points.** None — the verified merge **is** the consent (US-8.2).

**Failure & recovery.** Every anomaly **halts this stage** and surfaces with evidence. Nothing auto-resolves.

**Logging & observability.** Required. The verdict and its SHAs are **permanent**.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:163-181` (`_pr_view` — non-zero **or non-dict JSON** → `None`), `:181-190`
  (→ `halt` / `verification-failed`), `:214-234` (**the bounded single fetch-retry — verified: fetch once on
  the `error` path only; a failed fetch halts at :216-224; still-error after retry halts at :226-234**),
  `:283-291` (the fetch), `:291-311` (`_reachability` — **verified tri-state; exit 1 is the only trustworthy
  negative**), `:237-262` (the stranded-squash halt), `:313-333` (`_branches_containing` — **a failed
  `git branch --contains` cannot be read as "no branches"; that would DROP branches from the stranded-squash
  report**).
- *Preserve:* **every tri-state posture above.** **The stranded squash is the most-repeated failure in MARVIN's
  history — it recurred in three separate waves in a single week.**
- *Refactor/extract:* `_pr_view`, `_reachability`, and the fetch-retry behind the gateway, **with their tests**.
- *Replace:* the SHA-binding contract above is **net-new precision** over MARVIN's project-level checks.
- *Discard:* the stranded-squash **recovery** machinery — **v1 is single-run, so no PR can be stacked on a
  feature base and the stranding cannot occur.** Keep the **halt** as a cheap backstop for out-of-band merges.
- *Test provenance:* `merged_runner`'s reachability and fetch-retry tests port with the predicates.

**Out of scope.** Closing issues (S18). Deleting anything (S19).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_github.py`.

---

## S18 — Closeout: comment, close the EXACT run issue, update the parent epic; idempotent
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem (review-02 defect A, still live).** US-8.2 requires closing **the exact run issue**. Draft v2 closed
only formal `closingIssuesReferences`. **A PR with NO closing reference leaves the run issue OPEN. A PR with
MULTIPLE closing references can close issues OTHER than the run issue.** And MARVIN's own `close_issues`
**never checks the `gh` return code** — a transient failure silently becomes *"there were no issues to close"*,
**after** the destructive steps already ran.

**PRD criteria covered.** **US-8.2, US-8.4** (owner).

**Observable acceptance criteria**
- **Closeout operates on the PERSISTED, REPOSITORY-QUALIFIED RUN-ISSUE IDENTITY** — `(owner, repo, number)`,
  recorded at enqueue. **`closingIssuesReferences` may be VERIFIED and REPORTED, but can never SUBSTITUTE for
  that identity.** (Test both failure modes: a PR with no closing reference still closes the run issue; a PR
  with a reference to a *different* issue does **not** close that issue.)
- **A failed read of the closing references is a HALT, never "there were no issues to close."** *(This is the
  one unguarded failed read in MARVIN's runner. Extract with the fix.)*
- **Comment FIRST, then close. A failed comment does NOT close.** *An issue closed with no linkage comment
  violates the contract — record the failure and leave the issue open for a retry.* **The ordering is
  load-bearing and is what makes the retry safe.**
- **The parent epic is updated, idempotently, WITHOUT another approval** (US-8.2 — the verified merge **is** the
  consent). Defined, tested behavior for: **no parent** (a recorded no-op); **a failed epic read** (**halt** —
  never assume "no parent"); **repeated closeout** (no duplicate epic comment or edit).
- **Every mutation is repository-qualified.** *A bare `gh issue close 148` closes issue 148 in whatever repo the
  cwd happens to be.* **No API in the gateway accepts a bare number.**
- **US-8.4 — idempotence.** Repeated closeout produces the same completed state, **no duplicate comments, no
  failures.** **This slice's idempotence predicate covers ITS OWN mutations only** (already-commented,
  already-closed, epic-already-updated). **It does NOT key on branch/worktree absence — that is S19's, an
  independent sibling.** *(Draft v2 keyed S18's idempotence on cleanup state it did not own.)* Whole-closeout
  idempotence across S18 **and** S19 is the **engine's** coordination, asserted in an integration test.
- **Honor every return code.** Never report success on a command whose exit you did not check.
- **Closeout runs BEFORE destructive cleanup (S19).** *This deliberately INVERTS MARVIN's order, which closes
  issues LAST, after branches and worktrees are already deleted.* **Closing an issue is reversible; deleting a
  branch is not.**

**Expected footprint.** `src/issueforge/github.py` (+closeout), `engine.py`; `tests/test_github.py`.

**Dependencies.** Blocked by **S17**, and by **S20** for the gateway's write side. Unblocks **S19**.

**Deterministic / AI / Human.** All deterministic. No AI. **No approval — by PRD** (US-8.2 says so explicitly).

**Human approval points.** **None.** The verified merge is the consent.

**Failure & recovery.** A partial write (comment ok, close failed) is retryable with **no duplicate comment**.
A failed reference read halts **before** anything destructive.

**Logging & observability.** Required (third-party). Each outcome is **permanent**.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:507-565` (`_Closeout`), **`:509-515` (the UNGUARDED failed read — `res.returncode`
  is never checked; `info = {}` → `refs = []` → `action: "clean"`, exit 0, AFTER the branch and worktree were
  deleted at :841-858)**, `:528-546` (**comment-before-close, verified: a failed comment `continue`s and never
  closes**), `:544-551` (`gh issue close` return code honored), `:566-576` (`_prose_referenced_issues`),
  `:523-525` + `:534-546` (**cross-repo identity LOST — reads only `ref["number"]` and closes with
  `--repo project["repo"]`**), `:806-825` (the three-way idempotence no-op key);
  `agent_runs_lib.close_run_for_pr:423-453` (idempotence: exact match; only one status flips; every other case
  a byte-unchanged no-op).
- *Preserve:* **comment-before-close**; honoring every return code; `close_run_for_pr`'s guard semantics.
- *Refactor/extract:* `_Closeout` behind the gateway — **WITH THE TWO FIXES** (guard the reference read; carry
  `(owner, repo, number)`). `close_run_for_pr`'s **guard semantics** → IssueForge's idempotent closeout.
  Port `tests/test_close_run_for_pr_690.py`.
- *Replace:* **the parent-epic update — NET-NEW ENGINE POLICY, no MARVIN prior art.** Verified: `grep -i
  "epic\|parent"` over `merged_runner.py` and `merged/SKILL.md` returns **zero** matches. *(Draft v2 implied the
  closeout chain included an epic step. It does not.)* **And the ordering inversion** (closeout before cleanup)
  is a **deliberate reordering**, recorded as such, not an extraction.
- *Discard:* the `--project marvin` default; **`flip_run_record_for_pr`'s write into MARVIN's `agent-runs.json`
  store — extracting closeout wholesale would take `agent_runs_lib._repo_lock` and WRITE MARVIN'S STORE. That
  is the sharpest US-11.6 violation vector in the codebase.**
- *Test provenance:* `test_close_run_for_pr_690.py` ports with the idempotence guard.

**Out of scope.** Branch and worktree deletion (S19).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_github.py`.

---

## S19 — Safe cleanup: branches and worktrees (an INDEPENDENT stage result)
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem.** **Test health and cleanup safety are INDEPENDENT stage results.** Draft v1 said "do not port
MARVIN's blanket halt" and then wrote *"every anomaly HALTS"* — **recreating the exact coupling it had just
discarded.** Only failed **delivery verification** (S17) is a global destructive stop. A post-merge **health**
failure stays **loud and non-zero** but **must not veto independently-safe cleanup whose own predicates passed.**

**PRD criteria covered.** **US-8.3** (owner).

**Observable acceptance criteria**
- **Nothing is deleted without POSITIVE PROOF from S17.** The delete predicate is **content reachability of the
  exact merge commit**, never ancestry — under squash merges **no** feature branch is ever ancestry-merged, so
  `git branch -d`'s self-guard **never fires** and someone reaches for **`-D`, which is unguarded.**
- **No delete primitive exists that is not gated on a SUCCESSFUL stacked-PR discovery returning empty.** A
  **failed** `gh pr list --base` is **NOT** "no stacked PRs, safe to delete". Order: **discover → retarget →
  the `gh pr edit` must exit 0 BEFORE the verify runs** (*a failed edit paired with a stale `gh pr view` still
  reporting the default branch could otherwise clear the way to delete the base out from under an open PR*) →
  **read the new base back and confirm it equals the default → only then delete.**
  *(A plain ref-delete on a branch with open stacked PRs **CLOSES all of them** — this closed six PRs in MARVIN
  on 2026-07-08. GitHub auto-retargets **only** via the merged PR's "delete branch" button, never via
  `git push --delete`.)* **v1 is single-run so a stack should be impossible; the guard stays as the backstop
  for out-of-band state.**
- **Remote presence is read AUTHORITATIVELY:** `git ls-remote --exit-code --heads origin <branch>`, keyed on
  the exit code — **2 = authoritatively absent; anything else (including 128, origin unreachable) = treat as
  PRESENT**, *so a transient remote error never produces a false no-op that silently skips an orphaned remote
  branch.* **`refs/remotes/origin/<b>` is a CACHE and is NOT authoritative.**
- **Dirty or unverifiable worktrees are PRESERVED and reported.** A failed `git status --porcelain` is **NOT
  "clean"** — *that would remove a worktree whose real state is unknown, discarding possible uncommitted work.*
  A failed `git worktree list` is **NOT "no worktree"**. **A failed `worktree prune` after a successful
  `remove` is a PARTIAL result, not a clean removal.**
- **Never** `reset --hard`, `clean -fd`, or `worktree remove --force` on an unverified tree.
- **Cleanup emits its OWN stage result and exit status**, separable from test-health results, **with explicit
  acceptance tests for the independence.**

**Expected footprint.** `src/issueforge/workspace.py` (+cleanup), `github.py`; `tests/test_workspace.py`.

**Dependencies.** Blocked by **S17**, **S18**. Unblocks **S23**.

**Deterministic / AI / Human.** All deterministic. No AI. **Human:** resolve any preserved/flagged item.

**Human approval points.** None.

**Failure & recovery.** **Preserve-and-report is always the safe direction.** Recovery for an
accidentally-deleted branch with open PRs: `git push origin <tip-sha>:refs/heads/<branch>` → `gh pr reopen` →
`gh pr edit --base <default>` → delete again.

**Logging & observability.** Required. Each outcome is a **permanent** event.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:342-385` (`_BranchCleanup.cleanup_branch` — **verified order: content gate at
  :344-350 → discover at :358 → verify each retarget at :367-373 → only then delete**), `:387-437`
  (`_retarget_stacked_prs` — **`gh pr edit --base` return code checked at :426-428 BEFORE the read-back at
  :429-437; a failed edit records `verified: False` and SKIPS the read-back, so a stale view cannot
  rubber-stamp it**), `:400-409` (**a failed or non-list `gh pr list` → `discovery_ok=False` → flag, ZERO
  deletions**), `:440-446` (the deletes), `:456-497` (`_WorktreeCleanup` — status/remove/prune return codes all
  honored), `:735-761` (`_remote_branch_present` — **verified: `return res.returncode != 2` at :760**),
  `:763-781` (`_worktree_for_branch` returns `(path, ok)` **precisely so absence and failure are
  distinguishable**); `merged/SKILL.md:78,113` (the six PRs closed on 2026-07-08 and the restore-by-sha recovery).
- *Preserve:* **every tri-state posture above.** The retarget→verify→delete order. The recovery procedure.
- *Refactor/extract:* **`_WorktreeCleanup`, `_BranchCleanup`, and the three presence predicates behind a
  `CleanupPredicate` interface — they take only a project and a runner, so coupling is near-zero. This is the
  largest faithful extraction in the project.** Port their fixtures — **without MARVIN's PENDING-on-main
  convention.**
- *Replace:* the **stage-independence model** (above).
- *Discard — ⚠ ANTI-PORT:* **`merged_runner.py:~827-837` — the halt on any gate anomaly. The transfer ledger has
  ALREADY ruled this a defect.** *"Extracting `merged_runner` faithfully would import a known bug."*
  *(Precision, correcting draft v2: it is **per-PR**, and it halts on **any** gate anomaly, not on red-main
  specifically. Say what it is.)* Also `_DOCS_ONLY_PREFIXES = ("state/",)` (:623) and the `state/projects.md`
  resolution. The `no-test-command` anomaly **cannot occur** — S3 makes the baseline mandatory at registration.
- *Test provenance:* `_WorktreeCleanup` / `_BranchCleanup` fixtures port with the predicates.

**Out of scope.** Issue and epic mutation (S18).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_workspace.py`.

---

## S20 — Shape an issue: in-place revision + approved GitHub mutation plan
**Labels:** `v1` `phase:2` `route:spec-up`

**Problem.** A vague issue produces a vague contract. **S9 already classified it and got its implementation
write scope approved**; this slice proposes the **in-place revision** and performs the **first GitHub mutation
in the system** — behind a human gate.

**PRD criteria covered.** **US-3.1** (owner).

**Observable acceptance criteria**
- **US-3.1:** a buildable issue receives a **proposed in-place revision**, and **NO GitHub write occurs before
  human approval** (test: `gh` is never invoked in write mode pre-approval).
- The mutation plan is a **list of dicts** — `{"op": "update_body", "issue": (owner, repo, 148), ...}` — with
  `apply(plan, gateway)` dispatching on `op`. Four ops cover v1: `update_body`, `create_issue`, `add_comment`,
  `link_child`. **No visitor pattern, no op classes.**
- **Every op is repository-qualified** — `(owner, repo, number)`, never a bare number.
- Idempotency is keyed on **persisted mutation-operation IDs**, not on titles (see S21).

**Expected footprint.** `src/issueforge/shaper.py` (+revision), `github.py` (+write ops), `engine.py`;
`tests/test_shaper.py`, `test_github.py`.

**Dependencies.** Blocked by **S9** (the classification and the approved scope) and **S4** (the run store +
the gateway's read side). **It BUILDS the gateway's WRITE side**, which S18 and S21 then reuse.
**Unblocks S10** (a `buildable` run cannot author a contract until its approved revision is applied),
**S21**, and **S18**.
*(Corrected: draft v3 round 1 blocked this on S18, which put the first GitHub mutation AFTER closeout and left
S10 reachable with the revision never applied.)*

**Deterministic / AI / Human.** **Deterministic:** the mutation plan, the pre-approval write ban, op dispatch.
**AI:** the proposed revision text. **Human: EVERY GitHub mutation.**

**Human approval points.** **The in-place revision, before any GitHub write.**

**Failure & recovery.** A partial write is resumable via the persisted operation IDs, with no duplicates.

**Logging & observability.** Required (AI + third-party).

**Prior-art and source audit**
- *Sources:* `spec-up/SKILL.md:55-61`; `issues_to_findings.py:69-113`; `skills/prd-to-issues/SKILL.md`.
- *Preserve:* nothing is written to GitHub before approval.
- *Refactor/extract:* nothing new beyond S9's `FootprintExtractor`.
- *Replace:* the revision proposal and the mutation-plan dispatcher.
- *Discard:* the HITL/AFK label taxonomy; the `ACCEPT:` satellite-issue pattern (**GitHub-as-database — a
  workaround for having no run store; IssueForge has a manifest**).
- *Test provenance:* none ported.

**Out of scope.** Epic decomposition (S21). Readiness classification and the file-scope approval (S9).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_shaper.py`.

---

## S21 — Epic decomposition of an oversized issue
**Labels:** `v1` `phase:5` `route:spec-up`

**Problem.** S9 stops an oversized issue at `decomposition_required`. This slice processes it — the hardest AI
judgment surface in the system, built **last**, when every downstream consumer (queue, gateway, closeout) already
exists. **Until it lands, an oversized issue HALTS with a named reason** — honest fail-closed behavior, never a
silent downgrade to `buildable`.

**PRD criteria covered.** **US-3.2, US-3.3** (owner).

**Observable acceptance criteria**
- **US-3.2:** an oversized issue receives a **proposed epic + independently deliverable child issues**. **No
  issue is created or edited before approval.**
- **US-3.3:** approved decomposition **links every child from the epic**, and **each child enters the normal
  queue independently.**
- Children are **vertical tracer bullets**, not horizontal layers. **A slice is demoable on its own.**
- **Idempotency is keyed on PERSISTED MUTATION-OPERATION IDs and created-issue identities — NOT on child
  titles.** *Titles are not durable idempotency keys: they collide and they get edited.* **A partial GitHub
  write is resumable with NO duplicate children** (tested by killing the process mid-plan and re-running).

**Expected footprint.** `src/issueforge/shaper.py` (+decomposition), `github.py` (+`create_issue`,
`link_child`), `store.py` (operation ids); `tests/test_shaper.py`.

**Dependencies.** Blocked by **S20** — **for its mutation-plan/apply machinery and the gateway write side
ONLY**, not because an oversized issue must first succeed as a buildable one. *(An `oversized` run never
touches S20's in-place-revision path; it branches to this slice directly out of S9.)*

**Deterministic / AI / Human.** **Deterministic:** the mutation plan, the pre-approval write ban, epic↔child
linking, queue admission, operation-id idempotency. **AI: the decomposition judgment.**
**Human: every created or edited issue.**

**Human approval points.** **The proposed epic and every child, before any GitHub write.**

**Failure & recovery.** A resumable partial write, with no duplicates.

**Logging & observability.** Required (third-party).

**Prior-art and source audit**
- *Sources:* **`skills/prd-to-issues/SKILL.md` — this IS the decomposition procedure, written down**;
  `skills/findings-to-issues/SKILL.md`; `skills/write-a-prd/SKILL.md`; `spec-up/SKILL.md:34,38,81` (epic
  routing). *(Correction to draft v2, which claimed **"no decomposition code in MARVIN, nothing to port"** —
  true of *code*, false of *procedure*. The tracer-bullet rule, the demoable-slice rule, and the epic-routing
  triage are real, load-bearing prior art.)*
- *Preserve:* **vertical tracer bullets over horizontal layers; a slice is demoable on its own; children are
  independently grabbable.**
- *Refactor/extract:* nothing in code — the procedure transfers, the prose does not.
- *Replace:* the decomposition engine (net-new code implementing a proven procedure).
- *Discard:* the HITL/AFK taxonomy; `wave:N` / `serialize:<hotfile>` labels (wave scheduling — v1 is
  single-run; `schedule_waves.py` stays in MARVIN as prior art if concurrency is ever un-deferred).
- *Test provenance:* none ported.

**Out of scope.** Everything in S9 and S20. Wave scheduling (v2).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_shaper.py`.

---

## S22 — Retention and `issueforge purge`
**Labels:** `v1` `phase:5` `route:direct-tdd`

**PRD criteria covered.** **US-10.2, US-10.4** (owner). Integration assertion: US-10.1.

**Observable acceptance criteria**
- **US-10.2:** redacted prompts, responses, full command output, diffs, and review packets **expire after 30
  days by default.**
- **Permanent artifacts SURVIVE** (integration assertion on US-10.1, owned by S4): transitions, approvals,
  overrides, commit/PR ids, contract manifests, verification summaries, cleanup outcomes. **`purge` never
  removes a manifest.**
- **US-10.4:** retention is **configurable**; `purge` **never damages an active run.**
- Retention is **ONE number** (`detailed_retention_days = 30`), not a per-artifact-class policy table.
- **Purge is idempotent**, and a crash mid-purge leaves the store valid.
- **The age logic takes an INJECTABLE `now`**, so expiry is testable without touching the wall clock.

**Expected footprint.** `src/issueforge/retention.py`, `cli.py` (+`purge`); `tests/test_retention.py`.

**Dependencies.** Blocked by **S4**.

**Deterministic / AI / Human.** All deterministic. No AI. No approval (`purge` is explicitly invoked).

**Human approval points.** None.

**Failure & recovery.** Idempotent; a crash mid-purge leaves the store valid.

**Logging & observability.** Required (filesystem). Each purge outcome is **permanent**.

**Prior-art and source audit**
- *Sources:* **`scripts/prune_plan_files.py` — verified: `DEFAULT_MAX_AGE_DAYS = 30`, `SECONDS_PER_DAY`,
  `find_stale_plan_files(root, max_age_days, now=None)` with an INJECTABLE `now` so "the age logic is testable
  without touching the wall clock"** (its own docstring), and a documented permanent/ephemeral split: these are
  *"local audit-trail artifacts whose durable record lives in the PR, the issue, and the run store."*
  **This is a DIRECT PORT TARGET.** *(Correction to draft v2, which claimed **"MARVIN has NO retention; its
  store grows forever."** That is false — `prune_plan_files.py` is exactly a 30-day retention sweep.)*
- *Preserve:* the **permanent/detailed split** and the rationale for it. The injectable clock. **Never purge a
  manifest.**
- *Refactor/extract:* **`find_stale_plan_files`'s age logic and injectable-clock signature port near-verbatim**;
  only the glob set and the root resolution change.
- *Replace:* the artifact taxonomy (IssueForge's classes are prompts/responses/output/diffs/review packets, not
  plan files).
- *Discard:* the MARVIN-specific globs (`plan-issue-*.md`, `prd-*.md`, `requirements-brief-*.md`) and the
  `.plans/` subdir convention; the `/end`-skill invocation hook.
- *Test provenance:* `prune_plan_files`' tests port with the age logic.

**Out of scope.** Redaction (S4 — secrets are never **written**; this is **expiry**).

**Route into acceptance-test authoring.** `route:direct-tdd`. Planned: `tests/test_retention.py`.

---

## S23 — TUI + CLI/TUI parity — all eight views
**Labels:** `v1` `phase:5` `route:spec-up`

**Problem.** US-9.2 requires **eight** views. Draft v1 silently weakened this to *"logs and diffs may ship
thin"* while its matrix claimed full coverage. **The trim is removed and stays removed.**

**PRD criteria covered.** **US-9.1, US-9.2** (owner). Integration assertion: US-9.3 (owned by S5).

**Note.** **US-9.1 (one engine, one event stream, two surfaces) is an ARCHITECTURAL INVARIANT on every slice
from S4 onward**, not work done here: every earlier slice routes commands through the engine API and emits
structured events. **This issue builds the RENDERING**, and it is where the invariant is finally *asserted*.

**Observable acceptance criteria**
- CLI and TUI invoke the **same engine commands** and consume the **same structured event stream** — one JSONL
  file the TUI tails and the CLI prints. **No broker, no observer registry, no async fan-out.**
- **The TUI displays ALL EIGHT (US-9.2, verbatim): queue position, current stage, logs, diffs, approvals,
  failures, PR status, and cleanup warnings.** One acceptance test per view.
- **Closing either interface does not terminate or corrupt persisted state** (integration assertion; US-9.3 is
  owned by S5).
- **Deterministic rendering: the same event stream produces identical output** (required for testability).

**Expected footprint.** `src/issueforge/tui.py`, `cli.py`; `tests/test_tui.py`.

**Dependencies.** Blocked by **S5** (the event stream), **S16** (PR status), **S19** (cleanup warnings) — the
**producers** of the eight views. **Build last: building it early means rebuilding it as each stage lands.**

**Deterministic / AI / Human.** All deterministic. No AI. **The TUI is a SURFACE for approvals; it does not own
them.**

**Human approval points.** None of its own — it *renders* the gates owned by S9, S11, S12, S13, S15, S20, S21.

**Failure & recovery.** Closing the TUI never kills a run.

**Logging & observability.** Consumes the event stream; adds no new boundary.

**Prior-art and source audit**
- *Sources:* the existing `src/issueforge/tui.py` shell; `merged/SKILL.md:62` (**the engine-emits-data /
  interface-composes-prose split**); `pipeline_root.py:23-27` (the stdout/stderr discipline).
- *Preserve:* one engine, one event stream. **The engine emits data; the interface composes prose** — MARVIN
  arrived at this boundary the hard way and it holds. Deterministic output.
- *Refactor/extract:* **nothing. TUI is the ONE cleanly net-new area** — verified: zero `curses` / `textual` /
  `rich` / `prompt_toolkit` / `urwid` / `blessed` anywhere in MARVIN's `scripts/`.
- *Replace:* all of it.
- *Discard:* MARVIN's AskUserQuestion batching transport (a Claude Code affordance, not a design);
  `wave-status/SKILL.md`, which **greps for a `PENDING (#` marker string that no authoring skill actually
  mandates** — a known bug, and **IssueForge has no such marker anyway**.
- *Test provenance:* none ported.

**Out of scope.** A web GUI (v2 — the event log already covers it; no extra seam).

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_tui.py`.

---

## S25 — IO write seam, path resolver, and the boundary AST lint (a permanent CI gate)
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem.** *"IssueForge never writes MARVIN files"* (US-11.6) is worthless as an observational claim checked
at the end. **If the seam arrives after every module has already written to disk, adopting it is a
cross-project refactor** — and until then, nothing prevents a write. **The control must exist before the
writes do.** Likewise, a four-string grep for `marvin` is not a boundary proof; and MARVIN itself shows exactly
what a naive extraction imports: `sys.path.insert` + `spec_from_file_location` to load siblings by path, a
registry resolved relative to `__file__`, a store at `$AGENT_LOGS_DIR`, and a `--project` default of `"marvin"`.

**This gate owns no PRD criterion.** Like S2, it is an **enabling invariant that every implementation issue
must pass.** S24 owns US-11.5–11.7 by *executing* a full lifecycle; this slice makes that outcome
**structural** rather than hopeful.

**User-visible outcome.** `issueforge lint boundary` exits non-zero naming the file, line, and rule for any
code that could write outside IssueForge's own state root or a registered worktree, or that reaches for a
sibling checkout. It runs in CI on every PR **from the first implementation slice onward.**

**PRD criteria covered.** **None (an enabling gate).** It is the mechanism behind US-10.3 (S4), US-11.5,
US-11.6, and US-11.7 (S24).

**Observable acceptance criteria**
- **ONE IO seam for IssueForge's OWN direct filesystem writes** — the artifact/state writes the engine issues
  through Python (`open`, `Path.write_*`, `os.*`, `shutil`, `tempfile`). Every such target path resolves under
  **IssueForge's own state root** or **the registered target repo's WORKTREE**, and **never the normal
  checkout's working tree**, and never anywhere else. A violation **raises**.
- **Git operations are a SEPARATE, explicitly-modeled boundary — NOT the Python IO seam** (*resolves the
  round-1 finding that "ALL filesystem mutation routes through the IO seam" collided with `git worktree`*). A
  subprocess `git fetch` / `git worktree add|remove` is governed by the **executable-argv rule (class 2 below)**,
  not by the write-surface rule. `git worktree add` **necessarily writes `.git/worktrees/` bookkeeping** under
  the target repo's git-common-dir; that is **permitted**, because US-4.2's "without modifying the normal
  checkout" is a guarantee about the **working tree, HEAD, and index** — which **S6's isolation proof** asserts
  byte-identical — **not** a claim that git's own metadata never changes. The Python write-surface lint
  therefore does **not** try to police git's internal writes; it polices IssueForge's own `open`/`write` calls,
  and S6 owns the git-operation isolation guarantee.
- **ONE `paths.py`.** It is the **only** module permitted to contain a `Path(__file__)` expression followed by
  `.parent` / `.parents[...]`.
- **The six-class AST lint over the package:**
  1. **Imports:** fail on any module root that is not stdlib, a declared dependency, or `issueforge`. Fail
     unconditionally on `sys.path.insert/append`, `importlib.util.spec_from_file_location`, and `__import__`
     with a non-literal name.
  2. **Executable argv:** every `subprocess.*` / `Popen` call site — argv must be a **list literal or a typed
     `Command` value**; **fail on `shell=True`**; fail on any argv element containing `marvin`, `MARVIN_`,
     `$HOME/`, or `~`.
     **The allowlist is NOT a fixed set of binaries.** *(Corrected: draft v3 round 1 allowlisted only `git`,
     `gh`, and the provider — which would REJECT the repository's own baseline command, `pytest`/`uv`/whatever
     `.issueforge.toml` declares, that S1 and US-4.1 REQUIRE the engine to execute. The rule was
     self-contradictory and would have failed the build it was meant to protect.)*
     The real rule is **provenance, not identity**: `argv[0]` must be either (a) a literal in the small
     engine-owned set (`git`, `gh`), or (b) a value **read from configuration or from the frozen manifest** —
     the provider executable, or the repository's configured baseline/acceptance/lint/build command. The lint
     asserts case (b) is **not a literal** and that its **cwd and output paths are constrained** by the seam.
     *The engine must be able to run an arbitrary configured test command; it must not be able to run an
     arbitrary hardcoded one.*
  3. **Environment:** fail on any `os.environ` read matching `^MARVIN_` or `AGENT_LOGS_DIR`.
  4. **Defaults:** scan argparse `default=`, dataclass field defaults, and module constants for `str`/`Path`
     values that are absolute, start with `~`, or resolve outside the package/run root. *(This catches a
     `--project` default of `"marvin"` and a rates path inside a sibling checkout.)*
  5. **Path literals:** a denylist — `state/`, `context/`, `skills/`, `agentLogs`, `projects.md`,
     `model-rates.json`, `agent-runs.json`, `SKILL.md`, and any literal containing `/Users/`.
  6. **Write surface:** AST-forbid `open(mode w|a|x)`, `Path.write_text/write_bytes/mkdir/touch/unlink/
     rename/replace`, `os.remove/rename/replace/makedirs`, `shutil.*`, and `tempfile` **anywhere outside the
     seam.** **This is the class that makes the boundary structural.**
- **A real-subprocess negative test:** the engine attempts a write against a **MARVIN-shaped target** and the
  seam **raises** — proving the assertion is live at runtime, not merely linted.
- Reports **every** violation; no fail-fast. **Runs in CI and blocks merge, from the first implementation slice
  onward.**
- **A CI-ORDER ASSERTION: no module that writes to disk can land before the seam and lint are active** (*v2-report
  mechanical fix 3, extended by v5-round-1 fix to include S1*). **S1** (per-invocation artifact directory),
  **S3** (registry), and **S4** (run store) all write to disk, so **S25 is a hard blocker of all three** — the
  write seam and boundary lint must exist in the tree before, and be enforced by CI on, the very first
  disk-writing slice, which is S1. A test asserts the CI workflow runs `lint boundary` as a required check on
  every PR touching `src/issueforge/`, so the control cannot be added *after* a writing module already shipped.

**Expected footprint.** `src/issueforge/io.py`, `src/issueforge/paths.py`, `scripts/check_boundary.py`,
`cli.py` (+`lint boundary`); `tests/test_io_seam.py`, `tests/test_boundary_lint.py`; CI workflow.

**Dependencies.** Blocked by: none. **Gates every implementation issue that writes to disk — S1 (per-invocation
artifact dir), S3 (registry), and S4 (run store) onward** (*v2-report mechanical fix 3, extended by v5-round-1
fix: S25 hard-blocks S1/S3/S4; S1 is the first disk-writer*).

**Deterministic / AI / Human.** All deterministic. No AI. No approval.

**Human approval points.** None.

**Failure & recovery.** A violation fails CI, naming file, line, and rule.

**Logging & observability.** N/A (a build-time gate) — but it **owns** the runtime write assertion.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:44,55-74` (`_SCRIPTS_DIR`, `sys.path.insert`, `_load_sibling` via
  `spec_from_file_location`), `:714` (`state/projects.md` resolved script-relative), `:921` (`--project`
  defaults to `"marvin"`); `agent_runs_lib.py:27` (`context/model-rates.json`), **`:208-217` (`resolve_logs_dir`)**;
  `pipeline_root.py:36,50,70` (`MARVIN_PIPELINE_ROOT`); the ~14 scripts using
  `REPO_ROOT = Path(__file__).resolve().parent.parent`.
- *Preserve:* nothing — this slice exists to make MARVIN's coupling **unrepresentable**.
- *Refactor/extract:* nothing.
- *Replace:* every MARVIN-checkout assumption class above.
- *Discard:* all of them.
- *Test provenance:* none ported.
- *New engine policy:* the seam, the resolver, and the lint are **entirely net-new**. MARVIN has no equivalent
  and could not have one — **its store lives outside its repo precisely because its repo IS the session's repo.**

**Out of scope.** The lifecycle execution proof and the canary tree (**S24**).

**Route into acceptance-test authoring.** `route:direct-tdd`. Planned: `tests/test_io_seam.py`,
`tests/test_boundary_lint.py`.

---

## S24 — Self-contained boundary: a permanent CI invariant
**Labels:** `v1` `phase:5` `route:spec-up`

**Problem.** A one-time boundary proof **can pass and then be undone**: a later issue reintroduces a MARVIN
path, a runtime read, or a consumer-specific write **after** the proof closed. And a four-string grep is not
exhaustive. **This must be a PERMANENT invariant, not a milestone.**

**PRD criteria covered.** **US-11.5, US-11.6, US-11.7** (owner).

**Observable acceptance criteria**
- **EXECUTION, not inspection.** A full lifecycle runs in a sandbox with **no MARVIN directory on disk**, with
  `MARVIN_PIPELINE_ROOT` / `AGENT_LOGS_DIR` **unset**, from a **wheel installed into a clean venv under a temp
  `HOME`** — and completes. **Any residual `__file__`-walking or sibling-path import fails at import time.**
- **The write seam, the path resolver, and the six-class AST lint are S25's, and they land in phase 0.** They
  are **preconditions of this slice, not deliverables of it.** *(Corrected: draft v3 round 1 introduced the sole
  write seam HERE — after every module had already implemented its own writes — which forces a cross-project
  refactor at the very end of v1, bundled with a wheel-installed lifecycle test, a six-class AST analyzer,
  query interfaces, docs, and CI. That is not one focused slice, and the seam is worthless if it arrives after
  the writes.)* **This slice consumes S25 and asserts the invariant end to end.**
- **The registered NORMAL CHECKOUT is explicitly out of bounds.** *"Any write in the target repo" is too
  broad* — that is what US-4.2/4.3's isolation proof exists to guarantee.
- **The EXHAUSTIVE all-real-paths redaction canary lives here** (US-10.3, owned by S4 — this is the integration
  assertion). A known token, a credential path, an env value, and a synthetic secret are pushed through **every
  REAL capture path now that they all exist** — prompt, response, stdout, stderr, diff, review packet, event
  stream, error trace — and must appear in **zero** persisted artifacts. **S4 could not do this: prompts,
  responses, diffs, and review packets did not exist when S4 was built.**
- **A CANARY-TREE HASH MANIFEST replaces the filesystem write monitor.** Materialize a fixture MARVIN-shaped
  tree (`state/`, `context/`, `skills/`, an agent-runs store) in a temp dir, point every MARVIN-ish env var at
  it, snapshot `{relpath: (sha256, size, mode)}` recursively, run the **whole** lifecycle against a fake runner,
  re-snapshot, and assert **byte-identical, with no new paths**.
  ⚠ **This CORRECTS draft v2's plan.** A filesystem write monitor is **not viable on macOS**: FSEvents is
  directory-granular and coalesced (it can return "something under here changed, rescan yourself"); kqueue needs
  an fd per file and **structurally misses newly-created files** — exactly the violation being hunted; Endpoint
  Security needs an Apple-granted entitlement plus root; DTrace/`fs_usage` are SIP-restricted. **The hash
  manifest is portable, deterministic, and strictly stronger** — and combined with the seam assertion it catches
  a *would-be* write on a path the fixture never exercised, **which no monitor can do.**
- **US-11.7 — consumers PULL** via documented CLI/JSON, event, and artifact interfaces. **IssueForge never
  PUSHES** into a consumer's private storage. **IssueForge is the SOLE OWNER of its run state.**
- **This suite runs in CI on every PR and BLOCKS MERGE. It is not "done" when it first passes**, and it
  **blocks v1 completion**.

**Expected footprint.** `tests/test_boundary.py`, `tests/test_redaction_canary.py`, `cli.py` (a `--json`
query surface), CI workflow, `README.md`/`docs/` (the documented read interfaces).
**The write seam, path resolver, and AST lint are S25's footprint, not this slice's.**

**Dependencies.** Blocked by **S25** (the seam and the lint) and by **ALL v1 implementation issues** (it must
observe a complete lifecycle) — **and thereafter runs permanently.**

**Deterministic / AI / Human.** All deterministic. No AI. No approval.

**Failure & recovery.** N/A — an invariant, not a stage.

**Logging & observability.** None new.

**Prior-art and source audit**
- *Sources (the MARVIN-checkout assumptions a naive extraction imports):* `pipeline_root.py:36,50,70`
  (`MARVIN_PIPELINE_ROOT`, defaulting to `Path(__file__).resolve().parent.parent`);
  `merged_runner.py:44,55-74` (`_SCRIPTS_DIR`, `sys.path.insert`, `_load_sibling` via
  `spec_from_file_location`), `:714` (`state/projects.md` resolved script-relative), `:921`
  (`--project` **defaults to `"marvin"`**), `:623` (`_DOCS_ONLY_PREFIXES`);
  `agent_runs_lib.py:27` (`context/model-rates.json`), **`:208-217` (`resolve_logs_dir` → `$AGENT_LOGS_DIR` /
  `~/Projects/agentLogs` — THE SHARPEST US-11.6 VIOLATION VECTOR: `merged_runner.flip_run_record_for_pr:585-613`
  resolves that dir, takes `agent_runs_lib._repo_lock`, and calls `_atomic_write_log`, so a wholesale
  extraction of closeout WRITES MARVIN'S STORE)**; `check_cli_launch_hygiene.py:29-33`
  (`SKILLS_DIR`, `CANONICAL_FILE`); `check_validator_invocation.py:52-66` (`PIPELINE_SKILLS` and the byte-exact
  `"${MARVIN_PIPELINE_ROOT:-$HOME/marvin}"/` prefix); and the ~14 scripts using
  `REPO_ROOT = Path(__file__).resolve().parent.parent` — **the reason "copy `scripts/`" is never the move:
  extract FUNCTIONS, never the directory.**
- *Preserve:* the one-way boundary, **permanently**.
- *Refactor/extract:* nothing.
- *Replace:* **all four MARVIN-checkout assumption classes.** (1) `MARVIN_PIPELINE_ROOT` — a seam whose only
  job is to locate `~/marvin/scripts/*.py`; **IssueForge ships a package with entry points, so there is no root
  to resolve and nothing to lint.** (2) `state/projects.md` resolved relative to the script's own directory.
  (3) `resolve_logs_dir()` — MARVIN's store lives outside the repo **because MARVIN's repo IS the session's
  repo**. (4) Skill routing — prose orchestration executed by a model; **IssueForge's engine IS the replacement.**
- *Discard:* all of the above.
- *Test provenance:* none ported.

**Out of scope.** Anything MARVIN-side. **MARVIN is read-only provenance and is not modified by this project.**

**Route into acceptance-test authoring.** `route:spec-up`. Planned: `tests/test_boundary.py`.

---

# Scope additions the PRD does NOT require — filed, labeled `deferred-v2`, NOT in the v1 acceptance graph

These are real risks, so they are **filed and tracked** rather than silently dropped. They are **not** v1
acceptance criteria, and adopting either requires an explicit human decision (a PRD amendment).

## DV1 — Blocking mutation / anti-tautology gate · `deferred-v2`

**Why it matters.** Red-proof and mutation are **orthogonal; neither subsumes the other.**
`assert result is not None` **fails red** (the module is missing) and **passes green while constraining
nothing** — **red-proof CANNOT catch it; only mutation can.** Conversely, a test that recomputes its expected
value by importing the implementation survives mutation only if the mutation moves both sides.

**Why it is deferred.** **The PRD does not require mutation testing.** It requires meaningful red, integrity,
green verification, and review. Mandating a mutation harness enlarges S14/S15 substantially.

**If adopted:** port `check_acceptance_mutation.py` **with its v2 hardening intact** — the baseline-green gate;
package-path staging so `import pkg.impl` hits the **mutated** impl; BFS operator selection; **real pytest
collection to node ids** (`_collect_nodeids:150-186` + the in-process `pytest_runtest_logreport` hook at
`:193`); and the **verified 5-status vocabulary** (`:60-65`): `caught` / `survived` / `inconclusive` /
`baseline_red` / `collection_error`. **Nothing mutable → `inconclusive`, NEVER `is_tautology=True`** (v1
returned the latter; its own docstring calls that *"indefensible"*).
**⚠ AND FLIP THE DEFAULT. Verified: `main` ends in an unconditional `return 0` (`:389`), and the docstring says
*"the gate never hard-fails CI."* There is no `--strict` flag and no path that returns non-zero. A GATE THAT
CANNOT FAIL IS NOT A GATE. This is an ANTI-PORT.**

**Novel reuse worth noting:** `_assertion_dep_roots` / `_dep_closure` can double as a **deterministic tautology
detector** — *an assertion whose expected side transitively depends on an import from the implementation package
is a recomputation, not a golden value.* Proven code, new purpose.

## DV2 — The invariant lens for shaping · `deferred-v2`

**Why it matters.** MARVIN's most sophisticated shaping rule (`spec-up/SKILL.md:91-96`), with **no analogue in
IssueForge's PRD.** Real incident: *"two interleaved requests both pass the SELECT, both commit, one expense
double-matched."* **TDD-from-prose derives only the SEQUENTIAL criterion, which an app-level SELECT-then-UPDATE
guard satisfies while a concurrent race still violates the invariant.** When an issue asserts
ownership/uniqueness/"must never happen twice", the gate should REQUIRE criteria the happy path cannot satisfy:
a **DB-level constraint** (the durable fix was a partial unique index with `23505` → 409) **plus a concurrency
test** (two interleaved requests, not a sequential pair). Sibling lenses: idempotency-under-retry;
partial-failure/rollback.

**Why it is deferred.** It is an **unapproved scope addition** to shaping (S9). Adopt via PRD amendment or
leave out.

---

# Risks

1. **The tautology hole is real and v1 does not close it** (DV1 is deferred). A test that constrains nothing can
   pass every v1 gate. **Accepted knowingly.**
2. **Test-environment detection is an ACCEPTED RESIDUAL RISK, not a solved problem** (`prd.md:158`). An
   implementation branching on a test-runner environment variable defeats **every** static check — file hashing
   and import-closure analysis alike. It is carried by S15's code review (explicitly instructed to hunt for it)
   and by hermetic runs. **It is not eliminated, and no slice may claim it is.**
3. **The override is a cultural risk** (S15). Once it exists, it gets used whenever review is inconvenient. The
   friction is deliberate; it reduces the risk without eliminating it.
4. **Provider plan rate limits are SHARED with interactive use** (a rolling window plus a weekly cap, across all
   of the vendor's surfaces). **Harness throughput competes with the human's own use.** Budget for it.
5. **v1 ships a pytest adapter only, and registration REFUSES anything else** (US-1.5). This is stated, not
   silently degraded — but it narrows real-world v1 applicability, and the refusal will surprise a user who
   registers a Go or JS repo. **It surfaces at `repo add`, which is the whole point.**
6. **The adapter is per-(framework, reporter), not per-language.** `unittest` is Python and is **not** covered
   by the pytest adapter. "Add Go support" really means "add gotestsum support."
7. **The meaningful-red predicate is net-new and has no prior art.** It is the load-bearing control of the
   entire system and it **looks smaller than it is** (S10). Size it accordingly.

---

# 9. Round-2 corrections — the 8 blocking findings from the independent gate

The gate returned **REVISE** on round 1. Every finding was verified against the PRD and the source before being
accepted; none was taken on faith. All 8 are fixed.

| # | Finding | Fix |
|---|---|---|
| 1 | **S2's lint proved artifact completeness, not BEHAVIOR completeness.** A record could name `merged_runner.py`, classify one safeguard, and pass while six other failed-read inversions in the same file went uninventoried. | The inventory unit is now a **behavior** (symbols, associated tests, failure-driven updates), with a **5th failure mode: a discovered behavior inside an inventoried artifact with no disposition.** Authoritative discovery roots are declared and versioned. **A stage audit is now HUMAN-APPROVED** — a lint proves completeness, it cannot prove a "discard" is *correct*. |
| 2 | **S4's redaction canary tested capture paths that do not exist yet.** Prompts, responses, diffs, and review packets arrive in S7/S10/S11/S15. A canary against a fake early path proves nothing. | S4 owns a **structurally mandatory** redaction API (bypass is an S25 lint failure, not a convention). Each later producer carries **its own** canary. **The exhaustive all-real-paths canary moved to S24**, where a complete lifecycle exists. |
| 3 | **The runtime graph let contract authoring begin without the shaping mutation ever running.** S9 → S10 directly, while S20 sat after closeout — so US-3.1's "a buildable issue receives a proposed in-place revision" would be satisfied by an issue nobody revised. S21 also wrongly depended on the *buildable* path. | **An unavoidable runtime branch out of S9**, enforced by the transition table: `buildable → S20 (approve+apply) → S10`; `oversized → S21 → children queued, PARENT RUN STOPS`; `blocked → pause`. **S20 moved to phase 2** and now builds the gateway write side (S18 and S21 reuse it). **S21 depends on S20's mutation machinery only**, never on successful buildable processing. |
| 4 | **The observability verdict was produced from an input that did not exist.** S8's classifier analyzed a *diff*; S9 needs the verdict *before authoring*. Worse: a boundary missed at shaping would surface only as an **AI review finding — which US-6.5's override CAN waive**, though `prd.md:80` says it may never waive deterministic observability requirements. | **S8 now ships TWO analyses:** `classify_prospective(issue, proposed_footprint, existing_code)` for S9, and `classify_diff(diff)` for S15's reconciliation. **A newly-crossed boundary at readiness is a DETERMINISTIC HALT requiring a contract amendment — never an overridable finding.** |
| 5 | **The baseline-green set operation was unsound.** `canonical_collect(base) − new_acceptance_ids` is a no-op in the normal case, and if an authored test **reuses** a preexisting ID the subtraction **silently removes that real preexisting test from the check that exists to protect it.** | Anchored on the **base snapshot**: run **every** id in `BASE_IDS` at the candidate. `ADDED` is **computed**, never declared. **`BASE_IDS ⊄ CANDIDATE_IDS` is a hard failure. Reusing a base ID is a hard failure.** `revise`/`supersede` cannot shrink the baseline without the human-authorized amendment path. |
| 6 | **S11 consumed a counter owned by S14 — an impossible producer dependency.** `review_rounds` is defined within US-6 (implementation), and S14 is *downstream* of S11 through S12 and S13. It also conflated test-contract review with implementation review. | S11 owns its **own** `contract_review_rounds` (default 2, incremented inside the store lock). **Any test/fixture change re-runs the full S10 predicate set and mints NEW sha-bound red evidence**; S12 may not freeze a manifest whose red evidence predates the last test change. |
| 7 | **The dependency closure was too narrow AND too broad.** Narrow: *"transitive **repository** dependencies"* leaves **installed plugins and their external versions** outside the freeze, though US-5.6 names plugins explicitly. Broad: if the closure includes imports of the **production module under test**, S13's absolute protected-path gate **freezes the implementation and the issue becomes unbuildable.** | Closure roots specified precisely. **IN:** tests, fixture/config/plugin providers, outcome-influencing helpers, transitive deps, **and the pinned identity+version of every external package** (from `discover_contract_dependencies`). **OUT: the system under test.** The discriminator is the **approved implementation write scope** (D5) — inside it is expected to change; a frozen-contract path is protected; **a path proposed as both is a contradiction that fails the freeze.** Two fixtures prove both directions. |
| 8 | **S24 was internally impossible and far too large.** Its argv allowlist (`git`, `gh`, provider) **would have rejected the repository's own baseline command** — the `pytest`/`uv` invocation S1 and US-4.1 REQUIRE. And it introduced the sole write seam **after every module had already written**, forcing a cross-project refactor at the end of v1. | **New S25 (phase 0): the IO write seam, the path resolver, and the six-class AST lint** — the control now exists **before** the writes. The argv rule is **provenance, not identity**: `argv[0]` is either an engine-owned literal (`git`, `gh`) or a value **read from config or the frozen manifest** (the provider, the repo's configured test command), with cwd and output paths constrained by the seam. **S24 shrinks to the permanent invariant**: hermetic no-MARVIN lifecycle, canary-tree hash manifest, exhaustive redaction canary, documented read interfaces. |

**Accepted, unchanged, from the gate's non-blocking observations:** the PRD does contain exactly 59 criteria and
the matrix assigns each exactly once; the override is correctly bounded; the deterministic/semantic red boundary
is right; the push-order and closeout defects are materially corrected; and neither deferred-v2 item has been
smuggled into v1.

---

# 10. Final-draft corrections — D5, D6, and the three mechanical fixes

The second decomposition attempt was BLOCKED after two review rounds, surfacing five remaining findings: three
mechanical and two requiring an author decision (`reviews/decomposition-attempt-02-blocked.md` §1–2). D5 and D6
resolved the two decisions and were written into the PRD (`prd.md:170-171`). This final draft applies them,
plus the three mechanical fixes, on top of draft v3. Nothing else changed; 59/59 criteria remain owned once each.

| # | v2-report finding | Fix in this draft |
|---|---|---|
| **D5** | A file's role could not be derived from a single approved scope, because the acceptance tests are in the delivered PR **and** are the frozen contract. | **Two disjoint scopes** (`prd.md:170`): an **implementation write scope** (approved at S9, governing the implementation commit range only) and a **frozen contract set** (S12). Acceptance tests are never in the write scope. Readiness (S15) asks two clean questions; a path proposed as both fails the freeze (S12). Applied to **S9, S12, S13, S15**. |
| **D5 (S13 half)** | S13 recomputed file hashes but never re-resolved frozen external package identities/versions — a plugin version swap changes the contract's meaning with every hash intact. | **S13** now re-resolves and compares the **identity + pinned version** of every frozen external dependency in the **authoritative verification environment** (US-6.1). S12 freezes them (from `discover_contract_dependencies`). |
| **D6** | S2 reduced a "behavior" to a discoverable symbol/reference; failure mode five could not fire on private behaviors, multi-safeguard symbols, or unreferenced updates. | **S2** inventory unit is now a **test**; discovery scope is a **versioned, checked-in extraction manifest** (harness vs workspace, human-curated); a **human approves each stage audit and manifest membership** (`prd.md:171`). Five test-granular failure modes. |
| **Mechanical 1** | Producer issues promised "each carries its own canary" but contained no such acceptance criteria. | **Explicit redaction-canary acceptance criteria added to S7, S10, S11, S15**, each exercising the **success AND failure/timeout** persistence paths through S4's API. S24 keeps the exhaustive all-paths canary as the backstop. |
| **Mechanical 2** | S8 left contradictory "heuristic feeding a judgment call / `classify(diff)` as a HINT" language; S15 asserted observability was "deterministically verified" without defining the evidence. | **S8**'s obsolete language deleted; `classify_prospective` / `classify_diff` specified as **two separately testable APIs**, and S8 owns the **logger-convention detector** + **sensitive-field predicate**. **S15** now names the **evidence** for each US-6.9 obligation and marks the residual diagnosability judgment as the (overridable) reviewer's. |
| **Mechanical 3** | The build graph let S3 (persists the registry) and S4 (persists the store) land before S25, the write seam that constrains them. | **S25 is now a hard blocker of S3 and S4** in the table, the graph, and both issue bodies, with a **CI-order assertion** that no persisting module lands before the seam and lint are active. |

## 11. Round-1 gate repairs (independent review, fresh session)

The first fresh-session gate returned **REVISE** with four blocking findings. Each was verified against the PRD
(quoting the cited line) before being accepted; all four are fixed.

| # | Finding (PRD cite) | Fix |
|---|---|---|
| B1 | **S10** persisted the red evidence "VERBATIM into the manifest" while US-10.3 (`prd.md:124`) says secrets/env values/credential paths are **never retained** — the two acceptance criteria could not both hold. | S10's red evidence is now a **redacted canonical record** persisted through S4's writer; fidelity comes from **re-derivability** (re-run at the contract sha), not raw text; raw output is an expiring redacted artifact (US-10.2), never the permanent manifest. |
| B2 | **S25**'s "ALL filesystem mutation routes through the IO seam / never the normal checkout" collided with `git worktree add`, which must write `.git/worktrees/` bookkeeping (US-4.2 `prd.md:56`; S6). | The Python IO seam now governs **IssueForge's own `open`/`write` calls only**; **git is a separate boundary** (the executable-argv rule), and US-4.2's "without modifying the normal checkout" is a guarantee about **working tree/HEAD/index** — owned by **S6's isolation proof** — not about git's internal metadata. |
| B3 | **S9** recorded an observability justification but never invoked the **reviewer** that US-6.7 (`prd.md:82`) requires ("reviewer-confirmed justification"). | S9 now runs a **fresh secondary-role review** (S7 capability, session-bound, US-9.5) that confirms the observability category + justification before human approval — the US-5.3 pattern applied to shaping. |
| B4 | **S1**'s `probe` returns only "capabilities + pinned reporter version," but **S12/S13** needed the identity+version of **every** external plugin/package (US-5.6 `prd.md:69`; US-6.1 `:76`). | The external closure is now the output of **`discover_contract_dependencies`** (one coherent adapter op): S1 exposes it, S12 freezes its output, S13 re-runs the **same** op in the authoritative environment and compares. `probe` supplies only the reporter version. |

**Non-blocking, addressed:** S4 now asserts the **US-2.1 single-active-worker invariant** directly (a second
`run` while one is active enqueues; slot admission decided inside the store lock). **Non-blocking, retained as
designed:** S19 keeps stacked-PR retargeting as an explicitly-caveated **backstop** for out-of-band state (v1
is single-run, so a stack should be impossible); the branch is otherwise preserved-and-reported.

---

## 12. Attempt-3 gate repairs (the three findings that survived attempt 3's second review round)

Attempt 3 applied D5/D6 and the three mechanical fixes and passed its first review round's repairs, but its
**second** review round returned REVISE with three blocking findings
(`reviews/decomposition-attempt-03-blocked.md` §1). Each was verified against the PRD; all three are fixed here.

| # | Finding (PRD cite) | Fix |
|---|---|---|
| F1 | **S9** let a human override the observability confirmation "like US-5.4," but US-6.7 (`prd.md:82`) grants **no** override — "**every** shaped issue … reviewer-confirmed." | The override is removed. A reviewer failure is recorded and retried with a fresh secondary-role session; a human may act as the reviewer only by **recording a confirmation**; **no path proceeds with the justification unconfirmed** (asserted by a test). |
| F2 | No slice owned the **hermetic, separately-provisioned verification environment** the PRD names in the adapter seam (`prd.md:157`) and relies on at `:158` / US-6.1. | A **sixth adapter operation, `provision_environment`**, is declared in **S1**, implemented and owned in **S6** with a **testable G14 criterion** (a candidate/implementer env mutation cannot change the authoritative result), and used by **S10/S13/S14/S15** for every authoritative run and re-resolution. |
| F3 | **S5** said reorder and cancel were both "only before a run starts," contradicting US-2.3's cancellable **paused** run. | **Reorder** is queued-only (US-2.2); **cancel** applies to **both** a queued run and the current **paused** run (US-2.3), the latter releasing the worker — **two transition tests, one per path**. |

---

## 13. Draft-v5 gate repair (round 1)

The fresh two-round gate on draft v5 returned **REVISE** on round 1 with **one** blocking finding, verified
against the draft and fixed here (its round-1 notes confirmed F1–F3, D5, D6, and the mechanical fixes as
genuinely resolved).

| # | Finding | Fix |
|---|---|---|
| G1 | **S1 owns "a fresh artifact directory per invocation" (a disk write) but was "Blocked by: none,"** so a direct filesystem writer could land before S25's write seam — and S25's own AST lint (`tempfile`/`mkdir` forbidden outside the seam) would then reject S1. The v2-report "S25 blocks S3/S4" fix missed that **S1 also writes to disk.** | **S25 now hard-blocks S1** (table, graph, both bodies, CI-order assertion). S1's per-invocation artifact directory is **created through S25's IO/path seam**, so the earliest slice's write is guarded and the CI invariant holds from the first disk-writing slice. |

---

## 14. Draft-v5 gate repair (round 2)

The v5 gate's second round returned **REVISE** with two blocking findings; its notes confirmed the round-1
S1/S25 fix, D5/D6, and the canaries as coherent. Both were verified against the PRD and fixed here.

| # | Finding (PRD cite) | Fix |
|---|---|---|
| B1 | **S10** marked a call-phase `ImportError` as a VALID red, but US-5.1 (`prd.md:64`) requires tests that *"execute **without** … import … errors"* — at every phase, not only collection. | The rule stays **phase-based** (no `AssertionError` whitelist) **but import errors are invalid at any phase**: the module under test must import successfully, and the valid red is a **call-phase behavioral failure** (`AttributeError`/absent-API/`NotImplementedError`/assertion) on the imported module. Three tested cases: valid `AttributeError` red; invalid `ImportError`; invalid missing-fixture setup error. |
| B2 | The matrix assigned **US-5.2 to S10**, but S10 explicitly delegates the criterion's semantic "failed for the NAMED reason" half to S11 — so the nominated owner did not cover the full criterion (single-owner violation). | **US-5.2 is now owned and finally asserted by S11**, which consumes S10's deterministic evidence (baseline-green + sha-bound call-phase red) and adds the semantic correspondence. **S10 contributes the deterministic half as an enabling integration** (the US-6.8/6.9 pattern). Matrix, table, both bodies, and the §7 note updated. |

**Editorial (non-blocking):** G12 updated from "five adapter function names" to **six** (the F2 addition of
`provision_environment`).

---

## 15. Correction pass and gate status

The prior filing pass received a final **REVISE** verdict and then incorrectly introduced an author-level
override that the run protocol did not authorize. It also reused stale draft-v3 provenance footers. The 27
GitHub issues therefore remain quarantined planning artifacts until this corrected draft passes the automatic
independent review gate; they are not implementation-ready merely because they exist.

This correction resolves the four final blocking findings directly in the affected slice contracts:

| # | Affected slices | Resolution |
|---|---|---|
| B1 | S1, S3 | Configuration is loaded and validated from the verified **committed Git object**; untracked-only and dirty working-tree copies cannot influence a run. |
| B2 | S15 | Every scope expansion is checked against the current frozen contract set **before** mutation; overlap fails without changing the approved scope. |
| B3 | S8, S9, S13, S15 | An unanticipated boundary uses S9's distinct observability/buildability amendment with fresh reviewer confirmation and human approval; S13 cannot handle it. |
| B4 | S8, S15 | Required logging is proved by executable authoritative-run evidence with seeded canaries; static inspection is supplementary only. |

The correction changes no PRD owner and no dependency edge: all 59 criteria remain singly owned. The filed
issue bodies must be regenerated from this corrected draft and point to its immutable Git commit. A fresh
automatic review must return **ACCEPT** before any child enters `spec-up`, `spec-dev`, or direct TDD. If blocking
findings remain after the allowed review rounds, the issues stay quarantined and implementation does not begin.

---

*End of corrected draft v5 (F1–F3, G1, both earlier B1/B2 findings, and final B1–B4 resolved; pending a fresh
automatic independent review verdict).*
