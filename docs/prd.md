# PRD: IssueForge v1 — human-gated TDD issue runner

## End Goal

A developer hands IssueForge one GitHub issue and gets back one green, human-mergeable PR: the engine shapes the issue, freezes a human-approved red acceptance contract, implements without weakening it, opens the PR, waits for the human merge, and closes out idempotently. All AI work runs on subscription-authenticated CLIs, all safety contracts are enforced deterministically by the engine, and the whole loop runs without a MARVIN checkout.

## Problem Statement

Turning an existing GitHub issue into merged code currently requires an AI session to remember and coordinate issue shaping, test-first development, integrity checks, reviews, Git operations, approvals, recovery, and cleanup. This is inefficient and fragile: context can be lost, test contracts can be weakened, failures can strand work, and repeated manual steps differ between repositories.

## Solution

IssueForge is a Python workflow engine with CLI and Textual TUI interfaces. It processes one queued issue at a time, invokes subscription-authenticated AI CLIs for judgment-heavy work, deterministically enforces the TDD and safety contracts, opens one green PR, waits for human merge, and performs idempotent closeout.

The engine, the run store, the Git and GitHub layers, and the **verification adapter interface** are repository-agnostic. Semantic test integrity is not: freezing a contract requires stable test identity, an execution-phase distinction, and a dependency closure, none of which a generic argument-array command can supply. Those capabilities are therefore supplied by a per-framework **verification adapter**, and **version one ships a pytest adapter only** (see US-1 and Out of Scope).

## Milestones

Milestones render as Phases for stakeholders (marvin#811); GitHub and tooling keep `milestone`. Work before the milestone convention (stages S1-S13, phases 1-3: registration, run store/queue, contract machinery) shipped un-milestoned and is recorded here, not retro-tagged.

Story annotations below name the milestone that **completes the story's demo**; thinner slices of many stories already demo at M1 (PoC depth). Partial story splits (US-na/US-nb) are carved when `/agile` plans the increment that needs them, not up front.

- **M1: Walking skeleton** (renders as Phase 1 — Walking skeleton). Goal, re-scoped 2026-07-28: one real `issueforge run DandD#111` reaches `waiting-for-merge` with a live PR, against authenticated Claude and real GitHub. Shipped: PoC-A/B/C/D (#112, #113, #114, #115) and the live-run ladder fixes (#183, #189). Open: #193 (blocker: `engine._collect_ids` bare-root pytest seam), #144 (readiness epic: the live re-attempt), #131 (providers.invoke results unchecked), #184 (test suite leaks into the real state root the live run uses). S14-S16 (#20/#21/#22) moved to M2 as robustness successors, 2026-07-28.
- **M2: Full loop + TUI** (renders as Phase 2). S14-S19 (#20-#25), S23 TUI (#27); adopted by the 2026-07-28 orphan sweep: #105 (S13 integrity-enforcement remediation), #126 (wire real contract-integrity end-to-end), #130 (CLI-reaches-composed-stage test), #194 (S18 tracer `issueforge complete`).
- **M3: v1 completion** (renders as Phase 3). S21 (#26), S22 (#15), S24 (#28); adopted: #187 (dashboard/ outside every structural gate, paired with S24).

**Milestone log.** 2026-07-28: orphan sweep dispositioned all 46 unmilestoned open issues — 37 closed with the `deferred` label (see Out of Scope), 6 adopted into milestones as listed above, #123 executed by this migration, epics #1/#150 confirmed intentionally milestone-less.

## User Stories

### US-1: Register an existing local repository — [M1]

**As a** developer, **I want** to register a friendly repository alias, **so that** IssueForge can safely resolve issues to verified local clones.

**Acceptance criteria:**
- [ ] `issueforge repo add DandD:~/Projects/DandD` expands the path and records the alias, absolute path, normalized origin slug, and default branch.
- [ ] Alias lookup is case-insensitive while preserving entered spelling for display.
- [ ] Missing paths, non-Git paths, duplicate aliases, and mismatched remotes are rejected without changing the registry.
- [ ] IssueForge never clones or automatically registers a repository.
- [ ] Registration resolves a verification adapter for the repository and **rejects a repository whose test framework has no installed adapter**, naming the unsupported framework. Version one ships a pytest adapter only, so a non-pytest repository is refused **at registration**, not after a run has already been shaped and worktreed.

### US-2: Queue and resume issue runs — [M2]

**As a** developer, **I want** persistent issue runs, **so that** terminal closure and pauses do not lose work.

**Acceptance criteria:**
- [ ] `issueforge run DandD#148` enqueues a valid open issue and starts it when the single worker is available.
- [ ] Additional issues enter a persistent FIFO queue and may be reordered or cancelled before starting.
- [ ] A paused run blocks the worker until explicitly resumed, cancelled, or parked.
- [ ] Parking preserves exact run state and releases the worker to the next queued issue.

### US-3: Shape an issue into buildable work — [M3]

**As a** developer, **I want** vague or oversized issues shaped before coding, **so that** generated tests have an approved observable contract.

**Acceptance criteria:**
- [ ] A buildable issue receives a proposed in-place revision and no GitHub write occurs before human approval.
- [ ] An oversized issue receives a proposed epic and independently deliverable child issues; no issue is created or edited before approval.
- [ ] Approved decomposition links every child from the epic and each child enters the normal queue independently.
- [ ] Duplicate open work, unresolved design decisions, and an unknown expected footprint pause the run.
- [ ] Shaping emits a **buildability contract** before any acceptance test is authored: a readiness classification (`buildable`, `oversized`, or `blocked`), the duplicate verdict, the unresolved-design-decision list, a **proposed implementation write scope** (the allowed files and path patterns the implementation may modify, with justification), and the observability verdict required by US-6. The acceptance-contract files are **not** part of this scope; they are protected by the US-5 freeze instead (see D5).
- [ ] **A human approves the buildability contract — including the implementation write scope — before contract authoring begins.** That scope governs the **implementation commit range only** (the commits after the frozen contract commit). The approved implementation write scope enforced at PR readiness (US-6) is exactly this approved scope; it is never derived from the resulting diff, because a scope derived from the diff would approve itself.
- [ ] **Expanding the approved implementation write scope during implementation requires new human authorization** and preserves the prior approval in the audit trail. An implementation that writes outside the approved scope without that authorization pauses the run.

### US-4: Establish an isolated green baseline — [M1]

**As a** developer, **I want** every run based on fresh, isolated, green code, **so that** new failures are attributable to the issue.

**Acceptance criteria:**
- [ ] Every target repository commits `.issueforge.toml` with a mandatory baseline command expressed as an argument array.
- [ ] IssueForge fetches and creates a separate worktree from verified `origin/<default-branch>` without modifying the normal checkout.
- [ ] Dirty normal checkouts are allowed only when isolation is proven.
- [ ] A failed fetch, unprovable isolation, or red baseline pauses before AI changes files.

### US-5: Approve a meaningful red acceptance contract — [M1]

**As a** developer, **I want** to approve exact tests that fail for the missing behavior, **so that** implementation follows genuine TDD.

**Acceptance criteria:**
- [ ] The primary AI authors tests that collect and execute without syntax, import, fixture, configuration, or environment errors.
- [ ] The new tests fail for a recorded expected behavioral reason while the preexisting baseline remains green.
- [ ] An independent fresh AI session reviews coverage and validity before human approval.
- [ ] Reviewer failure may be explicitly overridden with a fresh same-provider session or human review, and the override is recorded.
- [ ] Human approval freezes the exact test commit, file hashes, collected identifiers, dependent fixtures/configuration, command, red evidence, **and the approved implementation write scope carried forward from the buildability contract (US-3)**. The **frozen contract set** (the test files and the discovered dependency closure below) and the **implementation write scope** are **disjoint**: a path is a protected contract input or a permitted implementation target, never approved as both, and a path proposed as both fails the freeze for the human to resolve (see D5).
- [ ] The frozen dependency set is **discovered by the verification adapter, not declared by configuration**: it covers the test modules, every fixture provider and configuration file on the collection path, plugins, and their **transitive** dependencies, **and it records the immutable identity and pinned version of every external plugin and package in that closure, not only in-repository files**. A user-supplied path list may add to the protected boundary but can never shrink it.

### US-6: Implement without weakening the contract — [M2]

**As a** developer, **I want** AI implementation constrained by the approved tests, **so that** green means the approved behavior was delivered.

**Acceptance criteria:**
- [ ] Implementation cannot proceed to PR readiness when approved contract files, their collection, configuration, command, **or the identity or pinned version of any frozen plugin or external package in the dependency closure** changed without new human authorization. These are re-resolved and compared in the authoritative verification environment, not assumed unchanged.
- [ ] The engine — not the AI — owns two **separate** and independently configurable repair budgets, each defaulting to **2**, because they recover from opposite failures and one counter would hide both. **`review_rounds`**: the independent review raised blocking findings, so the implementer fixes them **in place** and the worktree is **preserved**. **`repair_attempts`**: the implementer process failed or died, or the acceptance suite is **still red after the implementer reported done**, so the attempt is a write-off — the worktree is **reset to the branch base** and a **fresh** implementer session is dispatched, carrying the frozen contract and a compact failure trace but **never the prior transcript**. Exhausting **either** budget pauses the run with a schema-valid terminal record.
- [ ] Both counters are **persisted run state incremented inside the store lock**, and the engine gates the transition on them: a lock-free read-then-write would under-count and under-enforce the cap, and an AI session cannot bypass a budget it does not own. Attempts an implementer makes **inside** a single session are not engine state and are not counted here; they are a prompt instruction, not a workflow budget.
- [ ] PR readiness requires green acceptance tests, green full baseline, configured quality gates, the **approved implementation write scope** (every change in the implementation commit range lies inside it and no frozen contract path changed), and an independent code review with no blocking findings **except findings explicitly overridden under the following criterion**.
- [ ] A blocking implementation-review finding, or a reviewer execution failure, **may be overridden only by an authenticated human, and only after one fresh independent same-provider review attempt**. The override applies solely to identified AI-review findings at the exact reviewed head commit. **It can never waive contract integrity, acceptance tests, the full baseline, configured quality gates, approved file scope, or deterministically established observability and sensitive-data requirements.** It is issued per finding; a blanket "ignore review" action does not exist. Any subsequent code or contract change invalidates it. The permanent audit trail records the human identity, the commit, the reviewer sessions and verdicts, each overridden finding, the rationale, and the acknowledged risk. **Override means the PR may open, not that the finding is erased**: the PR reports every overridden finding for renewed human consideration before merge, and the override does not authorize the merge itself (US-7).
- [ ] IssueForge never calls a metered model API or silently falls back from a subscription-authenticated CLI.
- [ ] Every shaped issue records `required`, `existing coverage sufficient`, or `not applicable` for observability, with reviewer-confirmed justification.
- [ ] Logging is required when changed code crosses an HTTP, database, subprocess, filesystem, queue, third-party service, or AI boundary.
- [ ] Required logging follows the target project's logger, levels, formats, and correlation conventions and excludes contract-listed sensitive fields.

### US-7: Deliver one green PR — [M2]

**As a** developer, **I want** one reviewable PR containing the approved tests and implementation, **so that** main never receives intentionally pending tests.

**Acceptance criteria:**
- [ ] IssueForge pushes and opens a PR automatically only after all readiness gates pass.
- [ ] The PR reports the approved contract commit, integrity verdict, red/green evidence, verification summary, AI review verdicts, and overrides.
- [ ] IssueForge never merges the PR and waits for human review and merge.
- [ ] Attached watch mode and later `continue` both observe the same persisted `waiting-for-merge` state.

### US-8: Close and clean verified merged work — [M2]

**As a** developer, **I want** merged work closed automatically, **so that** branches, worktrees, issues, and run state do not remain stale.

**Acceptance criteria:**
- [ ] Only GitHub-verified delivery permits closeout and destructive cleanup.
- [ ] IssueForge closes the exact run issue, comments with its PR and verification result, and updates its parent epic without another approval.
- [ ] Proven-safe local/remote branches and clean worktrees are removed; dirty or unverifiable state is preserved and reported.
- [ ] Repeated closeout is idempotent and produces the same completed state without duplicate comments or failures.

### US-9: Operate through CLI and TUI — [M2]

**As a** developer, **I want** terminal-native control and visibility, **so that** I can run IssueForge directly or interactively.

**Acceptance criteria:**
- [ ] CLI and Textual TUI invoke the same engine commands and consume the same structured event stream.
- [ ] The TUI displays queue position, current stage, logs, diffs, approvals, failures, PR status, and cleanup warnings.
- [ ] Closing either interface does not terminate or corrupt persisted workflow state.
- [ ] IssueForge defines two AI **roles**, never two vendors: the **primary AI** authors and implements, and the **secondary AI** independently reviews. Each role binds to a provider profile whose executable, start, resume, and authentication commands are **configuration variables**. No provider name is hardcoded anywhere in the engine.
- [ ] **If no secondary provider is configured, the review role falls back to the primary provider in a brand-new session**, never the authoring session. Independence comes from session and role separation; a different vendor is a strengthening, not a requirement. The role, the provider, and the session identity are recorded on every AI result, and a review whose session identity equals the authoring session's is rejected.

### US-10: Retain a safe audit trail — [M3]

**As a** developer, **I want** recoverable and privacy-conscious run evidence, **so that** decisions can be audited without retaining unlimited sensitive data.

**Acceptance criteria:**
- [ ] State transitions, approvals, overrides, commit/PR identifiers, contract manifests, verification summaries, and cleanup outcomes persist permanently.
- [ ] Redacted prompts, responses, full command output, diffs, and review packets expire after 30 days by default.
- [ ] Authentication tokens, credential files, environment-variable values, detected secrets, and hidden model reasoning are never retained.
- [ ] Retention is configurable and `issueforge purge` removes eligible artifacts without damaging active runs or permanent manifests.

### US-11: Preserve proven MARVIN safeguards — [M3]

**As a** maintainer, **I want** IssueForge stages derived from MARVIN's lessons and tested scripts, **so that** simplification does not discard failure-driven safeguards or duplicate working code.

**Acceptance criteria:**
- [ ] Before stage implementation, its design record inventories the corresponding MARVIN **build-harness** artifacts drawn from a **versioned, checked-in extraction manifest** that declares which MARVIN skills, scripts, and tests belong to the build harness, as distinct from the MARVIN chief-of-staff workspace, which IssueForge does not extract (see D6). The inventory unit is a **test**: every test in each inventoried artifact's test files receives an explicit disposition.
- [ ] Every inventoried test is classified — ported, replaced-with-reason, or discarded-with-reason (deterministic engine policy, AI judgment, human approval, or MARVIN-specific behavior to discard) — with a reason; a behavior with no test is recorded as an author-supplied entry. **A human approves each stage audit before that stage is implementation-ready.** The completeness check is mechanical; whether a `discard` is correct, and whether an artifact belongs in the extraction manifest, are human judgments a lint cannot make.
- [ ] Applicable scripts are extracted and refactored behind IssueForge interfaces when safe; rewrites document why extraction was unsuitable.
- [ ] Tests explaining reused safeguards are ported with the code and remain traceable to the source behavior.
- [ ] IssueForge runs without a MARVIN checkout or MARVIN runtime dependency.
- [ ] IssueForge never writes MARVIN skills, context, state, ledgers, configuration, or generated files for MARVIN's use.
- [ ] MARVIN and other systems consume IssueForge information through documented read/query interfaces; IssueForge remains the sole owner of its run state and artifacts.

## Module Design

- **Workflow engine:** owns persistent state transitions, approvals, bounded retries, pause/resume, parking, and queue dispatch; depends only on module interfaces.
- **Issue shaper:** owns readiness assessment, deduplication, revision, decomposition, footprint estimation, and approved GitHub mutation plans.
- **Repository registry:** owns friendly aliases, clone validation, remote normalization, default-branch facts, and repository configuration loading.
- **Workspace manager:** owns fresh-base worktrees, branch lifecycle, isolation proofs, and predicate-based cleanup.
- **AI provider layer:** owns subscription CLI authentication, start/resume invocation contracts, session identity, role separation, and captured results.
- **Acceptance contract:** owns meaningful-red evidence, approved snapshots, dependency boundaries, integrity verification, and authorized revisions.
- **Verification runner:** owns baseline, targeted acceptance, lint, build, timeout, and structured command results.
- **Observability policy:** classifies boundary changes, adds logging requirements to shaped contracts, and supplies deterministic and reviewer checks for diagnostic coverage and sensitive-data exclusions.
- **GitHub gateway:** owns scoped issue/epic/PR operations, merge verification, comments, and remote-branch deletion; never merges.
- **Run store and queue:** owns atomic manifests, locks, append-only events, FIFO order, parking, retention, and recovery.
- **CLI/TUI interfaces:** render events and submit commands without owning business state.

## Implementation Decisions

- Version one supports any explicitly registered local GitHub clone **whose test framework has an installed verification adapter**, and executes only one active run; a persistent queue and explicit parking are included. Only a pytest adapter ships in version one; registration refuses anything else.
- Repository-agnostic orchestration is achievable; framework-neutral semantic test integrity is not. An exit code cannot distinguish a behavioral failure from a compile error, a collection error, zero tests collected, a skipped suite, or a timeout. The portable seam is therefore the **verification adapter interface** — prepare a hermetic environment, enumerate approved tests, run a selection, report structured execution and failure phases, normalize behavioral evidence, and detect zero/skipped/deselected tests — **not raw process output**. Adding Go, Cargo, or Jest support is an adapter, not a re-architecture.
- An implementation that behaves differently under test (for example, branching on a test-runner environment variable) defeats every static integrity check, including file hashing and import-closure analysis. This residual risk is carried explicitly by the independent code review, which is instructed to look for test-context-dependent behavior, and by hermetic, separately provisioned verification runs. It is not claimed to be eliminated.
- Stage design is refactor-first: inspect corresponding MARVIN skills, scripts, tests, and recorded fixes before writing replacement code, while keeping IssueForge runtime-independent.
- Integration is one-way: source systems and future consumers may read IssueForge interfaces, but IssueForge does not maintain consumer-specific files or push state into MARVIN.
- One branch contains a separately committed approved test contract followed by implementation; only one green PR enters main.
- Tests must demonstrate an expected behavioral red state, not merely any failure.
- Approved tests and their discovery/configuration boundary are deterministically frozen; any change requires human authorization.
- The engine speaks of a **primary AI** (authors and implements) and a **secondary AI** (independently reviews). These are roles bound to provider profiles by configuration; the engine names no vendor. Every provider uses an existing subscription login. Direct model APIs and API-key fallback are prohibited. Where no secondary provider is configured, the review role runs on the primary provider in a fresh session — role and session separation is the load-bearing property, and provider diversity is an optional strengthening on top of it.
- Independent test and code reviews require fresh sessions and support explicit recorded fallback or human override. The override is human-only, per-finding, bound to the reviewed commit, and reported in the PR; it never waives deterministic evidence (US-5, US-6).
- Retry budgets are engine state, not model discretion. MARVIN expressed its caps as prose that a model session had to remember to honor; IssueForge persists them, increments them under the store lock, and gates the transition, so a budget cannot be forgotten or exceeded by an AI session. It keeps `review_rounds` and `repair_attempts` separate because "iterate on nearly-right code" and "discard the attempt and restart from base" are opposite recoveries; collapsing them lets one transient implementer failure consume the budget intended for review iteration. Both default to 2 and are configurable, so quota pressure is answered by tightening a number rather than by merging the two concepts.
- Commands are argument arrays without a shell by default.
- External-boundary changes require a logging contract; independent implementation review judges diagnosability for all other changes. Libraries never introduce global logging configuration.
- Python 3.12+, uv, pytest, Ruff, Typer, and Textual form the initial implementation stack.
- **D5 — file roles are two disjoint scopes, not one.** A run has an **implementation write scope** (the paths the implementer may modify, approved by a human at shaping, governing the implementation commit range) and a **frozen contract set** (the acceptance-test files plus their adapter-discovered dependency closure, frozen at approval). They are disjoint by construction: the acceptance tests are delivered in the contract commit and are never inside the implementation write scope, so the same path is never both "expected to change" and "must not change." Deriving a file's role from membership in a single approved scope is incoherent, because the tests are in the delivered PR yet are also the frozen contract. Readiness therefore asks two clean questions over the implementation commit range: is every change inside the approved write scope, and did any frozen contract path (or the pinned identity/version of any frozen external dependency) change. A path proposed as both a contract input and an implementation target fails the freeze for the human to resolve — typically by amending the write scope or routing the issue to `blocked` when its real work is editing a shared fixture.
- **D6 — the source-audit inventory unit is a test, plus a checked-in extraction manifest.** US-11 completeness cannot be established against a curated, non-exhaustive source map, and it cannot be reduced to enumerating public symbols: a single MARVIN script carries several independently-learned safeguards, some in private functions, that a symbol-level scan would pass while classifying only one. The countable unit is therefore a **test** — every test in an inventoried artifact's test files needs a disposition (ported / replaced / discarded, each with a reason) — which is directly what US-11.4 already requires and is mechanically checkable. Behaviors with no test are author-supplied entries. Because MARVIN does not distinguish its build harness from its chief-of-staff workspace (the harness reaches into workspace state at several seams — the registry is `state/projects.md`, the run store resolves under `~/Projects/agentLogs`, the launch contract is enforced by linting `skills/*/SKILL.md` prose), the extraction scope is a **versioned, checked-in manifest** listing exactly the harness artifacts, and S2's discovery treats that manifest as its authoritative root. A lint proves the record is complete against the manifest; a human approves the record, because "this file is workspace, not harness" and "this safeguard is MARVIN-specific, discard it" are judgments a lint cannot make. Decoupling the harness from the workspace at these seams is a primary goal of version one, not an incidental cleanup.

## Testing Strategy

- **Workflow engine:** table-driven transition tests for every happy, paused, failed, parked, resumed, and idempotent path across US-2 and US-7–8.
- **Issue shaper:** GitHub fixtures for in-place revision, decomposition, deduplication, approval, and forbidden preapproval writes from US-3.
- **Repository/workspace:** temporary Git repositories covering alias normalization, dirty live checkouts, fresh worktrees, failed fetches, and safe cleanup from US-1 and US-4.
- **AI providers:** fake subprocess adapters covering auth, start/resume syntax, session separation, limits, retries, and prohibited API fallback from US-5–6 and US-9.
- **Acceptance contract:** fixtures demonstrating meaningful behavioral failure, infrastructure failure rejection, file/config mutation, collection loss, and authorized revisions from US-5–6.
- **Verification runner:** deterministic subprocess fixtures for pass, fail, timeout, malformed command, and evidence capture from US-4–7.
- **Observability policy:** fixtures for boundary classification, required event contracts, reuse of target logging conventions, sensitive-field exclusions, and reviewer evidence from US-3 and US-6.
- **GitHub gateway:** fake `gh` responses proving exact mutation scope, merge verification, epic updates, and idempotent closeout from US-3 and US-7–8.
- **Run store/UI:** crash recovery, locking, queue order, parking, event replay, retention, and CLI/TUI parity from US-2 and US-9–10.
- **MARVIN extraction:** provenance fixtures and migrated safeguard tests verify inventory, classification, extraction decisions, and runtime independence from US-11.
- **Prior art:** port behavioral safeguards from MARVIN's merged runner, agent-run atomic store, acceptance-integrity checker, and wave scheduler into focused IssueForge modules rather than copying their orchestration.

## Out of Scope

- **Deferred (in scope, later — the pointer line):** work set aside until a demo gate needs it is closed with the `deferred` label, never copied into a document; GitHub stores the text and the label query is the live index. Query: `gh issue list --state closed --label deferred --limit 200` (37 issues as of 2026-07-28: post-v1 hardening, deferred-v2 gates, CI infra, enhancements). `/agile` re-checks this list at every milestone boundary.
- Draft-PR lifecycle: deferred until the single-green-PR engine is hardened; it will become an additional GitHub event surface without changing core state.
- Concurrent issues: file-conflict scheduling within one repository and multi-repository workers are deferred until single-run recovery and safety are proven.
- Additional provider profiles (other subscription CLIs, local models): the provider interface and the primary/secondary role split ship in version one, and a second provider is a configuration entry rather than new engine code. Version one is validated against a single default provider profile.
- Non-pytest target repositories: the verification adapter interface is included, but only a pytest adapter ships initially. Go, Cargo, and Jest adapters are later work, and a repository with no adapter is refused at registration rather than degraded to a weaker contract.
- Automatic repository cloning or discovery: explicit local registration keeps filesystem authority clear.
- Automatic PR merge: the human remains the final merge authority.
- Web GUI: the engine/event boundary supports it later; version one ships CLI and Textual TUI.

## Open Questions

None.
