## Review

The proposal is broken as an integrity core.

It establishes a counterfactual dependency:

> Some content in `impl_footprint` is necessary for this command to transition from the recorded red state to green.

That is weaker than IssueForge’s required property:

> The approved behavior was delivered.

A dishonest implementation can make the tests pass only when its implementation sentinel is present. Removing the footprint then recreates the original red exactly.

This conflicts directly with the PRD’s promise that green means the approved behavior was delivered ([prd-v1.md:64](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:64)), rather than merely proving a causal relationship between an approved path and the test result.

## 1. Working bypass

Here is a minimal one-file attack entirely inside the approved implementation footprint. It requires no changes to tests, fixtures, configuration, or collection.

At `contract_sha`:

```python
# issueforge_demo/payments.py

def authorize_payment(amount: int) -> bool:
    return False
```

Approved test:

```python
# tests/test_payments.py

from issueforge_demo.payments import authorize_payment

def test_positive_payment_is_authorized():
    assert authorize_payment(100) is True
```

Recorded red evidence is an ordinary assertion failure:

```text
E assert False is True
```

Suppose `impl_footprint` contains `issueforge_demo/payments.py`.

At `HEAD`, the dishonest implementation is:

```python
# issueforge_demo/payments.py

import os

def authorize_payment(amount: int) -> bool:
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True

    # The real behavior remains unimplemented.
    return False
```

Results:

- Step 4 restores `payments.py` to its exact `contract_sha` content.
- The approved test produces the exact original assertion failure.
- Step 4 passes the discrimination requirement.
- Step 5 runs against `HEAD`.
- Pytest sets `PYTEST_CURRENT_TEST` while executing the test.
- The suite is green.
- In production, `authorize_payment(100)` still returns `False`.

This satisfies both gates without delivering the approved behavior.

It is not peculiar to pytest. Analogous attacks can inspect:

- process arguments;
- parent process names;
- CI-specific environment variables;
- stack frames;
- test-only build flags;
- Go test binaries ending in `.test`;
- Jest/Vitest worker variables;
- whether known test modules are loaded;
- a file, socket, or environment feature present during verification.

The discrimination run proves that the changed production file affects the tests. It cannot prove that the production behavior exists outside the verification context.

### The fixture bypass can also be made conditional

The proposal specifically claims an outside-footprint neutralizer would remain active during step 4 and therefore make step 4 green. That assumes neutralizers are unconditional.

For example, `HEAD` can add this harmless-looking sentinel inside the footprint:

```python
# app.py

ISSUEFORGE_READY = True

def feature():
    return False
```

And change an editable helper outside the footprint:

```python
# tests/helpers.py

import pytest

@pytest.fixture(autouse=True)
def implementation_compatibility(monkeypatch):
    import app

    if getattr(app, "ISSUEFORGE_READY", False):
        monkeypatch.setattr(app, "feature", lambda: True)
```

With `HEAD`, the fixture neutralizes the contract. With the implementation reverted, the sentinel disappears and the fixture deliberately stops neutralizing it. The original red returns.

Thus the proposal’s statement that neutralized artifacts “pass without the implementation” is false. A neutralizer can depend on the presence of the implementation.

If the approved-scope gate forbids editing `tests/helpers.py`, this particular variant is blocked—but the implementation-only attack above is not.

### Evidence can be forged too

If an attacker can change the command’s launcher, configuration, or an executable reached through `PATH`, it can:

- detect whether the implementation sentinel is present;
- exit zero when present;
- otherwise print the stored red evidence and exit nonzero.

Even byte-for-byte evidence comparison does not help. Output from a process controlled by the candidate tree is not an independent attestation.

## 2. False positives against honest implementations

Under an idealized model—complete footprint, deterministic command, hermetic environment, and exact restoration of every relevant input—an honest implementation should recreate the original red. The actual proposal supplies none of those conditions.

### New files

“Revert files to their `contract_sha` content” is undefined for paths that did not exist at `contract_sha`.

An honest implementation may add:

```text
src/client.ts
src/generated/types.ts
migrations/004_add_status.sql
```

Step 4 must delete those files, including tracked additions and renames. A naïve command such as:

```console
git checkout contract_sha -- src/client.ts
```

fails because the path does not exist in the old tree. If the file remains, the acceptance test may remain green and the honest implementation is blocked.

The manifest needs explicit path operations—add, modify, delete, rename—not just “restore content.”

### Installed dependencies are not Git paths

Consider an honest TypeScript implementation requiring a dependency upgrade:

```text
package.json
package-lock.json
src/feature.ts
```

Even if all three are in the footprint and their tracked content is restored, `node_modules` may still contain the `HEAD` dependency graph. The frozen `npm test` command does not necessarily reinstall dependencies.

The reverted source can then:

- stay green because the newer runtime supplies the behavior;
- fail during import rather than with the approved assertion;
- produce different stack traces or error formatting.

The same applies to:

- Python virtual environments and editable installs;
- Rust build artifacts under `target/`;
- Go build/test caches;
- generated sources;
- native extensions;
- databases and applied migrations;
- daemon state;
- compiler caches;
- files created by prior verification commands.

A scratch Git worktree is not a hermetic execution environment.

### Legitimate test-support changes

An honest implementation can require shared support changes. For example, introducing a required tenant identifier may require a shared object factory to populate it. At `contract_sha`, the factory works with the old model and the new acceptance test reaches its behavioral assertion. At `HEAD`, both the model and factory must change.

There are three possible classifications, all problematic:

1. The helper is a `contract_path`: the absolute gate forbids the honest change.
2. The helper is outside the footprint: the readiness scope gate forbids it, or step 4 runs the old implementation with the new helper and gets a different failure.
3. The helper is added to the footprint: it can be changed, but the supposedly preapproved footprint has now grown.

The workflow needs an authorized contract/scope revision. The current architecture already names “authorized revisions” as acceptance-contract responsibility ([prd-v1.md:137](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:137)); the proposal does not define how those revisions alter `contract_sha`, evidence, or footprint history.

### Collection failures

The PRD explicitly requires approved tests to execute without import, fixture, configuration, or environment errors ([prd-v1.md:58](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:58)). Step 4 can violate that even when the historical contract did not.

For example:

- `contract_sha` used dependency version A and produced an assertion failure.
- The honest implementation requires version B.
- The lockfile is restored, but the environment remains installed with B.
- Restored production code now raises `ImportError` at module import.
- The acceptance test never executes.

If step 4 requires exact behavioral evidence, the honest change is blocked. If it accepts any red exit, the integrity gate accepts an infrastructure failure expressly forbidden by US-5.

### Generated and migrated state

Suppose an honest implementation adds a schema migration. Step 5 applies it to a test database. A later step 4 source restoration does not reverse the database schema. Reversing execution order merely creates the opposite contamination risk.

Each counterfactual run needs an independently provisioned environment, not two Git states sharing mutable services and caches.

### Footprint growth

Shaping produces an expected footprint, not an omniscient one. Discovering that a clean implementation needs another module is routine, not exceptional misconduct.

A strict frozen footprint creates false positives. A mutable footprint without a new human gate lets the implementer decide what gets removed during discrimination. Either way, footprint revision is a security-sensitive operation requiring explicit authorization and a new manifest version.

## 3. The footprint dependency is underspecified

The proposal overstates what the existing documents promise.

The PRD says an unknown expected footprint pauses shaping ([prd-v1.md:41](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:41)), and readiness requires “approved file scope” ([prd-v1.md:71](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:71)). It does not say:

- the human approves an exact path set before contract authoring;
- whether new files, glob patterns, renames, or deletions are represented;
- who can revise that set;
- whether contract authoring may alter it;
- whether implementation may request expansion;
- whether expansion invalidates the prior contract approval.

The architecture’s human-gate list includes shaping changes and test approval, but no explicit footprint approval or expansion gate ([architecture.md:23](/Users/matthewdruhl/Projects/IssueForge/docs/architecture.md:23)).

### If scope enforcement is strict

If every changed path must be in a human-frozen footprint, then outside-footprint fixture poisoning is already rejected by a simple Git diff allowlist. The discrimination check is still not wholly redundant:

- it can detect accidental inclusion of support/config paths in an overly broad footprint;
- it checks that the approved implementation paths remain necessary for green;
- it provides a useful mutation-style signal.

But it does not stop malicious behavior implemented inside approved production paths, including the test-environment bypass above.

### If scope enforcement is loose

Then the implementer can change helpers, launchers, dependency configuration, generated artifacts, and environment setup. Conditional neutralization defeats discrimination.

The design therefore faces a hard boundary:

- Strict scope makes the footprint gate the primary defense and causes legitimate expansion pauses.
- Loose scope makes discrimination forgeable.
- Neither version proves semantic delivery.

## 4. “Reproduces the recorded red evidence” is not well-defined

There is no framework-neutral comparison that is both strong and practical.

### Exact bytes are too strict

Test output commonly includes unstable material:

- durations;
- temporary paths;
- random seeds;
- ordering;
- stack locations;
- dependency versions;
- terminal formatting;
- compiler diagnostics;
- process IDs;
- snapshot formatting.

Exact comparison will block honest changes and even unchanged reruns.

### Exit code alone is too weak

Any of these would satisfy “red”:

- zero tests collected;
- compiler failure;
- missing dependency;
- timeout;
- fixture crash;
- segmentation fault;
- malformed configuration;
- intentional `exit 1`;
- an unrelated preexisting test failure.

That directly contradicts the requirement for an expected behavioral red rather than merely any failure ([prd-v1.md:150](/Users/matthewdruhl/Projects/IssueForge/docs/prd-v1.md:150)).

### Text normalization remains weak

Matching a test name and error substring is framework-specific and forgeable. A launcher can print them. Matching structured failure type, phase, and test identity requires framework adapters—the machinery the proposal claims to eliminate.

There is no generic middle ground hidden inside stdout. Meaningful-red classification is semantic.

## 5. The D1 claim is false

Git plus argv plus an exit code can implement a portable counterfactual smoke test. It cannot implement the PRD’s integrity semantics.

For Go, Rust, or TypeScript, IssueForge still needs to determine:

- whether tests were discovered;
- whether the approved tests executed;
- whether failure occurred during compilation, setup, collection, or assertion;
- whether the same behavioral condition failed;
- whether packages or test files disappeared;
- whether the command silently skipped tests;
- whether cached output was reused;
- how dependencies and generated artifacts are provisioned;
- how evidence is normalized.

Examples:

- `go test ./...` can fail because a package does not compile.
- Cargo can fail while resolving dependencies before running a test.
- Jest can be configured to pass with no tests.
- An npm script can run something other than the approved runner after `package.json` changes.
- A command can exit nonzero for an unrelated lint or setup phase.

The architecture already says lifecycle step 6 must distinguish an expected missing-behavior failure from infrastructure failure ([architecture.md:14](/Users/matthewdruhl/Projects/IssueForge/docs/architecture.md:14)). An exit code cannot make that distinction.

Repository-agnostic orchestration is realistic. Framework-neutral semantic integrity is not. The portable interface should be an adapter contract, not raw process output:

```text
prepare hermetic environment
enumerate approved tests
run selected tests
report structured execution/failure phases
normalize behavioral evidence
detect zero/skipped/deselected tests
```

Go, pytest, Cargo, and Jest adapters can implement that interface differently.

## 6. What I would ship

I would ship v1 as pytest-target support only, while keeping the workflow engine and verification interfaces repository-agnostic.

For v1 I would combine:

- an absolute protected-path gate;
- a strict human-approved changed-path allowlist;
- pytest collection and execution-phase verification;
- frozen node IDs;
- configuration/plugin/dependency boundary protection;
- conservative import-closure analysis;
- the proposed discrimination run as defense-in-depth;
- independent adversarial code review specifically checking test-context behavior;
- hermetic, separately provisioned red and green runs.

Full AST/import-closure introspection is still incomplete; dynamic imports, plugins, subprocesses, and environment hooks prevent it from being a proof. But pytest-specific structured evidence can enforce the PRD’s meaningful-red requirement, whereas generic exit-code comparison cannot.

The discrimination idea is useful. It is essentially a targeted counterfactual mutation: remove the implementation and require the contract to fail again. That catches accidental test weakening and many unconditional neutralizers. It should be retained as one signal.

It cannot serve as the “integrity core,” because a conditional fake implementation passes it by construction. The residual semantic risk must be handled through framework-aware verification, strict scope control, hermetic execution, mutation testing, and human/AI review—not described as eliminated by a Git-level proof.

VERDICT: BROKEN
