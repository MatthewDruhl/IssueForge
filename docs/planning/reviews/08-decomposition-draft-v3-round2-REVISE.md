VERDICT: REVISE

| Round-1 finding | Status | Evidence from DRAFT-V3.md |
|---|---|---|
| 1. S2 behavior-level source-audit completeness | PARTIALLY FIXED | S2 now says, “**The inventory unit is a BEHAVIOR, not a file**” and adds a fifth failure mode for “**a discovered BEHAVIOR … inside an inventoried artifact with no disposition**” (lines 414–426), plus human approval (429–431). But discovery still enumerates “**public symbols, associated test files, and … references**” (418–420), which does not enumerate distinct behaviors within a symbol, private behavior, or unreferenced failure-driven updates. |
| 2. S4 redaction verification before producers exist | PARTIALLY FIXED | The structural part is improved: “**ONE redacting writer owns ALL artifact persistence, and bypassing it is MECHANICALLY IMPOSSIBLE**” (579–582), and the exhaustive test moved to S24 (583–588, 2310–2314). However, S4 also promises “**Each later producer … carries its own canary assertion**” (586–588), while S7, S10, S11, and S15 contain persistence statements but no such per-producer acceptance criteria. |
| 3. Runtime graph bypasses shaping mutations | FIXED | S9 now mandates: “**buildable → S20 … → THEN baseline + contract authoring**,” “**oversized → S21 … the PARENT RUN STOPS HERE**,” and “**S10 IS NOT REACHABLE until the applicable shaping mutation has completed**” (1032–1043). S20 now unblocks S10 and supplies shared mutation machinery (2004–2009); S21 says its dependency is machinery-only (2058–2060). |
| 4. Observability verdict ordering and override hole | PARTIALLY FIXED | The draft adds prospective and post-diff analyses (920–928) and makes an unanticipated boundary a “**DETERMINISTIC FAILURE**” requiring amendment (929–934, 1617–1625). But S8 later still describes “**a heuristic feeding a judgment call**,” “**one `classify(diff)` used as a hint**” (942–944), contradicting the two deterministic analyses and leaving the enforcement mechanism underspecified. |
| 5. Unsound preexisting-baseline subtraction | FIXED | S10 snapshots `BASE_IDS`, computes `ADDED`, runs “**EVERY id in `BASE_IDS`, at the candidate**,” rejects missing or reused base IDs, and requires amendment for revision/supersession (1129–1142). |
| 6. S11 consumes S14’s implementation-review counter | FIXED | S11 now owns `contract_review_rounds` and explicitly does not consume the two S14 counters (1249–1255). Every test/fixture change reruns S10 and creates new SHA-bound evidence before S12 may freeze (1256–1259). |
| 7. Dependency closure too narrow and too broad | PARTIALLY FIXED | S12 now names tests, fixture/config/plugin providers, helpers, transitive dependencies, and external identities/versions (1323–1333), and attempts to exclude the system under test (1334–1343). However, using the approved file scope as the discriminator makes legitimate contract files both protected and “system under test,” while S13 only explicitly rechecks dependency hashes—not external package identity/version. |
| 8. S24 impossible and oversized | PARTIALLY FIXED | S25 moves the write seam and AST lint to phase 0 and changes executable validation to provenance-based rules (2197–2258); S24 is reduced to lifecycle/integration proof (2298–2335). But the dependency metadata does not consistently make S25 a hard prerequisite: S4 writes to disk yet its table row and issue body omit S25 (179, 596), and S3 persists the registry before the claimed “S4 onward” gate. |

BLOCKING findings

1. **S2 — behavior completeness still reduces behavior to discoverable symbols and references.**

   The PRD requires: “**Before stage implementation, its design record inventories the corresponding MARVIN skills, scripts, tests, and relevant failure-driven updates**” and “**Every inventoried behavior is classified … with a reason**” (prd-v1.md lines 132–133).

   DRAFT-V3 says discovery enumerates “**public symbols, its associated test files, and the failure-driven updates that touch it (from commit/issue references in the file and its tests)**” (lines 418–420). A public symbol can implement several independently meaningful safeguards, private symbols can be load-bearing, and relevant updates need not be referenced inside the affected source or test. Such omissions never become “discovered behaviors,” so failure mode five cannot fire.

   What must change: define a behavior identifier/record independently of symbol identity; include private or nested behavior when relevant; discover update/test linkage from the authoritative provenance/history sources rather than only embedded references; and make the human approval packet include the complete discovery evidence so omissions are reviewable.

2. **S4/S7/S10/S11/S15 — promised per-producer redaction tests are not actually in the producer issues.**

   The PRD says: “**Authentication tokens, credential files, environment-variable values, detected secrets, and hidden model reasoning are never retained**” (prd-v1.md line 124).

   S4 correctly states: “**Each later producer therefore carries its own canary assertion through this API as part of its own acceptance criteria**” (DRAFT-V3 lines 586–588). But the later issues only say, for example, “**Full output persists … through S4’s redacting writer**” (S7, 845–846), “**The full review packet persists, redacted**” (S11, 1277), and “**Every verdict and override is permanent**” (S15, 1668). They do not contain the promised canary acceptance tests. S24’s lifecycle canary is valuable, but it does not replace producer-local tests for error, timeout, and alternate persistence branches that one lifecycle may not exercise.

   What must change: add explicit producer-level canary criteria to every producer of prompts, responses, command output, diffs, review packets, events, and error traces. Each must exercise success and failure persistence through S4’s API and assert the prohibited values never reach disk. Keep S24 as the exhaustive integration backstop.

3. **S8/S15 — the observability mechanism remains internally contradictory and partly declarative.**

   The PRD says: “**Logging is required when changed code crosses an HTTP, database, subprocess, filesystem, queue, third-party service, or AI boundary**” and “**Required logging follows the target project's logger, levels, formats, and correlation conventions and excludes contract-listed sensitive fields**” (prd-v1.md lines 83–84).

   S8 first defines two deterministic analyses (DRAFT-V3 lines 920–934), but later says the implementation is “**a heuristic feeding a judgment call**” with “**one `classify(diff)` used as a hint**” (942–944). S15 then asserts, without defining evidence or checks, that required events, project conventions, and sensitive exclusions are “**deterministically verified**” (1623–1625). This is the round-1 defect partly reworded: the desired result is asserted, but the actual classifier/enforcement contract remains inconsistent.

   What must change: remove the obsolete one-classifier/heuristic language; specify separately testable prospective and diff APIs; define how boundary additions, required success/failure events, logger selection, level/format/correlation reuse, and sensitive-field leakage are evidenced. Any portion that remains semantic must be assigned to review, while every deterministically established failure remains outside the override.

4. **S12/S13 — the closure discriminator can freeze legitimate contract files or leave them unprotected, and external dependencies are frozen but not revalidated.**

   The PRD says the approved manifest freezes “**dependent fixtures/configuration**” and the approved scope (prd-v1.md line 68), while adapter discovery must cover “**test modules, every fixture provider and configuration file … plugins, and their transitive dependencies**” (line 69).

   S12 declares test modules and outcome-influencing helpers protected (DRAFT-V3 lines 1323–1328), but then says “**a path inside the approved scope is the system under test and is expected to change**” and a path in both categories is a contradiction that fails freezing (1334–1340). Acceptance tests and supporting fixtures must normally be included in the approved final-PR scope, yet they are also necessarily protected contract inputs. The proposed discriminator therefore either excludes them from scope—causing S15’s scope check to fail—or places them in both sets and refuses to freeze.

   Separately, S12 freezes external package identity/version (1329–1333), but S13 only mandates recomputation of “**EVERY frozen dependency hash**” (1431–1433). It does not require re-probing and comparing the external identities/versions at candidate verification.

   What must change: model file roles explicitly rather than inferring “system under test” from approved-scope membership. The approved scope may contain both immutable contract paths and mutable implementation paths; their intersection is legitimate and must be governed by the stricter contract rule after freezing. Also require S13 to re-resolve and compare every frozen external package/plugin identity and version in the authoritative verification environment.

5. **S25/S3/S4 — the early write seam is still bypassable in the build dependency graph.**

   The PRD requires that IssueForge “**never writes MARVIN skills, context, state, ledgers, configuration, or generated files for MARVIN's use**” (prd-v1.md line 137).

   DRAFT-V3 calls S25 a gate for every disk-writing issue (lines 255, 2258), and one diagram annotates S4 as needing S25 (260). But the issue inventory lists S4 as blocked only by S1 and S3 (179), and S4’s own dependency section repeats only S1 and S3 (596). S3 itself persists a registry (487–500) yet S25 claims to gate only “S4 onward” (2258). Thus the actual issue dependencies permit persistent writers to land before the structural seam that is supposed to constrain them.

   What must change: make S25 an explicit hard dependency in the issue table and each affected issue body, beginning with the first production filesystem writer, including S3 and S4. Add a CI-order acceptance test proving no writing module can land before the seam and boundary lint are active.

NON-BLOCKING observations

1. The 59-entry coverage matrix remains single-owner and the listed owners generally contain substantive acceptance mechanisms rather than title-only mappings.

2. The S9 runtime branch is materially repaired; it now prevents authoring before the applicable shaping mutation and correctly terminates the oversized parent run.

3. S10’s base-ID-set mechanism fixes the prior subtraction bug and correctly treats removal, reuse, deselection, and implicit supersession as failures.

4. S11’s separate contract-review protocol is now coherent with the downstream implementation budgets and correctly invalidates stale red evidence after test changes.

5. S15’s implementation-review override matches the PRD’s bounds: “**human-only**,” “**per finding**,” exact-SHA-bound, invalidated by later changes, non-waiving of deterministic gates, retained in the PR, and not merge authorization (DRAFT-V3 lines 1637–1649; prd-v1.md line 80).

6. S20’s placement and gateway-write ownership are substantially better. As hardening, bind its proposed revision to the approved S9 artifact hash so an approved revision cannot silently change the requirements or footprint that S9 approved.

7. S10 and S13 remain unusually large, high-risk issues, although their load-bearing mechanisms are cohesive enough that splitting them carelessly could create temporarily permissive gates.

8. The two deferred-v2 items remain visibly outside the v1 criterion graph and have not been substituted for required v1 behavior.
