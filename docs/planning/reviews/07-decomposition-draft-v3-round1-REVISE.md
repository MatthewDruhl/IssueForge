VERDICT: REVISE

BLOCKING findings

1. **S2 — the source-audit lint still cannot establish behavior-level completeness.**  
   The PRD requires: “Before stage implementation, its design record inventories the corresponding MARVIN skills, scripts, tests, and relevant failure-driven updates,” and: “Every inventoried behavior is classified…” S2 detects an omitted artifact, but provides no mechanism for detecting omitted behaviors, supporting tests, or failure-driven updates inside an artifact. Its scan can list a file while a provenance record names only one of several relevant safeguards and still passes. The draft also assigns no approval gate to the human classification.  
   **Required change:** define authoritative discovery roots and artifact-to-test/update linkage, require behavior-level inventory coverage, and add an independent human review/approval of each stage audit before implementation readiness. The lint must detect missing behavior/test dispositions, not merely missing files.

2. **S4 — its claimed US-10.3 verification runs before most persistence paths exist.**  
   The PRD says authentication material, environment values, detected secrets, and hidden reasoning “are never retained.” S4 claims a canary traverses “every capture path (prompt, response, stdout, stderr, diff, review packet, event stream, error trace),” but prompts, AI responses, diffs, and review packets are introduced later by S7, S10, S11, and S15. A fake early path cannot prove those later modules use the redacting writer.  
   **Required change:** let S4 own a structurally mandatory persistence/redaction API, require each later producer to test through it, and place the exhaustive all-real-path canary in a final integration/CI issue. Make bypassing the writer mechanically impossible.

3. **S9/S10/S20/S21 — the runtime graph bypasses required shaping mutations.**  
   The PRD says: “A buildable issue receives a proposed in-place revision,” and an oversized issue receives an approved epic/children before those children enter the queue. Yet the draft explicitly has `S9 → S10`, while S20 is built after closeout and S21 depends on S20. Thus contract authoring can begin after S9 without the S20 revision path ever running. An oversized run is also routed through a dependency on the buildable-issue revision slice.  
   **Required change:** encode an unavoidable runtime branch after S9: `buildable → S20 approval/apply → baseline/authoring`; `oversized → S21 approval/apply → children queued, parent run stops`; `blocked → pause`. S10 must not be reachable until the applicable shaping mutation completes. S21 should depend on the shared mutation infrastructure, not successful buildable-issue processing.

4. **S8/S9/S15 — the observability verdict is produced from an input that does not yet exist, and later misses are overridable.**  
   The PRD requires: “Every shaped issue records `required`, `existing coverage sufficient`, or `not applicable`,” and: “Logging is required when changed code crosses an HTTP, database, subprocess, filesystem, queue, third-party service, or AI boundary.” S8 says its classifier analyzes “a diff introducing a call or import,” but S9 consumes its verdict before implementation produces a diff. S15 then requires only a recorded verdict and describes the classifier as a heuristic. Consequently, an approved `not applicable` verdict can survive an actual boundary-crossing diff unless the AI reviewer notices it; because it is then merely an AI finding, US-6.5’s override path could waive the underlying mandatory logging requirement.  
   **Required change:** define a pre-authoring analysis over the issue, proposed footprint, and existing code for S9, then require S15 to reconcile the actual diff against that approved verdict. Any newly detected boundary must become a non-overridable deterministic failure requiring contract amendment, not an overridable review finding. Explicitly verify required events, project conventions, and sensitive-field exclusions.

5. **S10 — the baseline-set formula is incorrect and permits exclusion of a preexisting test.**  
   The PRD requires: “The new tests fail for a recorded expected behavioral reason while the preexisting baseline remains green.” S10 specifies `canonical_collect(base) − new_acceptance_ids`. Newly added IDs do not exist at base, so this is normally just the base set; worse, if an authored test reuses a preexisting ID, the subtraction removes that preexisting test from the baseline check. This conflicts with S10’s separate permission to mark existing tests `revise` or `supersede`.  
   **Required change:** snapshot the exact base ID set, compute genuinely candidate-added IDs separately, and execute every base ID at the candidate. Reusing or removing a base ID must fail rather than be treated as “new.” If revision/supersession is allowed, define a separate human-authorized amendment path that cannot silently reduce the preexisting baseline.

6. **S11 — it consumes a downstream implementation-review budget and omits mandatory re-verification after test fixes.**  
   The PRD defines `review_rounds` specifically for when “the independent review raised blocking findings, so the implementer fixes them in place,” within US-6 implementation. S11 nevertheless says contract-review rounds consume `review_rounds` owned by S14, while S14 is downstream of S11 through S12 and S13. This is an impossible producer dependency and conflates test-author review with implementation review. S11’s “fix everything” round also does not require S10’s deterministic red proof to be rerun after test changes.  
   **Required change:** remove S11’s dependence on S14’s counters. Give contract review its own explicitly defined protocol/state if needed. After every test or fixture change, rerun all S10 predicates, generate new SHA-bound red evidence, and review that new head before S12 can freeze it.

7. **S12/S13 — the dependency boundary is simultaneously too narrow and potentially too broad.**  
   The PRD requires discovery covering “plugins, and their transitive dependencies.” S12 narrows this to “transitive repository dependencies,” leaving installed plugins and their external dependency versions outside the frozen contract. Conversely, if “the test modules … and their transitive repository dependencies” includes imports of the production module under test, S13’s absolute protected-path rule freezes the implementation itself and makes the issue unbuildable.  
   **Required change:** specify the closure roots precisely. Protect test logic, fixture/config/plugin providers, helper modules that influence expected outcomes, and their transitive dependencies—including immutable identities/versions for external packages—while explicitly excluding system-under-test imports intended to change. Add fixtures proving both the `conftest → helpers.py` bypass is caught and an imported production module remains editable.

8. **S24 — the final boundary issue is internally impossible and far too large for one focused slice.**  
   S24 permits subprocess executables only from an allowlist containing “`git`, `gh`, plus the configured provider executable.” S1 and the PRD require execution of the repository’s baseline command, and v1 necessarily runs pytest/uv or another configured verification executable; those commands would fail S24’s lint. S24 also introduces the sole filesystem-write seam only after every earlier module has already implemented writes, forcing a cross-project refactor alongside a wheel-installed lifecycle test, six-class AST analyzer, query interfaces, documentation, and CI.  
   **Required change:** introduce the write/path seam before filesystem-writing stages land, leaving S24 as the final permanent integration invariant. Split the AST lint and lifecycle/canary verification into focused issues if necessary. Model frozen configured verification commands explicitly, constrain their cwd/output paths, and test real subprocess attempts against forbidden MARVIN-shaped targets rather than allowing only `git`, `gh`, and the provider.

NON-BLOCKING observations

1. The PRD does contain exactly **59** acceptance criteria, and the matrix assigns each numerical criterion exactly once.

2. S15’s US-6.5 override is correctly bounded: authenticated-human-only, per-finding, SHA-bound, invalidated by later changes, preceded by a fresh review, non-waiving of deterministic gates, disclosed in the PR, and not merge authorization.

3. The draft correctly separates deterministic red evidence in S10 from semantic expected-reason/coverage review in S11.

4. The earlier push-order and closeout defects are materially corrected: S14 does not push, S16 is the only push/PR slice, and S18 keys closure on the persisted repository-qualified run issue.

5. S24’s intended no-MARVIN-write-back direction is appropriately structural rather than merely observational; the blocking problem is its timing and inconsistent executable model, not the intended boundary.

6. The two deferred-v2 items are properly identified as additions not required by the PRD. Neither mutation testing nor the invariant lens has been silently substituted for a v1 criterion.
