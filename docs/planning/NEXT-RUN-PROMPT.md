# Prompt for the next `/prd-to-issues` run (paste into a fresh session)

> **STATE AS OF 2026-07-14 (read this first).** A second decomposition attempt was also **BLOCKED** by the
> review gate (report: `docs/planning/issueforge-v1-decomposition-report-v2.md`, 0 issues created). It surfaced
> two decisions, now **resolved and amended into the PRD as D5 and D6** (PR #2, merge it before re-running so
> the base is real):
> - **D5** — file roles are two disjoint scopes: an approved **implementation write scope** and a discovered
>   **frozen contract set**. The acceptance tests are in the contract set, never in the write scope.
> - **D6** — the source-audit unit is a **test**, discovered against a **checked-in extraction manifest** that
>   declares harness vs. workspace; a human approves each stage audit.
>
> **The PRD now carries D1–D6 and has 59 acceptance criteria** (was 51). **Reuse `docs/planning/decomposition-draft-v3.md`
> as the starting point** — it owns 59/59 with a single owner each and is 25 issues (S1–S25). Do **not** start
> from zero, and do **not** re-derive the source audit (§4 of the v2 report already corrected eight false
> prior-art claims). **Three mechanical fixes remain**, fully specified in §2 of the v2 report: per-producer
> redaction canaries (S7/S10/S11/S15); delete S8's contradictory heuristic language; make S25 a hard blocker of
> S3/S4. Apply D5/D6 to S9/S12/S13/S15 and S2 per the amended criteria, then run the two-round gate again.

Run `/prd-to-issues` for MatthewDruhl/IssueForge#1.

This run is pre-authorized to complete autonomously. Do not pause for human approval. Perform the analysis,
independent review, GitHub issue creation, epic linking, and final PDF report in one workflow.

**Authorized scope**
- Read and update MatthewDruhl/IssueForge issue #1.
- Create and label child issues in MatthewDruhl/IssueForge.
- Create the planning report in the local IssueForge repository.
- Commit and push only the planning-report artifacts, on a branch (a `no-main-commit` hook blocks main).

**Forbidden**
- Do not change IssueForge source code or tests.
- Do not create implementation branches or PRs.
- Do not merge anything.
- Do not modify, label, comment on, close, or create anything in MARVIN.
- Do not write to any MARVIN file, state, skill, ledger, configuration, or generated artifact.

**Target repository**
- GitHub: MatthewDruhl/IssueForge · Local: /Users/matthewdruhl/Projects/IssueForge · Private.

---

## READ THIS FIRST — a previous attempt was BLOCKED, and its output is on disk

A prior `/prd-to-issues` run on this PRD **failed its own review gate twice** and, per protocol, **created
zero issues**. Everything it produced is committed in the repo. **Read it before you re-derive it:**

- `docs/planning/issueforge-v1-decomposition-report.md` — the failure report. **Read this first, in full.**
- `docs/planning/decomposition-draft-v2-SUPERSEDED.md` — the rejected 21-issue draft. A **starting point,
  not a plan to execute**. D1 and D3 below change its shape materially.
- `docs/planning/reviews/` — six fresh-session adversarial review transcripts, with their verdicts.

**Treat the transcripts as evidence, not authority.** Review 02's finding #3 is factually wrong (it claims
the PRD grants no implementation-review override; `prd-v1.md:153` grants one). Review 04 caught it. The gate
was right four times out of six. **Verify every claim a reviewer makes about the PRD against the PRD.**

## The PRD has been AMENDED since that attempt

PRD #1 and `docs/prd-v1.md` are identical and current. Four decisions were resolved and written in. **Do not
re-open them; build on them.**

- **D1 — pytest targets only.** The engine, run store, Git/GitHub layers, and the **verification adapter
  interface** are repository-agnostic. Semantic test integrity is not: it needs stable test identity, an
  execution-phase distinction, and a dependency closure. `repo add` **refuses a repo whose test framework has
  no installed adapter**, at registration. The per-framework surface is thin and **mandatory**: `probe`,
  `canonical_collect`, `classify`, `discover_contract_dependencies`, `validate_invocation`.
- **D2 — the implementation code review HAS a human override.** Human-only, per-finding, after one fresh
  replacement review, bound to the reviewed head sha, invalidated by any later change, reported in the PR. It
  can never waive tests, baseline, quality gates, contract integrity, approved file scope, or sensitive-data
  requirements. Override means *the PR may open*, not *the finding is erased*.
- **D3 — shaping is SPLIT, and this changes the build order.** A new early **Buildability Contract** slice
  emits readiness classification, duplicate verdict, unresolved design decisions, a **proposed file scope**,
  and the observability verdict — and **a human approves the file scope BEFORE contract authoring**. It owns
  US-3.4 and US-6.5 and produces US-6.3's approved scope. In-place revision and epic decomposition stay LATE.
  A "pass-through shaper" is not a real thing: it can only fail closed, skip the scope check, or approve the
  diff after the fact — and a scope derived from the diff approves itself.
- **D4 — two engine-owned repair budgets, not one.** `review_rounds` (blocking findings → fix in place,
  worktree preserved) and `repair_attempts` (implementer died, or the suite is still red after it reported
  done → reset the worktree to base, fresh session, no prior transcript). Each defaults to 2, both
  configurable, exhausting either pauses. They are **persisted engine state incremented inside the store
  lock**, not model discretion. Attempts *inside* one AI session are not engine state and are not counted.

**Vendor neutrality.** The PRD names no vendor. It has a **primary AI** (authors, implements) and a
**secondary AI** (independently reviews), bound to provider profiles by configuration. **If no secondary is
configured, the review runs on the primary provider in a brand-new session** — never the authoring session,
never a resumed one. A review whose session identity equals the authoring session's is rejected. Keep this
language; do not reintroduce product names into issue bodies.

## Known defects — already found and paid for. Do not rediscover them.

1. **The `helpers.py` fixture bypass.** `conftest.py` imports a fixture from `tests/helpers.py`; the
   implementer edits **only `helpers.py`**. Conftest hash, test hashes, command, and collected IDs are all
   unchanged, and the helper is in *conftest's* import closure, not the *test modules'*. The contract is
   neutralized undetected. **Fix (now in US-5.5): the frozen dependency set is DISCOVERED BY THE ADAPTER, not
   declared by config** — test modules, every fixture provider and config file on the collection path,
   plugins, and their **transitive** dependencies. A user path list may ADD to the boundary, never SHRINK it.
2. **Zero-collected is a THIRD state: BROKEN.** Not red, not green. pytest exits 5; Jest and `go test` exit
   **0**. MARVIN ships the opposite bug today (`merged_runner._parse_pytest_summary`, `:681-693`) and it fired
   on 2026-07-12: a DandD `/merged` run reported `red-main` with `passed: 0, failed: 0`.
3. **Meaningful-red has a hard determinism ceiling.** Deterministic: collection identity (set equality, not a
   count), call-phase vs setup/collection failure, baseline-still-green, sha binding, zero-collected and XPASS
   rejection. **Semantic (AI reviewer + human):** whether the failure matches the *named expected behavioral
   reason*. Call-phase failure proves the test executed and failed — **not that it failed for the right
   reason.** Do not assign the semantic half to a deterministic predicate.
   Note the **genuine-TDD trap**: a first red test for a not-yet-existing function *legitimately* raises
   ImportError/AttributeError in the call phase. A rule of "must be AssertionError" is WRONG. The rule is
   **phase-based**, never exception-type-whitelisted.
4. **Test-environment detection is an ACCEPTED RESIDUAL RISK, not a solvable problem.** An implementation that
   branches on `PYTEST_CURRENT_TEST` (or a `.test` binary suffix, a Jest worker var, a parent-process name)
   passes every static check — file hashing and import-closure analysis alike. It is carried by the
   independent code review, which is explicitly instructed to look for test-context-dependent behavior, and by
   hermetic verification runs. Do not claim to eliminate it.
5. **The load-bearing control has NO prior art.** `check_acceptance_integrity.py` imports only
   `argparse, ast, sys, pathlib` — it never runs pytest, never collects, never executes. The meaningful-red
   predicate is **net-new**. It therefore *looks smaller than it is*. Any plan that files "port MARVIN's
   guards" as its integrity slice ships a gate that accepts **any** failure as red.

## Prior art

`docs/provenance/marvin/` already holds the transfer ledger and 11 verbatim artifacts. **The MARVIN
open-issue transfer is DONE — do not redo it.** MARVIN is strictly read-only design provenance at
`/Users/matthewdruhl/marvin`. Every implementation issue still needs its **Prior-art and source audit**
(Sources / Preserve / Refactor-or-extract / Replace / Discard), and the prior draft's audits are a strong
starting point — but verify each claim against the real files; do not copy them on faith.

Carry the **six cross-cutting rules** into every issue's Preserve section: (1) a failed read is never negative
evidence; (2) honor every return code; (3) verify at the boundary, don't trust the report; (4) content, not
ancestry; (5) the contract is enforced by the harness or CI, never by the session being policed; (6) every
gate needs a legitimate escape hatch or people route around it.

**Anti-ports — do not extract these faithfully:** `merged_runner.py:828-836` (blanket halt-on-red-main; the
transfer ledger has already ruled it a defect, #760); `check_acceptance_mutation.py`'s exit-0-always posture
(a gate that cannot fail is not a gate); `_parse_pytest_summary` (summary-line scraping); MARVIN's sanctioned
marker-flip exception (IssueForge has no PENDING marker, so it needs no carve-out and importing one would add
a hole it does not have).

## Method — keep what worked

- **Discovery:** up to four **read-only** background agents in parallel (PRD coverage; IssueForge
  architecture; delivery & safety; MARVIN source audit). They must not write files, must not touch GitHub, and
  must not spawn agents. They return findings to the main session, which is the sole synthesizer and the sole
  writer. Give each a bounded task. If one fails, continue and record the missing analysis.
- **Decomposition:** vertical tracer bullets, not horizontal layers. Every PRD criterion mapped to exactly one
  **owning** issue; downstream repeats are labeled **integration assertions**, not second implementations.
  Avoid oversized issues. No premature abstraction (no provider ABC, no state-machine library, no event bus,
  no pydantic — v1 needs zero new dependencies). Do not create v2 work as v1 issues; file genuinely-deferred
  work as `deferred-v2` rather than dropping it silently.
- **Required per child issue:** title; problem statement; user-visible outcome; PRD criteria covered;
  observable acceptance criteria; expected file/module footprint; dependencies and what it unblocks;
  deterministic vs AI vs human responsibilities; human approval points; failure and recovery behavior; logging
  and observability impact; **Prior-art and source audit**; explicit out-of-scope; recommended route into
  acceptance-test authoring.
- **Independent review gate — automatic, no HITL.** Save the complete decomposition to
  `/private/tmp/issueforge-prd-to-issues-draft-final.md` (do NOT overwrite the committed input
  `docs/planning/decomposition-draft-v3.md`), then run a **fresh** secondary-AI session to review:
  complete PRD coverage; tracer-bullet quality; issue size and independence; dependency correctness;
  deterministic/AI/human boundaries; meaningful-red and contract-integrity coverage; source-audit completeness;
  no MARVIN write-back; observability; v1 vs deferred scope; duplicates or gaps. Require a structured
  **ACCEPT** or **REVISE**. If REVISE, repair and run **one** more fresh review. **If blocking findings remain
  after the second review, do not invent a resolution: create no GitHub issues, generate a failure report PDF,
  and exit nonzero.**
- **Guarded launch for every AI invocation:** stdin closed (`< /dev/null`), stderr **captured to a file**
  (never `2>/dev/null`), a wall-clock timeout (`timeout` does not exist on macOS — use
  `perl -e 'alarm N; exec @ARGV'`), full output persisted, and **empty output OR non-zero exit = FAILED, never
  a pass**. The reviewer has **no network**: materialize every input to local disk first.
- **GitHub creation (only after ACCEPT):** create every child issue; label consistently (epic/child, phase or
  order, route into acceptance-test authoring, `deferred-v2` where applicable — **note the existing
  `phase:0`–`phase:5` / `route:*` / `v1` / `deferred-v2` / `epic` labels were created for the SUPERSEDED
  ordering; reuse or replace them deliberately, don't inherit them by accident**); update #1 as the epic,
  linking every child, with recommended order and dependencies, **appending a bounded decomposition section
  rather than replacing the PRD body**; then **re-read every created issue from GitHub and verify** title,
  body, labels, epic linkage, acceptance criteria, source-audit section, and dependency references. Correct any
  mismatch before reporting success. Search existing open issues first; avoid duplicates.
- **Report:** `docs/planning/issueforge-v1-decomposition-report-final.md` + a PDF at
  `docs/planning/issueforge-v1-decomposition-report-final.pdf`, generated **from the final verified markdown**.
  **Do NOT overwrite the historical `issueforge-v1-decomposition-report.md` (pass 1) or
  `issueforge-v1-decomposition-report-v2.md` (pass 2) — they are the preserved failure evidence.**
  No pandoc/weasyprint/typst on this machine — render markdown → HTML → Chrome headless
  (`--headless --disable-gpu --no-pdf-header-footer --print-to-pdf`). **Inspect every page**: a paragraph
  beginning `#<digit>` gets parsed as a lazy `<h1>` and renders as a broken headline, and a list needs a blank
  line before it. Assert exactly one `<h1>`, zero blank pages, and that the stated page count matches the
  actual one. Record page count and verification result in the markdown.
- **Final repo actions:** run `git diff --check`; commit **only** the planning-report artifacts; **create a
  branch** (`docs/<name>`) — the `no-main-commit` hook blocks main, and it reads `$CLAUDE_PROJECT_DIR`, so use
  literal `git -C /Users/matthewdruhl/Projects/IssueForge …` paths; push and **verify the sha landed at
  origin** (`git rev-parse origin/<branch>`) rather than trusting the push output. Do not open a PR.

## Final response

Created issue count · links to #1 and every child · recommended first slice · dependency order · review
verdict · markdown and PDF paths · PDF page count and verification result · commit sha · confirmation that no
MARVIN file or issue was modified.
