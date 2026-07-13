# MARVIN open-issue transfer audit — 2026-07-12

This ledger records open MARVIN work reviewed before IssueForge implementation. A source issue is closed only when its remaining portable harness scope is captured here and in PRD #1. MARVIN-only or mixed operational work remains open.

## Fully transferred sources

| MARVIN issue | Extracted IssueForge requirement |
|---|---|
| [#404](https://github.com/MatthewDruhl/marvin/issues/404) | Standalone host-optional harness; deterministic orchestration invokes subscription-authenticated CLIs; MARVIN is not a runtime dependency. |
| [#694](https://github.com/MatthewDruhl/marvin/issues/694) | PR readiness must enforce that a build PR targets the repository default branch; provide a reusable pure predicate suitable for local and required-CI enforcement. |
| [#712](https://github.com/MatthewDruhl/marvin/issues/712) | Preserve the phased runner analysis, state-machine invariants, approval queue, provider-auth findings, and prior-art research archived beside this ledger. |
| [#725](https://github.com/MatthewDruhl/marvin/issues/725) | Cleanup predicates use branch-content safety, cross-repo issue identity, authoritative remote reads, failure-path coverage, and explicit batch semantics. |
| [#742](https://github.com/MatthewDruhl/marvin/issues/742) | Independent AI review runs against the real branch worktree with execution capability, a literal proof command, bounded time, captured stderr, and fail-loud empty/nonzero output. |
| [#743](https://github.com/MatthewDruhl/marvin/issues/743) | Before authoring tests, discover all existing contract tests and prior issue markers; every existing test receives an explicit keep/revise/supersede disposition. |
| [#748](https://github.com/MatthewDruhl/marvin/issues/748) | Version one executes one issue at a time, avoiding ambiguous batch-halt semantics. Future parallel scheduling must explicitly define per-run independence versus a global safety stop. |
| [#749](https://github.com/MatthewDruhl/marvin/issues/749) | GitHub closeout preserves full owner/repository/issue identity; a cross-repo reference can never be reduced to a number or applied to the current repository. |
| [#752](https://github.com/MatthewDruhl/marvin/issues/752) | Future batches share a SHA-bound baseline/gate result instead of rerunning the same full suite per issue; version one avoids this through single-run execution. |
| [#754](https://github.com/MatthewDruhl/marvin/issues/754) | Every subprocess has configurable timeout, live stage/elapsed events, captured output, and a typed timeout result. CI trust may be added only through verified SHA-bound required checks. |
| [#759](https://github.com/MatthewDruhl/marvin/issues/759) | Contract integrity distinguishes assertion weakening from legitimate fixture evolution and provides an explicit human-authorized amendment path; whole-body equality alone is insufficient. |
| [#760](https://github.com/MatthewDruhl/marvin/issues/760) | Test health and cleanup safety are independent stage results. Only failed merge/delivery verification is a global stop; safe, factual closeout remains possible while health failures stay loud/nonzero. |
| [#761–#766](https://github.com/MatthewDruhl/marvin/issues/760) | Preserve exact scenarios for missing commands, red tests, sync failure, scoped cleanup predicates, idempotent reruns, and the configured green path. They become IssueForge closeout acceptance fixtures, without MARVIN's PENDING-on-main convention. |

## Load-bearing extracted invariants

1. No implementation starts until the issue contract and meaningful-red tests are approved.
2. The approved contract is physically enforced by the harness; AI cannot silently modify its files, discovery boundary, or proof command.
3. A reviewer verdict is bound to the reviewed branch SHA and backed by execution in that branch's isolated worktree.
4. No PR is recommended unless its head is pushed and verified at origin and its base is the default branch.
5. GitHub merge state is not delivery proof until the delivered SHA/content is verified on the default branch.
6. No branch is deleted while an open PR uses it as a base; retarget, verify, then delete.
7. Dirty or unknown worktrees are preserved; no reset, clean, force removal, or inferred safety.
8. Cross-repository issue references retain repository identity through closeout.
9. Every command is bounded, observable, and produces structured pass/fail/timeout evidence.
10. Gate failures remain visible and nonzero but cannot veto unrelated mutations whose own safety predicates passed.
11. Run state and approval evidence persist outside AI transcripts and are safe to resume idempotently.
12. Human merge authority is absolute.

## Acceptance-integrity lessons

- CI or the deterministic harness, not the AI session, enforces contract integrity.
- Configuration and shared fixtures that can neutralize tests belong inside the protected boundary.
- Legitimate amendments require an issue-linked reason, exact diff, renewed human approval, and a new manifest.
- Python AST checking is a framework adapter, not a universal integrity solution.
- Review output is retained as an auditable artifact, subject to the 30-day detailed-artifact policy.
- Existing issue-number markers and suites must be discovered before re-authoring, preventing stale XPASS or contradictory contracts.

## Archived source artifacts

The adjacent files are verbatim copies from MARVIN and are inputs to stage design:

- Pipeline verification report and summary.
- Pipeline evaluation.
- Phase 1 merged-runner classification and requirements.
- Phase 2 build-runner classification and requirements.
- Phase 3 state machine and requirements.
- Codex ChatGPT-plan authentication findings.
- Harness prior-art research.

## Reviewed but retained in MARVIN

These open issues include MARVIN-host behavior or product-specific work and are not closed by this transfer:

- [#370](https://github.com/MatthewDruhl/marvin/issues/370): mixed candidate batch; portable review/mutation lessons are captured, but system-tax and MARVIN eval work remain host-specific.
- [#603](https://github.com/MatthewDruhl/marvin/issues/603): portable CI, amendment, configuration-bypass, review-artifact, and framework-adapter lessons are captured; watermark, legacy ledger, and MARVIN rollup corrections remain.
- [#691–#693](https://github.com/MatthewDruhl/marvin/issues/691): MARVIN scheduled reconciliation, rollup, and divergence behavior.
- [#501](https://github.com/MatthewDruhl/marvin/issues/501): MARVIN briefing/recap burn-down and spend surface.
- [#490](https://github.com/MatthewDruhl/marvin/issues/490): MARVIN harden-report routing behavior; IssueForge's shaper uses the lesson but does not replace the skill change.

## Refactor-first source code

Before building each stage, inspect and make an explicit extract/rewrite/discard decision for the corresponding MARVIN scripts and their tests, including at minimum:

- `merged_runner.py`, `agent_runs_lib.py`, and merge-runner tests.
- `check_acceptance_integrity.py`, `check_acceptance_mutation.py`, `validate_pending_markers.py`, and their tests.
- `validate_spec_up_issue.py`, `validate_accept_body.py`, and their tests.
- `issues_to_findings.py`, `schedule_waves.py`, and their tests.
- Build recovery, PR-base verification, validator invocation, and run-log helpers referenced by the archived Phase 1–3 artifacts.

No implementation issue is ready until its source audit and provenance entry exist.

## Permanent system boundary

This archive is a one-time, read-only source extraction. IssueForge does not sync changes back to MARVIN and will not update MARVIN files, state, skills, ledgers, configuration, or generated artifacts for MARVIN's use. IssueForge is self-contained and authoritative for its own workflow data. MARVIN and other systems may query IssueForge's documented outputs when they need status or evidence.
