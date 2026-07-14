# Adversarial conclusion

No. JUnit XML can provide a useful common transport, but it cannot determine IssueForge’s “TRUE red/green” contract with zero framework-specific knowledge.

The decisive failure is `<failure>` versus `<error>`. Those tags do not have consistent semantics across the proposed reporters. Even where a reporter distinguishes them, the distinction is usually runner phase—not proof that the failure is the expected missing behavior. JUnit XML also says nothing authoritative about dependency closure or whether the report was honestly produced by the frozen command.

That conflicts directly with IssueForge’s requirements to prove an expected behavioral red, freeze identifiers and transitive test dependencies, and prevent changes to collection/configuration/command ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:59), [architecture.md](/Users/matthewdruhl/Projects/IssueForge/docs/architecture.md:16)).

## 1. What JUnit XML actually delivers

### a. Stable test identity: useful, not universally stable or sufficient

`classname + name` is not a universal canonical identifier:

- **pytest:** Its JUnit writer derives these fields from the pytest node ID. Parameter components are included in `name`, so this is fairly good for ordinary pytest. But generated parameter IDs depend on explicit `ids`, parameter representation, and collection behavior. Pytest itself describes node IDs—not JUnit fields—as its selectable identity, including `[param]` components. The adapter should freeze node IDs directly rather than reverse-engineer them from XML. [Pytest node-ID documentation](https://docs.pytest.org/en/9.0.x/example/markers.html), [parametrization IDs](https://docs.pytest.org/en/stable/example/parametrize.html).

- **Jest/jest-junit:** The reporter constructs `classname` from `ancestorTitles` and `name` from the test title. The test file is optional. Consequently, two files containing the same `describe` and test titles can produce identical pairs. Templates can also redefine both fields. Jest’s `test.each` titles depend on interpolated values or row indexes, so changing data formatting or order can change identity. [jest-junit source](https://raw.githubusercontent.com/jest-community/jest-junit/master/utils/buildJsonResults.js), [Jest parameterized-test API](https://jestjs.io/docs/api).

- **Go/gotestsum:** The normal pair is package plus test/subtest name. Go subtests use slash-separated names and append sequence numbers to duplicate sibling names. Those suffixes may change if generation order changes. gotestsum also allows `classname` to be formatted as full, relative, or short package name, and short names can collide. [Go testing package](https://pkg.go.dev/testing), [gotestsum documentation](https://pkg.go.dev/gotest.tools/gotestsum).

- **Rust:** cargo2junit receives libtest’s emitted names; nextest normally uses test-binary identity plus test name. Custom harnesses and generated tests define their own names. There is no universal source-level identity. Nextest says each test binary becomes a suite and each test a testcase, which means the suite hierarchy is part of the identity; `classname + name` alone is an unnecessary lossy projection. [Nextest JUnit documentation](https://nexte.st/docs/machine-readable/junit/).

- **Retries:** A reporter may collapse attempts into the final testcase, emit duplicate cases, or use nonstandard extensions such as `<flakyFailure>` and `<rerunFailure>`. A plain final status does not identify which attempt is authoritative.

- **Sharding:** XML describes the tests in that report, not the intended global set. One valid green shard is not proof that all approved tests ran. The engine must know the expected shard plan and verify the union, duplicates, and completion of every shard.

- **Dynamic generation:** XML records what was generated this time. It cannot show whether the generator silently omitted cases that existed at approval.

Therefore, XML can carry observed identities, but canonicalization, collision rules, selection, and completeness remain framework/reporter-specific.

### b. `<failure>` versus `<error>`: not portable enough

This is where the zero-adapter proposal breaks.

#### pytest: a real but limited phase distinction

Pytest’s own JUnit implementation is comparatively strong:

- test-call failure → `<failure>`
- collection failure → `<error message="collection failure">`
- setup or teardown failure → `<error>`
- internal pytest error → `<error>`
- skip/xfail → `<skipped>`

That behavior is explicit in pytest’s source. [Pytest JUnit writer](https://raw.githubusercontent.com/pytest-dev/pytest/main/src/_pytest/junitxml.py).

But even pytest only establishes phase:

- An arbitrary exception, lazy import error, network failure, or fixture-like setup performed inside the test body is a call-phase `<failure>`.
- An assertion in a fixture setup is `<error>`, even if it is intentionally checking the missing behavior.
- A plugin can alter reports or replace the JUnit plugin.
- User-controlled test code can alter XML attributes in legacy modes.

Thus, `<failure>` is evidence that pytest reached the call phase, not proof of the recorded behavioral reason. IssueForge still needs to compare normalized failure evidence or have a reviewer judge causality, as the PRD requires at lines 64–69.

#### gotestsum: no testcase-level error representation

gotestsum’s current JUnit testcase structure has fields for skip and failure, but no `<error>` field. Its generator:

- represents failed Go tests with `<failure>`;
- represents a failing `TestMain` as a synthetic testcase with `<failure>`;
- records execution/package errors only in the top-level `errors` count.

This is concrete source behavior, not merely a schema concern. [gotestsum JUnit implementation](https://raw.githubusercontent.com/gotestyourself/gotestsum/main/internal/junitxml/report.go).

Go’s test protocol itself does not distinguish assertion failure from arbitrary test panic, setup failure implemented inside a test, or many forms of harness failure. All can become a failed test event. gotestsum cannot reconstruct a phase distinction absent from that event stream.

So the proposed rule “approved ID is present and has `<failure>`” is not enough for meaningful red in Go.

#### jest-junit: distinction exists, but hook handling is inconsistent

Current jest-junit does emit `<error>` for synthetic cases whose Jest status is `error`, including a suite that produced no test results when suite-error reporting is enabled.

However, the same source synthesizes a testcase for `suite.testExecError`—explicitly noting hooks such as `afterAll`—and assigns it status `failed`, causing `<failure>`. Reporter configuration can also disable suite-error reporting. [jest-junit source](https://raw.githubusercontent.com/jest-community/jest-junit/master/utils/buildJsonResults.js).

Therefore:

- module-load failures may be `<error>`;
- some hook/execution failures become `<failure>`;
- configuration can omit suite errors;
- custom Jest runners/reporters may choose different mappings.

That is not a reliable universal phase contract.

#### cargo2junit and nextest: runtime failure is not assertion provenance

cargo2junit converts libtest JSON from stdin. The underlying libtest “failed” result does not say whether the panic came from an assertion, arbitrary panic, failed initialization inside the test, abort, or another runtime cause. The converter cannot manufacture that distinction. Its documented invocation is a pipeline, which also creates a separate integrity problem: without correct pipeline-status handling, cargo can fail while the converter exits successfully and writes an empty-looking report. [cargo2junit documentation](https://docs.rs/crate/cargo2junit/latest).

Nextest has richer retry/status extensions, but configuration can make a flaky-fail test appear successful in JUnit even though the runner treats it as failed. That alone proves that XML status is a configurable presentation, not authoritative execution truth. [Nextest retry representation](https://nexte.st/docs/machine-readable/junit/).

#### Result

JUnit has no official single schema or semantic specification; it is a family of formats. [Google’s XUnit XML description](https://google.github.io/rich-test-results/xunitxml).

The same `<failure>` tag means:

- call-phase failure in pytest;
- essentially any failed Go test in gotestsum;
- ordinary Jest assertion failure plus some hook/execution failures;
- a failed Rust test process or panic in common Rust reporters.

The meaningful-red claim therefore collapses if the core treats the tag as a universal behavioral-phase fact.

### c. Zero-collected detection: achievable only as defensive artifact validation

When a complete report truthfully contains `tests="0"`, the core can reject it. Checking for approved-ID presence is stronger than trusting the aggregate count.

But the engine must distinguish several cases:

- valid complete report with zero tests;
- no report because the runner crashed before reporter initialization;
- zero-byte or truncated XML;
- stale report from a previous invocation;
- report generated by only one shard;
- valid report whose aggregate counts disagree with testcase children;
- reporter configured not to include suite-load errors;
- report written before forced process termination.

“No report” and “zero tests” are operationally distinguishable only if IssueForge:

1. creates a unique artifact directory outside the repository for every invocation;
2. ensures no report exists before starting;
3. never accepts a configured static path that can retain stale output;
4. waits for process termination and file closure;
5. requires well-formed XML and reconciles all nested counts;
6. requires exact expected-ID presence, with explicit duplicate rules;
7. combines exit status, timeout/signal status, and report contents;
8. rejects any missing shard or retry ambiguity.

Both no-report and zero-tests should be `BROKEN`, but with different diagnostics. A preexisting static `out.xml` destroys that distinction.

## 2. Report-forgery attacks

A JUnit report written under candidate-tree control is not independent evidence.

An implementer can:

- change `.issueforge.toml` or a package-script command;
- change a wrapper script while preserving the apparent top-level argv;
- replace or reconfigure the reporter;
- configure Jest templates to create approved-looking identities;
- disable Jest suite-error reporting;
- directly write the expected XML without running tests;
- leave a stale report at the configured path;
- arrange for a post-test command to rewrite it;
- use a custom pytest plugin or Jest test runner;
- manipulate reporter dependencies through the lockfile or dependency resolution;
- use retries so a failed attempt is collapsed into a green final result;
- run one green shard and omit the others;
- configure nextest to render flaky failures as successes;
- use `--forceExit` to suppress clean shutdown and teardown evidence. Jest itself calls this an escape hatch. [Jest CLI documentation](https://jestjs.io/docs/cli);
- make implementation code behave differently under a test environment, a residual risk the PRD already concedes ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:156)).

At minimum, the engine must:

- take the approved command from the frozen IssueForge manifest, not candidate HEAD;
- hash and freeze every invoked in-repository wrapper/configuration file;
- use an engine-selected, version-pinned reporter installed outside candidate dependency control;
- generate a fresh output location itself;
- reject candidate-specified postprocessors;
- prohibit or explicitly model retries, sharding, bail, force-exit, pass-with-no-tests, and custom reporters;
- validate reporter and runner versions/capabilities before approval and again at green;
- require the same normalized command, configuration, environment policy, and selection at red and green;
- capture exit/signal/timeout independently;
- make the artifact directory inaccessible except through the trusted reporter where feasible;
- run in a separately provisioned environment, as the PRD already requires.

Even that does not fully solve malicious candidate code. Test execution necessarily runs candidate-controlled code. If that code can write to the report location, signal the reporter, monkeypatch an in-process reporter, or detect the test environment, the artifact is forgeable. Stronger independence requires OS isolation plus an out-of-process trusted event consumer, and even then the consumer only attests to received events—not semantic correctness.

A nonce in an environment variable is not authentication: candidate code can read it.

## 3. Information JUnit XML cannot recover

JUnit XML cannot establish:

- the transitive dependency closure of test modules, fixtures, plugins, setup files, custom runners, build scripts, generated inputs, or configuration;
- which repository files can neutralize test discovery or behavior;
- whether the frozen command was actually the command executed;
- executable, runner, reporter, plugin, compiler, or dependency versions;
- effective working directory, environment, configuration, selection filters, or test-runner defaults;
- whether a wrapper inserted extra arguments;
- whether all shards ran;
- whether retries occurred unless the reporter preserves them;
- whether bail or early termination prevented expected cases;
- whether aggregate counts represent collected tests, executed tests, attempts, or synthetic error cases;
- whether a test was dynamically omitted;
- whether the test body reached the intended assertion;
- whether a passing test made any assertion;
- whether a failure matched the human-approved behavioral reason;
- whether a test is tautological or merely restates the implementation;
- whether candidate code special-cased the test identity or environment;
- whether a skip was legitimate;
- why a reporter chose `<failure>` rather than `<error>`;
- compile failure versus no tests when compilation produced no test events;
- whether the tested binary corresponds to candidate HEAD rather than a cache or stale build;
- whether external fixtures, services, databases, clocks, locale, credentials, network, filesystem, or environment variables neutralized the test;
- whether the reporter omitted unsupported/nonstandard events;
- whether the XML was produced by the runner, a wrapper, or direct file-writing code;
- whether output is complete rather than a well-formed prefix or selectively reconstructed report.

This is exactly why US-5 requires adapter-discovered dependencies rather than a configured path list ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:68)). Replacing that requirement changes the product contract; it does not satisfy it by another implementation.

## 4. Whole-test-tree freezing

The over-broad boundary is sound for one narrow statement:

> No tracked file under the declared, frozen paths changed.

It is simpler and safer than an incomplete import graph inside that boundary. It may be a good defense-in-depth rule.

It is not a complete integrity boundary.

### What it catches

- direct edits to frozen test files;
- test fixtures and snapshots located under frozen roots;
- declared runner configuration changes;
- obvious removal or renaming of tests under those roots.

### What it misses inside the repository

- helpers outside the conventional test tree;
- shared production utilities used by fixtures;
- root package manifests and lockfiles;
- build scripts, code generators, compiler configuration, and workspace manifests;
- pytest plugins or Jest setup modules outside the declared paths;
- npm scripts and wrapper executables;
- custom reporters;
- generated fixture sources stored elsewhere;
- symlink targets, submodules, or LFS-backed content;
- test-discovery rules in undeclared configuration;
- candidate production code that detects tests and returns approved answers.

The PRD explicitly says a user-supplied boundary may add protection but may never shrink the discovered closure. The proposal reverses that rule by making configuration authoritative.

### What it misses outside the repository

- runner/reporter upgrades;
- globally installed plugins;
- environment variables and credentials;
- compiler/toolchain changes;
- caches and stale binaries;
- services, databases, filesystem state, locale, time, randomness, and network behavior;
- compromised PATH resolution.

### Friction

Freezing the entire tree will force amendments for legitimate snapshot updates, golden files, shared fixtures, generated test assets, test-support refactors, and configuration changes caused by the new behavior. In repositories where implementation and test support evolve together, amendment becomes routine rather than exceptional.

The two documents supplied here do not themselves contain evidence for the stronger sociological claim that users will “route around” such gates. They do establish that amendments require renewed human authorization and preserved provenance, and that approved scope changes are intended as explicit gates ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:46)). Whether the friction is tolerable must be tested empirically; it cannot be assumed.

## 5. Bottom line

The zero-adapter core can reliably determine only:

- whether a fresh report exists;
- whether it is complete, well-formed XML within a supported syntactic profile;
- whether declared aggregate counts reconcile with testcase elements;
- which identity strings and status tags the reporter emitted;
- whether the approved identity strings are present;
- whether any are skipped, duplicated, or absent;
- whether the process exited, timed out, or was signaled;
- whether an expected set across known shards is complete.

It can call these facts “report-valid pass/fail,” not “TRUE behavioral red/green.”

Framework-specific code is irreducible for:

- canonical test identity and selection;
- collision handling for parameters, subtests, dynamic generation, retries, and shards;
- reporter capability/version validation;
- phase normalization from native runner events;
- distinguishing collection/build/setup/test/teardown where the runner exposes it;
- proving exact expected selection and detecting deselection;
- command construction that cannot silently enable dangerous modes;
- discovery of configuration, fixtures, plugins, wrappers, and transitive dependencies;
- interpreting native failure evidence against the approved red reason.

This is more accurately **per-framework/per-reporter** code than per-language code. Pytest and unittest share Python but have different semantics; Jest reporters can differ within JavaScript; cargo2junit and nextest differ within Rust.

The smallest defensible surface is a capability module that supplies:

```text
probe(toolchain) -> supported capabilities and pinned versions
canonical_collect(invocation) -> canonical IDs and selection metadata
classify(native events/report) -> phase-aware normalized outcomes
discover_contract_dependencies(collection) -> protected closure
validate_invocation(command/config) -> frozen, safe execution plan
```

The common core can own subprocess isolation, fresh artifacts, XML parsing, count reconciliation, manifests, approvals, and red/green state transitions. But once the module must perform the operations above, it is still a verification adapter—just a deliberately thin one.

## 6. Recommendation

Ship **(C): a universal report-ingestion core plus a thin, mandatory capability module for every supported framework**.

For v1, only enable pytest. This preserves the present registration behavior while avoiding pointless duplication of XML parsing and artifact hygiene. Later adapters can reuse the same core where their reporters meet the required capabilities.

Do not call the module “optional” for contract-grade verification. If its capability probe cannot prove canonical identity, selection completeness, trustworthy phase information, and dependency protection, registration must fail as the current PRD requires ([prd-v1.md](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:187)).

The residual risk accepted is semantic deception that no runner adapter can eliminate completely: tautological tests, implementation code that recognizes the test environment, malicious candidate code interacting with its execution environment, and a human-approved test that does not actually specify the intended behavior. The design already assigns part of that residual risk to independent review and hermetic execution; JUnit XML does not reduce it.

VERDICT: HYBRID
