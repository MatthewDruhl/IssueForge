# IssueForge — Context Manifest

**Type**: Personal (standalone TDD issue runner — harness extracted from MARVIN's build pipeline)
status: active
note: Human-gated TDD issue runner (the build harness extracted from MARVIN, not the chief-of-staff workspace). v1 main line built strictly sequential by slice id: S9 #13, S20 #14, S10 #16 (the load-bearing deterministic red-proof), S11 #17 + hardening #82, and S12 #18 all shipped through the full pipeline (spec-up -> Codex adversarial gate -> human plain-English gate -> spec-dev -> Codex build-gate -> merge); main 796 passed. 2026-07-23 REPRIORITIZED to PoC-before-hardening: milestones M1 walking skeleton (#18-#22, ends at the first real green PR) -> M2 full loop + TUI (#23-#25, #27) -> M3 v1 completion (#15, #26, #28), then batched hardening. Gate findings now get exactly two dispositions - fix inline (happy-path correctness) or label post-v1 and do NOT schedule (robustness) - recorded in CLAUDE.md via PR #94; 12 hardening follow-ups relabeled post-v1 accordingly (#42/#43/#67/#69/#70/#73/#76/#79/#83/#87/#90/#91). Test workflow: PR #100 added a Makefile (`make test-fast TEST=<file>` for the dev loop, `make test` for the full gate) and routed CI through it with -n auto; full suite 4:53 -> 1:19 locally. Next: S13 #19 (enforcement gate) via /spec-up -> #20 -> #21 -> #22. Filed post-v1: #101 (hook to block raw full-suite pytest).
Last updated: 2026-07-23

> This manifest is read by MARVIN's `scripts/generate_projects.py` for the
> `status:` and `note:` fields above. Keep `note:` to a single current-state
> line; dated history belongs in the repo's GitHub issues + MARVIN `sessions/`.

## What This Is

A standalone, human-gated test-driven-development issue runner: it takes a GitHub
issue with committed PENDING acceptance tests and drives it red → green through a
plan-authenticated implementer, with a deterministic red-proof and an AI
contract-review gate. It is the build HARNESS extracted from MARVIN's spec-driven
pipeline (`/spec-up` → `/spec-dev` → `/merged`), rebuilt as its own project so the
orchestration is reusable outside the chief-of-staff workspace.

## Canonical Sources (what is authoritative for what)

- **Status / in-flight**: MARVIN `state/current.md` (priority 1), `state/projects.md`
- **Code**: `MatthewDruhl/IssueForge` (private), local at `~/Projects/IssueForge/`
- **Design / plan**: PRD #1 + the 25-slice decomposition (#4–#30); `docs/`, `.plans/`
- **Decisions (why)**: `./decisions.md` (this repo) + PRD amendments on #1
- **Issues / backlog**: GitHub issues on `MatthewDruhl/IssueForge` (authoritative work list; issue #s ≠ slice order)

## Stakeholders

- **Matt**: sole developer/owner. Personal project.

## Key Decisions

- **Harness, not workspace** (2026-07-18): IssueForge is the extracted build pipeline, kept separate from MARVIN the chief-of-staff.
- **Strict-sequential v1 main line by slice id** (S9 → S10 → S11 → S12 → S13 → phase 4); hardening follow-ups run off to the side.
- **Every slice ships through the full pipeline** — no direct `/tdd`; each build passes a Codex adversarial suite gate AND a Codex build-gate before merge. The gates repeatedly catch real defects a green suite misses (S8 classifier chain, S11 write-anywhere + proof-not-bound-to-HEAD, S12 idempotency).
- **PENDING convention** = pytest `xfail(strict=True)`; acceptance suites committed PENDING on main before the build slice.
- **Fixture-must-exercise-the-spec** (S12 #18 lesson): a suite can pass every text-review + xfail gate while its fixtures encode a weaker model than the spec; the build-gate is what catches it.

## Current Status (2026-07-23)

- **v1 slices S9–S11 + S11 hardening shipped** through the pipeline (see note). S10 #16 = "the single most important line" (deterministic red-proof: `contract.py`/`prove_red` + `select_baseline` + wasxfail seam).
- **S12 #18 CHECKPOINTED (open decision):** spec-up merged (PR #88); build PR #89 (branch `b70929d`, CI-green) holds 17 of 24 build-gate fixes. The remaining 7 findings CONFLICT with the acceptance suite — verified against the test code as genuine suite-vs-D5 defects (my spec-up fixtures encode a weaker model than the composition formula), routed to elevated **#90**. Disposition pending: **B** correct the suite first (re-author the 7 D5-divergent tests via `/spec-up #90`, then re-fix impl and ship a D5-faithful S12) vs **A** ship the weaker contract now to unblock S13. Lean = B for a load-bearing freeze gate.
- **Hardening / follow-up backlog:** #90 (suite correction), #91 (lint gap), #87/#83 (S11 reprove-side-effect + remove_scratch defense-in-depth), #79/#73/#76 (S10/S9/S20 robustness), earlier #67/#69/#70.

## Where to Find Details

- PRD #1 + slice decomposition #4–#30 (GitHub)
- `./decisions.md` (this repo)
- MARVIN `state/current.md` priority 1 (running narrative) + `sessions/`
