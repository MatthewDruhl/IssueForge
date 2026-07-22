"""Regression tests for the S11 (#17) build-gate hardening round.

The frozen S11 acceptance contract in ``test_contract.py`` exercises the gate with well-behaved
inputs. These ADDITIONAL unit tests lock in the adversarial edges the Codex build gate surfaced:
the verdict-vocabulary line-separator boundary, currency reading the override outcome, the counter's
type guard, the proof-binding precondition, the override provenance requirements, manifest-block
redaction, and the ``WriteSeam.allow_scratch`` security boundary.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from issueforge import contract, store
from issueforge.io import BoundaryViolation, WriteSeam


# ------------------------------------------------------------------- verdict vocabulary (#13)
@pytest.mark.parametrize(
    "verdict, legal",
    [
        ("skipped:provider-unavailable", True),
        ("skipped:first\rsecond", False),  # \r is a line separator to consumers
        ("skipped:a b", False),  # Unicode LINE SEPARATOR
        ("skipped:a\x0bb", False),  # vertical tab
        ("skipped:   ", False),
        ("skipped:", False),
    ],
)
def test_skip_reason_is_strictly_one_nonempty_line(verdict, legal):
    assert contract.valid_contract_verdict(verdict) is legal


# ------------------------------------------------------------------- currency + override (#11)
def test_currency_reads_override_outcome_not_just_verdict():
    overridden = {"contract_review": {"verdict": "blocking:1", "outcome": "done", "head_sha": "h"}}
    assert contract.red_evidence_current(overridden, "h") is True
    # a bare done verdict with no outcome key still counts (the frozen predicate shape)
    assert contract.red_evidence_current(
        {"contract_review": {"verdict": "done", "head_sha": "h"}}, "h"
    )
    # a blocking verdict with no override does not
    assert not contract.red_evidence_current(
        {"contract_review": {"verdict": "blocking:1", "head_sha": "h"}}, "h"
    )


# ------------------------------------------------------------------- counter type guard (#14)
def test_counter_bump_rejects_non_int_existing_value():
    st = store.RunStore()
    st.apply("bump", lambda _r: {"status": "running", "contract_review_rounds": True}, create=True)
    with pytest.raises(TypeError):
        contract._bump_rounds(st, "bump")
    st.apply("bump2", lambda _r: {"status": "running", "contract_review_rounds": 10.9}, create=True)
    with pytest.raises(TypeError):
        contract._bump_rounds(st, "bump2")


# ------------------------------------------------------------------- proof binding (#1)
def test_review_precondition_requires_proof_bound_to_current_head():
    accepted = {
        "red_proof": {
            "accepted": True,
            "reason": "behavioral_red",
            "base_sha": "B",
            "head_sha": "H",
        }
    }
    assert contract._require_bound_proof(accepted, "B", "H")["accepted"] is True
    # stale head, wrong base, non-red reason, and unaccepted proof each refuse
    with pytest.raises(ValueError):
        contract._require_bound_proof(accepted, "B", "OTHER")
    with pytest.raises(ValueError):
        contract._require_bound_proof(accepted, "WRONG", "H")
    with pytest.raises(ValueError):
        contract._require_bound_proof(
            {
                "red_proof": {
                    "accepted": True,
                    "reason": "not_red",
                    "base_sha": "B",
                    "head_sha": "H",
                }
            },
            "B",
            "H",
        )
    with pytest.raises(ValueError):
        contract._require_bound_proof({"red_proof": {"accepted": False}}, "B", "H")


# ------------------------------------------------------------------- override provenance (#10)
def test_override_requires_complete_failed_review_and_inputs():
    st = store.RunStore()
    st.apply(
        "ov",
        lambda _r: {
            "status": "paused",
            "contract_review": {
                "verdict": "blocking:1",
                "head_sha": "h",
                "reviewer_session_id": None,  # incomplete provenance
                "provider": "p",
                "outcome": "blocking:1",
            },
        },
        create=True,
    )
    with pytest.raises(ValueError):  # null session id
        contract.override_contract_review("ov", by="m", reason="r", verdict="done", method="human")

    st.apply(
        "ov2",
        lambda _r: {
            "status": "paused",
            "contract_review": {
                "verdict": "blocking:1",
                "head_sha": "h",
                "reviewer_session_id": "s",
                "provider": "reviewer-cli",
                "outcome": "blocking:1",
            },
        },
        create=True,
    )
    for bad in [
        {"by": "", "reason": "r", "verdict": "done", "method": "human"},
        {"by": "m", "reason": " ", "verdict": "done", "method": "human"},
        {"by": "m", "reason": "r", "verdict": "approved", "method": "human"},  # invalid vocab
        {"by": "m", "reason": "r", "verdict": "done", "method": ""},
    ]:
        with pytest.raises(ValueError):
            contract.override_contract_review("ov2", **bad)

    # a complete failed review with valid inputs succeeds and flips the effective outcome to done
    event = contract.override_contract_review(
        "ov2", by="matt", reason="checked", verdict="done", method="human"
    )
    assert event["overrode"] == "blocking:1" and event["reviewer_session_id"] == "s"
    assert contract.red_evidence_current(store.RunStore().read("ov2"), "h") is True


# ------------------------------------------------------------------- manifest-block redaction (#7)
def test_persist_block_redacts_findings_secrets_in_manifest():
    st = store.RunStore()
    st.apply("red", lambda _r: {"status": "running"}, create=True)
    contract._persist_block(
        st,
        "red",
        verdict="blocking:1",
        head_sha="h",
        reviewer_session_id="s",
        authoring_session_id="a",
        provider="p",
        findings=("weak golden CANARY7 here",),
        outcome="blocking:1",
        secrets=frozenset({"CANARY7"}),
    )
    body = (store.run_dir("red") / "manifest.json").read_text()
    assert "CANARY7" not in body
    assert "[REDACTED]" in body


# ------------------------------------------------------------------- allow_scratch security (#9)
def test_allow_scratch_rejects_broad_sensitive_and_repo_roots(tmp_path):
    # filesystem root, home, and an ancestor of the state root are all refused
    for bad in [Path(Path("/").anchor), Path.home()]:
        with pytest.raises(BoundaryViolation):
            WriteSeam().allow_scratch(bad)
    # a real Git checkout is refused (the MARVIN-checkout clobber attack)
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(BoundaryViolation):
        WriteSeam().allow_scratch(repo)
    # a populated (sub-directoried) tree is refused; a flat scratch dir is accepted
    populated = tmp_path / "tree"
    (populated / "sub").mkdir(parents=True)
    with pytest.raises(BoundaryViolation):
        WriteSeam().allow_scratch(populated)
    flat = tmp_path / "scratch"
    WriteSeam().allow_scratch(flat)  # fresh
    flat.mkdir()
    (flat / "diff.txt").write_text("x")
    WriteSeam().allow_scratch(flat)  # flat reuse across rounds (fresh seam each time)


# --------------------------------------------- N2: nonexistent scratch inside a checkout (build-confirm)
def test_allow_scratch_rejects_nonexistent_path_inside_checkout():
    """A NOT-YET-CREATED scratch path inside a Git checkout must be refused. ``git -C <nonexistent>``
    cannot discover the parent worktree, so the guard probes the nearest EXISTING ancestor: otherwise
    ``<checkout>/new-review-scratch`` would be authorized and then created inside a real checkout."""
    repo = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    nonexistent = repo / "new-review-scratch"
    assert not nonexistent.exists()
    with pytest.raises(BoundaryViolation):
        WriteSeam().allow_scratch(nonexistent)


# --------------------------------------------- N3: finalize preserves an overridden verdict (build-confirm)
def test_finalize_review_preserves_overridden_reviewer_verdict():
    """finalize_review must not erase a reviewer verdict that an override preserved: it sets the
    terminal ``outcome`` while keeping ``verdict='blocking:n'`` for provenance, and currency (which
    reads the effective outcome) recovers at the bound head."""
    run = "fin-preserve"
    store.RunStore().apply(
        run,
        lambda _r: {
            "status": "running",
            "contract_review": {"verdict": "blocking:1", "outcome": "done", "head_sha": "h"},
        },
        create=True,
    )
    contract.finalize_review(run, "done")
    block = store.RunStore().read(run)["contract_review"]
    assert block["verdict"] == "blocking:1"  # reviewer verdict/provenance preserved
    assert block["outcome"] == "done"  # terminal outcome recovered
    assert contract.red_evidence_current(store.RunStore().read(run), "h") is True


# A fresh run with no prior block still mints the verdict on finalize (unchanged behavior).
def test_finalize_review_mints_verdict_when_no_prior_block():
    run = "fin-fresh"
    store.RunStore().apply(run, lambda _r: {"status": "running"}, create=True)
    contract.finalize_review(run, "done")
    assert store.RunStore().read(run)["contract_review"]["verdict"] == "done"
