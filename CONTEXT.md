# IssueForge — Context Manifest

**Type**: Personal (standalone TDD issue runner — harness extracted from MARVIN's build pipeline)
status: active
note: Human-gated TDD issue runner (the build harness extracted from MARVIN, not the chief-of-staff workspace). v1 main line built strictly sequential by slice id: S9 #13, S20 #14, S10 #16 (the load-bearing deterministic red-proof), S11 #17 + hardening #82, S12 #18, and S13 #19 all SHIPPED through the full pipeline. S13 #19 (contract-integrity enforcement + validate_invocation + amendment path) SHIPPED 2026-07-24 (PR #104 merged) after a 4-round adversarial remediation: build passed its suite while enforcement was inert -> Codex build-gate NO-SHIP/13 -> two-phase fix (11 strengthening tests + impl) -> NO-SHIP/6 -> in-house rounds 3-4 (the Codex gate got BLOCKED by its cybersecurity content filter, so the adversarial review was taken over in-house). Round 4 found `-o addopts=-x`/`--override-ini` inject any dangerous mode past the flag scan, so validate_invocation was redesigned from a leaky flag-DENYLIST to a default-deny ALLOWLIST. 5/6 findings + the whole bypass class fixed; tracked-.pyc (#106) + env-file-added-at-HEAD (#107) deferred post-v1; CI speedup #108 (14-min real-venv suite). PoC-before-hardening milestones (M1/M2/M3) still open; gate findings get two dispositions (fix-inline happy-path vs label post-v1). Filed post-v1: #101 (raw-pytest hook).
Last updated: 2026-07-24

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
- **Anti-gaming guards need allowlists, not denylists** (S13 #19 lesson): a flag/input denylist on an adversarial surface is bottomless (each review round finds channel N+1: `-c` spellings → clusters → `-o addopts=` injection). Default-deny is the fix. The Codex build-gate can also be BLOCKED by its cyber content filter on security-adjacent slices — take over the review in-house.

## Current Status (2026-07-24)

- **v1 slices S9–S13 shipped** through the pipeline (see note). S10 #16 = "the single most important line" (deterministic red-proof). S12 #18 shipped D5-faithful.
- **S13 #19 (contract-integrity enforcement) SHIPPED — PR #104 merged 2026-07-24.** A 4-round remediation from Codex NO-SHIP/13 → /6 → in-house rounds 3-4. `validate_invocation` is now a default-deny ALLOWLIST (only sanctioned-baseline flags permitted; `-o`/`-k`/`-m`/`--deselect`/unknown refused as a class). Other fixes: gate provisions autoload-ON symmetric with freeze (#1), env-defining files freeze+hashed (#2), gate reads the amended manifest (#3), pytest 9.1 config formats + precedence (#5). Full suite 1006 passed, 2 xfailed, 0 xpassed; no acceptance test weakened.
- **Deferred post-v1 (filed):** #106 tracked-.pyc dirty-tree exemption (forged-bytecode; fights the freeze/collection model), #107 env-file-added-at-HEAD (not run-affecting), #108 CI speedup (uv cache + split real-venv tests; the suite is ~14 min in CI).
- **Hardening / follow-up backlog:** #101 (raw-pytest hook), #91 (lint gap), #87/#83, #79/#73/#76, earlier #67/#69/#70. PoC-before-hardening milestones M1/M2/M3 (#15/#20-#28) still open.

## Where to Find Details

- PRD #1 + slice decomposition #4–#30 (GitHub)
- `./decisions.md` (this repo)
- MARVIN `state/current.md` priority 1 (running narrative) + `sessions/`
