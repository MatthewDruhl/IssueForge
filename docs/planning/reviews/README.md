# Independent review transcripts — IssueForge v1 decomposition, 2026-07-13/14

Six fresh-session adversarial reviews run during the first `/prd-to-issues` attempt on PRD #1.
That attempt was **BLOCKED**: the gate returned REVISE twice, so **no child issues were created**
and no resolution was invented. See `../issueforge-v1-decomposition-report.md`.

| # | Subject | Verdict |
|---|---|---|
| 01 | Decomposition draft v1 | **REVISE** — 12 blocking findings |
| 02 | Decomposition draft v2 | **REVISE** — 7 blocking findings |
| 03 | D1: "discriminates-or-fails" framework-neutral integrity core | **BROKEN** |
| 04 | D2: does implementation code review get a human override? | **ADD-OVERRIDE** |
| 05 | D3: must shaping precede contract authoring? | **SPLIT-SHAPER** |
| 06 | D1: can we have ZERO per-language adapters? | **HYBRID** |

All four decisions (D1–D4) are now resolved and amended into PRD #1.

**Review 02 contains a known error.** Its finding #3 asserts the PRD grants no implementation-review
override. It does — `prd-v1.md:153`. Review 04 caught it. Treat these transcripts as evidence, not
as authority: the gate was right four times out of six.

`decomposition-draft-v2-SUPERSEDED.md` is the 21-issue draft these reviews rejected. It is a starting
point for the next attempt, not a plan to execute. D1 and D3 change its shape materially.
