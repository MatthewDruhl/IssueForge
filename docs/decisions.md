# Decisions

Co-located decision log for IssueForge. Newest first.

## 2026-07-22 — S12 (#18): SUT-vs-helper discriminator is provenance + write-scope HYBRID

**Decision.** `discover_contract_dependencies` classifies every collection-reached in-repo path by
PROVENANCE only (fixture/conftest/config/plugin closure vs test-body-only import closure) and pins
external identities; it does NOT know the write scope. The FREEZE combines that provenance with the
approved `shape["write_scope"]` to decide protection:

- `fixture_closure` (conftest/fixture/config/plugin-reached, + transitive) is ALWAYS protected; any
  such path that also appears in the write scope is a CONTRADICTION and the freeze fails naming it.
- `test_body_imports` (in-repo, reached ONLY via a test-module body import, + transitive) is protected
  UNLESS it is in the write scope. In scope -> excluded as an editable SUT and SURFACED in
  `manifest.excluded_sut` (never silently dropped). Out of scope -> protected (fail-closed).
- A path reached via BOTH a fixture route and a test body -> fixture provenance WINS (protected).
- Test modules are always protected; external identities are always frozen.

**Why the hybrid.** Two mandated fixtures force both halves. (1) The helpers-bypass fixture: a helper
imported by a conftest must be protected even though it is not a test file, so pure "what is a test
file" structural role is insufficient — provenance (reached via the fixture graph) is required. (2)
The never-shrink fixture: an under-scoped production module imported by a test body must stay frozen,
while a genuinely-in-scope SUT must be editable — so protection cannot be decided from provenance
alone; it needs the approved write scope. Neither signal alone is sufficient; the freeze needs both.

**Rejected alternatives.**
- *Pure subtraction* (protect everything collected minus the write scope): silently laundered a
  fixture-reached helper out of the boundary the moment it appeared in scope, and could not surface an
  excluded SUT distinctly from an accidental omission. Rejected — it hides contradictions instead of
  failing on them.
- *Structural-role-only* (a path is protected iff it "looks like" a test/conftest/config file): missed
  transitively-imported helpers and production oracles reached through the fixture or test-body graph,
  and had no principled place for the in-scope-SUT exclusion. Rejected — role is not provenance.
