The reviewer is directionally correct, but their wording hides a specification gap: the PRD implies that the shaper proposes the expected file scope; it does not explicitly define how that proposal becomes the “approved file scope.”

## 1. Does implementation genuinely depend on a shaper artifact?

Yes, if “approved file scope” is supposed to be a prospective constraint rather than an after-the-fact description of the diff.

The PRD assigns the relevant production responsibility unambiguously:

> “Issue shaper: owns readiness assessment, deduplication, revision, decomposition, **footprint estimation**, and approved GitHub mutation plans.” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:133))

It also makes a known footprint a precondition for continuing:

> “Duplicate open work, unresolved design decisions, and an **unknown expected footprint pause the run**.” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:41))

And implementation readiness requires:

> “PR readiness requires green acceptance tests, green full baseline, configured quality gates, **approved file scope**, and an independent code review with no blocking findings.” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:71))

That is a genuine data dependency:

`expected footprint → approval → implementation diff comparison → readiness verdict`

But the reviewer overstates how completely it is specified. Neither document says:

- that expected footprint and approved file scope are the same serialized artifact;
- who approves the scope;
- at which human gate approval happens;
- whether it is exact files, allowed path patterns, or a semantic boundary;
- how scope expansion is authorized.

The contract-freeze criterion does not include file scope. Its exhaustive-looking list is:

> “Human approval freezes the exact test commit, file hashes, collected identifiers, dependent fixtures/configuration, command, and red evidence.” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:62))

The architecture repeats that list without file scope. Therefore, claiming that scope is already approved “at contract freeze” is unsupported. It could be added there, but that would be a design decision, not a reading of the current text.

Other proposed sources are worse:

- **Derived from the final diff:** invalidates the word “scope” as a constraint. Every diff would approve itself.
- **Declared directly by a human:** possible, but the engine still needs a scope proposal or explicit human entry. It also bypasses the PRD’s assignment of footprint estimation to the shaper.
- **Approved at contract freeze:** sensible, but requires extending the shape/contract boundary and manifest schema.
- **Inferred from acceptance-test files:** unsound. Tests identify observable behavior, not necessarily all permitted implementation paths.

So the reviewer is correct about the dependency, but not that the PRD fully pins its mechanics. The missing approval transition is a requirements defect that the decomposition must resolve.

## 2. Split shaping into a buildable-path slice

Yes. Treating epic decomposition and expected-footprint estimation as one indivisible “shaper” is bad decomposition. They share a lifecycle label, not an implementation risk profile.

The first shaping slice should be **Buildability Contract**, with this boundary:

Inputs:

- issue title/body and repository identity;
- open-issue search results for duplicate assessment;
- read-only repository structure or equivalent repository evidence;
- observability-policy rules.

Outputs:

- readiness classification: `buildable`, `oversized`, or `blocked`;
- duplicate verdict and evidence;
- unresolved-design-decision list;
- proposed expected footprint, expressed as allowed files/path patterns plus justification;
- explicit `footprint_known` verdict;
- observability verdict: `required`, `existing coverage sufficient`, or `not applicable`;
- reviewer-confirmed observability justification;
- when required, important success/failure events and prohibited sensitive fields;
- immutable shape-artifact version/identifier.

Transitions:

- duplicate, unresolved design decision, or unknown footprint → pause;
- oversized → stop at `decomposition_required`; do not pretend this slice can process it;
- buildable → human approves the buildability contract, including file scope, before contract authoring;
- later scope expansion → new human authorization, preserving the old approval in the audit trail.

Explicit exclusions:

- epic and child-issue drafting;
- GitHub creation or mutation;
- in-place issue revision and its approval/write path;
- child queue insertion;
- sophisticated decomposition judgment.

This slice owns:

- **US-3.4 completely:** duplicate work, unresolved decisions, and unknown expected footprint pause.
- **US-6.5 completely:** every shaped issue records the observability enum with reviewer-confirmed justification.
- The classification portion of the US-3 story and module-level “readiness assessment.”
- The shape-side production of US-6.3’s approved-file-scope input, although final readiness enforcement remains owned by the implementation/verification issue.
- The shape-side observability contract needed by **US-6.6 and US-6.7**; implementation and review still own enforcement of those criteria.

It should not claim US-3.1 through US-3.3. Those require revision/decomposition proposals and approved GitHub writes.

A temporary milestone may accept only already-clear, non-oversized issues, but it must pause everything else. Calling that a complete US-3 implementation would be dishonest.

## 3. Concrete failure in the late-shaper plan

Consider an issue: “Add retry handling to outbound payment requests without leaking authorization data.”

A pass-through shaper sends it into contract authoring with no footprint or observability record. Tests are approved. Implementation changes:

- `src/payments/client.py`
- `src/config.py`
- `src/shared/http.py`
- a logging formatter
- the acceptance tests

At readiness, the engine must evaluate “approved file scope.” It has no approved set to compare against. Only three behaviors are possible:

1. **Fail closed:** every run pauses because scope is absent. The alleged end-to-end buildable path does not work.
2. **Skip the check:** the milestone ships a readiness gate that cannot enforce US-6.3.
3. **Ask for approval after seeing the diff:** the implementation was never constrained by approved scope; approval becomes retrospective ratification.

The third option is especially deceptive because the run appears compliant while defeating the story’s purpose:

> “AI implementation constrained by the approved tests...” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:66))

The observability failure is equally concrete. Without a pre-implementation contract naming sensitive fields, the implementer might log request headers during retries. A final reviewer may notice, but US-6.7 requires exclusion of “contract-listed sensitive fields.” No shaped contract means there is no such list to enforce.

A “pass-through shaper” is only real if it validates pre-supplied shape artifacts, records them, and pauses when they are absent. Once it estimates or validates footprint and observability, it is already the proposed minimal shaping slice. If it does neither, it is a test stub—not a compliant product stage.

## 4. Concrete failure in the early-shaper plan

Building the entire shaper early would front-load the least constrained functionality:

- distinguishing oversized work from merely vague work;
- generating independently deliverable children;
- designing GitHub mutation plans;
- linking children and queueing them correctly;
- handling approval, partial GitHub failures, retries, and idempotency.

Those outputs have consumers scattered across the queue, GitHub gateway, audit store, and closeout logic. Before those consumers exist, the implementation is likely to invent fields and transitions that later prove wrong: child identity, parent linkage, mutation idempotency keys, approval snapshots, enqueue semantics, and recovery after only some children were created.

That is where “guessing the output format” is real.

It is not a persuasive objection to the minimal slice. The required semantic output is already substantially pinned:

- footprint must be known or the run pauses;
- file scope must eventually be approved and checked;
- the observability enum is explicitly enumerated;
- justification must be reviewer-confirmed;
- required contracts name success/failure events and sensitive fields.

The architecture states:

> “Every shaped issue records an observability impact...” and “The issue contract names the important success/failure events and sensitive fields that must never be emitted.” ([architecture.md](/Users/matthewdruhl/Projects/IssueForge/docs/architecture.md:85))

Serialization details remain open, but waiting for implementation does not magically determine them. The correct technique is consumer-driven interface design: define the smallest shape artifact jointly with contract-authoring and verification needs, then implement the producer.

Thus “full shaper early” is premature; “no shape contract until late” is evasive.

## 5. Observability is a second dependency

Yes, but it is a reason to build the **observability-producing shape slice** early, not the epic decomposer.

The PRD says:

> “Every shaped issue records `required`, `existing coverage sufficient`, or `not applicable` for observability, with reviewer-confirmed justification.” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:73))

It also assigns the mechanism to a separate module:

> “Observability policy: classifies boundary changes, adds logging requirements to shaped contracts...” ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:139))

Therefore, the issue shaper need not internally calculate the verdict. The observability-policy module can produce it. But lifecycle ownership remains early: its result is added to the shaped contract and then consumed by implementation and review.

Producing the verdict only during implementation review is insufficient. By then:

- the implementer did not receive the required event contract;
- sensitive-field exclusions were unavailable during coding;
- acceptance tests may omit required diagnostic behavior;
- the reviewer is reconstructing requirements after implementation.

Independent implementation review confirms compliance; it should not originate the requirement it is supposed to review.

## 6. Exact first-five-issue build order

1. **Persistent workflow kernel and artifact interfaces**

   Implement run state, stage transitions, pause/resume/park, approvals, immutable artifact references, event persistence, and fake stage adapters. Define the shape-contract, acceptance-contract, and readiness-result interfaces. Own the foundational portions of US-2 and US-10.

2. **Repository registration, isolated workspace, and baseline**

   Implement repository aliases/configuration, fetch/worktree isolation, command execution, and baseline pause behavior. Own US-1 and US-4. This may be built before shaping even though it executes afterward at runtime.

3. **Buildability-contract shaping slice**

   Implement readiness classification, duplicate/design-decision/unknown-footprint pauses, expected-footprint proposal and human approval, observability classification, justification review, event/sensitive-field contract, persistence, and fail-closed handoff. Oversized or vague issues requiring mutation pause for the later full shaper.

   This issue owns US-3.4 and US-6.5, plus the producer side of US-6.3 and the shaped-contract side of US-6.6–6.7. It does not claim US-3.1–3.3.

4. **Acceptance-contract authoring and freeze**

   Implement test authoring, meaningful-red validation, independent review, human approval, and immutable manifest. Consume the approved shape artifact and include its scope and observability references in the frozen run contract. Own US-5.

5. **Implementation, integrity verification, readiness, and green PR**

   Implement bounded repair, contract-integrity checks, diff-versus-approved-scope enforcement, observability enforcement, quality gates, independent code review, and PR creation/waiting. Own US-6 and the delivery portion of US-7.

After these five, add in-place revision, then epic decomposition and child mutation/queueing. That preserves the hard AI surface for when its downstream consumers exist without lying about whether the buildable path has a shaper.

Between the two unsplit alternatives, I would choose early shaping because fail-closed missing artifacts are safer than silently unenforced gates. But the full early shaper wastes risk on decomposition before it is needed. Splitting produces the safer dependency order without front-loading the hardest judgment surface.

RECOMMENDATION: SPLIT-SHAPER
