# IssueForge v1 architecture

## Outcome

IssueForge turns an existing issue in any registered local GitHub repository into one human-approved, TDD-built, green pull request. A regular Python process owns the workflow; subscription-authenticated AI CLIs are replaceable workers.

## Lifecycle

1. Queue an existing issue by repository alias and number.
2. Assess whether it is buildable. After human approval, update a vague issue in place or convert an oversized issue into an epic linked to new child issues.
3. Fetch the default branch and create a proven-isolated worktree without touching the normal checkout.
4. Run the repository's required baseline command. Pause if the baseline is red.
5. Invoke Codex CLI to author acceptance tests.
6. Prove the tests collect and fail for the expected missing behavior, not an infrastructure error.
7. Obtain an independent AI review, with explicit fallback to a fresh session or human override.
8. Ask the human to approve the exact test contract.
9. Commit and freeze a manifest containing test hashes, collected identifiers, configuration dependencies, command, and expected failure evidence.
10. Invoke Codex CLI to implement the issue. Permit at most two automatic repair cycles.
11. Verify contract integrity, acceptance tests, full baseline, configured quality gates, file scope, and an independent code review.
12. Push and open one green PR, then wait for the human to merge it.
13. Verify the merge, close the exact issue, update its parent epic, delete safe local/remote branches, and remove a clean worktree.

## Human gates

IssueForge pauses for human action when:

- an issue revision or decomposition would change GitHub;
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
- AI provider layer: configurable start, resume, and authentication commands; Codex is the default v1 adapter.
- Acceptance contract: meaningful-red evidence, immutable test manifest, and authorized revisions.
- Verification runner: baseline, acceptance, lint, and build command execution.
- Observability policy: mandatory diagnostic logging contracts at external boundaries and reviewer judgment elsewhere.
- GitHub gateway: scoped issue, PR, merge-status, closeout, and remote-branch operations.
- Run store and queue: one active run, persistent FIFO queue, explicit parking, and atomic events.
- Interfaces: Typer CLI and Textual TUI over the same engine and event stream.

## Repository configuration

Repositories already exist locally and are registered explicitly:

```console
issueforge repo add DandD:~/Projects/DandD
issueforge run DandD#148
```

Each target repository commits an `.issueforge.toml` containing a mandatory baseline command and optional acceptance, lint, and build commands. Commands are argument arrays and do not use a shell by default.

## AI constraints

- Version one defaults to Codex CLI through configuration, never hardcoding it into the engine.
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
