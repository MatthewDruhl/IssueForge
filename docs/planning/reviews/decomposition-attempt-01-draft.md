# IssueForge v1 — PRD #1 decomposition (draft v2, post-Codex-review)

**Repo:** MatthewDruhl/IssueForge · **Source PRD:** #1 · **Date:** 2026-07-13
**Review status:** v1 draft → Codex REVISE (12 blocking findings) → this v2 addresses all 12 → re-review.

PRD #1 and `docs/prd-v1.md` are byte-identical. **51 acceptance criteria / 11 user stories. 21 child issues.**

## Governing rule adopted after review

**The PRD is the authority. This decomposition does not amend it.**
Codex's finding #10 is correct and load-bearing: *"MARVIN's three nested counters are provenance, not
authority to reopen the PRD."* Where MARVIN's experience contradicts the PRD, the PRD wins and the tension is
recorded as a **risk**, not as a blocking question inside an implementation issue. Where prior art suggests
work the PRD does not require (mutation testing, the invariant lens), it is filed as an explicitly
**`deferred-v2`** issue — surfaced, tracked, and NOT smuggled into the v1 acceptance graph.

## Changes from draft v1 (all 12 blocking findings)

| # | Codex finding | Fix |
|---|---|---|
| 1 | US-8.2 parent-epic update missing (matrix concealed it) | Verified at `prd-v1.md:93`. New **#16** owns it, idempotent + repo-qualified. |
| 2 | US-9.2 silently weakened ("logs/diffs may ship thin") | Verified at `prd-v1.md:103` — **eight** views. Trim REMOVED; **#22** ships all eight. |
| 3 | Repo-agnostic product decomposed pytest-only | **#2** now owns a framework-neutral `VerificationAdapter` contract + an explicit v1 support policy. |
| 4 | Call-phase ≠ "failed for the NAMED reason" | Boundary corrected: **#9** deterministic (collection/phase/baseline/sha/zero-collected/XPASS); **#10** AI+human own semantic correspondence. |
| 5 | "Absolute" gate not complete (hashes frozen, never compared) | **#12** now recomputes and compares EVERY frozen dependency hash at head; the protected set is **engine-discovered**, not glob-trusted. |
| 6 | Observability ordered after its consumers | Moved to **#8**, before shaping (#17), the readiness gate (#13), and the PR body (#14). |
| 7 | Oversized / horizontal issues | Store merged into the enqueue slice (no standalone storage layer). #10/#12/#13 and closeout split. 21 issues, none XL. |
| 8 | Source audit is prose, not a testable artifact | **#20** builds a machine-checked provenance artifact + lint; US-11.1–11.4 become enforced, not "DoD". |
| 9 | Boundary proof can pass, then be reintroduced | **#21** is a **permanent CI invariant** with a write monitor, not a one-time issue. |
| 10 | PRD reopened (retry caps, fix-rounds, watch mode) | **PRD followed.** Repair budget = 2 (US-6.2). Watch mode = read-only observation, mutations separately gated. Tensions → Risks. |
| 11 | #13 "every anomaly HALTS" recreated the coupling it discarded | Fixed: only failed **delivery verification** is a global destructive stop; health failures stay loud/nonzero without vetoing independently-safe cleanup. |
| 12 | Delete-safety was a slogan, not a predicate | **#15** binds the exact GitHub merge commit + recorded PR head sha; defines missing/disagreeing facts. |

Also fixed: single-owner rule for double-mapped criteria (downstream repeats are labeled integration
assertions); `closingIssuesReferences` moved from the PR issue to closeout; torn-final-JSONL recovery
specified; epic idempotency keyed on persisted operation IDs, not titles; typed stage failures replace the
copied anomaly-name catalogue; "zero new dependencies" demoted from law to default.

---

## The six cross-cutting rules (in EVERY issue's Preserve section)

Each is a separate line of defensive code in `merged_runner.py`, and **each one was a review finding.**

1. **A failed read is NEVER negative evidence.** Failed `gh pr view` ≠ unmerged. Failed `git status` ≠ clean.
   Failed `git worktree list` ≠ no worktree. Failed `gh pr list --base` ≠ no stacked PRs. Failed
   `git branch --contains` ≠ no branches. **Empty AI output ≠ no findings.**
2. **Honor every return code.** Never report success on a command whose exit status you did not check.
3. **Verify at the boundary; do not trust the report.** Agents report "pushed" when the push silently failed
   (#604/#607). **The authoritative test run is the ENGINE's, not the agent's.**
4. **Content, not ancestry** (squash merges) — via a tri-state predicate whose only trustworthy negative is
   exit 1. **See #15: this is only real once bound to exact SHAs.**
5. **The contract is enforced by the harness or CI, NEVER by the session being policed.**
6. **Every gate needs a legitimate escape hatch, or people route around it** (#759 — the amendment path).

## Headline findings

**A. The load-bearing control has no prior art.** `check_acceptance_integrity.py` imports only
`argparse, ast, sys, pathlib` — it never runs pytest, never collects, never executes. "Verify it is red
today" is prose in one place (`spec-wave/SKILL.md:137`). Red-proof looks smaller than it is; #9 is sized
accordingly.

**B. The zero-collected false-read is LIVE IN MARVIN — verified first-hand.**
`merged_runner._parse_pytest_summary` (`:681-693`) regexes `N passed`/`N failed` and flags `red-main` on
`failed > 0 or returncode != 0`. It cannot see pytest exit 5, a collection error, or XPASS. **It fired on
2026-07-12**: a DandD `/merged` run reported `red-main` with `passed: 0, failed: 0` — a suite that collected
NOTHING. (`tests/test_root_pytest_collection.py` (#425) guards MARVIN's OWN collection; it is not a reusable
predicate.) Zero-collected is a THIRD state: **broken.**

**C. IssueForge's integrity gate can be strictly stronger than MARVIN's.** MARVIN needs a sanctioned
exception because its implementer removes the PENDING marker. IssueForge drops PENDING-on-main (#761–#766),
so there is no flip, so the protected-path diff gate needs **no carve-out**. But per Codex #5, the gate is
only complete when frozen dependency hashes are **recomputed and compared at head** and the protected set is
**engine-discovered**, not user-glob-declared.

**D. Determinism has a hard ceiling.** Call-phase failure proves the test *executed and failed*. It does
**not** prove it failed for the *named expected reason*. That correspondence is semantic and belongs to the
AI reviewer and the human approver. Claiming otherwise builds a gate that cannot do its job.

## PRD gaps (prose requirements with no acceptance criterion) — each given a home

G1 argv-array/no-shell rule beyond the baseline command (shell-injection surface, zero coverage) → **#2**.
G2 subprocess timeouts (a hung CLI is undefined in v1) → **#2**. G3 "libraries never install global logging
config" → **#8**. G4 author/reviewer session separation mechanism → **#7**. G5 locking → **#4**. G6 crash
recovery / event replay → **#4**. G7 `issueforge continue` semantics → **#5**. G8 optional
acceptance/lint/build commands → **#2**. G9 queue reorder/cancel verbs → **#5**. G10 PR body reports logging
added/reused/unnecessary → **#14**. G11 **not-testable triage exit** (MARVIN routes refactor/doc/research
issues elsewhere; IssueForge has no such exit, so they'd be forced through a TDD contract they cannot have)
→ **#17**.

---

## The 21 child issues

| # | Title | Phase | Size | Criteria owned | Blocked by |
|---|---|---|---|---|---|
| 2 | Subprocess seam, tri-state results, config, **framework-neutral verification adapter** | 0 | M | G1, G2, G8 | — |
| 3 | Register a repository and resolve it | 0 | S | US-1.1–1.4, US-4.1 | 2 |
| 4 | Run store + enqueue + stub stage (one locked write path, redacting) | 0 | M | US-2.1, US-10.1, US-10.3, G5, G6 | 2, 3 |
| 5 | Queue control: FIFO, pause, park, cancel, resume, `continue` | 0 | M | US-2.2–2.4, US-7.4, US-9.3, G7, G9 | 4 |
| 6 | Isolated worktree from verified origin + green baseline gate | 1 | M | US-4.2–4.4 | 3, 4 |
| 7 | AI provider layer: guarded launch, subscription-only, session separation | 1 | M | US-6.4, US-9.4, G4 | 2, 4 |
| 8 | Observability contract (boundary classifier + required verdict) | 1 | M | US-6.5–6.7, G3 | 2 |
| 9 | Author acceptance tests + **deterministic** red proof | 2 | M | US-5.1, US-5.2 | 6, 7 |
| 10 | Independent review: **semantic** red validity + recorded override | 2 | M | US-5.3, US-5.4 | 9 |
| 11 | Human approval freezes the contract manifest | 2 | M | US-5.5 | 9, 10 |
| 12 | Contract integrity enforcement + **amendment path** | 2 | L | US-6.1 | 11 |
| 13 | Implement under contract; bounded repair (2); readiness gate | 3 | L | US-6.2, US-6.3 | 12, 8 |
| 14 | One green PR, verified at origin; never merged | 4 | M | US-7.1–7.3 | 13, 8 |
| 15 | **Delivery verification**: exact merge-commit + head-sha binding | 4 | M | US-8.1 | 14 |
| 16 | Closeout: comment, close exact issue, **update parent epic**; idempotent | 4 | M | US-8.2, US-8.4 | 15 |
| 17 | Safe cleanup: branches + worktrees (independent stage result) | 4 | M | US-8.3 | 15 |
| 18 | Shape an issue: in-place revision + pause conditions + not-testable exit | 5 | M | US-3.1, US-3.4, G11 | 5, 7, 8 |
| 19 | Epic decomposition of an oversized issue | 5 | M | US-3.2, US-3.3 | 18, 16 |
| 20 | **Source-audit artifact + lint** (makes US-11.1–11.4 enforceable) | 0 | M | US-11.1–11.4 | — |
| 21 | Retention and `issueforge purge` | 5 | S | US-10.2, US-10.4 | 4 |
| 22 | TUI + CLI/TUI parity — **all eight views** | 5 | M | US-9.1, US-9.2 | 5, 14, 17 |
| 23 | Self-contained boundary: **permanent CI invariant** | 5 | M | US-11.5–11.7 | ALL |

Plus two **`deferred-v2`** issues (surfaced, tracked, NOT in the v1 acceptance graph — see Scope Additions):
**#24** blocking mutation / anti-tautology gate · **#25** the invariant lens for shaping.

**Recommended first vertical slice: #3** (`repo add` → `repo list`). Smallest demoable end-to-end unit.
**Recommended first full tracer bullet: #2 → #3 → #4 → #5 → #6** — the first chain with **no AI in it at
all**: `repo add` → enqueue → fetch → isolated worktree → run baseline → pause. Deterministic tests, zero
provider dependency, and it de-risks every seam the rest of the system sits on.

**Single-owner rule.** Where a criterion appears downstream (US-7.4 in #14, US-9.3 in #22, US-6.3 in #14,
US-10.1 in #21), the downstream appearance is an **integration assertion**, not a second implementation.
Each criterion has exactly one owning issue, named in the matrix.

---

# Issue bodies

Every issue below carries the required 14 sections. To keep this reviewable, the six cross-cutting rules are
incorporated by reference into each **Preserve** rather than restated; each issue lists only its *additional*
preserves.

---

## #2 — Subprocess seam, tri-state results, config, framework-neutral verification adapter
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem.** Every module shells out. MARVIN re-derived "a failed read is not a negative answer" by hand at
five call sites, each comment marking a shipped bug. And **IssueForge claims to be repository-agnostic while
every verification concept in the PRD's testing strategy is pytest-shaped** (Codex #3). A generic argv
baseline command cannot always yield "collected node IDs."

**User-visible outcome.** `issueforge config check <alias>` loads and validates a repo's `.issueforge.toml`,
prints the resolved argv arrays and the selected verification adapter, or fails loudly naming the field.

**PRD coverage.** G1, G2, G8. Foundation for US-4.1.

**Observable acceptance criteria**
- `CommandResult` frozen dataclass: `argv, returncode, stdout, stderr, duration_ms, timed_out`.
  `run(argv, *, cwd, timeout, env)` NEVER raises on non-zero exit.
- **Timeout is a state DISTINCT from failure** (typed `timed_out`), not a non-zero returncode (#754).
  MARVIN's gate **hangs today** and the live workaround is to skip it — *a verification runner with no
  timeout is a verification runner people turn off.*
- **An error result cannot coerce to `False`.** A predicate handed an error RAISES. Tested against all five
  MARVIN inversions.
- **`VerificationAdapter` protocol (framework-neutral):** `collect() -> CollectionResult` and
  `run_tests(selection) -> TestRunResult`, where `TestRunResult` carries a **tri-state per-unit outcome
  (`passed`/`failed`/`errored`) plus a distinct `NOTHING_COLLECTED` result** — expressed WITHOUT pytest
  vocabulary. The pytest adapter maps exit 5 → `NOTHING_COLLECTED`, and `error` (collect/setup/teardown) vs
  `failure` (call) → `errored`/`failed`.
- **Explicit v1 support policy, written down:** the **pytest adapter is the only adapter shipped in v1**. A
  repo whose baseline is not pytest-compatible gets a **`generic` adapter** that supports ONLY
  pass/fail/timeout on the whole command and **cannot support the contract-freeze features that require
  collection identity** — and IssueForge **REFUSES to author a contract for it** rather than silently
  degrading the gate. (Provenance is explicit: *"Python AST checking is a framework adapter, not a universal
  integrity solution"*; and pipeline-eval #7 — *"for any non-Python project the model-free layer does not
  exist, and no skill says so."* IssueForge says so.)
- `.issueforge.toml`: `baseline` REQUIRED as an argv array; optional `acceptance`/`lint`/`build`;
  `contract_paths`; `sensitive_fields`. **A shell string where argv is required is REJECTED at load time**,
  naming the field. `run()` never uses `shell=True`.
- The test `FakeRunner` enforces a **READ-ONLY ALLOWLIST**: any command not a known read-only prefix and not
  explicitly registered **raises AssertionError**, so an unforeseen destructive command (`git reset`/`clean`/
  `checkout`/`rm`/`update-ref`, `gh api`) is caught **BY CONSTRUCTION, not by a denylist** (port the pattern
  from `tests/test_merged_runner.py` :44-51). An inverted-default harness is how you *prove* nothing
  destructive ran.
- CLI discipline: success → exit 0, payload on stdout; failure → exit 1, message on **stderr, stdout EMPTY**,
  no traceback. Lints report EVERY violation, no fail-fast.

**Footprint.** `src/issueforge/process.py`, `config.py`, `adapters/` (`base.py`, `pytest_adapter.py`,
`generic.py`), `tests/test_process.py`, `test_config.py`, `test_adapters.py`, `tests/conftest.py`, `cli.py`.

**Dependencies.** Blocked by: none. Unblocks: everything.
**Deterministic / AI / Human.** All deterministic. No AI. No runtime approval.
**Failure & recovery.** Malformed config fails at load naming the field. Timeout returns a typed result.
**Logging.** REQUIRED — this IS the subprocess boundary. Every invocation emits argv/cwd/duration/exit/
timed_out. stderr always captured; `2>/dev/null` forbidden (#713). Raw output is persisted only via #4's
redacting writer.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:80-101` (`RunResult`/`run`), `:164-190, 293-311, 313-330, 460-469, 735-760,
  763-780` (the five inversions), `:681-693` (`_parse_pytest_summary` — the false-read); `open-issue-transfer`
  #754; `tests/test_merged_runner.py:44-51` (FakeRunner allowlist); memory `feedback_stderr_capture_background`.
- *Preserve:* cross-cutting rules 1 & 2. `run()` never raises on non-zero. stderr always captured.
- *Refactor/extract:* `merged_runner.run`/`RunResult` port near-verbatim, extended with timeout/duration.
  The FakeRunner allowlist **pattern**, not just its tests.
- *Replace:* **`_parse_pytest_summary` — DO NOT PORT.** Summary-line scraping is the concrete zero-collected
  false-read. Replace with structured collection (`check_acceptance_mutation._collect_nodeids` :150-186 and
  its in-process `pytest_runtest_logreport` plugin :187-248) behind the adapter.
  Also replace the tri-state discipline itself: MARVIN hand-codes it at five sites; here it is a TYPE, so a
  sixth site cannot forget it.
- *Discard:* commands as shell strings (`list_projects.py` shlex-splits `test_command`); `_DOCS_ONLY_PREFIXES
  = ("state/",)` (`merged_runner.py:623`) — a literal MARVIN path convention inside a code-vs-docs classifier.

**Out of scope.** Retry policy (#13). Artifact persistence/redaction (#4). Git/gh semantics (#3, #6).
**Route.** `route:direct-tdd`. Planned: `tests/test_process.py`, `test_config.py`, `test_adapters.py`.

---

## #3 — Register a repository and resolve it
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem.** Resolve a friendly alias to a verified local clone, never cloning, never auto-discovering.
**User-visible outcome.** `issueforge repo add DandD:~/Projects/DandD` → `issueforge repo list` prints alias,
absolute path, normalized `owner/repo` slug, default branch, baseline command. **Smallest demoable unit.**
**PRD coverage.** US-1.1, US-1.2, US-1.3, US-1.4, US-4.1.

**Observable acceptance criteria**
- `repo add` expands `~`; records alias, absolute path, normalized origin slug, default branch.
- Alias lookup case-insensitive; entered spelling preserved for display.
- Rejected **without changing the registry** (assert the file is byte-identical after each): missing path,
  non-Git path, duplicate alias, mismatched remote.
- **Never clones, never auto-registers.** No `git clone` call exists in the codebase.
- `.issueforge.toml` is loaded at registration; **a missing baseline command is a REGISTRATION-time
  rejection**, not a runtime surprise.
- **`default_branch` is a recorded fact, never assumed. No `or "main"` fallback exists anywhere.**

**Footprint.** `registry.py`, `tests/test_registry.py`, `conftest.py` (temp-git-repo factory), `cli.py`.
**Dependencies.** Blocked by #2. Unblocks #4, #6.
**Deterministic / AI / Human.** All deterministic. No AI. No approval (registration is explicit).
**Failure & recovery.** Every rejection leaves the registry byte-unchanged.
**Logging.** Required (filesystem + subprocess).

**Prior-art and source audit**
- *Sources:* `agent_runs_lib.py:455-486` (`repo_slug`); `list_projects.py:45,66,88,123,135`;
  `merged_runner.py:200, 653-659, 702-719`; `harness-phase1-requirements-brief` G5/OQ4.
- *Preserve:* **a missing baseline command BLOCKS the gate, never silently skips it** (`merged_runner.py:653-659`:
  *"with no command we cannot prove main is green, so block the gate with a clear anomaly instead of…
  silently skipping the safety gate"*). **This fired for real on 2026-07-12** — four rdv `/merged` runs
  silently fell back to manual because the registry carried no `test_command`. This is *why* the PRD makes the
  baseline mandatory.
- *Refactor/extract:* **`repo_slug(url)` — extract HERE, not with the store.** Normalizes ssh/https, idempotent
  on a bare `owner/repo`, tolerant of `.git`/trailing slash, takes the first two path segments (so `/issues/1`
  is dropped), and **raises rather than silently producing a garbage bucket.** Exactly US-1.1's "normalized
  origin slug" and US-1.3's "mismatched remotes rejected".
- *Replace:* the registry FORMAT — `state/projects.md` is generated markdown parsed by regex, carrying
  MARVIN-only fields.
- *Discard:* **`merged_runner.py:714` — `_SCRIPTS_DIR.parent / "state" / "projects.md"`. The MARVIN-checkout
  assumption in its purest form.** **`merged_runner.py:200` — `project.get("default_branch") or "main"`: DO
  NOT COPY.** `generate_projects.py` entirely; the `status`/`type` dashboard fields.

**Out of scope.** Worktree creation and baseline execution (#6).
**Route.** `route:direct-tdd`. Planned: `tests/test_registry.py`.

---

## #4 — Run store + enqueue + stub stage (one locked write path, redacting)
**Labels:** `v1` `phase:0` `route:spec-up`

**Problem.** MARVIN's store drifted to **44 phantom `needs-review` and 7 stuck `running` records**
(`harness-phase3-state-machine:85`) precisely because writes were not funnelled through one primitive. Root
cause: *"wave state lives in the session transcript, which is why crash/resume is impossible."* A
decomposition giving the engine, the gate, and closeout each their own writes has already lost atomicity.

**This is NOT a storage layer.** It ships `issueforge run <alias>#<n>` end to end through a **stub stage that
actually completes**. If you cannot demo `run` → state persisted → completes, the slice went horizontal.

**User-visible outcome.** `issueforge run DandD#148` enqueues and completes through a stub stage. Run state
survives `kill -9`. Two terminals cannot corrupt a record. No secret lands in an artifact.
**PRD coverage.** US-2.1, US-10.1, US-10.3. Gaps G5, G6.

**Observable acceptance criteria**
- Store root is IssueForge-owned (`~/.issueforge/`, one `ISSUEFORGE_HOME` override):
  `runs/<run-id>/manifest.json` + `events.jsonl`, `queue.json`, one lock.
- **ONE `apply(run_id, fn)` primitive. Every mutation routes through it. No module opens the JSON.**
- Atomic write: temp file + `os.replace`. A crash mid-write leaves the previous file intact.
- **The lock is an OS advisory lock (`fcntl.flock`), NOT a lockfile** — the kernel releases it when the
  process dies. (Test: hold in a child, kill the child, assert re-acquirable.) **Do not decompose "locking"
  in a way that lets someone reinvent it with a PID file:** a lockfile STRANDS, which needs stale-lock
  reaping, which needs liveness detection — the classic footgun.
- **The lock spans the WHOLE read-modify-write.** A lock-free pre-read then update is a lost-update race.
- **Validation runs UNDER the lock on the merged record about to land.** A raising validator leaves the file
  byte-unchanged.
- **Existence checks INSIDE the lock** (a pre-lock `exists()` is a TOCTOU that mints a phantom record —
  `merged_runner.py:605-614`, a Codex finding on #746).
- **Non-int persisted values (incl. `bool`) RAISE rather than coerce** — `int(True) == 1` would mint a
  valid-looking record the validator can no longer catch.
- **The same validator runs on BOTH sides of the boundary** (write-time and read-time). MARVIN enforced
  `cross_review` at read time but not write time, so two DandD runs wrote fine, later failed validation, and
  **the entire DandD repo was dropped from the rollup.** Fixed by importing the read-time validator into the
  write path — port that discipline.
- **Torn-final-event recovery** (Codex): a process death mid-append can leave a truncated final JSONL line.
  Replay MUST detect and discard a torn trailing record without losing the preceding history, and a test
  must simulate it. (Atomic manifest replacement does not cover the append-only log.)
- Events are append-only and permanent: transitions, approvals, overrides, commit/PR ids, manifests,
  verification summaries, cleanup outcomes.
- **ONE redacting writer owns ALL artifact persistence.** A **canary test** pushes a known token, a
  credential path, an env value, and a synthetic secret through **every** capture path (prompt, response,
  stdout, stderr, diff, review packet, event stream, error trace) and asserts the canary appears in **zero**
  persisted artifacts. **Hidden model reasoning is dropped AT INGEST, not at display.** `~/.codex/auth.json`
  contents never enter an artifact and the harness never dumps its environment.

**Footprint.** `store.py`, `engine.py` (minimal states + stub stage), `github.py` (read side: validate issue
open), `tests/test_store.py`, `test_engine.py`, `test_github.py`, `cli.py`, `conftest.py`.
**Dependencies.** Blocked by #2, #3. Unblocks #5, #6, #7, #11, #21. **Must land before any other stage.**
**Deterministic / AI / Human.** All deterministic. No AI. No approval.
**Failure & recovery.** Crash mid-write → previous manifest intact. Dead lock holder → kernel releases.
Validator raises → byte-unchanged. Torn final event → discarded, history preserved.
**Logging.** Required — this module IS the filesystem boundary, the event stream, and the redaction owner.

**Prior-art and source audit**
- *Sources:* `agent_runs_lib.py` `_repo_lock`(:184-205), `_atomic_write_log`(:269-289), `update_run`(:292-316),
  **`apply_run`(:319-345)**, `_update_run_unlocked`(:348-420), `_require_int`(:68-79),
  `close_run_for_pr`(:423-452), `next_run_id`(:144); `validate_agent_runs.py:104,123`;
  `log_run.py:109-186` (`_gate_terminal_record`); `merged_runner.py:588-614`;
  `tests/test_agent_runs_writepath_lock_unit.py`, `_integrity.py`; `harness-phase3-state-machine` §3-§4,
  INV-10; `pipeline-eval-2026-07-07.md` finding #4.
- *Preserve:* the lock spanning the READ is what makes a second writer see the first's record instead of a
  stale snapshot (`:501` says so). Fail loud rather than coerce. `close_run_for_pr`'s guard semantics: exact
  match on the PR URL; **only `needs-review` flips**; every other case (no match, already merged, **still
  `running`**) is a byte-unchanged no-op — *a `running` record is NEVER promoted straight to merged.*
- *Refactor/extract:* **`_repo_lock` + `_atomic_write_log` + `update_run` + `apply_run` +
  `_update_run_unlocked` AS A UNIT (~250 lines, fully tested, concurrency-correct). This is THE thing to lift
  from MARVIN.** Only the path resolver and schema change. Port their tests with a provenance comment (US-11.4).
- *Replace:* **`resolve_logs_dir()` (:208-216)** — `$AGENT_LOGS_DIR` / `~/Projects/agentLogs/...`. MARVIN's
  store lives outside the repo *because* MARVIN's repo is the session's repo. **A MARVIN-checkout assumption;
  IssueForge owns its store root.** Also the status vocabulary: MARVIN has **no `paused`/`parked`/`queued`**,
  which is *why* `escalate_run` had to overload `needs-review` with an `unmeasurable: True` cost waiver.
  IssueForge needs real states. **The queue itself: MARVIN HAS NONE** (no FIFO, parking, reorder, cancel).
- *Discard:* `_DEFAULT_RATES_PATH` → `<marvin>/context/model-rates.json` (:27); the `--skill` schema field;
  `generate_agent_runs.py` + the seen-watermark rollup (a derived view that permanently hid four source
  records — the lesson survives, the code does not).

**Out of scope.** Queue control (#5). Retention (#21). Real stages (#6+).
**Route.** `route:spec-up`. Planned: `tests/test_store.py`, `test_engine.py`.

---

## #5 — Queue control: FIFO, pause, park, cancel, resume, `continue`
**Labels:** `v1` `phase:0` `route:spec-up`

**Problem.** Queue and worker-slot semantics must be provable before any AI or git mutation exists.
**User-visible outcome.** `issueforge queue|pause|park|cancel|continue` all work; closing the terminal loses
nothing. **Demo `run → queue → park → resume` from the CLI or the slice went horizontal.**
**PRD coverage.** US-2.2, US-2.3, US-2.4, US-7.4 (owner), US-9.3 (owner). Gaps G7, G9.

**Observable acceptance criteria**
- Additional issues enter a persistent FIFO queue; **reorder and cancel** have named CLI verbs (G9).
- A paused run blocks the worker until explicitly resumed, cancelled, or parked.
- **Parking preserves exact state AND releases the worker.** (Pause and park are two exits from one
  worker-slot state: a park that does not release the worker is meaningless; a pause that does IS a park.)
- `kill -9` mid-run, then `continue`: resumes from persisted state; event replay works (G6/G7).
- **`continue` is ONE verb with a defined meaning** (G7): resume the run at its persisted state, whatever the
  pause reason. Attached watch mode and a later `continue` observe the SAME persisted state (US-7.4).
- **Resume RECONCILES, never silently heals** (`harness-phase3-state-machine` §4, Q3/R3): **GitHub is
  authoritative for PR/branch/merge facts; the run record is authoritative for gate artifacts (approvals,
  verdicts, attempt counts); a divergence is SURFACED, never overwritten.** Decompose it any other way and
  you build a reconciler that overwrites a human approval.
- States are a `State` enum + `TRANSITIONS: dict[State, set[State]]`, table-driven tests. **No
  state-machine library.**
- **Failures are TYPED STAGE RESULTS, not a copied string catalogue** (Codex): each stage declares its own
  failure type. MARVIN's nine anomaly names are *provenance for the recovery procedures*, not a taxonomy to
  import wholesale into a stub queue engine — several (`no-test-command`, `red-main`) are stage-specific or
  made impossible by #3.

**Footprint.** `engine.py`, `store.py` (queue), `cli.py`, `tests/test_queue.py`, `test_engine.py`.
**Dependencies.** Blocked by #4. Unblocks #18, #22.
**Deterministic / AI / Human.** All deterministic. No AI. Pause/park/cancel are human-initiated.
**Failure & recovery.** See resume reconciliation above.
**Logging.** Required (queue + filesystem). Every transition is an event.

**Prior-art and source audit**
- *Sources:* `harness-phase3-state-machine` §1 (states I0–I15), §3 (INV-1…INV-15 with incidents), §4;
  `harness-phase3-requirements-brief:79-82` (Q3/R3); `open-issue-transfer` #748 (*"v1 executes one issue at a
  time, avoiding ambiguous batch-halt semantics"*); `merged/SKILL.md:76-85` (the nine anomalies **with their
  recovery procedures** — mine these, don't copy the names).
- *Preserve:* "Halt and surface, never auto-resolve." Resume reconciles; divergence is surfaced.
- *Refactor/extract:* nothing directly.
- *Replace:* **The state machine itself. MARVIN HAS NO WORKFLOW-ENGINE CODE.** States I0–I15/W0–W9 exist only
  as *analysis* derived from `spec-wave/SKILL.md` prose. That document is a design input, not a codebase.
  **Building this IS the point of IssueForge.**
- *Discard:* wave barriers (W1 recon, W1.5 question gate, W4 batched human gate) — v1 is single-run, they
  collapse; the wave-record shape (`wave_id`, `repos[]`, `gates.questions`) built around MARVIN's multi-repo
  AskUserQuestion transport; everything downstream of the Agent tool's `isolation:"worktree"`
  (`spec-dev/SKILL.md:285-315`) — that conditional exists only because the Agent tool worktrees *the session's
  repo*, which for MARVIN is MARVIN. **IssueForge creates worktrees in the target explicitly; the trap cannot
  exist.** `schedule_waves.py` (parallel scheduling) stays in MARVIN.

**Out of scope.** Real stages. The TUI (#22).
**Route.** `route:spec-up`. Planned: `tests/test_queue.py`.

---

## #6 — Isolated worktree from verified origin + green baseline gate
**Labels:** `v1` `phase:1` `route:spec-up`

**Problem.** A run must be based on fresh, isolated, green code or a new failure is not attributable to the
issue.
**User-visible outcome.** Fetch `origin/<default>`, create a proven-isolated worktree, run the baseline, and
**pause on red before any AI touches a file**.
**PRD coverage.** US-4.2, US-4.3, US-4.4.

**Observable acceptance criteria**
- Worktree HEAD equals the sha **just fetched** from `origin/<default>`, not a local ref.
- **Isolation proof, all three:** worktree path outside the normal checkout's working tree; the normal
  checkout's HEAD byte-identical before/after; its index byte-identical before/after.
- **A dirty normal checkout is permitted ONLY when all three hold** (US-4.3).
- Failed fetch → pause. Unprovable isolation → pause. Red baseline → pause. **All before AI changes files.**
- Baseline runs from the argv array, no shell, **with a timeout** (#754).
- **Zero collected is a THIRD state — BROKEN, neither green nor red** — and pauses with its own reason. Read
  via #2's adapter (`NOTHING_COLLECTED`), never by scraping a summary line. **MARVIN ships the opposite bug
  today and it fired on 2026-07-12** (`red-main` with `passed: 0, failed: 0`).
- **Never run the suite against a stale tree and call it green** — a failed checkout/pull halts, suite NOT
  run (`merged_runner.py:661-671`).

**Footprint.** `workspace.py`, `verify.py`, `tests/test_workspace.py`, `test_verify.py`, `engine.py`.
**Dependencies.** Blocked by #3, #4. Unblocks #9.
**Deterministic / AI / Human.** All deterministic. No AI. Human pauses on the four halt conditions.
**Failure & recovery.** **Nothing uncommitted is ever destroyed.** No `reset --hard`, no `clean -fd`, no
`worktree remove --force` on an unverified tree. Dirty/unknown state PRESERVED and reported.
**Logging.** Required (subprocess + filesystem). Structured pass/fail/timeout evidence per command.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:455-498` (`_WorktreeCleanup`), `:460-469` (a failed `git status` is NOT clean —
  *"that would remove a worktree whose real state is unknown, discarding possible uncommitted work"*),
  `:763-780` (`_worktree_for_branch` returns `(path, ok)` **precisely so absence and failure are
  distinguishable**), `:664-671` (sync-failed); `build_recovery.py:36-71` (`reset_worktree`);
  `context/agent-contract.md:5-19` (the linked-worktree pattern + its five caveats);
  `harness-phase3-state-machine:78` (#687-#689: the recovery pass conflicted because it cut from **stale
  LOCAL main**).
- *Preserve:* dirty/unknown worktrees PRESERVED — never reset, clean, force-remove, or infer safety.
  `reset_worktree` verifies the base sha is a real commit (`git rev-parse --verify --quiet <sha>^{commit}`)
  **before** `reset --hard`, raising if the ref is a branch/tag/tree, *"so a typo cannot silently reset to
  the wrong object"*, and is `git -C`-scoped so failure notes outside the worktree survive.
- *Refactor/extract:* `build_recovery.reset_worktree` behind the workspace seam, with its tests.
- *Replace:* **worktree CREATION — MARVIN has none in code**; the pattern is prose in `agent-contract.md`.
  Carry its caveats as requirements: suffix the worktree name with **timestamp PLUS PID** (*"a timestamp alone
  collides when two agents launch in the same second"*); serialize only the `worktree add`/`remove` bracketing
  (they mutate `.git/worktrees/`); `worktree remove` does NOT delete the branch. **But base on verified
  `origin/<default>`, which is a BETTER base than MARVIN's `HEAD`** — a rewrite informed by the caveats, not a
  lift. **The isolation PROOF is net-new: MARVIN merely assumes isolation.**
- *Discard:* `worktree_root` conventions tied to `~/Projects/<repo>-worktrees` and `state/projects.md`.

**Out of scope.** The provider (#7); authoring (#9).
**Route.** `route:spec-up`. Planned: `tests/test_workspace.py`, `test_verify.py`.

---

## #7 — AI provider layer: guarded launch, subscription-only, session separation
**Labels:** `v1` `phase:1` `route:spec-up`

**Problem.** The independent review gate is the only thing between an AI's work and a human's merge. **If an
empty or failed AI invocation reads as a PASS, the gate is decorative.** MARVIN observed exactly this: Codex
blocking on stdin without a TTY, untrusted-dir errors, and **empty output silently read as a clean review.**
**User-visible outcome.** `issueforge provider check` verifies the configured CLI is authenticated on a
subscription plan and prints the profile.
**PRD coverage.** US-6.4, US-9.4. Gap G4.

**Observable acceptance criteria**
- `ProviderProfile` dataclass (executable, start argv, resume argv, auth-check argv) **loaded from TOML**;
  `[providers.codex]` is the default v1 profile, **never hardcoded in the engine** (US-9.4).
- **ONE `invoke(profile, prompt, *, cwd, timeout, runner) -> CommandResult`. No provider ABC, no adapter
  registry.** Config is the polymorphism; a second provider is a second TOML table, not a second class.
- **The six-concept guarded-launch contract**, each a confirmed silent-failure mode: **(1)** stdin closed
  (`</dev/null`) — a bare `codex exec` **blocks reading stdin without a TTY and hangs forever**; **(2)** stderr
  CAPTURED, never `2>/dev/null` — *without stderr you cannot distinguish "hung" from "failed"*; **(3)** full
  output persisted; **(4)** an explicit wall-clock timeout; **(5) empty output = FAILED, never a clean
  review**; **(6)** non-zero exit = FAILED, never a pass. Plus **(7)**: a `tee` pipeline reports the TAIL's
  exit status and hides a failed launch — use `pipefail` / `PIPESTATUS[0]`.
- **`codex exec` is read-only and HAS NO NETWORK** (#621): *"`gh` calls inside it stall forever with an empty
  output file."* **The review packet (diff, test files, contract, literal proof command) MUST be materialized
  to LOCAL DISK before invoking.** Capture full output — *"piping through `tail` truncates finding lists."*
- **No metered API, ever.** With no API key in the environment `invoke` still works (verified by live
  execution: `codex exec --sandbox read-only --skip-git-repo-check` ran with `OPENAI_API_KEY: null` and billed
  the ChatGPT plan). With the CLI unauthenticated it **FAILS rather than falling back**. No
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` read exists in the codebase. **Use the full CLI, NOT the Agent-SDK
  `--bare` mode, which is API-key-only.**
- **Author and reviewer use separate sessions** (G4); session identity is recorded on every result.
  (*"Claude wrote the code; a different model grading it catches sycophancy bias and blind spots a single
  model shares with itself."*)
- Full output persisted as an auditable artifact via #4's redacting writer.

**Footprint.** `providers.py`, `config.py` (+profiles), `cli.py` (+`provider check`),
`tests/test_providers.py`, `conftest.py` (fake provider subprocess).
**Dependencies.** Blocked by #2, #4. Unblocks #9, #10, #13, #18.
**Deterministic / AI / Human.** The guarded-launch wrapper is entirely deterministic. **This issue BUILDS the
invocation seam; it does not use AI judgment.** No runtime approval.
**Failure & recovery.** Nonzero exit, empty output, or timeout = hard FAILED. **No silent fallback of any
kind.**
**Logging.** Required (AI + subprocess). Prompt, response, stderr, exit, duration, session id — redacted.

**Prior-art and source audit**
- *Sources:* `check_cli_launch_hygiene.py` `CONCEPTS`(:803-810), `_DEVNULL_STDERR`(:814), `_SET_PIPEFAIL`(:816);
  `spec-dev/SKILL.md:413-435` (the Background Launch Contract), `:347-391` (the Cross-Review Gate);
  `spec-wave/SKILL.md:177-181` (Codex inputs must be local files); `harness-codex-plan-auth-2026-07-10.md`
  (**read in full** — the empirical basis for subscription-only); `harness-phase2-step-classification:48`;
  `open-issue-transfer` #742, #713; `tests/test_cli_launch_hygiene.py`.
- *Preserve:* all six concepts + pipefail. **Empty output OR nonzero exit = FAILED — the single
  highest-blast-radius weakening in the system.** Author/reviewer session separation.
- *Refactor/extract:* **Do NOT port `check_cli_launch_hygiene.py`. It is a LINT OVER SKILL.md PROSE** — it
  scans markdown for launch commands violating the contract. **In IssueForge the six concepts become
  properties of the subprocess invocation itself. That inversion — from "lint the prose that tells the model
  how to launch" to "the code launches correctly" — is the entire win.** Mine `tests/test_cli_launch_hygiene.py`
  for the failure cases that become the fake-subprocess adapter's tests.
- *Replace:* how the launch happens (MARVIN spawns via the Claude Code Agent tool from inside a session;
  IssueForge spawns a subprocess with captured streams — no code transfers, only the contract). Provider
  configurability (MARVIN hardcodes Codex).
- *Discard:* the `/codex:adversarial-review` plugin invocation and the `codex@openai-codex` Claude-Code-plugin
  dependency; `PIPELINE_SKILLS` (:765).

**RISK to record (not a blocker).** Plan auth draws from a **5-hour rolling window plus a weekly cap, shared
across CLI/IDE/cloud surfaces** — *harness throughput competes with the human's own Codex use.* Budget and
surface it; don't discover it. Also: `auth.json` holds live OAuth tokens, must be treated as a password, and
must stay writable (the CLI refreshes tokens in place).

**Out of scope.** What the AI is ASKED to do (#9, #10, #13, #18). Alternate adapters (v2 = a second TOML table).
**Route.** `route:spec-up`. Planned: `tests/test_providers.py`.

---

## #8 — Observability contract (boundary classifier + required verdict)
**Labels:** `v1` `phase:1` `route:spec-up`

**Problem.** Changes crossing an external boundary need diagnostic logging, and no reviewer reliably asks for
it unless the contract does. **Moved EARLY (Codex #6): the verdict is an INPUT to shaping (#18), to the
readiness gate (#13), and to the PR body (#14). Ordering it after them was backwards.**
**User-visible outcome.** Every shaped issue carries an observability verdict; the PR reports what logging was
added, reused, or deemed unnecessary.
**PRD coverage.** US-6.5, US-6.6, US-6.7. Gap G3.

**Observable acceptance criteria**
- Every shaped issue records **exactly one** of `required` / `existing coverage sufficient` / `not
  applicable`, with reviewer-confirmed justification. **A missing verdict is a HARD REFUSAL, not a default.**
  **An unjustified "not applicable" FAILS validation** (port the enforcement, not just the vocabulary —
  MARVIN's `cross_review` requires an explicit one-line reason at terminal status, `_validate_cross_review`,
  #497).
- **The boundary TRIGGER is deterministic** (Codex): a diff introducing a call/import crossing HTTP, database,
  subprocess, filesystem, queue, third-party, or AI is a boundary change — AST/import analysis over the diff.
  **It must NOT be an LLM judgment, because an LLM judgment cannot be regression-tested.** Diagnosability
  *elsewhere* remains the reviewer's judgment (architecture.md:87).
- Required logging reuses the **target project's** logger, levels, formats, correlation conventions.
- **G3 — libraries never install global logging configuration.** (In the prose twice, in no criterion.) This
  is the same discipline as MARVIN's *"never widen repo-global config to satisfy a hook… fix the narrow
  cause"* (`agent-contract.md:19`), applied to a new domain.
- Contract-listed **sensitive fields are excluded** from emitted logs, with concrete tests.
- Implementation is a heuristic feeding a judgment call: a module-level tuple of boundary markers, one
  `classify(diff)` used as a HINT plus a REQUIRED contract field, and one paragraph appended to the reviewer
  prompt. **Not a rule engine. The boundary list is NOT user-configurable in v1.**

**Footprint.** `observability.py`, `tests/test_observability.py`, `contract.py` (+field), `shaper.py`.
**Dependencies.** Blocked by #2. Unblocks #13, #14, #18. **(Corrected: was blocked by #11/#14 in draft v1.)**
**Deterministic / AI / Human.** Deterministic: the boundary trigger, the required field, sensitive-field
exclusion. AI: the diagnosability judgment for everything else. Human: none beyond the contract approval it
feeds.
**Failure & recovery.** A missing or unjustified verdict blocks contract approval.
**Logging.** This issue IS the policy.

**Prior-art and source audit**
- *Sources:* `docs/architecture.md:83-87` (source of G3/G10); `context/agent-contract.md:52-58` (the
  verification-plan rule), `:19` (don't-widen-config), `:33` (never echo secrets);
  `validate_agent_runs.py:123-179` (`_validate_cross_review`); `check_current_pii.py` + its tests;
  `open-issue-transfer` #490.
- *Preserve:* **the verification-plan-up-front rule** (`agent-contract.md:54`, from #318): *"Before
  implementing, state a verification plan… **Design verification in up front; do not leave it to be
  remembered at the end.**"* **This is the structural precedent for "every shaped issue records an
  observability impact" — the same move applied to logging instead of testing.** And the three-valued
  classification **with enforcement**: a skip must carry a reason, checked at terminal status.
- *Refactor/extract:* the *pattern* of `check_current_pii.py` (a deterministic pre-write scan) for "detected
  secrets are never retained". The code is small and MARVIN-specific.
- *Replace:* **the entire boundary-classification engine. Net-new — there is no MARVIN code OR prose for it.**
  Likewise "reuse the target project's logger/levels/formats".
- *Discard:* the `/harden` skill routing and `harden_recon_scan.py`. (The *lesson* — a deferred finding becomes
  a filed issue — informs the shaper; the skill does not transfer.)

**Out of scope.** Redaction of IssueForge's OWN artifacts (#4 — different concern, same word).
**Route.** `route:spec-up`. Planned: `tests/test_observability.py`.

---

## #9 — Author acceptance tests + deterministic red proof
**Labels:** `v1` `phase:2` `route:spec-up`

**Problem.** **The load-bearing control of the entire system, and the ONE contract in the PRD with no prior
art to extract.** `check_acceptance_integrity.py` never runs pytest — it diffs syntax trees. The slice
therefore LOOKS smaller than it is. **If red-proof ships as a later slice, an approval flow exists that
accepts ANY failure as red and every downstream gate is decorative. Authoring and red-proof CANNOT be
separate deliverables.**

**Scope boundary corrected (Codex #4).** This issue owns the **deterministic** half only: that the test was
collected, executed, and failed in the call phase, on a healthy baseline, at a bound sha. **It does NOT — and
cannot — prove the failure was for the NAMED expected behavioral reason.** That correspondence is semantic
and belongs to #10 (AI reviewer) and #11 (human approver).

**User-visible outcome.** A run produces AI-authored acceptance tests plus machine-checked evidence that they
collected, executed, and failed in the call phase — and refuses to proceed otherwise.
**PRD coverage.** US-5.1, US-5.2.

**Observable acceptance criteria**
- **Collection proven by IDENTITY, not count:** every targeted unit id appears in the collection report.
  **SET EQUALITY**, not `collected > 0`.
- **The failure occurs in the CALL phase.** The adapter's `errored` (collect/setup/teardown: import, fixture,
  config, environment) vs `failed` (call) distinction **is the mechanical discriminator**, and is exactly
  US-5.1's wording made checkable.
- **PHASE-BASED, never exception-type-whitelisted.** A first red test for a not-yet-existing function
  **legitimately** raises ImportError/AttributeError in the call phase. A rule of "must be AssertionError" is
  **WRONG and breaks real TDD.** **Test BOTH:** a missing-symbol red (VALID) and a missing-fixture red
  (INVALID), and require the checker to separate them.
- **The preexisting baseline stays green IN THE SAME RUN.** This closes the conftest/config channel at
  AUTHORING time: if the author breaks shared setup, the baseline goes red too and the contract is REJECTED.
- **Zero collected → REJECTED as BROKEN** (the third state).
- **XPASS → REJECTED.** A pending-marked test that already passes (a repeat MARVIN miss).
- **Empty `parametrize` (collects to nothing) → REJECTED.** A REAL false-allow Codex caught on MARVIN #491.
- The failure representation (exception type, message, unit id, assertion line) is captured **VERBATIM** into
  the manifest and is **RE-DERIVABLE**: the runner can check out the contract commit and reproduce the same
  per-unit verdict. **IssueForge has no PENDING-on-main self-reporting artifact, so the manifest's red
  evidence is the ONLY record — it must be a runner capability, not a string someone wrote down once.**
- **Red evidence is SHA-BOUND**, and the build worktree forks from the SAME verified `origin/<default>` sha
  the red was proven against (#687-#689: a recovery pass conflicted because it cut from stale local main).
- **Discover before authoring** (#743): find existing contract tests and prior issue markers FIRST; every
  existing test gets an explicit **keep / revise / supersede** disposition. Prevents stale XPASS and
  contradictory contracts.
- **Verbatim-example fixture rule** (#470/#478): when the source issue shows a concrete input/output example,
  one committed fixture reproduces it **verbatim** — not paraphrased, not re-shaped. (*"The suite tested the
  label and golden value on separate lines while the issue's canonical form was one line; the build passed
  locally and still rejected the issue's own example, and only the Codex gate caught it."*)
- **Refuses to author a contract on the `generic` adapter** (no collection identity → no freezable boundary).

**Footprint.** `contract.py` (authoring + red proof), `verify.py` (per-unit reporting), `tests/test_contract.py`,
`engine.py`, `conftest.py`.
**Dependencies.** Blocked by #6, #7. Unblocks #10, #11.
**Deterministic / AI / Human.** **Deterministic:** collection identity, call-phase discrimination,
baseline-still-green, zero-collected/XPASS/empty-parametrize rejection, sha-binding, re-derivation. **AI:**
authoring the test bodies ONLY. **Human:** none here (approval is #11).
**Failure & recovery.** Any rejection pauses with the specific reason.
**Logging.** Required (AI + subprocess). The per-unit verdict report is a permanent manifest artifact.

**Prior-art and source audit**
- *Sources:* `check_acceptance_integrity.py` — **read it to confirm what it does NOT do** (imports only
  `argparse, ast, sys, pathlib`; never collects, never executes); `spec-wave/SKILL.md:137` (the ONLY
  "verify it is red today", and it is prose); **`spec-up/SKILL.md:55-61`, esp. the lazily-satisfiable guard
  (#433)**; `write-a-prd/SKILL.md:194-284`, `:235-244`; `validate_accept_body.py:78-153`;
  `validate_spec_up_issue.py:63, 118-165`; `validate_pending_markers.py` (the false-green catalogue);
  `check_acceptance_mutation.py:150-248` (structured collection); `pipeline-eval:30` (#491);
  `open-issue-transfer` #743; memories `feedback_acceptance_literal_xfail`, `feedback_verify_breadth_before_done`,
  `feedback_fixture_shape_matches_live`.
- *Preserve:* **the lazily-satisfiable guard — the shaping-time ancestor of meaningful red**: reject a
  criterion whose only proposed test is trivially passable (a bare "exit 0", an assertion an empty
  implementation would satisfy). *"Push for a stronger observable that a wrong implementation would fail."*
  Discover-before-authoring. The verbatim-example fixture rule. The **golden-value arrow proxy**: an arrow
  needs a real, NON-PLACEHOLDER token on BOTH sides (`TBD -> TBD`, `... → ...`, one-sided `-> exit 0` all
  rejected) — **keeping its honest caveat that it checks SHAPE only, which is exactly why #10's semantic
  review sits above it.** **Suite-level anti-false-green discipline** (#412/#483): a "blocked" test asserts
  non-zero exit **AND** a keyword **AND** the offending test name — *a test that only asserts "it failed" is
  satisfied by the script not existing.*
- *Refactor/extract:* `validate_accept_body.parse_body/validate` → an `AcceptanceContractBody` validator
  (plain-English region before the `technical (contract):` label; a golden-value arrow inside the contract
  region; **arrows inside fenced code blocks do not count**). ~150 lines, zero MARVIN coupling.
  **`validate_pending_markers.py`'s CATALOGUE of false-green shapes** (skip in any form; non-strict /
  condition-bearing / extra-kwarg xfail; aliased/imported/module-level/class-level/parametrize placement;
  **whitelist-shaped, fail-closed, walked at ANY depth**) — **port the catalogue as what the red-verifier must
  REJECT, and drop the marker.**
- *Replace:* **The red-proof predicate. NET-NEW. NOTHING TO PORT. This is the single most important line in
  this document.**
- *Discard:* **MARVIN's PENDING-on-main convention** (#761–#766) — one branch, test commit then impl commit.
  Dropping it kills the whole marker-downgrade attack class **and dissolves an unresolved MARVIN conflict**
  (Phase-2 OQ1: `spec-dev/SKILL.md:233-236` has the IMPLEMENTER remove the marker, directly contradicting
  #712's "suite physically outside the implementer's write scope" — both cannot hold). **IssueForge has no
  marker to flip; do not port the conflict.** Also discard the `ACCEPT:` satellite-issue pattern — **that is
  GitHub-as-database, a workaround for having no run store.** IssueForge has a manifest.

**Out of scope.** Semantic validity (#10). The freeze (#11). Integrity enforcement (#12). Mutation (#24).
**Route.** `route:spec-up` — **mandatory.** This issue's own contract must be shaped before it is built.

---

## #10 — Independent review: semantic red validity + recorded override
**Labels:** `v1` `phase:2` `route:spec-up`

**Problem.** #9's predicate proves the test *executed and failed*. It **cannot** prove it failed **for the
named missing behavior**, nor that the tests actually COVER the issue, nor that a shaped golden is
semantically weak — `run cmd -> exit 0` passes every syntactic check. **That judgment is irreducibly
semantic** (Codex #4) and needs a fresh, independent AI session.
**User-visible outcome.** Before a human is asked to approve, an independent session has validated that the
observed red corresponds to the expected missing behavior, and its verdict is on the record.
**PRD coverage.** US-5.3, US-5.4.

**Observable acceptance criteria**
- **The reviewer explicitly validates the OBSERVED red evidence against the EXPECTED behavioral reason.**
  This is the semantic half #9 cannot do, and it is this issue's core deliverable — not a coverage
  rubber-stamp.
- Runs in a **fresh session, separate from the authoring session** (session ids differ and are recorded).
- Runs **against the real branch worktree with execution capability**, given the **literal proof command**
  (copied verbatim, not paraphrased) and bounded time (#742). *A reviewer that can only read a diff is a
  weaker gate than MARVIN thought it had.*
- **Empty output OR non-zero exit = FAILED review, never a pass** (from #7; re-asserted here because this is
  the gate that matters).
- The verdict is **bound to the reviewed head sha**. A verdict whose sha ≠ head is STALE and must not be
  reused (INV-4: **two PRs merged before their fix agents landed**).
- **The batched adversarial contract** (#617): ONE exhaustive pass enumerating **ALL** findings (not stopping
  at the first) → fix everything blocking in ONE batch → ONE confirmation round. **Reopen only if the
  confirmation round finds a NEW blocking finding.** Without this the review ping-pongs indefinitely.
- Reviewer failure may be **explicitly overridden** by a fresh same-provider session or human review, and
  **the override is RECORDED** (who, why, when, which verdict). A skip carries an explicit one-line reason;
  the verdict is `done` / `blocking:<n>` / `skipped:<reason>`, required at terminal status (#497).
- **Reviewer fix-round mechanics (PRD-conformant, per Codex #10):** review sessions are **fresh per round**;
  review rounds are a **review protocol** and do **NOT** consume the US-6.2 implementation repair budget of
  two. (MARVIN left this open — Phase-2 OQ3, *"a fire-and-forget session has ENDED by the time findings
  arrive"*. IssueForge resolves it here rather than inheriting the ambiguity; the provider `resume` argv
  template exists if session resumption is later preferred.)
- Review output retained as an auditable artifact (30-day policy). **MARVIN's #491 review left only a
  one-token ledger field — its four rounds are reconstructible only from session-log prose.** Fixed here.

**Footprint.** `contract.py` (+review), `providers.py`, `tests/test_contract.py`, `engine.py`.
**Dependencies.** Blocked by #9. Unblocks #11.
**Deterministic / AI / Human.** Deterministic: session separation, sha-binding, fail-loud posture, override
recording. **AI: the semantic correspondence judgment.** Human: overriding a failed review (recorded).
**Failure & recovery.** A failed review pauses. An override is explicit, never inferred.
**Logging.** Required (AI). The full review packet persists, redacted.

**Prior-art and source audit**
- *Sources:* `open-issue-transfer` #742; `harness-phase3-state-machine` INV-4 (*"wave-3: two PRs merged before
  their fix agents landed"*); `validate_spec_up_issue.py` (its own caveat that the proxy checks SHAPE only);
  `spec-dev/SKILL.md:352-354, 361-366, 387-390`; `pipeline-eval` finding #9; memory
  `feedback_merge_mid_codex_round` (#700/#718→#721 — merging before a Codex fix-round completes STRANDS the fix).
- *Preserve:* reviewer executes in the real worktree; sha-bound verdicts; fail-loud on empty/nonzero; the
  batched contract; an override is a first-class recorded event, never a silent retry.
- *Refactor/extract:* nothing — MARVIN's Codex gate is a bash invocation inside SKILL.md prose.
- *Replace:* all of it (net-new code implementing recorded lessons).
- *Discard:* MARVIN's `cross_review` verdict-string format and its cost-tuple coupling.

**Out of scope.** The human approval itself (#11).
**Route.** `route:spec-up`. Planned: `tests/test_contract.py`.

---

## #11 — Human approval freezes the contract manifest
**Labels:** `v1` `phase:2` `route:spec-up`

**Problem.** Approval must freeze an exact, complete boundary — and the boundary must be **discovered by the
engine**, not declared by a user glob (Codex #5).
**User-visible outcome.** A human approves an exact contract; the engine records precisely what was frozen.
**PRD coverage.** US-5.5.

**Observable acceptance criteria**
The manifest freezes, and the engine **DISCOVERS** (does not merely accept a configured glob):
- the contract commit sha;
- a content hash of **every test file**;
- a content hash of **every `conftest.py` on the collection path** (engine-discovered);
- a content hash of the **test configuration** (`[tool.pytest.ini_options]`, `pytest.ini`, `tox.ini`,
  `setup.cfg`) — adapter-supplied, not hardcoded;
- a content hash of the **transitive import closure of the test modules** (engine-computed);
- the `.issueforge.toml` **command arrays** (US-5.5 freezes "the command", so the config file is INSIDE the
  protected boundary, not outside it);
- the **collected unit-id set**;
- the **red evidence** from #9 and the **review verdict** from #10.
- **The discovered set is UNIONED into `contract_paths`, and the union is what is protected.** A
  user-configured glob may ADD to the boundary but can never SHRINK it. *"User-configured globs cannot be
  trusted to enumerate the boundary the engine claims to discover."*
- Symlinks, renames, deletions, and generated files inside the boundary each have **defined, tested
  behavior** (a deleted contract file reads as an EMPTY MODULE so every test in it reads as deleted —
  deletion is not an escape).
- **Human approval is THE gate.** Freezing is the human's act, recorded as an event with the approver's
  decision and the exact manifest hash.

**Footprint.** `contract.py` (+manifest/freeze/discovery), `tests/test_contract.py`, `engine.py`.
**Dependencies.** Blocked by #9, #10. Unblocks #12.
**Deterministic / AI / Human.** Deterministic: discovery + hashing. AI: none. **Human: the approval.**
**Failure & recovery.** Incomplete discovery (e.g. an unresolvable import) **fails closed** — refuse to freeze
rather than freeze a partial boundary.
**Logging.** Required. The manifest is a permanent artifact.

**Prior-art and source audit**
- *Sources:* `check_acceptance_integrity.py` — **its documented SCOPE LIMIT at :79-83: conftest.py is
  explicitly OUTSIDE its guard**; `pipeline-eval:76` — **that conftest hole is filed as a LIVE OPEN issue**;
  `_dep_closure` (:632-693, #588); `ci_acceptance_gate.py:15-19, 120-121`; `open-issue-transfer` #759.
- *Preserve:* **"Configuration and shared fixtures that can neutralize tests belong INSIDE the protected
  boundary."** **This is the most important thing IssueForge inherits AS A REQUIREMENT rather than as code —
  US-5.5's "dependent fixtures/configuration" is precisely MARVIN's known hole, fixed by specification.**
  **You cannot escape the contract by deleting your membership in it** (`ci_acceptance_gate.py` keys the tag
  strictly on the BASE revision: *"tag removal is not an escape hatch… silent un-designation would disable
  future protection"*).
- *Refactor/extract:* `_dep_closure`/`_assertion_dep_roots` (#588) as the import-closure computer — it is a
  **RESOLVER, not fail-closed**: unrelated fixture/helper/constant changes stay allowed; pytest-injected
  params resolve to nothing; **a name bound LOCALLY in the test body shadows a same-named module def and
  never enters the closure.**
- *Replace:* the freeze itself is net-new (MARVIN has no manifest — it AST-compares two revisions).
- *Discard:* the `ACCEPTANCE_CONTRACT = True` in-file tag **as the membership mechanism** (the frozen manifest
  — hashes + collected identifiers — is strictly stronger). **Keep the can't-un-designate rule.**

**Out of scope.** Enforcement (#12).
**Route.** `route:spec-up`. Planned: `tests/test_contract.py`.

---

## #12 — Contract integrity enforcement + amendment path
**Labels:** `v1` `phase:2` `route:spec-up`

**Problem.** The invariant is **NOT "the test files are frozen." It is "the discovery boundary is frozen"** —
the exact units collected, the exact code deciding their outcome, and the exact command running them.
Freezing file hashes alone leaves ~16 channels open through which an implementing AI neuters an approved test
**without ever editing the test file**: `conftest.py` autouse fixtures; `xfail_strict = false`; `addopts`
gaining `--deselect`/`--ignore`/`-k`/`-m`/`-p no:`; `collect_ignore`; `testpaths`/`norecursedirs`; an imported
helper; the command itself; renaming the test or its class; empty `parametrize`; marker downgrade; alias/
import indirection under a kept decorator; a changed fixture `params` or helper param default; deleting the
file; removing the contract designation; `sitecustomize.py` / a shadowing stub package; amend/rebase/
force-push. **Enumerating 16 checks is whack-a-mole; channel 17 is the one nobody thought of.**

**User-visible outcome.** After approval, **any** change to a contract path fails the build, regardless of how
it was written — and a legitimate amendment has a real, auditable path.
**PRD coverage.** US-6.1.

**Observable acceptance criteria**
- **The protected-path diff gate is ABSOLUTE.** After the approved commit,
  `git diff --name-only <contract_sha>..HEAD` → **ANY** change under a protected path FAILS the build. **No
  sanctioned exception exists.** IssueForge has no PENDING marker and therefore no flip step, so — unlike
  MARVIN — it needs **no carve-out.** This layer is COMPLETE rather than enumerative: **unforgeable from
  inside the session** (file tool, shell redirect, `python -c 'open(...,"w")'`, `git checkout --` all fail
  identically).
- **EVERY frozen dependency hash from #11 is RECOMPUTED at the candidate head and COMPARED** (Codex #5). The
  diff gate alone is not sufficient: a file outside `contract_paths` but inside the discovered import closure
  must still be caught. **Recollection is necessary but does not subsume semantic dependency integrity.**
- **Re-collection: the collected unit-id set reproduces EXACTLY (set equality).** The strongest single check,
  and **it does not exist in MARVIN** — MARVIN never collects, it only AST-compares. It subsumes most
  config/conftest/rename tricks in one predicate.
- `git merge-base --is-ancestor <contract_sha> <head>` — the approved commit is still an ancestor (defeats
  amend/rebase/force-push).
- The **AST weaken-check runs as DEFENSE-IN-DEPTH** on the final diff (demoted from load-bearing).
- **THE AMENDMENT PATH SHIPS WITH THE GATE, NOT AFTER IT.** `pipeline-eval` finding #5 is blunt: *"The
  amendment path is unrealistic, so amendments route around it."* PR #592 legitimately aligned seed args in
  two committed suites; the #491 guard, merged two hours later, *"would classify those exact edits as
  WEAKENED. The pipeline has no lightweight, auditable amend procedure, so legitimate amendments are
  indistinguishable in kind from the attack the guard exists to block."* An amendment requires: an
  issue-linked reason, the exact diff, **renewed human approval**, and a **NEW manifest** (#759). Whole-body
  equality alone is insufficient. **Build the escape hatch with the gate or the gate gets bypassed.**
- Implementation **cannot proceed to PR readiness** when contract files, collection, configuration, or command
  changed without new human authorization.
- **Enforcement is by the harness/CI, NEVER by the session being policed** (cross-cutting rule 5).

**Footprint.** `integrity.py` (AST backstop), `contract.py` (+verify), `tests/test_integrity.py`,
`test_contract.py`, `engine.py`.
**Dependencies.** Blocked by #11. Unblocks #13.
**Deterministic / AI / Human.** **All deterministic. The AI is NEVER asked whether the contract is intact.**
Human: authorizing an amendment.
**Failure & recovery.** Any violation halts before PR readiness, naming the violated predicate.
**Logging.** Required. Every integrity verdict is permanent.

**Prior-art and source audit**
- *Sources:* `check_acceptance_integrity.py` — **read the 83-line docstring; it is a complete specification of
  every weakening vector, discovered one incident at a time (#491 → #610 → #627 → #588)**;
  `_kept_decorator_bindings_changed` (:379-399, #627); `_dep_closure` (:632-693, #588); class-keying
  `ClassName::method` (#610, so *"a same-named method in another class can never shadow a weakened one"*, and
  **the class's own decorator list is part of the contract**); `harness-phase2-step-classification:70`
  (**Option B, the runner-owned diff boundary gate, "unforgeable from inside the session"; the recommended
  layering is B + A + E**); `harness-prior-art-research:41-45` (**weak verifiers get gamed: Anthropic's
  C-compiler agent shelled out to GCC to beat a code-size limit**); `open-issue-transfer` #759, #603
  (*"Python AST checking is a framework adapter, not a universal integrity solution"*); `pipeline-eval`
  findings #1, #5, #6, #7.
- *Preserve:* the **B+A+E layering** with IssueForge's diff gate made **ABSOLUTE**. The four false-allows the
  AI reviewer caught during #491's own build (aliased `xfail→skip`; empty-`parametrize`; decorator reorder;
  re-marking an active test pending). Deletion is not an escape.
- *Refactor/extract:* `check_acceptance_integrity.py`'s AST machinery (~928 lines) into `integrity.py` **as
  defense-in-depth, not as the primary gate**, behind a **framework-adapter boundary**. Port all six of its
  test files.
- *Replace:* **the primary gate.** MARVIN's is AST-diff (enumerative, with a documented conftest hole).
  IssueForge's is protected-path diff + dependency-hash comparison + re-collection (complete).
- *Discard:* **MARVIN's sanctioned marker-flip exception.** It exists ONLY because MARVIN's implementer must
  remove the PENDING marker. **Carrying it across would import a hole IssueForge does not have.**

**Out of scope.** Mutation/anti-tautology (#24, `deferred-v2`). The implementation (#13).
**Route.** `route:spec-up` — **mandatory.**

---

## #13 — Implement under the frozen contract; bounded repair; readiness gate
**Labels:** `v1` `phase:3` `route:spec-up`

**Problem.** Green must mean "the approved behavior was delivered", not "the tests stopped failing." Two
things break that: an implementer that owns the git boundary (making every file-level protection advisory),
and an unverified self-report of green.
**User-visible outcome.** The AI implements against a frozen contract. **The runner — not the AI — owns diff,
commit, push, and verify-at-origin.** A composite readiness gate decides PR-worthiness.
**PRD coverage.** US-6.2, US-6.3 (owner).

**Observable acceptance criteria**
- **The runner owns diff → commit → push → verify-at-origin. The implementing session NEVER runs
  `git worktree`, NEVER pushes, NEVER opens a PR.** (MARVIN's memory records agents reporting "pushed" when
  the push silently failed — #604/#607.)
- **`origin/<branch>` is FETCHED and CONFIRMED to contain the sha** before any review or PR step.
- **The AUTHORITATIVE test run is the ENGINE's, not the agent's.** The implementer runs tests as feedback;
  the authoritative run executes the **verbatim proof command** plus the full suite **after the session ends**.
  **Never trust an agent's self-report of green.**
- **PR readiness requires ALL of:** green acceptance tests; green full baseline **(a SEPARATE gate — this
  slice may not regress any previously-passing test)**; configured quality gates; approved file scope; an
  independent code review with **no blocking findings**; and **a recorded observability verdict from #8**.
- **The repair budget is TWO automatic attempts (US-6.2, as written).** Consumed by: an implementer process
  failure/timeout; a still-red committed suite after the agent reports done; a reviewer rejection with a
  concrete failure scenario. **Review ROUNDS (#10) are a review protocol and do NOT consume this budget.**
  *(Risk recorded: MARVIN used three nested caps and warns one counter hides two different failures. The PRD
  says two; the PRD governs. See Risks.)*
- The repair prompt carries the **frozen contract + a compact trace, and NEVER the prior transcript**
  (context-rot; `build_retry_prompt` deliberately accepts a `transcript` arg and DROPS it — an engine that
  "helpfully" re-attaches logs re-introduces the bug).
- **The attempt counter increments INSIDE the store lock** — a lock-free read-then-write under-counts and
  **under-enforces the cap**.
- **Cap exhaustion writes a VALID terminal record, not a crash** — schema-valid, human-readable, never
  half-written.
- **Test-run economy:** run the full suite ONCE; if it regresses, iterate on just the failing tests; then ONE
  confirming full run. *"Never loop on the full suite: a failing full run dumps output the agent must re-read
  every cycle, which is where test tokens actually burn."*

**Footprint.** `engine.py`, `workspace.py` (+commit/push), `verify.py` (+quality gates), `contract.py`,
`tests/test_engine.py`, `test_verify.py`.
**Dependencies.** Blocked by #12, #8. Unblocks #14.
**Deterministic / AI / Human.** Deterministic: the readiness gate, the git boundary, the repair budget, the
store writes, the authoritative run. AI: writing the implementation; reviewing it (independent session).
Human: pause on cap exhaustion; override of a blocking finding (recorded).
**Failure & recovery.** `reset_worktree` verifies the base sha is a real commit before any reset. Cap
exhaustion → valid terminal record + pause.
**Logging.** Required (AI + subprocess + filesystem).

**Prior-art and source audit**
- *Sources:* `build_recovery.py` — all five functions: `next_action(attempt, cap=2)` (:98),
  `build_retry_prompt` (:73-97), `escalate_run` (:115-155), `record_attempt` (:156), `reset_worktree` (:37-48)
  — plus `tests/test_build_recovery.py`; `spec-dev/SKILL.md:161-166` (the verbatim proof command), `:170-204`
  (the three caps), `:237-253` (verify + full-suite regression + test-run economy);
  `harness-phase2-step-classification` row 15 and *"prose-encoded failure recovery the runner must own"*;
  memory `feedback_verify_agent_push`.
- *Preserve:* context-rot avoidance; counter-inside-the-lock; cap exhaustion → valid terminal record;
  full-suite regression as a separate gate; the verbatim (never paraphrased) proof command.
- *Refactor/extract:* **`build_recovery.py`'s five functions behind a `RepairPolicy` seam. The module's own
  docstring says it was built as exactly this seam:** *"the Claude-loop re-dispatch action itself stays prose
  in skills/…; this module is only the testable seam those skills call."* `next_action` and
  `build_retry_prompt` port near-verbatim; `record_attempt`/`escalate_run` re-point at IssueForge's store.
  Port its tests.
- *Replace:* the readiness-gate composition (net-new).
- *Discard:* MARVIN's `needs-review`-as-escalation workaround (IssueForge has a real `paused` state).

**Out of scope.** Opening the PR (#14). Mutation testing (#24, `deferred-v2` — **not in the PRD**).
**Route.** `route:spec-up` — **mandatory.**

---

## #14 — One green PR, pushed and verified at origin; never merged
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem.** A PR is meaningful only if its head is actually at origin and its base is the default branch.
**PRD coverage.** US-7.1, US-7.2, US-7.3.

**Observable acceptance criteria**
- Push + PR open happen **automatically only after ALL readiness gates pass**. (US-6.3 is owned by #13; this
  is the downstream **integration assertion**, not a second gate.)
- **The head is pushed and VERIFIED at origin** before the PR is opened.
- **The base is the repository default branch**, enforced by a **reusable PURE predicate** (#694), suitable
  for local AND required-CI enforcement. **EXACT, case-sensitive string equality** — *"no substring/prefix/
  strip: `gh baseRefName` yields the short branch name, so a qualified or near-miss ref is not the expected
  input and rejects by default."*
- The PR body reports: approved contract commit, integrity verdict, red/green evidence, verification summary,
  AI review verdicts, overrides, **and logging added / reused / intentionally unnecessary (G10, from #8).**
- **IssueForge NEVER merges. No merge or approve call exists in the gateway — BY CONSTRUCTION**, which
  Phase-1 classification calls *"stronger than the skill's prose."* (Test: assert the string `pr merge`
  appears nowhere in the source.)
- The run persists `waiting-for-merge`. **Watch mode is a READ-ONLY observation of merge state** (US-7.4);
  it performs **no mutation**. *(Resolves the MARVIN C1 conflict — "never watches or polls" protects
  MUTATING cleanup, not a read-only poll. The PRD requires watch mode; the PRD governs.)*
- The gateway takes `(owner, repo, number)` as an **INDIVISIBLE identity. No API accepts a bare number** — a
  cross-repo reference can never be reduced to a number and resolved against the current repo (#749).

**Footprint.** `github.py` (+PR write side), `engine.py`, `tests/test_github.py`, `test_engine.py`.
**Dependencies.** Blocked by #13, #8. Unblocks #15.
**Deterministic / AI / Human.** All deterministic (the PR body is assembled from recorded evidence, never
narrated). **Human: MERGE — absolute authority.**
**Failure & recovery.** A failed push halts before the PR is opened. A non-default base is refused.
**Logging.** Required (third-party boundary via `gh`).

**Prior-art and source audit**
- *Sources:* `check_build_pr_base.py` (all 100 lines) + its tests (#694 — transferred specifically as *"a
  reusable pure predicate"*); `merged_runner.py:13` (the by-construction boundary), `:161-190`;
  `harness-phase1-step-classification:24`; `harness-phase3-state-machine:76-78`; memories
  `feedback_stacked_pr_merge_order`, `feedback_verify_agent_push`, `feedback_never_auto_merge`.
- *Preserve:* human merge authority is absolute (invariant 12). No PR recommended unless its head is pushed
  and verified at origin and its base is the default branch (invariant 4). **Merge state comes from
  `gh pr view`, never from the invocation wording — the human saying "I merged it" is not evidence.**
- *Refactor/extract:* **`check_build_pr_base(base_ref, default_branch) -> (bool, str|None)` — extract
  verbatim.** ~20 lines, pure, unit-testable with no live `gh`.
- *Replace:* PR-body assembly (net-new, from the manifest).
- *Discard:* MARVIN's stacked-PR/wave concepts — **v1 is single-run, so there IS no stack and the stranding
  cannot occur by construction.** Keep the base predicate anyway as a cheap regression guard.
  `_build_report_data` (:886-904) as designed — it emits data only *because MARVIN's runner is called by a
  Claude session*. **IssueForge's CLI/TUI renders its own report.**

**Out of scope.** Closeout (#15/#16/#17). Draft PRs (v2 — a `draft: bool` kwarg plus one state).
**Route.** `route:spec-up`.

---

## #15 — Delivery verification: exact merge-commit + head-sha binding
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem (Codex #12).** *"Content, not ancestry" is a policy slogan, not an executable predicate,* until the
**exact** GitHub merge commit and the **recorded PR head sha** are bound into it. `state == MERGED` is NOT
delivery proof.
**User-visible outcome.** A single, auditable verdict: this run's work IS (or is NOT) delivered on the
default branch — with the SHAs that prove it.
**PRD coverage.** US-8.1 (owner). **This is the global destructive stop; #16 and #17 depend on it.**

**Observable acceptance criteria**
- The predicate is bound to **named, authoritative GitHub fields**: the PR's `mergeCommit.oid`, its
  `headRefOid` **as recorded in the run manifest at PR-open time** (not re-read later), `baseRefName`, and
  `state`. **Which fields are checked is written down, not implied.**
- **Delivery proof = the merge commit is reachable from `origin/<default>`**, verified with
  `git merge-base --is-ancestor <mergeCommit> origin/<default>`, which is **TRI-STATE: exit 0 = reachable;
  exit 1 is the ONLY trustworthy negative; anything else is an ERROR → HALT as `verification-failed`, never
  "not reachable".**
- **The freshly-merged squash may not be in the local object store yet** — `merge-base` errors 128 on a
  **genuine clean merge** until you fetch. **Fetch ONCE, retry ONCE, only on the error path, never on the
  clean exit-0 path.** A failed fetch HALTS (*"a failed fetch must NOT be read as a successful sync"*).
  **Without this the runner false-alarms on every fresh merge; with a naive `!=0 → unreachable` it reads a
  clean merge as a STRANDING, which is worse.**
- **Missing or disagreeing facts are DEFINED, not undefined** (Codex): no `mergeCommit` on a MERGED PR; a
  `headRefOid` that no longer matches the manifest; a `baseRefName` that is not the default branch. **Each
  halts with its own named verdict; none is inferred as success or as failure.**
- **A failed `gh pr view` is `verification-failed`, NEVER "not merged"** — *"that would let a transient gh/
  network failure clear the way to delete a branch."*
- **Only a PROVEN delivery permits the destructive cleanup in #17.**

**Footprint.** `github.py` (+verification), `workspace.py` (reachability), `tests/test_github.py`.
**Dependencies.** Blocked by #14. Unblocks #16, #17.
**Deterministic / AI / Human.** All deterministic. No AI. Human: resolve any halt.
**Failure & recovery.** Every anomaly HALTS **this stage** and surfaces with evidence; nothing auto-resolves.
**Logging.** Required. The verdict and its SHAs are permanent.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:161-190` (`_pr_view`/verify), `:210-234` (the bounded single fetch-retry, #731),
  `:237-262` (the stranded-squash halt), `:293-311` (`_reachability`, tri-state), `:313-330`
  (`_branches_containing` — **a failed `git branch --contains` cannot be read as "no branches", that would
  DROP branches from the stranded-squash report**); `open-issue-transfer` invariant 5.
- *Preserve:* every tri-state posture above. **The stranded squash is the most-repeated failure in MARVIN's
  history: #633, then 6× in wave-2 (#667–#672) and 3× in wave-3 (#687–#689), all in one week.**
- *Refactor/extract:* `_PrVerification` and `_reachability` behind the gateway, with their tests.
- *Replace:* the SHA-binding contract above is net-new precision over MARVIN's project-level checks.
- *Discard:* the stranded-squash **recovery** machinery — **v1 is single-run, so no PR can be stacked on a
  feature base and the stranding cannot occur.** Keep the halt as a cheap backstop for out-of-band merges.

**Out of scope.** Closing issues (#16). Deleting anything (#17).
**Route.** `route:spec-up`.

---

## #16 — Closeout: comment, close the exact issue, update the parent epic; idempotent
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem (Codex #1 — a REAL coverage miss in draft v1).** `prd-v1.md:93` requires closeout to *"close the
exact run issue, comment with its PR and verification result, **and update its parent epic** without another
approval."* Draft v1 mapped US-8.2 but **never required the parent-epic update.** Fixed here.
**PRD coverage.** US-8.2 (owner), US-8.4 (owner).

**Observable acceptance criteria**
- **Comment FIRST, then close. A failed comment does NOT close.** *"An issue closed with no linkage comment
  violates the contract — record the failure and move on, leaving the issue open for a retry."* **The
  ordering is load-bearing** and is what makes the retry safe.
- **Close ONLY formal `closingIssuesReferences`.** Prose-referenced `#\d+` mentions go to the **report**,
  never to `gh issue close`. The invocation is consent for closing exactly the linked issues **"and nothing
  beyond them."**
- **The parent epic is updated, idempotently, WITHOUT another approval** (US-8.2). Defined behavior for: **no
  parent** (no-op, recorded); **a failed epic read** (halt, never assume "no parent"); **repeated closeout**
  (no duplicate epic comment/edit).
- **Every mutation is repository-qualified** — `(owner, repo, number)`, never a bare number (#749). *A bare
  `gh issue close 148` closes issue 148 in whatever repo the cwd happens to be.*
- **Idempotence keys on: already-closed AND local-branch-absent AND remote-branch-absent AND
  worktree-absent.** Keying on the local branch alone would miss an **orphaned remote** from a failed prior
  delete and never retry it. **Repeated closeout produces the same state, no duplicate comments, no failure.**
- **Honor every return code** — `gh issue close` (:548), `gh pr edit` (:426). Never report success on a
  command whose exit you did not check.
- **No new human approval is required** (US-8.2 says so explicitly); the verified merge IS the consent.

**Footprint.** `github.py` (+closeout), `engine.py`, `tests/test_github.py`.
**Dependencies.** Blocked by #15. Unblocks #19.
**Deterministic / AI / Human.** All deterministic. No AI. **No approval (by PRD).**
**Failure & recovery.** A partial write (comment ok, close failed) is retryable with no duplicate comment.
**Logging.** Required (third-party). Each outcome is permanent.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:507-577` (`_Closeout.close_issues`, `_prose_referenced_issues`), `:518-522`
  (already-closed tracked separately, never re-commented), `:528-540` (comment-before-close), `:806-826`
  (the three-way noop key); `agent_runs_lib.py:423-452` (`close_run_for_pr` idempotence);
  `merged/SKILL.md:121`; `open-issue-transfer` #749.
- *Preserve:* comment-before-close; close only formal references; the three-way idempotence key; full
  owner/repo/number identity.
- *Refactor/extract:* `_Closeout` behind the gateway; `close_run_for_pr`'s **guard semantics** (exact match;
  only one status flips; every other case a byte-unchanged no-op) → IssueForge's idempotent closeout. Port
  `tests/test_close_run_for_pr_690.py`.
- *Replace:* the parent-epic update (**net-new — MARVIN has no epic concept**).
- *Discard:* the `--project marvin` default (:921).

**Out of scope.** Branch/worktree deletion (#17).
**Route.** `route:spec-up`.

---

## #17 — Safe cleanup: branches and worktrees (an INDEPENDENT stage result)
**Labels:** `v1` `phase:4` `route:spec-up`

**Problem (Codex #11).** Draft v1 said "do not port MARVIN's blanket halt-on-red-main" and then wrote *"every
anomaly HALTS"* — **recreating the exact coupling it had just discarded.** Fixed: **test health and cleanup
safety are INDEPENDENT stage results.** Only failed **delivery verification** (#15) is a global destructive
stop. A post-merge health failure stays **loud and nonzero** but **must not veto independently-safe cleanup
whose own predicates passed** (#760, invariant 10).
**PRD coverage.** US-8.3 (owner).

**Observable acceptance criteria**
- **Nothing is deleted without POSITIVE PROOF from #15.** The delete predicate is **content reachability of
  the exact merge commit**, never ancestry — under squash merges NO feature branch is ever ancestry-merged,
  so `git branch -d`'s self-guard never fires and someone reaches for **`-D`, which is unguarded** (Phase-1
  conflict C2).
- **No delete primitive exists that is not gated on a SUCCESSFUL stacked-PR discovery returning empty.** A
  **failed** `gh pr list --base` is **NOT** "no stacked PRs, safe to delete". *(A plain ref-delete on a
  branch with open stacked PRs **CLOSES all of them — this closed PRs #640–#646 on 2026-07-08.**)* Order:
  discover → retarget → **the `gh pr edit` must exit 0 BEFORE the verify runs** (*"a failed edit paired with a
  stale `gh pr view` still reporting main could otherwise clear the way to delete the base out from under an
  open PR"*) → read the new base back and confirm it equals the default → **only then delete.**
  *(v1 is single-run so a stack should be impossible; the guard stays as the backstop for out-of-band state.)*
- **Remote presence is read AUTHORITATIVELY** (#750): `git ls-remote --exit-code --heads origin <branch>`,
  keyed on exit code — **2 = authoritatively absent; anything else (incl. 128, origin unreachable) = treat as
  PRESENT**, *"so a transient remote error never produces a false noop that silently skips an orphaned remote
  branch."* **`refs/remotes/origin/<b>` is a CACHE and is NOT authoritative.**
- **Dirty or unverifiable worktrees are PRESERVED and reported.** A failed `git status --porcelain` is **NOT
  "clean"** — *"that would remove a worktree whose real state is unknown, discarding possible uncommitted
  work."* A failed `git worktree list` is **NOT "no worktree"**. A failed `worktree prune` after a successful
  `remove` is a **partial** result, **not a clean removal**.
- **Never** `reset --hard`, `clean -fd`, or `worktree remove --force` on an unverified tree.
- Cleanup emits its **own stage result and exit status**, separable from test-health results, with explicit
  acceptance tests for the independence.

**Footprint.** `workspace.py` (+cleanup), `github.py`, `tests/test_workspace.py`.
**Dependencies.** Blocked by #15.
**Deterministic / AI / Human.** All deterministic. No AI. Human: resolve any preserved/flagged item.
**Failure & recovery.** Preserve-and-report is always the safe direction.
**Logging.** Required. Each outcome is a permanent event.

**Prior-art and source audit**
- *Sources:* `merged_runner.py:339-446` (`_BranchCleanup`), `:343-348` (delete gated on
  `content_on_main is True`), `:351-438` (the #647 retarget→verify→delete order), `:455-498`
  (`_WorktreeCleanup`), `:725` / `:735-760` / `:763-780` (the three presence predicates);
  `merged/SKILL.md:78` (the restore-by-sha recovery); `open-issue-transfer` #725, #750, #760.
- *Preserve:* every tri-state posture; **`_worktree_for_branch` returns `(path, ok)` PRECISELY so absence and
  failure are distinguishable.** The #647 order. The recovery procedure: `git push origin
  <tip-sha>:refs/heads/<branch>` → `gh pr reopen` → `gh pr edit --base main` → delete again.
- *Refactor/extract:* **`_WorktreeCleanup`, `_BranchCleanup`, and the three predicates behind a
  `CleanupPredicate` interface — they take only `project` and a `runner`, so coupling is near-zero. This is
  the largest faithful extraction in the project.** Port the #761–#766 fixtures (missing commands, red tests,
  sync failure, scoped predicates, idempotent reruns, the configured green path) — **without MARVIN's
  PENDING-on-main convention.**
- *Replace:* the stage-independence model (above).
- *Discard — ⚠ ANTI-PORT:* **`merged_runner.py:828-836`, the blanket halt-on-red-main. The transfer ledger has
  ALREADY ruled this a defect (#760).** *"Extracting `merged_runner` faithfully would import a known bug."*
  Also `_DOCS_ONLY_PREFIXES = ("state/",)` (:623) and `state/projects.md` resolution. The `no-test-command`
  anomaly **cannot occur** — #3 makes the baseline mandatory at registration.

**Out of scope.** Issue/epic mutation (#16).
**Route.** `route:spec-up`.

---

## #18 — Shape an issue: in-place revision, pause conditions, not-testable exit
**Labels:** `v1` `phase:5` `route:spec-up`

**Problem.** A vague issue produces a vague contract. Shaping is the hardest AI-judgment surface, so the
pipeline first runs end to end on an already-buildable issue with shaping as a pass-through.
**PRD coverage.** US-3.1, US-3.4. Gap G11.

**Observable acceptance criteria**
- A buildable issue receives a proposed in-place revision. **NO GitHub write occurs before human approval**
  (test: `gh` is never invoked in write mode pre-approval).
- The mutation plan is a **list of dicts** — `{"op": "update_body", "issue": 148, ...}` — with `apply(plan,
  gateway)` dispatching on op. Four ops cover v1: `update_body`, `create_issue`, `add_comment`, `link_child`.
  **No visitor pattern, no op classes.**
- **Pause conditions:** duplicate open work; unresolved design decisions; unknown expected footprint.
- **Dedup runs against open issues** before minting anything.
- **Footprint extraction REJECTS an empty footprint** (no files-affected section, or one with zero paths).
- **G11 — a `not-testable` exit exists.** MARVIN's triage has **three** outcomes, not two
  (`spec-up/SKILL.md:80-82`): buildable → continue; oversized → decompose; **not-testable (pure refactor,
  docs, research) → route ELSEWHERE.** IssueForge's US-3 has no such exit, so such an issue **would be forced
  through a TDD contract it cannot have.** This adds a `not-testable` park outcome.
- **The testability-seam pre-check:** if the code under test is not reachable in isolation, **the seam becomes
  part of the work and is NAMED in the issue** (real cases: #411 added a `--conserve`/`validate_pair`
  surface; #414 added a `_run` monkeypatch seam). Without it the acceptance author later discovers the code
  is untestable and **guesses**.
- **Records the observability verdict from #8** (a shaped issue without one is refused).

**Footprint.** `shaper.py`, `github.py` (+write ops), `engine.py`, `tests/test_shaper.py`.
**Dependencies.** Blocked by #5, #7, #8.
**Deterministic / AI / Human.** Deterministic: the mutation plan, the pre-approval write ban, dedup, footprint
rejection, pause conditions. AI: readiness assessment and the proposed revision text. **Human: EVERY GitHub
mutation.**
**Failure & recovery.** Any pause condition halts, naming the reason.
**Logging.** Required (AI + third-party).

**Prior-art and source audit**
- *Sources:* `spec-up/SKILL.md:55-61` (the five Step-0 readiness gates), `:80-82` (three-outcome triage);
  `issues_to_findings.py:69-113`; `validate_spec_up_issue.py` + its three test files; `open-issue-transfer`
  #743, #490.
- *Preserve:* the five readiness gates; the testability-seam pre-check; dedup; **three** triage outcomes.
- *Refactor/extract:* `issues_to_findings.py:69-113` → a `FootprintExtractor` that **rejects an empty
  footprint**. (The `serialize:<file>` label half is wave scheduling — deferred.)
- *Replace:* readiness assessment (MARVIN's is model prose).
- *Discard:* the `route:*` / `wave:N` / `serialize:<hotfile>` label taxonomy; MARVIN's 8 canonical section
  names — **including "Pending-test convention", which encodes the PENDING-on-main model IssueForge rejects.**

**Out of scope.** Epic decomposition (#19). **The invariant lens (#25, `deferred-v2` — not in the PRD).**
**Route.** `route:spec-up`.

---

## #19 — Epic decomposition of an oversized issue
**Labels:** `v1` `phase:5` `route:spec-up`

**PRD coverage.** US-3.2, US-3.3.

**Observable acceptance criteria**
- An oversized issue receives a proposed epic + independently deliverable child issues. **No issue is created
  or edited before approval.**
- Approved decomposition **links every child from the epic**; each child **enters the normal queue
  independently**.
- Children are **vertical tracer bullets**, not horizontal layers.
- **Idempotency is keyed on PERSISTED MUTATION-OPERATION IDs and created-issue identities — NOT on child
  titles** (Codex): *titles are not durable idempotency keys; they collide and they get edited.* A partial
  GitHub write is resumable with **no duplicate children**.

**Footprint.** `shaper.py` (+decomposition), `github.py` (+`create_issue`, `link_child`), `store.py`
(operation ids), `tests/test_shaper.py`.
**Dependencies.** Blocked by #18, #16 (the epic-update surface).
**Deterministic / AI / Human.** Deterministic: the mutation plan, the pre-approval write ban, epic↔child
linking, queue admission, operation-id idempotency. AI: the decomposition judgment. **Human: every created/
edited issue.**
**Failure & recovery.** Resumable partial write, no duplicates.
**Logging.** Required (third-party).

**Prior-art and source audit**
- *Sources:* `prd-to-issues/SKILL.md:30-45`; `write-a-prd/SKILL.md:194-284`.
- *Preserve:* vertical tracer bullets over horizontal layers; a slice is demoable on its own.
- *Refactor/extract:* **nothing. `prd-to-issues/SKILL.md` is ENTIRELY model prose — there is NO decomposition
  code in MARVIN.**
- *Replace:* all of it (net-new).
- *Discard:* the HITL/AFK label taxonomy; the `ACCEPT:` satellite-issue pattern (GitHub-as-database).

**Out of scope.** Everything in #18.
**Route.** `route:spec-up`.

---

## #20 — Source-audit artifact + lint (makes US-11.1–11.4 enforceable)
**Labels:** `v1` `phase:0` `route:direct-tdd`

**Problem (Codex #8).** Draft v1 mapped US-11.1–11.4 to "per-issue definition of done". **That is prose, not
enforcement.** The criteria demand a *design record inventorying* the corresponding MARVIN skills/scripts/
tests/failure-driven updates, with **every inventoried behavior individually classified with a reason**. A
curated "Sources to review" list does not establish completeness. The transfer ledger is explicit: **"No
implementation issue is ready until its source audit and provenance entry exist"** — that is an acceptance
criterion **on the decomposition itself**, and it is checkable.

**User-visible outcome.** `issueforge audit check <stage>` validates a stage's provenance record and exits
non-zero with the specific gap.
**PRD coverage.** US-11.1, US-11.2, US-11.3, US-11.4 (owner — **now enforced, not aspirational**).

**Observable acceptance criteria**
- A machine-checkable artifact per stage: `docs/provenance/stages/<stage>.md` with a required schema —
  **source identifiers** (path + symbol/line), **behavior-level classification** with a reason (one of:
  deterministic engine policy / AI judgment / human approval / **discard-with-reason**), an **extraction
  decision** (extract / refactor / rewrite-with-reason), and **test provenance** (which MARVIN test ports
  with the code).
- **A completeness check against the stage source map** (`architecture.md`'s "Initial source map" +
  `open-issue-transfer`'s "Refactor-first source code" list): a stage whose map names a script with **no
  classification entry FAILS the lint.**
- **US-11.3:** a rewrite must **document why extraction was unsuitable**. A rewrite with no reason FAILS.
- **US-11.4:** ported tests carry a **provenance comment naming the MARVIN source**, and the lint checks it.
- The lint runs in CI. **No implementation issue may be marked ready while its audit is incomplete.**

**Footprint.** `docs/provenance/stages/`, `scripts/` or `src/issueforge/audit.py`, `tests/test_audit.py`,
`cli.py` (+`audit check`), CI workflow.
**Dependencies.** Blocked by: none. **Gates every implementation issue.**
**Deterministic / AI / Human.** Deterministic lint. AI: none. Human: authoring the classification.
**Failure & recovery.** A failing lint blocks readiness, naming the missing source or classification.
**Logging.** n/a (a build-time gate).

**Prior-art and source audit**
- *Sources:* `open-issue-transfer-2026-07-12.md` ("Refactor-first source code" + *"No implementation issue is
  ready until its source audit and provenance entry exist"*); `architecture.md` ("MARVIN extraction rule" +
  the initial source map); MARVIN's validator house style (`validate_*.py`: exit 0 + `OK`; exit 1 + one
  `ERROR:` per violation on stderr, stdout empty, **no fail-fast — report EVERY violation**).
- *Preserve:* the validator CLI convention and the report-every-violation rule.
- *Refactor/extract:* the `validate_*.py` structural pattern (file-path positional or `-` for stdin).
- *Replace:* the artifact schema (net-new).
- *Discard:* **`check_validator_invocation.py` ENTIRELY** — it lints SKILL.md files to enforce the byte-exact
  `"${MARVIN_PIPELINE_ROOT:-$HOME/marvin}"/scripts/<v>.py` invocation. **It is the purest expression of the
  coupling IssueForge exists to remove: a lint whose only job is to keep model-executed prose pointing at a
  MARVIN checkout.** Once IssueForge is a package with a CLI, there is nothing to lint.

**Out of scope.** The audits themselves (each issue authors its own).
**Route.** `route:direct-tdd`. Planned: `tests/test_audit.py`.

---

## #21 — Retention and `issueforge purge`
**Labels:** `v1` `phase:5` `route:direct-tdd`

**PRD coverage.** US-10.2, US-10.4.

**Observable acceptance criteria**
- Redacted prompts, responses, full command output, diffs, review packets **expire after 30 days by default**.
- **Permanent artifacts survive** (integration assertion; US-10.1 is owned by #4): transitions, approvals,
  overrides, commit/PR ids, contract manifests, verification summaries, cleanup outcomes.
- `purge` **never damages an active run** and never removes a permanent manifest.
- Retention is **ONE number** (`detailed_retention_days = 30`), not a per-artifact-class policy table.
- Purge is idempotent.

**Footprint.** `retention.py`, `tests/test_retention.py`, `cli.py` (+`purge`).
**Dependencies.** Blocked by #4.
**Deterministic / AI / Human.** All deterministic. No AI. No approval (`purge` is explicitly invoked).
**Failure & recovery.** Idempotent; a crash mid-purge leaves the store valid.
**Logging.** Required (filesystem). Each purge outcome is permanent.

**Prior-art and source audit**
- *Sources:* `open-issue-transfer` (the 30-day detailed-artifact policy); `agent_runs_lib.py` (the permanence
  model).
- *Preserve:* the permanent/detailed split. **Never purge a manifest.**
- *Refactor/extract:* nothing.
- *Replace:* all of it — **MARVIN has NO retention; its store grows forever.**
- *Discard:* n/a.

**Out of scope.** Redaction (#4 — secrets are never WRITTEN; this is expiry).
**Route.** `route:direct-tdd`.

---

## #22 — TUI + CLI/TUI parity — all eight views
**Labels:** `v1` `phase:5` `route:spec-up`

**Problem (Codex #2).** Draft v1 said logs and diffs "may ship thin" — **a silent v1 deferral while the matrix
claimed full coverage.** `prd-v1.md:103` requires **eight** views. **The trim is removed.**
**PRD coverage.** US-9.1, US-9.2 (owner).

**Note.** US-9.1 (one engine, one event stream, two surfaces) is an **architectural invariant on every slice
from #4 onward**, not work done here. Every earlier slice routes commands through the engine API and emits
structured events. This issue builds the *rendering*.

**Observable acceptance criteria**
- CLI and TUI invoke the **same engine commands** and consume the **same structured event stream** — one JSONL
  file the TUI tails and the CLI prints. **No broker, no observer registry, no async fan-out.**
- **The TUI displays ALL EIGHT (US-9.2, verbatim): queue position, current stage, logs, diffs, approvals,
  failures, PR status, and cleanup warnings.**
- **Closing either interface does not terminate or corrupt persisted state** (integration assertion; US-9.3 is
  owned by #5).
- Deterministic rendering: same event stream → identical output (required for testability).

**Footprint.** `tui.py`, `tests/test_tui.py`, `cli.py`.
**Dependencies.** Blocked by #5 (event stream), **#14 (PR status), #17 (cleanup warnings)** — the producers of
the eight views. **Build last:** building it early means rebuilding it as each stage lands.
**Deterministic / AI / Human.** All deterministic. No AI. The TUI is a SURFACE for approvals; it does not own
them.
**Failure & recovery.** Closing the TUI never kills a run.
**Logging.** Consumes the event stream; adds no new boundary.

**Prior-art and source audit**
- *Sources:* the existing `src/issueforge/tui.py` shell; `harness-phase3-requirements-brief` (the approval
  queue); `merged/SKILL.md:62` (the engine-emits-data / interface-composes-prose split);
  `pipeline_root.py:23-27` (the stdout/stderr discipline); `pipeline-verification-summary-2026-07-09.md`.
- *Preserve:* one engine, one event stream. **The engine emits data; the interface composes prose** — MARVIN
  arrived at this boundary the hard way and it holds. Deterministic output.
- *Refactor/extract:* nothing (MARVIN has no TUI).
- *Replace:* all of it.
- *Discard:* MARVIN's AskUserQuestion batching transport (a Claude Code affordance, not a design);
  `wave-status/SKILL.md`, which **greps for a `PENDING (#` marker string that no authoring skill actually
  mandates** — a known bug, and IssueForge has no such marker anyway.

**Out of scope.** A web GUI (v2 — the event log already covers it; no extra seam).
**Route.** `route:spec-up`.

---

## #23 — Self-contained boundary: a permanent CI invariant
**Labels:** `v1` `phase:5` `route:spec-up`

**Problem (Codex #9).** A one-time boundary proof **can pass and then be undone**: a later issue reintroduces
a MARVIN path, a runtime read, or a consumer-specific write **after** the proof closed. And a four-string grep
is not exhaustive. **This must be a PERMANENT invariant, not a milestone.**
**PRD coverage.** US-11.5, US-11.6, US-11.7 (owner).

**Observable acceptance criteria**
- **EXECUTION, not inspection.** A full lifecycle runs in a sandbox with **no MARVIN directory on disk** and
  with `MARVIN_PIPELINE_ROOT` / `AGENT_LOGS_DIR` **unset**, and completes.
- **A filesystem WRITE MONITOR asserts every write lands in exactly two places: IssueForge's own state root,
  or the registered target repo's WORKTREE.** **The registered NORMAL CHECKOUT is explicitly out of bounds**
  (Codex: "any write in the target repo" is too broad — that is what US-4.2/4.3's isolation proof exists to
  guarantee).
- **A GENERIC source scan**, not a four-string grep: scan **imports, executable argv, defaults, and path
  literals** for any reference reaching outside IssueForge — `marvin`, `MARVIN_*`, `state/projects.md`,
  `skills/`, `~/Projects/agentLogs`, `context/model-rates.json`, and **anything resolving relative to a
  sibling checkout.**
- IssueForge **never writes** MARVIN skills, context, state, ledgers, configuration, or generated files.
- **Consumers PULL** via documented CLI/JSON, event, and artifact interfaces. **IssueForge never PUSHES** into
  a consumer's private storage. IssueForge is the **sole owner** of its run state.
- **This suite runs in CI on every PR and blocks merge.** It is not "done" when it first passes.

**Footprint.** `tests/test_boundary.py`, CI workflow, `README.md`/`docs/` (the documented read interfaces),
`cli.py` (a `--json` query surface).
**Dependencies.** **Blocked by ALL v1 implementation issues** (it must observe a complete lifecycle) — **and
thereafter runs permanently.**
**Deterministic / AI / Human.** All deterministic. No AI. No approval.
**Failure & recovery.** n/a (an invariant, not a stage).
**Logging.** None new.

**Prior-art and source audit**
- *Sources:* `pipeline_root.py:36` (`ENV_VAR = "MARVIN_PIPELINE_ROOT"`) and `check_validator_invocation.py:728`
  (which **lints SKILL.md files for the byte-exact `"${MARVIN_PIPELINE_ROOT:-$HOME/marvin}"` form**);
  `agent_runs_lib.py:208-216` (`resolve_logs_dir`), `:27` (`_DEFAULT_RATES_PATH`); `merged_runner.py:714`
  (`_SCRIPTS_DIR.parent / "state" / "projects.md"`); `open-issue-transfer` ("Permanent system boundary").
- *Preserve:* the one-way boundary, permanently.
- *Refactor/extract:* nothing.
- *Replace:* **all four MARVIN-checkout assumptions.** (1) `MARVIN_PIPELINE_ROOT` — a seam whose ONLY job is
  to locate `~/marvin/scripts/*.py`; **IssueForge ships a package with entry points, so there is no root to
  resolve and nothing to lint.** (2) `state/projects.md` resolved relative to the script's own directory.
  (3) `resolve_logs_dir()` — MARVIN's store lives outside the repo *because MARVIN's repo is the session's
  repo*. (4) Skill routing (`--skill spec-dev`, `/merged`) — prose orchestration executed by a model;
  **IssueForge's engine IS the replacement.**
- *Discard:* all of the above.

**Out of scope.** Anything MARVIN-side. **MARVIN is read-only provenance and is not modified by this project.**
**Route.** `route:spec-up`.

---

# Scope additions the PRD does NOT require — filed, labeled `deferred-v2`, NOT in the v1 acceptance graph

Codex was right that draft v1 smuggled these in from prior-art analysis. They are real risks, so they are
**filed and tracked** rather than silently dropped — but they are **not** v1 acceptance criteria, and adopting
them requires an explicit human decision (a PRD amendment).

## #24 — Blocking mutation / anti-tautology gate  ·  `deferred-v2`
**Why it matters.** Red-proof and mutation are **orthogonal; neither subsumes the other.**
`assert result is not None` fails red (the module is missing) and passes green **while constraining nothing** —
**red-proof CANNOT catch it; only mutation can.** Conversely, a test that recomputes its expected value by
importing the implementation survives mutation only if the mutation moves both sides.
**Why it is deferred.** **The PRD does not require mutation testing.** It requires meaningful red, integrity,
green verification, and review. Mandating a mutation harness enlarges #13 substantially and is undefined for
non-pytest targets.
**If adopted:** port `check_acceptance_mutation.py` **with its v2 (#686) hardening intact** — the baseline-green
gate; package-path staging so `import pkg.impl` hits the MUTATED impl; BFS operator selection; **real pytest
collection to node ids**; and the 5-status vocabulary `caught / survived / inconclusive / baseline_red /
collection_error`. **Nothing mutable → `inconclusive`, NEVER `is_tautology=True`** (v1 returned the latter;
its own docstring calls that *"indefensible"*). **And flip the default: MARVIN's "prints the per-test report
and exits 0 by default — it never hard-fails CI" is an ANTI-PORT.** A gate that cannot fail is not a gate.
**Novel reuse worth noting:** `_assertion_dep_roots`/`_dep_closure` (#588) can double as a **deterministic
tautology detector** — *an assertion whose expected side transitively depends on an import from the
implementation package is a recomputation, not a golden value.* Proven code, new purpose (US-11.3).

## #25 — The invariant lens for shaping  ·  `deferred-v2`
**Why it matters.** MARVIN's most sophisticated shaping rule (`spec-up/SKILL.md:91-96`), and it has **no
analogue in IssueForge's PRD.** Incident: rdv-expenses #217 / PR #383 — *"two interleaved requests both pass
the SELECT, both commit, one expense double-matched."* **TDD-from-prose derives only the SEQUENTIAL criterion,
which an app-level SELECT-then-UPDATE guard satisfies while a concurrent race still violates the invariant.**
When an issue asserts ownership/uniqueness/"must never happen twice", the gate REQUIRES criteria the happy
path cannot satisfy: a **DB-level constraint** (the durable fix was `UNIQUE(matched_expense_id) WHERE
status='matched'` with `23505` → 409) **plus a concurrency test** (two interleaved requests, not a sequential
pair). Sibling lenses: idempotency-under-retry, partial-failure/rollback.
**Why it is deferred.** It is an **unapproved scope addition** to shaping. Adopt via PRD amendment or leave out.

---

# PRD coverage matrix — 51/51, single owner each

| Criterion | Owner | Criterion | Owner | Criterion | Owner |
|---|---|---|---|---|---|
| US-1.1 | #3 | US-4.4 | #6 | US-8.2 | **#16** |
| US-1.2 | #3 | US-5.1 | #9 | US-8.3 | #17 |
| US-1.3 | #3 | US-5.2 | #9 | US-8.4 | #16 |
| US-1.4 | #3 | US-5.3 | #10 | US-9.1 | #22 |
| US-2.1 | #4 | US-5.4 | #10 | US-9.2 | **#22 (all 8 views)** |
| US-2.2 | #5 | US-5.5 | #11 | US-9.3 | #5 |
| US-2.3 | #5 | US-6.1 | #12 | US-9.4 | #7 |
| US-2.4 | #5 | US-6.2 | #13 | US-10.1 | #4 |
| US-3.1 | #18 | US-6.3 | #13 | US-10.2 | #21 |
| US-3.2 | #19 | US-6.4 | #7 | US-10.3 | #4 |
| US-3.3 | #19 | US-6.5 | #8 | US-10.4 | #21 |
| US-3.4 | #18 | US-6.6 | #8 | US-11.1 | **#20** |
| US-4.1 | #3 | US-6.7 | #8 | US-11.2 | **#20** |
| US-4.2 | #6 | US-7.1 | #14 | US-11.3 | **#20** |
| US-4.3 | #6 | US-7.2 | #14 | US-11.4 | **#20** |
| | | US-7.3 | #14 | US-11.5 | #23 |
| | | US-7.4 | #5 | US-11.6 | #23 |
| | | US-8.1 | #15 | US-11.7 | #23 |

**51/51 owned. Zero silently weakened. Zero silently deferred.**
Downstream repeats (US-6.3 in #14, US-7.4 in #14, US-9.3 in #22, US-10.1 in #21) are **integration
assertions**, explicitly labeled, not second implementations.

# Dependency graph

```
#20 (source-audit lint) ── gates every implementation issue
#2  (process + config + verification adapter)
├── #3 (registry) ──┐
├── #8 (observability) ──────────────────┐
└── #4 (store + enqueue + stub stage) ───┤
    ├── #21 (retention)                  │
    ├── #5 (queue control) ──────────────┤
    ├── #7 (providers) ──────────────────┤
    └── #6 (worktree + baseline) ────────┤
         └── #9 (author + DETERMINISTIC red proof)
              └── #10 (independent SEMANTIC review)
                   └── #11 (approval freezes manifest)
                        └── #12 (integrity + amendment path)
                             └── #13 (implement + repair(2) + readiness)   [also needs #8]
                                  └── #14 (green PR)                       [also needs #8]
                                       └── #15 (DELIVERY VERIFICATION — the global stop)
                                            ├── #16 (closeout + parent epic)
                                            │    └── #19 (epic decomposition)  [also needs #18]
                                            └── #17 (safe cleanup — independent stage result)
#5 + #7 + #8 ── #18 (shaper)
#5 + #14 + #17 ── #22 (TUI, all 8 views)
ALL ── #23 (boundary invariant — then runs permanently in CI)
```

# Risks

1. **Retry-budget divergence.** The PRD's single budget of two (US-6.2) merges what MARVIN split into three
   nested caps, and MARVIN's provenance warns *one counter hides two different failures*. **We follow the
   PRD.** #13 mitigates by making review rounds a separate protocol that does not consume the budget. If cap
   exhaustion proves noisy in practice, amend the PRD.
2. **Codex plan rate limits are SHARED with interactive use** (5-hour rolling window + weekly cap, across
   CLI/IDE/cloud). **Harness throughput competes with the human's own Codex use.** Budget for it.
3. **The tautology hole is real and v1 does not close it** (#24 is deferred). A test that constrains nothing
   can pass every v1 gate. Accepted knowingly; revisit if it bites.
4. **v1 ships a pytest adapter only.** A non-pytest repo gets the `generic` adapter and **cannot** get a
   frozen contract. This is stated, not silently degraded — but it narrows real-world v1 applicability.
5. **The `generic` adapter's refusal to author a contract** may surprise users who registered a JS/Go repo.
   Surface it at `repo add`, not at contract time.
6. **#23's write monitor is platform-specific.** Budget for a macOS/Linux-portable implementation.
