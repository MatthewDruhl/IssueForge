# Pipeline Verification Summary — 2026-07-09

Multi-agent verification of the project-build pipeline artifacts, with the 11 pipeline skills as ground truth. Full report with file:line evidence: `content/reviews/pipeline-verification-2026-07-09.md`.

**Method:** 47 agents. 7 parallel extractors (4 skill groups, docs, diagram, recent practice), 4 cross-checkers (doc drift, diagram drift, contradictions, improvement lens), then one adversarial skeptic per finding instructed to refute it against direct file reads.

**Outcome:** 33 findings confirmed, 3 refuted. 7 high, 16 medium, 10 low.

**Where the problems live:** the diagram is the worst offender (18 findings), the routing playbook `context/project-build-pipeline.md` has 8, and 7 are cross-skill contradictions.

## The 7 high-severity findings

1. **The diagram is missing the entire brownfield half of the pipeline.** No spec-up, spec-wave, findings-to-issues, or dispatch anywhere. It only depicts the greenfield write-a-prd path, which is not how most recent work has run.
2. **`/findings-to-issues` is missing from the routing playbook entirely.** The /harden route dead-ends at "findings," and the wave route consumes `wave:N` labels without naming what produces them.
3. **The diagram contradicts the #618 stacked-build model** and omits the #647 safe post-merge order, the one that already destroyed PRs #640-#646.
4. **No failure/recovery path exists in the diagram or playbook**, despite recovery being 9 of the last 40 commits on main.
5. **write-a-prd Step 7.5 sanctions a "skip marker" pending convention that spec-up forbids** and the #491 guard treats as weakening. Two skills disagree about a load-bearing convention.
6. **spec-dev's unconditional `isolation:"worktree"` spawn contradicts agent-contract, dispatch, and spec-wave for cross-repo targets** (the worktree-forks-marvin-not-the-target trap).
7. **The diagram shows no entry decision points** for when to use which skill.

## Notable mediums

- Playbook says plain `pytest xfail`; skills require literal `xfail(strict=True)`, and non-strict is exactly the false-green failure mode.
- Diagram shows PRD -> GitHub issue **before** suite authoring; the skill order is the reverse.
- Diagram shows suite approval as "one at a time"; spec-wave requires one batched pass.
- The deterministic #491 integrity checks are invisible in both artifacts; verification is attributed solely to Codex.
- `/merged` and `/wave-status` appear nowhere in the playbook, yet spec-wave's own Step 6 depends on /merged.
- wave-status greps for a `PENDING (#` marker string that no authoring skill actually mandates.

## Next step (pending decision)

Route the report through `/findings-to-issues` in report mode to conflict-group the findings into a parallel-safe wave of GitHub issues. Not yet run as of this note.
