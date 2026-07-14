# Independent review

The decomposition is not ready to file. The coverage matrix overstates coverage, the contract-integrity design still has bypasses, several dependencies run backward, and the largest issues are multi-session epics rather than focused vertical slices.

## BLOCKING FINDINGS

1. **US-8.2 is falsely marked covered: parent-epic closeout is missing.**  
   The matrix assigns US-8.2 to #13, but #13 never requires updating the run issue’s parent epic. The only occurrence acknowledges that deferring #16 would remove the requirement. Closing/commenting on the exact issue is only part of US-8.2. Add an idempotent, repository-qualified parent-epic update to #13, with behavior for no parent, failed reads, and repeated closeout.

2. **US-9.2 is explicitly weakened.**  
   The PRD requires the TUI to display logs, diffs, approvals, failures, PR status, cleanup warnings, queue position, and stage. #18 says log and diff rendering “may ship thin” and makes only queue, stage, and approval the v1 bar. That is a silent v1 deferral despite the matrix claiming complete coverage. Either implement all eight views or amend the PRD.

3. **The supposedly repository-agnostic product is decomposed as pytest/Python-only.**  
   #6 treats pytest exit 5 and an in-process pytest plugin as universal baseline semantics. #8 defines meaningful red entirely through pytest phases. #10 ports Python AST integrity machinery. #11 requires Python-oriented mutation operators. Yet the PRD supports any explicitly registered GitHub clone and permits arbitrary argv baseline/acceptance commands. The provenance itself warns that Python AST checking is only a framework adapter.  
   The decomposition needs a framework-neutral result/adapter contract and an explicit v1 support policy. A generic baseline command cannot always have “collected node IDs,” and an npm/build/composite baseline cannot be interpreted using pytest exit codes.

4. **Meaningful-red semantics are assigned to a deterministic predicate that cannot prove them.**  
   The phase-based discriminator is correct: collection/setup/environment failures must not count, and valid missing-behavior failures need not be `AssertionError`. But call-phase failure proves only that the test executed and failed; it does not prove it failed for the named expected behavioral reason. #8 nevertheless says the AI “never judges whether the red is meaningful.” #9 reviews coverage/validity but never explicitly validates observed red evidence against the expected behavioral reason.  
   The correct boundary is:

   - deterministic: collection identity, execution phase, baseline health, SHA binding, zero-collection/XPASS rejection;
   - AI reviewer and ultimately human approval: semantic correspondence between the observed failure and the missing behavior.

5. **The “absolute” contract-integrity gate is not actually complete.**  
   #10 says the manifest freezes hashes for transitive imports, every applicable `conftest.py`, configuration, and command arrays. Its executable checks, however, only say:

   - reject changes under configured `contract_paths`;
   - reproduce node-ID collection;
   - preserve ancestry;
   - protect `.issueforge.toml`;
   - run the AST backstop.

   It never explicitly requires recomputing and comparing every frozen dependency hash at the candidate head, nor does it state that automatically discovered fixtures/config/import-closure files are added to the protected set. A changed fixture or imported helper can preserve the exact node-ID set while changing or neutralizing test semantics. User-configured globs cannot be trusted to enumerate the boundary the engine claims to discover.  
   Add an exact manifest-to-head comparison for every frozen dependency and define symlink, rename, deletion, generated-file, command/environment, and import-closure behavior. Recollection is necessary but does not subsume semantic dependency integrity.

6. **Observability is ordered after the stages that must enforce it.**  
   #15 is blocked by #11, but US-6.5–6.7 are inputs to the shaped contract and PR-readiness review performed by #11. #14 can shape an issue without #15 even though every shaped issue must record an observability disposition. #12 must report logging in its PR body but does not depend on #15.  
   This is a backward dependency. The observability contract must exist before shaping/contract approval and be consumed by #11’s readiness gate. #12 then needs a hard dependency on the resulting recorded verdict.

7. **The decomposition contains several horizontal layers and oversized issues.**  
   The claimed “first vertical slice” requires five separate issues (#2→#3→#4→#5→#6), which concedes that those issues are not individual vertical tracer bullets. In particular:

   - #3 is a pure storage/locking layer with no independently demoable workflow outcome.
   - #7 is a provider abstraction dressed as `provider check`.
   - #8 combines authoring orchestration, source discovery, contract parsing, per-test execution reporting, meaningful-red evidence, SHA binding, and engine integration.
   - #10 combines manifests, dependency discovery, recollection, protected paths, a 928-line AST port, amendments, authorization, and engine transitions.
   - #11 combines implementation dispatch, git ownership, retries, mutation testing, quality gates, origin verification, and independent review.
   - #13 combines merge proof, squash delivery, retargeting, local and remote deletion, worktree safety, issue mutation, health verification, and idempotence.
   - #16 combines AI decomposition, mutation planning, partial-write recovery, epic linking, and queue admission.

   #8, #10, #11, #13, and #16 are not credible one-focused-session issues. Split them along end-to-end outcomes while ensuring no temporarily unsafe approval or PR path can exist.

8. **The MARVIN source-audit mapping is procedural prose, not complete acceptance coverage.**  
   US-11.1–11.4 are mapped to “per-issue DoD,” but most issue bodies contain a curated “Sources to review” list rather than a completed inventory. They do not require a checked artifact proving that all corresponding skills, scripts, tests, and failure-driven updates were inspected. Nor is every inventoried behavior individually classified with a reason as deterministic policy, AI judgment, human approval, or MARVIN-specific discard.  
   “Preserve/Replace/Discard” headings are present, but that does not establish completeness. Add a standard, testable source-audit artifact per implementation issue, with source identifiers, behavior-level classification, extraction decision, test provenance, and a completeness check against the stage source map.

9. **The no-MARVIN proof can finish before later implementation reintroduces a dependency or write-back.**  
   #19 is blocked only by #13. It can therefore pass before #14–#18 are implemented. Those later issues can introduce a MARVIN path, runtime read, or consumer-specific write after the boundary proof is closed. The four-string grep in #19 is also not exhaustive.  
   Make the boundary test a permanent suite/CI invariant and block its completion on every v1 implementation issue. Run the lifecycle with the MARVIN checkout absent and a filesystem write monitor; also scan imports, executable arguments, defaults, and paths generically rather than checking four known strings.

10. **Required execution semantics are left unresolved even though the PRD resolved them.**  
    #11 calls retry accounting a “blocking design question,” although US-6.2 unambiguously says at most two automatic repair attempts. MARVIN’s three nested counters are provenance, not authority to reopen the PRD. #9 similarly leaves reviewer fix-session mechanics unresolved, and #12 treats read-only merge watching as an open conflict despite US-7.4 explicitly requiring watch mode. These issues are not implementation-ready.  
    Follow the PRD unless it is amended: define one v1 automatic-repair budget of two, specify which failure transitions consume it, use fresh/resumed reviewer sessions as a separate review protocol only if they do not create extra implementation repairs, and permit read-only merge observation while keeping cleanup mutations separately gated.

11. **Closeout preserves contradictory MARVIN behavior.**  
    #13 correctly says not to port MARVIN’s blanket halt on a red post-merge suite because health and cleanup safety are independent. Its own failure section then says “Every anomaly HALTS,” recreating the same coupling. Post-merge health failure must remain loud/nonzero while independently safe close/comment/cleanup operations proceed; failed delivery verification remains the global destructive stop. This distinction needs explicit stage-result and exit-status acceptance tests.

12. **The delete-safety predicate is underspecified at the exact point where determinism is mandatory.**  
    #13 alternates between “content reachability,” “delivered sha/content,” and `merge-base` reachability. For squash merges, these are not interchangeable unless the exact GitHub merge commit and recorded PR head are bound into the predicate. Define precisely which authoritative GitHub fields and SHAs are checked, how a squash merge is associated with the exact run PR/head, and what happens when those facts are missing or disagree. “Content, not ancestry” is a policy slogan, not yet an executable predicate.

## Coverage-matrix audit

The raw matrix lists each of the 51 identifiers once, but that does not mean 51 criteria are substantively covered.

- **Unmapped in substance:** US-8.2 parent-epic update.
- **Silently weakened/deferred:** US-9.2 logs and diffs.
- **Left unresolved:** US-6.2 repair limit; operational portions of US-5.4/US-7.4.
- **Insufficiently mapped:** US-11.1–11.4 are assigned to a non-enforced DoD rather than completed, auditable behavior inventories.
- **Double-mapped or multiply owned despite the matrix showing one owner:**
  - US-7.4 is owned by #5 and reimplemented in #12.
  - US-9.3 is owned by #5 and reasserted in #18.
  - US-6.3 is owned by #11 while #12 also defines the readiness-to-open behavior.
  - US-10.1 is owned by #3 while #17 again specifies preservation of permanent artifacts.
- **Misplaced:** #12 contains “close only formal `closingIssuesReferences`,” a closeout mutation belonging to #13, while #12 otherwise ends at `waiting-for-merge`.

Integration assertions can legitimately repeat a criterion, but the decomposition must identify one owning issue and distinguish downstream integration tests from duplicate implementation responsibility.

## Dependency defects

Missing hard edges:

- #14 must consume #15’s observability policy, not precede it.
- #11 must depend on the approved observability contract produced during shaping.
- #12 must depend on #15 for its required PR-body observability report.
- #18 needs the event/artifact producers for logs, diffs, PR status, and cleanup warnings if it is to satisfy the complete US-9.2.
- #19 must follow all v1 implementation issues or remain an always-running invariant.

False or questionable edges:

- #3’s filesystem store does not inherently require the subprocess/config implementation in #2.
- #15 being blocked by #11 is backward.
- The draft’s phase order puts shaping after implementation machinery, while the runtime lifecycle requires shaping before workspace creation and test authoring. Implementation order may differ from runtime order, but the final transition graph and integration tests must make the runtime order impossible to bypass.

## Additional findings

- The “no new dependencies” declaration is premature product design, not derived from the PRD. It should not constrain a repository-agnostic test-adapter design before that design exists.
- Mandatory blocking mutation testing is v1 work smuggled in from prior-art analysis. The PRD requires meaningful red, integrity, green verification, and review—not universal mutation testing. In its current form it greatly enlarges #11 and is undefined for non-Python targets.
- G12’s invariant lens is useful, but it is another unapproved scope addition. It should either become an explicit PRD amendment or stay out of the v1 acceptance graph.
- #5 imports MARVIN closeout anomaly names such as `red-main`, `no-test-command`, and retarget/worktree failures into an early stub queue engine. Several are later declared impossible or stage-specific. Model typed stage failures, not a global copied string catalogue.
- #3 specifies atomic manifest replacement but does not specify recovery from a torn final JSONL event after process death. That is necessary for its claimed crash-safe event replay.
- #16’s “idempotent on child title” is unsafe: titles are not durable idempotency keys and may collide or be edited. Persist mutation-operation IDs and created issue identities.
- #15 says sensitive-field exclusion is deterministic but defines no verifier for target-project logging. The contract can be deterministic; compliance still needs concrete tests and independent review evidence.
- #19’s allowance for any write anywhere in “the target repo” is too broad for proving the normal checkout is untouched. Authorized worktree/branch paths should be distinguished from the registered normal checkout.

The draft shows strong awareness of MARVIN’s failure history, and its phase-based red/error distinction is correct. But it repeatedly turns good safety commentary into claims of completeness without defining the final executable predicate, and the coverage matrix conceals at least two direct PRD misses.

VERDICT: REVISE
