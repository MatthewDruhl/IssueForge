# IssueForge — Context Manifest

**Type**: Personal (standalone TDD issue runner — harness extracted from MARVIN's build pipeline)
status: active
note: Human-gated TDD issue runner (the build harness extracted from MARVIN, not the chief-of-staff workspace). v1 main line S9-S13 SHIPPED through the full pipeline; then PoC-before-hardening M1 walking skeleton BUILT: the Wave-1 seams (#114 candidate / #112 readiness / #113 delivery) + composed default stage #115, then #129 (real provider profile + pre-authoring human scope gate) and #135 (operator-level provider role resolution — role config reads paths.providers_config(): ISSUEFORGE_PROVIDERS env else ~/.config/issueforge/providers.toml; repo .issueforge.toml stays minimal) all shipped spec-up->spec-dev->/merged, Codex-gated, blocking fixes inline. First live `issueforge run DandD#111` (2026-07-25) = shakedown: reached scope->fetch->worktree->paused-at-baseline; integration gaps batched into epic #144 (#140 headless mode, #141 baseline-env/pytest-reportlog, #142 queue-wedge, #143 real read_issue_body contract_paths=[]). Next: work epic #144 top-down, then re-attempt the live run; M2/M3 after.
Last updated: 2026-07-25

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
- **Buildability probe is standing** (#115 lesson): an acceptance suite can pass every text-review + Codex gate + green CI and still be UN-buildable (fixture encodes a collection-phase red the real prove_red rejects). Every authored suite gets a real-seam dry-run proving a CORRECT impl CAN pass, not just that a wrong impl fails.
- **Provider config is operator-level, not per-repo committed** (#135, 2026-07-25): the target repo's `.issueforge.toml` stays the minimal build contract (baseline/acceptance/framework); role resolution reads an operator-level providers config (`ISSUEFORGE_PROVIDERS` env → default `~/.config/issueforge/providers.toml`), consistent with `provider-check --config`. Surfaced by DandD #188's minimal-keys guard.
- **First live run is a shakedown** (2026-07-25): a fake-seam-tested pipeline surfaces integration gaps (interactive stdin gates, env deps, single-worker queue semantics, empty real inputs) only on the first real invocation; batch them into an epic, don't whack-a-mole live.

## Current Status (2026-07-25)

- **v1 slices S9–S13 shipped** + **M1 walking skeleton BUILT.** The M1 PoC wave (#188 DandD onboarding + #114 candidate / #112 readiness / #113 delivery) and the composed default stage **#115** shipped; then **#129** (real provider profile + pre-authoring human scope gate, PR #133) and **#135** (operator-level provider role resolution, PR #137) shipped — all spec-up→spec-dev→/merged, Codex-gated, blocking findings fixed inline.
- **First live `issueforge run DandD#111` (2026-07-25) = shakedown.** Operator config `~/.config/issueforge/providers.toml` created (`provider check` green). The run reached scope→fetch→worktree→paused-at-baseline; integration gaps the fakes can't catch batched into **epic #144** (ordered task list): #140 headless mode (`--scope`/`--yes`), #141 baseline-env requires pytest-reportlog (+ DandD PR #193), #142 paused-run wedges the worker slot, #143 real `read_issue_body` returns `contract_paths=[]`.
- **Deferred post-v1 (filed):** #134 (#129 robustness), #138 (#135 operator-config load error handling), #124 (delivery robustness), #126 (real contract-integrity), #139 (#135 missing-config test coverage, v1); earlier #101/#106/#107/#108/#91/#87/#83/#79/#73/#76/#67/#69/#70.
- **Milestones:** M1 (walking skeleton) essentially built; real-run readiness (#144) is the gate to a first green live PR. M2 (full loop + TUI #23–#25/#27) and M3 (v1 completion #15/#26/#28) after.

## Where to Find Details

- PRD #1 + slice decomposition #4–#30 (GitHub)
- `./decisions.md` (this repo)
- MARVIN `state/current.md` priority 1 (running narrative) + `sessions/`
