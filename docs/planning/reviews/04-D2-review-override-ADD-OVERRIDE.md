The premise that the PRD grants no implementation-review override is incomplete. US-6.3 omits it, but the same PRD later states:

> “Independent test and code reviews require fresh sessions and support explicit recorded fallback or human override.”

The architecture reinforces this by listing a human gate whenever:

> “an independent AI review must be overridden.”

So the documents already intend an implementation-review override. The defect is that this policy is not expressed where the enforceable US-6 acceptance criteria define PR readiness.

## 1. Oversight or deliberate asymmetry?

It is an oversight—more precisely, an internal specification inconsistency—not a defensible deliberate asymmetry.

Evidence against deliberate asymmetry:

- The Implementation Decisions explicitly cover both “test and code reviews.”
- The architecture’s Human gates refer generically to “an independent AI review,” not only the test-contract review.
- US-7.2 requires the PR to report “AI review verdicts, and overrides.” Although that could include only test-review overrides, the plural and surrounding policy are consistent with both review stages.
- The architecture has no stated security rationale distinguishing which AI-review verdicts humans may overrule.

Evidence that creates ambiguity:

- US-5.4 explicitly defines an override; US-6.3 does not.
- Lifecycle step 7 explicitly mentions fallback or human override for test review.
- Lifecycle step 11 merely requires “an independent code review.”
- US-6.3 says “no blocking findings,” an absolute condition if read locally.

That is bad requirements drafting. A decomposition author should not silently infer detailed implementation-review override semantics “by symmetry.” But rejecting all such overrides is also inconsistent with the PRD’s explicit cross-cutting Implementation Decision.

The narrow US-6 wording should control implementation until the PRD is clarified, because it is the stage-specific acceptance criterion. The intended product policy, however, is plainly to allow an override.

## 2. Is the deadlock real?

The workflow deadlock is real. Parking does not resolve it.

The concrete path is:

1. Implementation finishes.
2. Acceptance tests, baseline, quality gates, contract integrity, and file scope pass.
3. The required fresh code reviewer reports a blocking finding.
4. Under US-6.2, Codex receives at most two automatic repair attempts for the review failure.
5. Each attempted repair either cannot address the false finding or produces another review cycle with the same false finding.
6. The bounded repair allowance is exhausted.
7. The architecture says IssueForge pauses when “a step fails after bounded repair attempts.”
8. While paused, US-2.3 says the run blocks the sole worker until it is resumed, cancelled, or parked.
9. Resuming without changing the verdict merely returns to the unmet US-6.3 gate. It does not transform the blocking review into “no blocking findings.”
10. Parking under US-2.4 preserves that exact unmet state and releases the worker. It does not make the run PR-ready.
11. US-7.1 prohibits IssueForge from pushing and opening the PR because not all readiness gates passed.

The human is not trapped in an absolute operational sense. They can park or cancel the run and manually push/open/merge the branch. But that abandons the promised IssueForge lifecycle:

- IssueForge does not open the PR.
- The run cannot enter its persisted `waiting-for-merge` state.
- Automatic verified closeout may no longer have its expected state and identifiers.
- The user must route around the workflow and reconcile the stranded run manually.

Thus pause and park are recovery and scheduling mechanisms, not gate escape hatches. Parking prevents one bad review from monopolizing the worker; it does not provide a legitimate path through the gate.

The redundancy argument also fails: US-7.3’s human merge authority applies only after a PR exists, while US-6.3 prevents that PR from being opened. A downstream gate cannot correct an upstream deadlock that makes it unreachable.

## 3. What an override must require

The override must be narrow enough that it cannot waive deterministic evidence.

It should have these properties:

- Only an authenticated human may issue the override. The implementer AI, reviewer AI, workflow engine, and automatic repair process may not.
- It may override only an AI code-review finding or reviewer-process failure. It may not override:
  - red acceptance tests;
  - a red baseline;
  - failed configured quality gates;
  - acceptance-contract mutation;
  - unapproved file scope;
  - missing required observability or sensitive-data protections when deterministically established.
- A fresh independent review must already have occurred. Empty output and non-zero exit remain failures, not passes.
- After implementation repairs, one fresh replacement reviewer session should be attempted before offering the human override. This distinguishes recoverable reviewer/session failure from a conscious acceptance of risk.
- The override must be bound to the exact reviewed head commit and review packet. Any subsequent code or contract change invalidates it and triggers verification and independent review again.
- The human must address each blocking finding individually; a blanket “ignore review” action is unacceptable.
- Permanent audit data must include:
  - human identity;
  - timestamp;
  - repository, branch, and head commit;
  - reviewer provider and session identifiers;
  - original and replacement verdicts;
  - the exact overridden findings;
  - the human’s reason and supporting evidence;
  - remaining acknowledged risk.
- The PR must prominently report every overridden finding and rationale under US-7.2. Override means “allowed to open the PR,” not “erase the finding.”
- The overridden finding must remain visible for merge review. The system cannot silently convert it into an approved reviewer verdict.
- The override does not authorize merge; US-7.3 remains unchanged.

The override need not require a different provider. V1 ships only Codex, and the PRD specifically contemplates a fresh same-provider session. Independence comes from a fresh session and role separation, not necessarily vendor diversity.

The serious failure mode is cultural: once an override exists, teams may use it whenever review is inconvenient. Per-finding rationale, immutable commit binding, permanent audit, and PR disclosure add friction deliberately. They do not eliminate that risk.

## 4. If no override is added

There is no adequate in-engine escape hatch in the present stage-specific criteria.

The only concrete path would be:

1. Exhaust repair attempts.
2. Pause.
3. Human inspects the finding.
4. Park or cancel the run.
5. Manually push the branch and open a PR outside IssueForge.
6. Manually review, merge, close the issue, and clean up.
7. Reconcile or abandon the parked run.

That is a route around the system, not a legitimate IssueForge escape hatch. It also conflicts with the product outcome of opening “one green PR” and performing idempotent closeout.

A no-override design could be made coherent, but it would need a different formal escape hatch—such as a human command that terminates IssueForge ownership and exports an explicit handoff manifest. That would still concede that the normal run cannot complete, and it would weaken end-to-end audit and cleanup guarantees. Neither US-2.3 nor US-2.4 currently defines such a handoff.

The no-override position does have a valid safety advantage: “no blocking findings” stays literal, and humans cannot normalize bypassing an inconvenient reviewer. Its serious failure mode is equally clear: a probabilistic reviewer gains unappealable veto power over a deterministic workflow.

## 5. Recommendation and exact PRD change

Add an explicit, tightly scoped criterion immediately after US-6.3. This does not invent a new product policy; it reconciles US-6 with the PRD’s existing Implementation Decision and architecture Human gates.

Proposed criterion:

> “A blocking implementation-review finding or reviewer execution failure may be overridden only by an authenticated human, after one fresh independent same-provider review attempt. The override applies only to identified AI-review findings at the exact reviewed head commit; it cannot waive contract integrity, acceptance tests, the full baseline, configured quality gates, approved file scope, or deterministic observability and sensitive-data requirements. The permanent audit trail records the human identity, commit, reviewer sessions and verdicts, each overridden finding, supporting rationale, and acknowledged risk. Any subsequent code or contract change invalidates the override. The PR prominently reports the overridden findings and rationale for renewed human consideration before merge.”

Also tighten US-6.3 so its absolute language recognizes that exception:

> “PR readiness requires green acceptance tests, green full baseline, configured quality gates, approved file scope, and an independent code review with no blocking findings except findings explicitly overridden under the following criterion.”

Without that amendment, implementers face contradictory instructions: the stage criterion forbids advancement while the cross-cutting product decision says code-review overrides are supported. Leaving that contradiction unresolved is worse than either deliberate policy.

RECOMMENDATION: ADD-OVERRIDE
