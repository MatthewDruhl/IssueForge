# PoC-D — the composed walking skeleton (#115)

PoC-D wires the three Wave-1 seams into the engine's DEFAULT stage so a single bare command drives one
registered issue end-to-end: fetch the fresh default branch, open an isolated detached worktree, prove
the committed baseline green, author + implement one human-approved candidate, gate its readiness, and
deliver EXACTLY one pull request — landing `waiting-for-merge`, never merging.

## Proof command

```
issueforge run DandD#111
```

Run against a registered `DandD` clone with an open issue #111, this composes `engine.run_candidate`
(#114) -> `verify.issue_readiness` (#112) -> `github.deliver_pr` (#113) and persists the run as
`waiting-for-merge` with a flat `pr_url`. The committed acceptance suite is
`tests/test_poc_integration.py`, driven with:

```
.venv/bin/python -m pytest -n auto tests/test_poc_integration.py -q -p no:cacheprovider
```

## Scope — M1 walking skeleton only

PoC-D is the M1 tracer bullet. It proves the composition reaches delivery on the happy path and pauses
correctly on the failure branches (rejected approval, acceptance-still-red, out-of-scope write, failed
reads). It deliberately does NOT implement the full production behavior of the successor slices.

## Deferrals — successor slices are NOT closed by this PoC

The following v1 slices remain open; PoC-D is a proof of composition, not their implementation, and each
is deferred to its own successor issue:

- #20 is a deferred successor and is NOT closed by this PoC (out of scope): S14 implements under the
  frozen contract with engine-owned repair budgets, whereas PoC-D runs a single implementation pass with
  no repair-budget retries.
- #21 is a deferred successor and is NOT closed by this PoC (out of scope): S15 is the full readiness
  gate with independent code review and human override, whereas PoC-D's readiness is acceptance +
  baseline + write-scope only, with contract-integrity scoped out for M1 (see #126).
- #22 is a deferred successor and is NOT closed by this PoC (out of scope): S16 is the fully hardened
  one-green-PR delivery; PoC-D exercises the #113 delivery seam only, and the successor's production
  hardening remains deferred.

Deeper robustness (full failure-stage attribution, byte-for-byte checkout preservation, integrity-mutation
readiness, and composed resume/retry re-entry) is deferred to #124. End-to-end contract integrity is
deferred to #126.
