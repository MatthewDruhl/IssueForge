"""Focused regression unit tests for the #20 (S14) refactor of ``run_candidate``.

When ``run_candidate`` was decomposed so the impl+verify sub-stage could be reused by
``run_candidate_with_repair``, an early version skipped the authoritative verify whenever the
provider PROCESS exited non-OK. That silently changed ``run_candidate``'s original always-verify
semantics: a provider that exits nonzero but nonetheless leaves a GREEN tree used to verify-and-land,
and would instead have PAUSED. This pins the restored behavior — the AUTHORITATIVE verify is the only
authority on landing; the provider's process status never overrides it for ``run_candidate``.
"""

from __future__ import annotations

# Reuse the #114 seam fakes + seed (tests/ is on sys.path under pytest's prepend import mode).
from test_engine_candidate_poc import _Fakes, _call, _seed


def test_run_candidate_lands_when_process_nonok_but_verify_green(
    make_git_repo, isolated_state_home
) -> None:
    """Regression: a provider that returns a non-OK status but wrote a GREEN implementation still
    verifies-and-LANDS (authoritative verdict decides, not the self-report). If the sub-stage
    short-circuited on process status this would wrongly pause."""
    from issueforge import store

    run_id, record, candidate, base_checkout, base_sha = _seed(make_git_repo)
    # Non-OK process status, but the impl WAS written and the authoritative verify is green.
    fakes = _Fakes(
        candidate,
        base_sha,
        impl_status="FAILED",
        impl_writes=True,
        acceptance_verdict="green",
        baseline_verdict="green",
    )

    result = _call(record, fakes)

    # The verify ran (both commands) and the run landed despite the non-OK process status.
    assert fakes.baseline_calls, (
        "the authoritative verify must ALWAYS run, even after a non-OK process"
    )
    assert result.paused is False, (
        "a green authoritative verify must land regardless of process status"
    )
    assert result.candidate_sha, "a landed run has a candidate sha"
    assert store.RunStore().read(run_id).get("candidate_sha") == result.candidate_sha
