# IssueForge v1 architecture

## Outcome

IssueForge turns an existing issue in a registered local GitHub repository into one human-approved, TDD-built, green pull request. A regular Python process owns the workflow; subscription-authenticated AI CLIs are replaceable workers.

The engine, store, Git/GitHub layers, and the verification adapter interface are repository-agnostic. Semantic test integrity is per-framework, so a repository is supported only when a verification adapter exists for its test framework. Version one ships a pytest adapter; registration refuses anything else.

## Lifecycle

1. Queue an existing issue by repository alias and number.
2. Assess whether it is buildable and emit a buildability contract: readiness classification, duplicate verdict, unresolved design decisions, the proposed expected file scope, and the observability verdict. The human approves that contract, including the file scope, before any acceptance test is authored; the approved scope is the one enforced at PR readiness and is never derived from the resulting diff. After human approval, update a vague issue in place or convert an oversized issue into an epic linked to new child issues.
3. Fetch the default branch and create a proven-isolated worktree without touching the normal checkout.
4. Run the repository's required baseline command. Pause if the baseline is red.
5. Invoke the primary AI to author acceptance tests.
6. Prove the tests collect and fail for the expected missing behavior, not an infrastructure error.
7. Obtain an independent AI review, with explicit fallback to a fresh session or human override.
8. Ask the human to approve the exact test contract.
9. Commit and freeze a manifest containing test hashes, collected identifiers, configuration dependencies, command, and expected failure evidence.
10. Invoke the primary AI to implement the issue. The engine enforces two separate, persisted repair budgets, each defaulting to 2: `review_rounds` (blocking review findings, fixed in place, worktree preserved) and `repair_attempts` (implementer died or the suite is still red after it reported done — worktree reset to base, fresh session, no prior transcript). Exhausting either pauses the run.
11. Verify contract integrity, acceptance tests, full baseline, configured quality gates, file scope, and an independent code review.
12. Push and open one green PR, then wait for the human to merge it.
13. Verify the merge, close the exact issue, update its parent epic, delete safe local/remote branches, and remove a clean worktree.

## Human gates

IssueForge pauses for human action when:

- an issue revision or decomposition would change GitHub;
- a buildability contract, including its proposed file scope, is ready for approval;
- an approved file scope must be expanded during implementation;
- generated acceptance tests are ready for approval;
- a PR is ready for human merge review;
- a step fails after bounded repair attempts;
- a new design decision is required;
- an independent AI review must be overridden.

## Modules

- Workflow engine: persistent, idempotent state transitions and bounded retries.
- Issue shaper: readiness, deduplication, revision, decomposition, and footprint estimation.
- Repository registry: case-insensitive aliases mapped to verified local clones and GitHub remotes.
- Workspace manager: fresh-base worktrees and predicate-based cleanup.
- AI provider layer: two roles — primary (authors, implements) and secondary (independently reviews) — bound to provider profiles by configuration; configurable start, resume, and authentication commands; no vendor named in the engine.
- Acceptance contract: meaningful-red evidence, immutable test manifest, and authorized revisions.
- Verification runner: baseline, acceptance, lint, and build command execution.
- Observability policy: mandatory diagnostic logging contracts at external boundaries and reviewer judgment elsewhere.
- GitHub gateway: scoped issue, PR, merge-status, closeout, and remote-branch operations.
- Run store and queue: one active run, persistent FIFO queue, explicit parking, and atomic events.
- Interfaces: Typer CLI and Textual TUI over the same engine and event stream.

## MARVIN extraction rule

Before an IssueForge stage is designed or implemented, inspect every corresponding canonical MARVIN skill, supporting script, test suite, and failure-driven update. Build a traceability record that classifies each applicable behavior as deterministic engine policy, AI judgment, human approval, or MARVIN-specific behavior to discard.

Prefer extracting and refactoring proven scripts behind clean IssueForge interfaces when their contracts remain applicable. Rewrite only when coupling or assumptions make extraction unsafe; never reimplement a safeguard merely because its current code lives in MARVIN. Port the tests that explain the safeguard alongside reused code. IssueForge must have no runtime dependency on a MARVIN checkout.

The relationship is permanently one-way. MARVIN is read-only migration provenance, not a host or persistence adapter. IssueForge never writes MARVIN skills, context, state, ledgers, configuration, or generated files for MARVIN's use. IssueForge owns its registry, runs, approvals, logs, and artifacts. MARVIN and other consumers may read or query IssueForge through documented CLI/JSON, event, and artifact interfaces; consumers pull from IssueForge rather than IssueForge pushing into their private storage.

Initial source map:

- Shape: `findings-to-issues`, `prd-to-issues`, and spec-up readiness rules.
- Author contract: `spec-up`, write-a-prd acceptance authoring, and acceptance validators.
- Build: `spec-dev`, `tdd`, and the agent contract.
- Verify/review: acceptance-integrity, mutation, and pending-marker scripts and tests.
- Deliver/close: `merged`, `merged_runner.py`, and its tests.
- Parallelize later: `spec-wave`, `issues_to_findings.py`, and `schedule_waves.py` with their tests.

## Repository configuration

Repositories already exist locally and are registered explicitly:

```console
issueforge repo add DandD:~/Projects/DandD
issueforge run DandD#148
```

Each target repository commits an `.issueforge.toml` containing a mandatory baseline command and optional acceptance, lint, and build commands. Commands are argument arrays and do not use a shell by default.

## AI constraints

- The engine addresses a **primary AI** (authoring, implementation) and a **secondary AI** (independent review). Both are roles bound to provider profiles through configuration; the engine hardcodes no vendor. When no secondary profile is configured, the review role runs on the primary provider in a **new session** — never the authoring session, and never a resumed one. Session and role separation is what makes the review independent; a different vendor strengthens it but is not required.
- Provider profiles define executable, start arguments, resume arguments, and authentication checks.
- AI access uses an already authenticated monthly-plan CLI; IssueForge never falls back to metered model APIs.
- Author and reviewer roles use separate sessions. Other subscription CLIs and local models are future adapters.

## Target-project observability

Every shaped issue records an observability impact: `required`, `existing coverage sufficient`, or `not applicable`. Logging is required when changed code crosses an HTTP, database, subprocess, filesystem, queue, third-party service, or AI boundary. The issue contract names the important success/failure events and sensitive fields that must never be emitted.

Implementers reuse the target project's logger, levels, formats, and correlation conventions. Libraries do not install global logging configuration. The independent implementation reviewer judges diagnosability for every change, including work without a predefined external-boundary rule. The final PR reports logging added, reused, or intentionally unnecessary.

## Storage and retention

Concise manifests and event histories are retained permanently. Redacted prompts, responses, complete command output, diffs, and review packets expire after 30 days by default. Secrets and hidden model reasoning are never retained.

## Deferred designs

### Draft-PR lifecycle

A future interface may open a draft PR immediately after test approval, publish progress there, add implementation commits, and mark it ready when green. The workflow engine and contract remain unchanged; GitHub becomes an additional event and approval surface.

### Parallel execution

Version one is repository-agnostic but has one active worker and a persistent queue. Future releases may schedule file-disjoint issues within one repository, then run multiple repositories concurrently using deterministic footprint conflict graphs.
