# Independent review

Draft v2 is materially better, but it still has blocking contradictions and incomplete gates. The 51-row matrix is numerically complete; several mapped owners do not fully implement the criteria they claim.

## BLOCKING FINDINGS

1. **#2 breaks the repository-agnostic v1 promise.**

   The PRD and architecture promise operation on “any registered local GitHub repository” ([PRD](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:9), [architecture](/Users/matthewdruhl/Projects/IssueForge/docs/architecture.md:5)). Yet #2 ships only pytest and explicitly refuses to author a contract for the generic adapter ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:168), [draft](/private/tmp/issueforge-prd-to-issues-draft.md:632)). A successfully registered Go, JavaScript, Rust, or non-pytest Python repository therefore cannot complete the defining v1 workflow.

   Calling that limitation explicit does not make it PRD-conformant. Either v1 needs another framework-neutral way to freeze stable test identities and dependencies, or the PRD must be amended to say v1 is pytest-only.

2. **#11/#12 still do not freeze the complete fixture/configuration boundary.**

   #12 correctly recomputes every hash that #11 records ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:825)), but #11’s discovery set remains incomplete. It includes test modules’ import closure and hashes `conftest.py`, but not the transitive dependencies of `conftest.py`, pytest plugins, or other adapter-loaded fixture/configuration modules ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:751)).

   Concrete bypass: `conftest.py` imports a fixture implementation from `tests/helpers.py`. The implementer edits only `helpers.py`. The conftest hash, test hashes, command, and collected IDs remain unchanged; the helper is not necessarily in the test modules’ import closure. The fixture can therefore neutralize the contract without detection. This violates US-5.5 and US-6.1.

   The adapter must return an engine-discovered dependency closure covering tests, fixture providers, plugins, configuration loaders, and their transitive repository dependencies.

3. **#13 pushes before readiness, directly contradicting US-7.1.**

   #13 says the runner performs `diff → commit → push → verify-at-origin`, and that origin must contain the SHA “before any review” ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:891)). US-7.1 says IssueForge pushes only after all readiness gates pass ([PRD](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:82)). #14 repeats the PRD-correct ordering ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:955)), so the two issues contradict each other.

   Use a local immutable candidate SHA for code review and readiness. Push only after the complete gate succeeds.

   #13 also says readiness requires “no blocking findings” but then permits a human override of a blocking finding ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:898), [draft](/private/tmp/issueforge-prd-to-issues-draft.md:920)). US-6.3 provides no code-review override. Test-contract review has an explicit override in US-5.4; implementation review does not. The override must be removed or the PRD amended.

4. **#16 does not guarantee closing the exact run issue.**

   US-8.2 requires closing the exact run issue ([PRD](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:93)). #16 instead closes only formal `closingIssuesReferences` ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:1067)), while #14 never requires the exact run issue to be installed as such a reference.

   Consequently:

   - A PR without a closing reference leaves the run issue open.
   - A PR with multiple closing references can close issues other than the exact run issue.

   Closeout must operate on the persisted repository-qualified run-issue identity. Closing references may be verified or reported, but cannot replace that identity.

   Its US-8.4 ownership is also internally coupled to cleanup: #16’s idempotence key includes branch and worktree absence ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:1075)), although cleanup belongs to independent sibling #17 and #16 does not depend on it. Either a coordinator must own whole-closeout idempotence after both #16 and #17, or #16’s idempotence predicate must be limited to its own mutations.

5. **#20’s lint cannot establish the completeness demanded by US-11.1–11.4.**

   The PRD requires every corresponding skill, script, test suite, and relevant failure-driven update to be inventoried ([PRD](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:122)). #20 checks only the architecture’s explicitly “Initial source map” and the transfer ledger’s “including at minimum” list ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:1277)). Those inputs are expressly non-exhaustive.

   The lint can therefore pass a complete-looking record that omitted an unlisted canonical skill, supporting test, or failure update. A provenance comment also proves only that a ported test names something; it does not prove that every reused safeguard’s explanatory tests were ported.

   A real enforcement mechanism needs a versioned authoritative inventory of canonical source artifacts and behavior/test identifiers, with failures for:

   - unclassified inventory entries;
   - extract/refactor decisions lacking source-test disposition;
   - reused safeguards lacking mapped ported tests;
   - discovered canonical artifacts absent from the inventory.

6. **The dependency graph does not enforce shaping before authoring and implementation.**

   The architecture orders shaping before workspace creation and test authoring ([architecture](/Users/matthewdruhl/Projects/IssueForge/docs/architecture.md:9)). The draft instead places #18 outside the main #6→#9→#13 chain ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:1513), [draft](/private/tmp/issueforge-prd-to-issues-draft.md:1524)).

   This is especially consequential because #13 requires an approved file scope, whose producer is #18’s approved footprint, and observability is supposed to be a shaped-contract input. #13 can currently be declared unblocked without #18 ever landing.

   The implementation dependency should make shaping—or an explicitly scoped buildable-issue shaping slice—a prerequisite to contract authoring. #19 may remain later for oversized decomposition, but #18’s buildable path cannot.

7. **#9 leaves the “preexisting baseline remains green” execution contract ambiguous and potentially impossible.**

   #9 requires the preexisting baseline to remain green “in the same run” ([draft](/private/tmp/issueforge-prd-to-issues-draft.md:614)), but does not specify how the baseline excludes the intentionally failing new acceptance tests. Running the repository’s ordinary baseline command at the test commit will commonly include those tests and therefore be red.

   This is load-bearing because #9 claims the rerun detects author-introduced conftest/config breakage; merely reusing #6’s earlier baseline result cannot do that. The issue must define an adapter operation that executes the preexisting test-ID set at the contract candidate, excluding only the newly authored acceptance IDs, or another equally precise comparison.

## Prior v1 findings re-check

- **US-8.2 parent-epic update:** Added, but the criterion remains blocked by the exact-run-issue defect above.
- **US-9.2 all eight TUI views:** Fixed. #22 explicitly requires all eight and depends on their producers.
- **Deterministic versus semantic meaningful-red:** The #9/#10 boundary is now conceptually correct. Collection, phase, baseline health, XPASS, and SHA binding are deterministic; correspondence with the named behavioral reason is semantic.
- **Dependency-hash comparison at head:** Added correctly, but the discovered dependency set remains incomplete.
- **Observability ordering:** #8 is earlier than its stated consumers, but the disconnected shaping dependency prevents the lifecycle ordering from being complete.
- **Boundary invariant/#23:** Substantially fixed. The sandbox lifecycle, write monitor, source scan, read interfaces, and permanent CI execution are credible. Deferring its initial implementation until the full lifecycle exists is acceptable only if it then blocks v1 completion and remains required thereafter.
- **No MARVIN write-back:** Adequately explicit in #23.
- **Delete-safety/#15 and cleanup independence/#17:** Substantially fixed.

## Non-blocking observations

- Deferring mutation testing is defensible. Mutation testing strengthens anti-tautology confidence, but US-5’s meaningful red can be achieved through deterministic red proof plus independent semantic coverage review. Mutation is not textually required by the PRD.
- The invariant lens is likewise a useful v2 shaping enhancement rather than a v1 criterion.
- #12 and #13 remain large. Their cohesion is understandable, but both need careful internal milestones; #13 in particular combines implementation dispatch, repair accounting, code review, verification, scope enforcement, and Git ownership.
- The proposed first full tracer bullet is useful infrastructure validation, but it is not a complete product-lifecycle tracer until a buildable shaping pass is present.
- The matrix has no literal duplicate owners, and US-9.2 is no longer weakened. Its “51/51” claim is nevertheless semantically false because the owners identified above do not fully satisfy their mapped criteria.

VERDICT: REVISE
