"""Committed PENDING acceptance suite for #19 (S13) — contract-integrity verify, the amendment
path, and the engine gate that runs before the first GitHub mutation.

``issueforge.contract.verify_contract_integrity`` / ``amend_contract`` / ``IntegrityReport`` /
``IntegrityViolation`` / ``WrongAmendmentRouteError`` do not exist yet, so every test imports them
INSIDE its body and is ``@pytest.mark.xfail(strict=True, reason="PENDING (#19)")`` (an
ImportError/AttributeError while pending). ``freeze_contract`` (S12, #18) and ``shaper.run_shaping``
/ ``shaper.amend`` (S9, #13) already exist and are used LIVE here — every fixture in this file
builds a REAL frozen manifest over a REAL two-commit git repo via ``freeze_contract`` with an
approving approver, never a hand-built manifest dict, so the amend/verify/gate surfaces bind to the
real schema.

REMEDIATION (post-Codex NO-SHIP, findings 15-24; see
``/private/tmp/.../scratchpad/s13-api-contract.md`` REMEDIATION ADDENDUM, esp. R5/R6/R7/R8). Three
design decisions this suite AUTHORS, not given an exact signature by the locked S13 contract —
flagged rather than silently improvised:

1. Engine-gate call surface (contract §5 / R5) — REDESIGNED AGAIN post-confirmation-round (finding
   6). The FIRST DRAFT gave ``engine.apply_revision`` five OPTIONAL keyword-only params defaulting to
   ``None`` (Codex finding #23: a caller-controlled bypass). The SECOND DRAFT "fixed" that by making
   ``candidate_worktree``/``base_sha``/``adapter``/``provisioner`` MANDATORY keyword-only params —
   but that is ALSO wrong: caller-supplied integrity context is spoofable (a caller can simply pass a
   clean alternate worktree/adapter and bypass the real frozen run), and it breaks every pre-S13
   caller with no frozen contract at all. This suite's FINAL design keeps ``apply_revision``'s
   ORIGINAL signature — ``apply_revision(run_id, plan, gateway, *, approver)``, no new required
   kwargs — and has the gate derive its integrity context FROM PERSISTED RUN STATE: a
   ``candidate_worktree`` field the run record itself carries (set via ``store.RunStore().apply``,
   never via an apply_revision argument), verified against the run's own frozen manifest. The gate
   runs ONLY when the run HAS a frozen manifest; a manifest-less (pre-S13-shape) buildable run
   proceeds exactly as it always did — this is the backward-compat answer the earlier drafts left
   open. There is no spelling of an ``apply_revision`` call that supplies a caller-chosen bypass
   context, because there is no such parameter to supply.
2. Amendment "wrong route" detection (contract §4 / R6 finding #20). The FIRST DRAFT hand-mutated
   ``record["contract_review"]["verdict"]`` directly — never a real transition — and asserted on a
   message substring, which a stale ``head_sha`` alone could also trigger (a false-positive pass on
   the wrong cause). This suite instead drives the REAL S9 route (``shaper.amend``, already built)
   to genuinely bump the run's shape version and observability verdict, keeps the S11
   ``contract_review`` correctly bound to the CURRENT head (ruling out staleness as the cause), and
   asserts a DEDICATED exception type — ``contract.WrongAmendmentRouteError`` — fires because the
   run's CURRENT approved ``shape["write_scope"]`` has diverged from the manifest's frozen
   ``write_scope`` (the one field ``freeze_contract`` already persists verbatim from the shape that
   was current at freeze time — no change to the already-shipped ``freeze_contract`` is required).
3. "Issue-linked reason" (R6 finding #15) is treated as any string containing a ``#<digits>`` issue
   reference (e.g. ``"#19: ..."``); a reason with no such reference (e.g. ``"cleanup"``) is refused
   the same as ``None``/``""``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import store

_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]
_CONFIG = 'baseline = ["-m", "pytest"]\nframework = "pytest"\n'


# =============================================================== self-contained scaffolding
# (deliberately NOT imported from test_contract.py — this file stands alone).


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args], check=True, capture_output=True, text=True
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))


def _repo(tmp_path: Path, name: str, *, candidate_files: dict[str, str]) -> SimpleNamespace:
    """A REAL two-commit repo: a base commit (config + a green base test) and a candidate commit
    overlaying ``candidate_files`` — mirrors test_contract.py's ``_scenario`` shape, self-contained.
    """
    base_files = {"tests/test_base.py": "def test_base():\n    assert True\n"}
    repo = tmp_path / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(_CONFIG)
    _write(repo, base_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _write(repo, candidate_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    candidate_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return SimpleNamespace(path=repo, base_sha=base_sha, candidate_sha=candidate_sha)


def _commit(repo: Path, files: dict[str, str]) -> str:
    """Overlay ``files`` onto ``repo`` as a NEW commit; returns the new HEAD sha."""
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "amend")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _add_symlink_commit(repo: Path, link_rel: str, target_rel: str) -> str:
    """Commit a REAL in-repo symlink ``link_rel`` -> ``target_rel`` (relative, resolved from
    ``link_rel``'s own directory); returns the new HEAD sha."""
    link_path = repo / link_rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target_rel, link_path)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "symlink")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _real_diff(repo: Path, old: str, new: str) -> str:
    """The REAL unified diff old..new — the independent oracle a candidate ``diff_text`` binds to."""
    return _git(repo, "diff", old, new).stdout


def _adapter():
    from issueforge.adapters.pytest_adapter import PytestAdapter

    return PytestAdapter()


def _provisioner():
    """A host-side provisioner seam (no network denial, plugin autoload disabled)."""
    counter = {"n": 0}

    def _provision(worktree, frozen_deps=None):
        counter["n"] += 1
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        artifact = Path(worktree).parent / f"if-env-{Path(worktree).name}-{counter['n']}"
        return SimpleNamespace(
            interpreter=sys.executable, env=env, artifact_dir=artifact, network=False
        )

    return _provision


def _approve_all(_payload) -> bool:
    return True


def _reject_all(_payload) -> bool:
    return False


def _mk_run(run_id: str = "run-1", *, write_scope: list | None = None) -> str:
    shape = {"classification": "buildable", "write_scope": write_scope or []}
    record = {"status": "running", "shape": shape, "revision_ledger": {}}
    store.RunStore().apply(run_id, lambda _r: record, create=True)
    return run_id


def _seed_candidate_worktree(run_id: str, path: Path) -> None:
    """Persist ``candidate_worktree`` onto the run record itself — the ONLY way a gate test ever
    supplies a candidate to ``apply_revision``. ``apply_revision`` has no ``candidate_worktree``
    keyword; the gate reads this field from the run's OWN persisted state (finding 6), so a caller
    cannot spoof a clean alternate worktree to bypass the run's real frozen contract."""
    store.RunStore().apply(run_id, lambda _r: {"candidate_worktree": str(path)})


def _seed_freeze_evidence(run_id: str, repo: SimpleNamespace, *, head: str | None = None) -> None:
    """Hand-seed an accepted S10 red proof + a ``done`` S11 review bound to ``head`` (default the
    candidate sha) — the precondition ``freeze_contract``/``amend_contract`` consume, exactly like
    test_contract.py's ``_seed_freeze_proof``/``_seed_done_review`` (ported, self-contained)."""
    head = repo.candidate_sha if head is None else head
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "red_proof": {
                "accepted": True,
                "reason": "behavioral_red",
                "base_sha": repo.base_sha,
                "head_sha": head,
                "added_ids": ["tests/test_new.py::test_x"],
                "records": [
                    {
                        "nodeid": "tests/test_new.py::test_x",
                        "exception_type": "AssertionError",
                        "assertion_line": 2,
                        "message": "assert 1 == 2",
                    }
                ],
            },
            "contract_review": {
                "verdict": "done",
                "outcome": "done",
                "head_sha": head,
                "reviewer_session_id": "rev-1",
                "authoring_session_id": "auth-1",
                "provider": "cli",
                "findings": [],
            },
        },
    )


def _freeze(run_id: str, repo: SimpleNamespace, *, approver=None, provisioner=None):
    from issueforge import contract

    _seed_freeze_evidence(run_id, repo)
    return contract.freeze_contract(
        run_id,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=provisioner or _provisioner(),
        approver=_approve_all if approver is None else approver,
    )


def _manifest_bytes(run_id: str) -> bytes:
    from issueforge.paths import run_dir

    return (run_dir(run_id) / "contract-manifest.json").read_bytes()


# =============================================================== Group A — amend_contract (§4)


def test_amend_happy_path_writes_new_manifest_and_preserves_review_block(tmp_path):
    """A valid amendment (issue-linked reason + the exact real diff + renewed approval) writes a
    genuinely NEW manifest with a different hash and an updated contract_commit, while leaving the
    frozen contract_review block byte-for-byte untouched (amend touches only the acceptance
    manifest, never the observability verdict).

    technical: FreezeResult.approved is True; manifest_hash != the original; manifest["contract_commit"]
    == the new HEAD; manifest["contract_review"] == the original manifest's contract_review.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-happy",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    assert original.approved is True

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    repo.candidate_sha = new_head
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)

    result = contract.amend_contract(
        run,
        reason="#19: widen the assertion tolerance per reviewer feedback",
        diff_text=diff_text,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all,
    )

    assert result.approved is True
    assert result.manifest_hash != original.manifest_hash
    assert result.manifest["contract_commit"] == new_head
    assert result.manifest["contract_review"] == original.manifest["contract_review"]


@pytest.mark.parametrize("reason", [None, "", "cleanup"])
def test_amend_refused_when_reason_missing(tmp_path, reason):
    """An amendment with no issue-linked reason is refused, even when the diff is the exact real
    diff and the approver would have approved — no new manifest is ever persisted. This covers
    both the absent forms (None/"") AND a concrete but NON-issue-linked reason ("cleanup") — a
    plausible-sounding excuse is not itself sufficient; the reason must reference a real issue.

    technical: contract.amend_contract(..., reason=<None|""|"cleanup">, diff_text=<real diff>,
    approver=_approve_all) raises ValueError; the on-disk manifest bytes are unchanged.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-noreason",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    before = _manifest_bytes(run)

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)

    with pytest.raises(ValueError):
        contract.amend_contract(
            run,
            reason=reason,
            diff_text=diff_text,
            candidate_worktree=repo.path,
            base_sha=repo.base_sha,
            adapter=_adapter(),
            provisioner=_provisioner(),
            approver=_approve_all,
        )
    assert _manifest_bytes(run) == before == original.artifact_path.read_bytes()


def test_amend_refused_when_diff_text_does_not_match_real_diff(tmp_path):
    """A candidate ``diff_text`` that does not match the ACTUAL contract_commit..HEAD diff is
    refused — a caller cannot authorize one change while a different change is really staged.

    technical: diff_text is a fabricated, plausible-looking unified diff (not the real one);
    amend_contract raises ValueError and persists no new manifest.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-fakediff",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    before = _manifest_bytes(run)

    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    fabricated_diff = (
        "--- a/tests/test_new.py\n+++ b/tests/test_new.py\n"
        "@@ -1,2 +1,2 @@\n-def test_x():\n-    assert 1 == 2\n"
        "+def test_x():\n+    assert 1 == 999\n"
    )

    with pytest.raises(ValueError):
        contract.amend_contract(
            run,
            reason="#19: bump the tolerance",
            diff_text=fabricated_diff,
            candidate_worktree=repo.path,
            base_sha=repo.base_sha,
            adapter=_adapter(),
            provisioner=_provisioner(),
            approver=_approve_all,
        )
    assert _manifest_bytes(run) == before == original.artifact_path.read_bytes()


def test_amend_refused_when_approver_returns_false(tmp_path):
    """Rejecting the renewed approval writes nothing: FreezeResult.approved is False and the
    manifest fields are all None, and the original manifest artifact is untouched on disk.

    technical: approver=_reject_all -> FreezeResult(approved=False, manifest=None, manifest_hash=
    None, artifact_path=None); the retained artifact bytes still equal the ORIGINAL freeze's bytes.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-rejected",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    before = _manifest_bytes(run)

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)

    result = contract.amend_contract(
        run,
        reason="#19: widen tolerance",
        diff_text=diff_text,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_reject_all,
    )

    assert result.approved is False
    assert result.manifest is None
    assert result.manifest_hash is None
    assert result.artifact_path is None
    assert _manifest_bytes(run) == before == original.artifact_path.read_bytes()


def test_amend_body_byte_identical_does_not_bypass_exact_diff_check(tmp_path):
    """R6 finding #16: the ORIGINAL test proved nothing (its diff was empty, so it failed only on
    the missing reason). This constructs a REAL boundary change — a decorator line — while the
    function's BODY stays byte-identical in both revisions, then proves whole-body equality is not
    a bypass: a fabricated diff_text claiming no change is refused even though the body text never
    changed, while the CORRECT diff_text (capturing the decorator hunk) succeeds.

    technical: old_src carries ``@pytest.mark.slow`` above ``def test_x(): assert 1 == 2``; new_src
    drops the decorator; the literal body text ``def test_x():\\n    assert 1 == 2\\n`` is identical
    in both. diff_text="" is refused (ValueError, no persist); the real diff_text succeeds
    (approved is True, contract_commit == new_head).
    """
    from issueforge import contract

    run = _mk_run()
    body = "def test_x():\n    assert 1 == 2\n"
    old_src = "import pytest\n\n\n@pytest.mark.slow\n" + body
    new_src = "import pytest\n\n\n" + body
    assert body in old_src and body in new_src

    repo = _repo(tmp_path, "amend-decorator", candidate_files={"tests/test_new.py": old_src})
    original = _freeze(run, repo)
    before = _manifest_bytes(run)

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": new_src})
    _seed_freeze_evidence(run, repo, head=new_head)
    real_diff = _real_diff(repo.path, old_head, new_head)
    assert real_diff  # a REAL, non-empty diff despite the byte-identical function body

    with pytest.raises(ValueError):
        contract.amend_contract(
            run,
            reason="#19: drop the slow marker",
            diff_text="",  # a body-equality-shaped bypass attempt: claims no real change at all
            candidate_worktree=repo.path,
            base_sha=repo.base_sha,
            adapter=_adapter(),
            provisioner=_provisioner(),
            approver=_approve_all,
        )
    assert _manifest_bytes(run) == before == original.artifact_path.read_bytes()

    result = contract.amend_contract(
        run,
        reason="#19: drop the slow marker",
        diff_text=real_diff,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert result.approved is True
    assert result.manifest["contract_commit"] == new_head


def test_amend_approver_receives_the_exact_bytes_subsequently_persisted(tmp_path):
    """R6 finding #17: the renewed-approval callback is not a rubber stamp — it must receive the
    EXACT canonical bytes amend_contract goes on to persist (happy path), and the exact real
    candidate bytes even on the no-persist rejection path (never a stub/placeholder payload).

    technical: a capturing approver's single positional argument, on approval, equals
    result.artifact_path.read_bytes() byte-for-byte; on the rejection path, the SAME rigor applies:
    the captured payload is compared WHOLE-PAYLOAD, byte-for-byte, against the independently-derived
    canonical bytes an otherwise-identical APPROVED call against the same run/repo/diff produces —
    not merely a JSON substring/field check.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-approver-payload",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)

    captured: list[bytes] = []

    def _capturing_approve(payload):
        captured.append(payload)
        return True

    result = contract.amend_contract(
        run,
        reason="#19: widen tolerance",
        diff_text=diff_text,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_capturing_approve,
    )
    assert result.approved is True
    assert len(captured) == 1
    assert captured[0] == result.artifact_path.read_bytes()
    decoded = json.loads(captured[0])
    assert decoded["contract_commit"] == new_head

    # A SEPARATE run for the no-persist rejection path.
    run2 = _mk_run("amend-approver-payload-reject")
    repo2 = _repo(
        tmp_path,
        "amend-approver-payload-reject",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run2, repo2)
    old_head2 = repo2.candidate_sha
    new_head2 = _commit(repo2.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run2, repo2, head=new_head2)
    diff_text2 = _real_diff(repo2.path, old_head2, new_head2)
    reject_kwargs = dict(
        candidate_worktree=repo2.path,
        base_sha=repo2.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )

    captured2: list[bytes] = []

    def _capturing_reject(payload):
        captured2.append(payload)
        return False

    rejected = contract.amend_contract(
        run2,
        reason="#19: widen tolerance",
        diff_text=diff_text2,
        approver=_capturing_reject,
        **reject_kwargs,
    )
    assert rejected.approved is False
    assert len(captured2) == 1
    decoded2 = json.loads(captured2[0])
    assert decoded2["contract_commit"] == new_head2  # the REAL candidate, not a stub payload

    # Finding 4 (R6 finding #17, sharpened further): the false-return path deserves the SAME rigor
    # as the happy path — whole-payload byte equality, not a JSON-field check. Nothing was persisted
    # by the rejection, so run2/repo2/diff_text2/reason are all still exactly as they were; an
    # otherwise-identical APPROVED call against that same unmodified state is the independent oracle
    # for the EXACT canonical bytes the rejecting approver was shown.
    oracle = contract.amend_contract(
        run2,
        reason="#19: widen tolerance",
        diff_text=diff_text2,
        approver=_approve_all,
        **reject_kwargs,
    )
    assert oracle.approved is True
    assert captured2[0] == oracle.artifact_path.read_bytes()  # whole-payload byte-for-byte


def test_amend_new_manifest_is_a_genuinely_new_retained_artifact_prior_preserved(tmp_path):
    """R6 finding #18: "writes a new manifest" must be proven on the RETAINED ARTIFACT, not only
    the returned dataclass — and the prior manifest must remain auditable (never clobbered), exactly
    like freeze_contract's own single-assignment guarantee.

    technical: result.artifact_path != the original freeze's artifact_path (a genuinely new
    retained file); sha256(retained bytes) == result.manifest_hash; the retained JSON's
    contract_commit == new_head; the ORIGINAL manifest artifact's bytes are unchanged afterward.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-retained",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    original_bytes = original.artifact_path.read_bytes()

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)

    result = contract.amend_contract(
        run,
        reason="#19: widen tolerance",
        diff_text=diff_text,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert result.approved is True
    assert result.artifact_path != original.artifact_path  # a genuinely NEW retained artifact

    retained = result.artifact_path.read_bytes()
    assert hashlib.sha256(retained).hexdigest() == result.manifest_hash
    decoded = json.loads(retained)
    assert decoded["contract_commit"] == new_head

    # The FIRST manifest remains retained, byte-for-byte, at its own path — never clobbered.
    assert original.artifact_path.read_bytes() == original_bytes


def test_amend_records_a_permanent_audit_event_binding_reason_diff_approval_and_manifest(tmp_path):
    """R6 finding #19: a successful amendment appends exactly one PERMANENT "amend" event to the
    run's append-only event log that binds all four amendment facts together — the issue-linked
    reason, the exact diff that was authorized, the renewed approval, and the new manifest's
    identity — the same auditable mechanism freeze_contract's own "freeze" event already uses.

    technical: exactly one event with transition == "amend" after the call; its "reason" ==
    the exact reason string passed; its "diff_text" == the exact diff_text passed; its "approved"
    is True; its "manifest_hash" == result.manifest_hash.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-audit",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)
    reason = "#19: widen the assertion tolerance per reviewer feedback"

    result = contract.amend_contract(
        run,
        reason=reason,
        diff_text=diff_text,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert result.approved is True

    events = [e for e in store.RunStore().replay_events(run) if e.get("transition") == "amend"]
    assert len(events) == 1
    event = events[0]
    assert event.get("reason") == reason
    assert event.get("diff_text") == diff_text
    assert event.get("approved") is True
    assert event.get("manifest_hash") == result.manifest_hash


def test_amend_rejects_when_a_real_s9_observability_amendment_has_since_landed(tmp_path):
    """R6 finding #20: the ORIGINAL test hand-mutated a dict field (never a real transition) and
    asserted on a message substring that a stale head_sha alone could also trigger. This drives an
    ACTUAL S9 observability-state transition through its real, specified route — ``shaper.amend``,
    already built — and asserts a STABLE, dedicated error TYPE. The S11 review is explicitly
    re-bound to the CURRENT head before the call, so a stale-head side effect cannot explain the
    refusal: the only remaining defect is the routing violation itself.

    technical: shaper.run_shaping mints shape v1 (verdict "required", write_scope
    ["docs/NOTES.md"]); freeze_contract freezes it (manifest["write_scope"] == ["docs/NOTES.md"]);
    shaper.amend (the real S9 route) mints shape v2 (verdict "existing coverage sufficient",
    write_scope ["docs/OTHER.md"]) — store-verified. amend_contract(...) with an otherwise valid
    reason/diff/approval and a CURRENT (head-bound) S11 review then raises
    contract.WrongAmendmentRouteError (isinstance check, not a regex/substring match).
    """
    from issueforge import contract, shaper
    from issueforge.providers import AIResult, InvocationStatus

    issue = {
        "slug": "MatthewDruhl/IssueForge",
        "number": 19,
        "body": "S13 amendment routing",
        "contract_paths": ["tests/"],
    }
    justification = "changed code crosses the AI boundary via providers.invoke"
    github = SimpleNamespace(search_open_issues=lambda *_a, **_k: [])

    def _review_for(verdict, session_id):
        def _r(_proposal):
            return SimpleNamespace(
                result=AIResult(
                    role="review",
                    provider="fake",
                    session_id=session_id,
                    prompt="p",
                    stdout="",
                    stderr="",
                    returncode=0,
                    duration_ms=1.0,
                    status=InvocationStatus.OK,
                    timed_out=False,
                    artifact_path=Path("transcript.txt"),
                ),
                verdict=verdict,
                justification=justification,
                duplicate_verdict=False,
            )

        return _r

    def _proposal_for(verdict, write_scope_path):
        return {
            "classification": "buildable",
            "blocked_reason": None,
            "duplicate": {"verdict": False, "evidence": []},
            "unresolved_design_decisions": [],
            "write_scope": [{"op": "modify", "path": write_scope_path, "justification": "n/a"}],
            "footprint_known": True,
            "observability": {
                "verdict": verdict,
                "justification": justification,
                "events": ["shape.emitted"],
                "prohibited_sensitive_fields": ["issue_body"],
            },
            "authoring_session_id": "auth-1",
        }

    run = "amend-wrongroute-real-s9"
    shaper.run_shaping(
        run,
        issue,
        assessor=lambda _i: _proposal_for("required", "docs/NOTES.md"),
        reviewer=_review_for("required", "rev-1"),
        approver=lambda _s: True,
        github=github,
    )
    record = store.RunStore().read(run)
    assert record["shape"]["version"] == 1
    assert record["shape"]["write_scope"][0]["path"] == "docs/NOTES.md"

    repo = _repo(
        tmp_path,
        "amend-wrongroute-real-s9",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    assert original.manifest["write_scope"] == ["docs/NOTES.md"]

    # The REAL S9 route: a fresh confirmation + renewed human approval mints a NEW shape version
    # with a genuinely different observability verdict and write scope.
    shaper.amend(
        run,
        issue,
        assessor=lambda _i: _proposal_for("existing coverage sufficient", "docs/OTHER.md"),
        reviewer=_review_for("existing coverage sufficient", "rev-2"),
        approver=lambda _s: True,
        github=github,
    )
    record = store.RunStore().read(run)
    assert record["shape"]["version"] == 2
    assert record["shape"]["write_scope"][0]["path"] == "docs/OTHER.md"
    assert record["shape"]["observability"]["verdict"] == "existing coverage sufficient"

    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    diff_text = _real_diff(repo.path, old_head, new_head)
    # Re-bind the S11 review to the CURRENT head so currency alone cannot explain a refusal.
    _seed_freeze_evidence(run, repo, head=new_head)
    assert store.RunStore().read(run)["contract_review"]["head_sha"] == new_head

    with pytest.raises(contract.WrongAmendmentRouteError):
        contract.amend_contract(
            run,
            reason="#19: widen tolerance",
            diff_text=diff_text,
            candidate_worktree=repo.path,
            base_sha=repo.base_sha,
            adapter=_adapter(),
            provisioner=_provisioner(),
            approver=_approve_all,
        )


@pytest.mark.parametrize("path", ["success", "refusal"])
def test_amend_contract_never_invokes_the_ai_provider(tmp_path, monkeypatch, path):
    """R7 finding #21: amend_contract's every precondition (reason validation, exact-diff
    verification, renewed approval) is fully deterministic — no AI/provider call on either the
    SUCCESS path or a REFUSAL (a non-issue-linked reason).

    technical: issueforge.providers.invoke is monkeypatched to raise AssertionError if called;
    amend_contract completes (approved True, or a ValueError refusal) without tripping it.
    """
    import issueforge.providers as providers
    from issueforge import contract

    def _no_provider(*args, **kwargs):
        raise AssertionError("amend_contract must never invoke the AI/provider seam")

    monkeypatch.setattr(providers, "invoke", _no_provider)

    run = _mk_run()
    repo = _repo(
        tmp_path,
        f"amend-noai-{path}",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    old_head = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_freeze_evidence(run, repo, head=new_head)
    diff_text = _real_diff(repo.path, old_head, new_head)

    kwargs = dict(
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    if path == "success":
        result = contract.amend_contract(
            run, reason="#19: widen tolerance", diff_text=diff_text, **kwargs
        )
        assert result.approved is True
    else:
        with pytest.raises(ValueError):
            contract.amend_contract(run, reason="cleanup", diff_text=diff_text, **kwargs)


# =============================================================== Group B — verify_contract_integrity (§2)


def test_verify_contract_integrity_ok_true_for_clean_candidate(tmp_path):
    """A candidate HEAD that exactly matches the frozen contract passes every check: ok is True and
    violations is empty.

    technical: verify_contract_integrity(run_id, candidate_worktree=<unchanged worktree>, ...).ok is
    True and .violations == ().
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "verify-clean",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)

    report = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )
    assert report.ok is True
    assert report.violations == ()


def test_verify_contract_integrity_flags_protected_path_diff_by_name(tmp_path):
    """Editing a frozen (protected) contract file at HEAD is caught as a named "protected_path_diff"
    violation naming the changed path — never silently absorbed as "ok".

    technical: after freezing, tests/test_new.py is edited (uncommitted change on top of the frozen
    HEAD... simulated here as a NEW commit changing a frozen path) -> a violation with predicate ==
    "protected_path_diff" and detail == "tests/test_new.py" is present; ok is False.
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "verify-diff",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    frozen = _freeze(run, repo)
    assert "tests/test_new.py" in frozen.manifest["contract_paths"]

    _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})

    report = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )
    assert report.ok is False
    names = {(v.predicate, v.detail) for v in report.violations}
    assert ("protected_path_diff", "tests/test_new.py") in names


@pytest.mark.parametrize("outcome", ["clean", "violation"])
def test_verify_contract_integrity_never_invokes_the_ai_provider(tmp_path, monkeypatch, outcome):
    """The static integrity check is fully deterministic: it never calls out to the AI/provider seam,
    on either the clean or the violating path — the harness runs this, never the policed session.

    technical: issueforge.providers.invoke is monkeypatched to raise AssertionError if called;
    verify_contract_integrity(...) completes (either ok True or ok False) without tripping it.
    """
    import issueforge.providers as providers
    from issueforge import contract

    def _no_provider(*args, **kwargs):
        raise AssertionError("verify_contract_integrity must never invoke the AI/provider seam")

    monkeypatch.setattr(providers, "invoke", _no_provider)

    run = _mk_run()
    repo = _repo(
        tmp_path,
        f"verify-noai-{outcome}",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    if outcome == "violation":
        _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})

    report = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )
    assert report.ok is (outcome == "clean")


def test_verify_contract_integrity_env_var_branch_in_excluded_sut_is_not_caught(tmp_path):
    """R8 finding #22: the ORIGINAL residual-risk test used a pre-approved branch on an unchanged
    HEAD, which is trivially "ok" for reasons unrelated to the claimed risk. This freezes a manifest
    whose write_scope EXCLUDES a real editable SUT (a test-body import, mirroring the already-proven
    ``app/calc.py`` pattern from test_contract.py's excluded-SUT tests), then, at HEAD, adds a
    test-runner env-var branch to THAT EXCLUDED impl file only (never to the test file itself).
    Because the SUT is legitimately outside the frozen boundary, the static check cannot and does
    not see the change — this pins that the residual risk is CARRIED forward (to S15's hermetic
    runs), not silently caught here.

    technical: app/calc.py in test_body_imports ∩ write_scope -> excluded from contract_paths,
    surfaced in excluded_sut (frozen boundary confirmed via freeze_contract, S12's real schema).
    At HEAD, app/calc.py gains an ``if os.environ.get("IF_TEST"): ...`` branch — the frozen test
    file is untouched. verify_contract_integrity(...) still reports ok is True.
    """
    from issueforge import contract

    run = _mk_run(
        "verify-envcheat-excluded",
        write_scope=[{"op": "modify", "path": "app/calc.py", "justification": "the SUT"}],
    )
    repo = _repo(
        tmp_path,
        "verify-envcheat-excluded",
        candidate_files={
            "app/calc.py": "def calc():\n    return 1\n",
            "tests/test_new.py": (
                "from app.calc import calc\n\n\ndef test_x():\n    assert calc() == 1\n"
            ),
        },
    )
    frozen = _freeze(run, repo)
    assert "app/calc.py" in frozen.manifest["excluded_sut"]
    assert "app/calc.py" not in frozen.manifest["contract_paths"]

    _commit(
        repo.path,
        {
            "app/calc.py": (
                "import os\n\n\n"
                "def calc():\n"
                "    if os.environ.get('IF_TEST'):\n"
                "        return 2\n"
                "    return 1\n"
            )
        },
    )

    report = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )
    assert report.ok is True


# =============================================================== Group C — engine gate (§5)


def _plan(repo_marker: str = "op-1") -> list[dict]:
    return [
        {
            "op": "update_body",
            "id": repo_marker,
            "issue": ("MatthewDruhl", "IssueForge", 19),
            "body": "R",
        }
    ]


class _ExplodingGateway:
    """Any attribute access is a test failure — the FIRST-mutation gate must never touch it when
    the integrity check refuses."""

    def __getattr__(self, name):
        raise AssertionError(f"gateway.{name} touched before/without integrity clearance")


class _SpyGateway:
    def __init__(self):
        self.calls: list[tuple] = []

    def update_body(self, issue, body):
        self.calls.append(("update_body", issue, body))


class _DriftingAdapter:
    """Delegates to the REAL PytestAdapter for everything except discover_contract_dependencies,
    which reports one EXTRA external pin the real freeze never saw — a real, grounded external_pin
    drift proven through the same adapter seam ``verify_contract_integrity`` itself consumes,
    rather than a fabricated version-resolution monkeypatch."""

    def __init__(self):
        self._real = _adapter()

    def provision_environment(self, *args, **kwargs):
        return self._real.provision_environment(*args, **kwargs)

    def canonical_collect(self, *args, **kwargs):
        return self._real.canonical_collect(*args, **kwargs)

    def discover_contract_dependencies(self, collection):
        from issueforge.adapters.pytest_adapter import ContractClosure

        real = self._real.discover_contract_dependencies(collection)
        return ContractClosure(
            test_files=real.test_files,
            fixture_closure=real.fixture_closure,
            test_body_imports=real.test_body_imports,
            external=real.external + (("drifted-dist", "9.9.9"),),
        )

    def validate_invocation(self, command):
        return self._real.validate_invocation(command)


def _scenario_protected_path_diff(tmp_path):
    run = _mk_run("gate-ppd")
    repo = _repo(
        tmp_path,
        "gate-ppd",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    return run, repo, None


def _scenario_dep_hash(tmp_path):
    """R2: a symlink whose in-repo TARGET is outside contract_paths; mutating the target — never
    the symlink path itself, which the diff gate already watches — proves dep_hash independently."""
    run = _mk_run("gate-dep")
    repo = _repo(
        tmp_path,
        "gate-dep",
        candidate_files={
            "tests/test_new.py": "def test_x():\n    assert 1 == 2\n",
            "lib/shared_data.txt": "v1\n",
        },
    )
    repo.candidate_sha = _add_symlink_commit(repo.path, "tests/data_link", "../lib/shared_data.txt")
    _freeze(run, repo)
    _commit(repo.path, {"lib/shared_data.txt": "v2\n"})
    return run, repo, None


def _scenario_external_pin(tmp_path):
    run = _mk_run("gate-ext")
    repo = _repo(
        tmp_path,
        "gate-ext",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)  # no external imports -> frozen external_pins == []
    return run, repo, _DriftingAdapter()


def _scenario_recollection(tmp_path):
    """A brand-new test file post-freeze changes the collected id set without touching any
    already-frozen contract_paths member — isolates recollection from protected_path_diff."""
    run = _mk_run("gate-recol")
    repo = _repo(
        tmp_path,
        "gate-recol",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    _commit(repo.path, {"tests/test_extra.py": "def test_extra():\n    assert True\n"})
    return run, repo, None


def _scenario_invocation(tmp_path):
    """A frozen config source (pytest.ini, selected + hashed at freeze time) mutated at HEAD to
    add a prohibited bail flag — R4's wrapper/config-drift route."""
    run = _mk_run("gate-inv")
    repo = _repo(
        tmp_path,
        "gate-inv",
        candidate_files={
            "tests/test_new.py": "def test_x():\n    assert 1 == 2\n",
            "pytest.ini": "[pytest]\n",
        },
    )
    _freeze(run, repo)
    _commit(repo.path, {"pytest.ini": "[pytest]\naddopts = -x\n"})
    return run, repo, None


def _scenario_ancestry(tmp_path):
    """``git commit --amend`` mints a sibling commit sharing the same parent as contract_commit —
    contract_commit is no longer an ancestor of the new HEAD (a clean lineage break, no content
    change, isolating ancestry from every other predicate)."""
    run = _mk_run("gate-anc")
    repo = _repo(
        tmp_path,
        "gate-anc",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    _git(repo.path, "commit", "--amend", "--no-edit", "-q")
    return run, repo, None


def _scenario_ast_backstop(tmp_path):
    """A frozen acceptance test's xfail(strict=True) marker is removed at HEAD — a weakening the
    AST backstop is meant to catch as defense-in-depth (co-fires with protected_path_diff, which
    R1 explicitly allows)."""
    run = _mk_run("gate-ast")
    repo = _repo(
        tmp_path,
        "gate-ast",
        candidate_files={
            "tests/test_new.py": (
                "import pytest\n\n\n"
                "@pytest.mark.xfail(strict=True, reason='PENDING')\n"
                "def test_flaky():\n"
                "    assert 1 == 2\n"
            ),
        },
    )
    _freeze(run, repo)
    _commit(
        repo.path,
        {"tests/test_new.py": ("import pytest\n\n\ndef test_flaky():\n    assert 1 == 2\n")},
    )
    return run, repo, None


_GATE_SCENARIOS = {
    "protected_path_diff": _scenario_protected_path_diff,
    "dep_hash": _scenario_dep_hash,
    "external_pin": _scenario_external_pin,
    "recollection": _scenario_recollection,
    "invocation": _scenario_invocation,
    "ancestry": _scenario_ancestry,
    "ast_backstop": _scenario_ast_backstop,
}


def test_engine_refuses_apply_revision_before_any_mutation_on_integrity_violation(tmp_path):
    """REDESIGNED (finding 6): given a run with a frozen manifest and a candidate_worktree PERSISTED
    on the run's OWN state (never on the apply_revision call) whose HEAD violates a predicate,
    apply_revision — called with its ORIGINAL, unchanged signature — refuses BEFORE touching the
    gateway at all: the human approver is never even consulted, and the exploding gateway proves
    zero mutation happened.

    technical: engine.apply_revision(run_id, plan, _ExplodingGateway(), approver=_approve_all) — no
    integrity kwargs at all — raises (naming the violated predicate "protected_path_diff") and
    records no "revision" event.
    """
    from issueforge import engine

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-violation",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_candidate_worktree(run, repo.path)

    with pytest.raises(Exception, match=r"(?i)protected_path_diff"):
        engine.apply_revision(run, _plan(), _ExplodingGateway(), approver=_approve_all)
    events = store.RunStore().replay_events(run)
    assert not any(e.get("transition") == "revision" for e in events)


def test_engine_gate_error_names_the_violated_predicate_precisely(tmp_path):
    """REDESIGNED (finding 6): the raised integrity error names the SPECIFIC violated predicate, not
    a generic failure — a caller resolving the refusal must be able to tell WHICH invariant broke,
    with the candidate sourced entirely from the run's own persisted state.

    technical: the exception text contains the exact predicate string "protected_path_diff" (not
    merely a bare "integrity" or "refused" word with no named predicate).
    """
    from issueforge import engine

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-named",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_candidate_worktree(run, repo.path)

    with pytest.raises(Exception) as excinfo:
        engine.apply_revision(run, _plan(), _ExplodingGateway(), approver=_approve_all)
    assert "protected_path_diff" in str(excinfo.value)


def test_engine_proceeds_to_mutation_when_candidate_is_clean(tmp_path):
    """REDESIGNED (finding 6): a clean candidate (verify ok), with its worktree PERSISTED on the
    run's own state, is NOT blocked by the gate: apply_revision — original signature, no integrity
    kwargs — proceeds exactly as it does today, reaching the gateway and recording the revision
    event.

    technical: candidate HEAD unchanged from the frozen contract_commit, candidate_worktree
    persisted on the run record -> apply_revision(run_id, plan, gateway, approver=approver) writes
    through a real gateway spy (one update_body call) and records a "revision" event with completed
    op-IDs == ["op-1"]. A direct verify_contract_integrity call against the same state is the
    independent oracle for "clean": it must report ok True, so the engine's internal gate decision
    is provably the SAME verdict a caller would get calling the check directly (not a coincidence of
    an unimplemented gate always proceeding).
    """
    from issueforge import contract, engine

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-clean",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    # Freeze under a REAL venv (autoload ON), the SAME policy the gate provisions under, so the frozen
    # external pins (pytest + its infra) match what the gate re-resolves — a host-provisioned freeze
    # would pin nothing and the venv-provisioned gate would read every infra dist as a phantom (#105 #1).
    _freeze(run, repo, provisioner=_real_pin({}))
    _seed_candidate_worktree(run, repo.path)

    oracle = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_real_pin({}),
    )
    assert oracle.ok is True

    gw = _SpyGateway()
    engine.apply_revision(run, _plan(), gw, approver=_approve_all)
    assert [c for c in gw.calls if c[0] == "update_body"]
    events = store.RunStore().replay_events(run)
    revisions = [e for e in events if e.get("transition") == "revision"]
    assert revisions and revisions[-1]["completed"] == ["op-1"]


def test_engine_gate_check_itself_never_invokes_the_ai_provider(tmp_path, monkeypatch):
    """REDESIGNED (finding 6): the gate's integrity check, run from inside apply_revision using the
    run's own persisted candidate_worktree, is exactly as deterministic as a direct
    verify_contract_integrity call — no provider invocation on the refusal path.

    technical: issueforge.providers.invoke raises if called; apply_revision on a violating candidate
    still raises the NAMED integrity error (not an AssertionError from a provider call).
    """
    import issueforge.providers as providers
    from issueforge import engine

    def _no_provider(*args, **kwargs):
        raise AssertionError(
            "apply_revision's integrity gate must never invoke the AI/provider seam"
        )

    monkeypatch.setattr(providers, "invoke", _no_provider)

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-noai",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    _seed_candidate_worktree(run, repo.path)

    with pytest.raises(Exception, match=r"(?i)protected_path_diff"):
        engine.apply_revision(run, _plan(), _ExplodingGateway(), approver=_approve_all)


def test_engine_gate_check_never_invokes_the_ai_provider_on_clean_path(tmp_path, monkeypatch):
    """Finding 5: the earlier no-AI engine coverage was refusal-only. The gate is exactly as
    deterministic on the CLEAN (proceeding) path as it is on the refusal path — no provider
    invocation while verifying a clean candidate and writing the approved revision through to the
    gateway.

    technical: issueforge.providers.invoke raises if called; apply_revision on a clean candidate
    still completes (gateway called, "revision" event recorded) without tripping it. A direct
    verify_contract_integrity call under the SAME patched provider is the independent oracle proving
    the check itself never calls out, matching the engine's internal decision.
    """
    import issueforge.providers as providers
    from issueforge import contract, engine

    def _no_provider(*args, **kwargs):
        raise AssertionError(
            "apply_revision's integrity gate must never invoke the AI/provider seam on the "
            "clean path"
        )

    monkeypatch.setattr(providers, "invoke", _no_provider)

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-noai-clean",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    # Freeze under a REAL venv (autoload ON), symmetric with the gate's provisioner, so the frozen infra
    # pins match what the gate re-resolves and the clean candidate is not phantom-blocked (#105 #1).
    _freeze(run, repo, provisioner=_real_pin({}))
    _seed_candidate_worktree(run, repo.path)

    oracle = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_real_pin({}),
    )
    assert oracle.ok is True

    gw = _SpyGateway()
    engine.apply_revision(run, _plan(), gw, approver=_approve_all)
    assert [c for c in gw.calls if c[0] == "update_body"]
    events = store.RunStore().replay_events(run)
    revisions = [e for e in events if e.get("transition") == "revision"]
    assert revisions and revisions[-1]["completed"] == ["op-1"]


def test_engine_apply_revision_proceeds_unchanged_with_no_frozen_manifest(tmp_path):
    """Finding 6 (backward compatibility, superseding the deleted mandatory-kwarg test): a run with
    NO frozen contract manifest at all — the pre-S13 shape, e.g. a plain buildable run that never
    went through freeze_contract — is UNCHANGED by S13. apply_revision, called with its ORIGINAL
    signature and with no candidate_worktree persisted either, still proceeds straight to the
    gateway: no integrity refusal, no TypeError. The gate activates ONLY once a run actually carries
    a frozen manifest.

    technical: a buildable run with no "contract-manifest.json" artifact and no persisted
    candidate_worktree field -> apply_revision(run_id, plan, gateway, approver=approver) reaches the
    gateway (one update_body call) and records a "revision" event, exactly as it did before S13.
    """
    from issueforge import engine
    from issueforge.contract import verify_contract_integrity
    from issueforge.paths import run_dir

    # This manifest-less path is real, ALREADY-BUILT apply_revision behavior (pre-S13). The only
    # thing keeping this test PENDING/red is the S13 gate symbol itself not existing yet.
    assert callable(verify_contract_integrity)

    run = _mk_run("gate-nomanifest")
    assert not (run_dir(run) / "contract-manifest.json").exists()

    gw = _SpyGateway()
    engine.apply_revision(run, _plan(), gw, approver=_approve_all)
    assert [c for c in gw.calls if c[0] == "update_body"]
    events = store.RunStore().replay_events(run)
    revisions = [e for e in events if e.get("transition") == "revision"]
    assert revisions and revisions[-1]["completed"] == ["op-1"]


@pytest.mark.parametrize("predicate", sorted(_GATE_SCENARIOS))
def test_engine_gate_blocks_every_predicate(tmp_path, monkeypatch, predicate):
    """R5 finding #24, REDESIGNED per finding 6: the gate must block on EVERY predicate, sourcing
    its integrity context entirely from the run's OWN PERSISTED state — never from a caller-supplied
    kwarg (there is no such kwarg on apply_revision at all). Each scenario constructs a real frozen
    manifest and a real HEAD mutation triggering exactly (or, per R1, at least among) the named
    predicate, PERSISTS ``candidate_worktree`` onto the run record via the store (never passes it to
    apply_revision), and proves apply_revision — called with its ORIGINAL pre-S13 shape — refuses
    BEFORE any gateway mutation, naming the predicate. A caller has no kwarg through which to supply
    a clean alternate worktree/adapter to bypass this: the only candidate the gate ever consults is
    the one THIS run's own persisted state names.

    technical: for each of {protected_path_diff, dep_hash, external_pin, recollection, invocation,
    ancestry, ast_backstop}, engine.apply_revision(run_id, plan, gateway, approver=approver) — with
    NO candidate_worktree/base_sha/adapter/provisioner argument — raises an exception whose text
    names the predicate, and no "revision" event is recorded. external_pin drifts the adapter the
    gate resolves for itself internally, via the same registry.resolve(framework="pytest",
    reporter="pytest") seam the S13 contract (§2) names as the adapter source — never an adapter
    passed by the caller.
    """
    from issueforge import engine
    from issueforge.adapters.base import registry

    run, repo, adapter_override = _GATE_SCENARIOS[predicate](tmp_path)
    _seed_candidate_worktree(run, repo.path)
    if adapter_override is not None:
        monkeypatch.setattr(registry, "resolve", lambda *, framework, reporter: adapter_override)

    with pytest.raises(Exception) as excinfo:
        engine.apply_revision(run, _plan(), _ExplodingGateway(), approver=_approve_all)
    assert predicate in str(excinfo.value)
    events = store.RunStore().replay_events(run)
    assert not any(e.get("transition") == "revision" for e in events)


# =============================================================== S13 remediation (#19) — T7/T9/T11
# PENDING acceptance tests closing the Codex NO-SHIP holes where a wrong/inert impl passes the
# existing suite (see s13 Phase A contract, findings #7/#9/#11). Each FAILS the branch impl today.


def test_engine_gate_runs_from_worktree_persisted_by_real_build_lifecycle(tmp_path):
    """Finding #7: the engine gate is inert in production because NOTHING production-side persists a
    run's ``candidate_worktree`` — only the ``_seed_candidate_worktree`` TEST helper does, so every
    green gate test is fake-green. Per D-T7 the REAL persistence seam is the ENGINE RUN-STAGE,
    ``engine._execute_stage``: it holds the store ``s`` and ``run_id`` and runs the run's stage, whose
    baseline establishment (``verify.establish_green_baseline``) returns a ``BaselineOutcome`` carrying
    the isolated worktree ``workspace.create_isolated_worktree`` created. Production MUST persist that
    worktree onto the run record FROM INSIDE ``_execute_stage`` (not by threading ``run_id``/``store``
    into ``establish_green_baseline`` — a caller-supplied context is the wrong seam). This drives that
    REAL run-stage seam — a real ``RunStore`` + ``run_id`` — via a stage that establishes the baseline
    through the DOCUMENTED injection seams (fake workspace + green runner, so no real remote/AI), and,
    WITHOUT ever calling ``_seed_candidate_worktree``, proves the persisted worktree is what the gate
    then consults to refuse a violating HEAD before any mutation.

    technical: engine._execute_stage(RunStore(), run, <stage that establishes the baseline>, record)
    persists store.RunStore().read(run)["candidate_worktree"] == the worktree the stage's
    BaselineOutcome carried (repo.path, from the injected create_isolated_worktree). Today
    _execute_stage RUNS the stage but persists nothing, so the field is absent — a MISSING-FIELD
    assertion failure, never a TypeError from a call signature. engine.apply_revision(run, plan,
    _ExplodingGateway(), approver=_approve_all) — no integrity kwargs — then raises naming
    "protected_path_diff" before touching the gateway, and records no "revision" event. Fail-closed
    control: a run WITH a frozen manifest but NO persisted candidate_worktree does NOT silently skip
    the gate — apply_revision raises and the gateway is touched for ZERO ops.
    """
    from issueforge import engine, verify
    from issueforge.adapters.base import BaselineStatus

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-lifecycle",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run, repo)
    # Advance HEAD so the worktree the lifecycle persists has a protected-path violation.
    _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})

    # Documented injection seams (workspace/run_baseline) so no real remote is needed — but the
    # worktree the lifecycle CREATES is a real checkout whose HEAD violates the frozen contract.
    fake_ws = SimpleNamespace(
        fetch_default_sha=lambda checkout: SimpleNamespace(ok=True, sha=repo.base_sha, reason=None),
        create_isolated_worktree=lambda checkout, sha: SimpleNamespace(
            ok=True, isolated=True, path=repo.path, reason=None
        ),
    )

    def _green_runner(worktree, command, *, adapter, provisioner):
        return SimpleNamespace(status=BaselineStatus.GREEN)

    def _baseline_stage(_record):
        # The run-stage establishes the baseline through the documented injection seams; the returned
        # BaselineOutcome carries the isolated worktree production must persist onto the run record.
        return verify.establish_green_baseline(
            repo.path,
            adapter=_adapter(),
            workspace=fake_ws,
            provisioner=_provisioner(),
            run_baseline=_green_runner,
            dispatch=None,
        )

    # The REAL engine run-stage seam, holding the store + run_id. PRODUCTION must persist
    # candidate_worktree here from the stage's BaselineOutcome; today _execute_stage runs the stage
    # but persists nothing, which is exactly the inert-gate hole this test pins.
    s = store.RunStore()
    engine._execute_stage(s, run, _baseline_stage, s.read(run))

    # Persisted by the run-stage lifecycle, NOT by a test helper (_seed_candidate_worktree unused).
    persisted = store.RunStore().read(run).get("candidate_worktree")
    assert persisted is not None and Path(persisted) == repo.path

    # The gate sources its candidate ENTIRELY from that persisted state and refuses before mutating.
    with pytest.raises(Exception, match=r"(?i)protected_path_diff"):
        engine.apply_revision(run, _plan(), _ExplodingGateway(), approver=_approve_all)
    assert not any(e.get("transition") == "revision" for e in store.RunStore().replay_events(run))

    # Fail-closed control: a frozen run with NO persisted candidate_worktree must refuse, never fall
    # through to a mutation — missing gate context is a refusal, not a pass-through.
    run2 = _mk_run("gate-lifecycle-failclosed")
    repo2 = _repo(
        tmp_path,
        "gate-lifecycle-failclosed",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    _freeze(run2, repo2)
    gw2 = _SpyGateway()
    with pytest.raises(Exception):
        engine.apply_revision(run2, _plan(), gw2, approver=_approve_all)
    assert gw2.calls == []
    assert not any(e.get("transition") == "revision" for e in store.RunStore().replay_events(run2))


def test_verify_after_successful_amendment_loads_the_new_manifest(tmp_path):
    """Finding #9: amend writes a content-addressed ``contract-manifest-<hash>.json``, but verify's
    ``_load_frozen_manifest`` always reads the fixed ``contract-manifest.json`` — so an AUTHORIZED
    amendment never takes effect: verify keeps reloading the pre-amend manifest M0 and re-flags the
    now-authorized change. This freezes M0, advances HEAD across a real boundary change, amends to a
    new authoritative manifest M1 at the new commit, and proves a re-run verify passes.

    technical: freeze M0 (protects tests/test_new.py at C0); a real edit advances HEAD to C1; verify
    at C1 against M0 flags ("protected_path_diff","tests/test_new.py"); amend_contract(reason="#19: ...",
    diff_text=<real C0..C1 diff>, ...) succeeds with contract_commit == C1; a re-run
    verify_contract_integrity for the SAME run reports ok is True (M1 authoritative). Control: the
    prior M0 artifact stays retained byte-for-byte (audit history intact).
    """
    from issueforge import contract

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "verify-after-amend",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    original_bytes = original.artifact_path.read_bytes()
    assert "tests/test_new.py" in original.manifest["contract_paths"]

    c0 = repo.candidate_sha
    c1 = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    repo.candidate_sha = c1

    # Pre-amend: verify at C1 against M0 flags the as-yet-unauthorized boundary change.
    pre = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )
    assert pre.ok is False
    assert ("protected_path_diff", "tests/test_new.py") in {
        (v.predicate, v.detail) for v in pre.violations
    }

    # Authorize it: amend to a genuinely new authoritative manifest M1 at contract_commit C1.
    _seed_freeze_evidence(run, repo, head=c1)
    diff_text = _real_diff(repo.path, c0, c1)
    amended = contract.amend_contract(
        run,
        reason="#19: authorized boundary change",
        diff_text=diff_text,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all,
    )
    assert amended.approved is True
    assert amended.manifest["contract_commit"] == c1

    # M1 is now authoritative: the previously-flagged change is authorized, so verify passes.
    post = contract.verify_contract_integrity(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
    )
    assert post.ok is True

    # Audit history intact: the prior M0 artifact remains retained, byte-for-byte.
    assert original.artifact_path.read_bytes() == original_bytes


def test_amend_refused_on_noop_empty_diff_unchanged_head(tmp_path):
    """Finding #11: amend_contract has no guard that the diff is non-empty / HEAD advanced / the new
    manifest hash differs. With HEAD unchanged from contract_commit the real diff is empty, so an
    empty diff_text matches it and approval succeeds — persisting the original bytes as a "new"
    content-addressed artifact. A no-op amendment must instead be REFUSED and write nothing.

    technical: freeze M0 at HEAD C0; HEAD is NOT advanced (real diff is ""); amend_contract(reason=
    "#19: no-op", diff_text="", ...) is refused (raises ValueError OR returns FreezeResult(approved=
    False, manifest=None)); no new manifest artifact is written (only contract-manifest.json exists);
    the on-disk manifest bytes and sha256 are unchanged.
    """
    from issueforge import contract
    from issueforge.paths import run_dir

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "amend-noop",
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )
    original = _freeze(run, repo)
    before = _manifest_bytes(run)

    # HEAD is NOT advanced: candidate HEAD == contract_commit C0, so the real diff is empty.
    assert _real_diff(repo.path, repo.candidate_sha, repo.candidate_sha) == ""

    refused = False
    try:
        result = contract.amend_contract(
            run,
            reason="#19: no-op",
            diff_text="",
            candidate_worktree=repo.path,
            base_sha=repo.base_sha,
            adapter=_adapter(),
            provisioner=_provisioner(),
            approver=_approve_all,
        )
        assert result.approved is False
        assert result.manifest is None
        refused = True
    except ValueError:
        refused = True
    assert refused

    # No new manifest artifact was written — only the original contract-manifest.json exists.
    manifests = sorted(p.name for p in run_dir(run).glob("contract-manifest*.json"))
    assert manifests == ["contract-manifest.json"]
    # The retained manifest bytes and hash are unchanged.
    assert _manifest_bytes(run) == before == original.artifact_path.read_bytes()
    assert hashlib.sha256(_manifest_bytes(run)).hexdigest() == original.manifest_hash


# ============================================================ round-2 remediation (#105, Codex NO-SHIP/6)
# Finding #3: engine._integrity_gate derives its provisioner's frozen external pins from the ORIGINAL
# contract-manifest.json (M0), while verify_contract_integrity loads the LATEST approved amended
# manifest (M1) via _load_frozen_manifest. After an approved amendment that changes an external pin,
# the gate provisions M0's pins but verify checks against M1's, so a legitimate M1-valid candidate is
# FALSE-BLOCKED. Committed xfail(strict=True) until Phase B derives the gate's pins from the active
# (amended) manifest too.


def _real_pin(pins: dict[str, str]):
    """A provisioner building a REAL venv with ``pins`` installed (mirrors
    test_integrity_verify._real_pin_provisioner), so external identity resolves from the provisioned
    interpreter — the only channel that makes the frozen vs amended external-pin values differ."""

    def _provision(worktree, frozen_deps=None):
        merged = dict(pins)
        if frozen_deps:
            merged.update(frozen_deps)
        return _adapter().provision_environment(worktree, merged)

    return _provision


def test_engine_gate_provisions_from_the_amended_manifest_not_the_original(tmp_path):
    """Finding #3: after an approved amendment changes an external pin (M0 platformdirs 4.10.0 ->
    M1 4.9.0), the engine gate must provision under the ACTIVE amended manifest's pins, exactly as
    verify loads them. Today the gate reads M0's contract-manifest.json for its provisioner pins while
    verify loads M1, so the gate installs 4.10.0, verify expects 4.9.0, and a candidate that is clean
    under the approved amendment is FALSE-BLOCKED with an external_pin violation. The fix makes the gate
    derive its pins from _load_frozen_manifest (M1); then apply_revision proceeds cleanly to the gateway."""
    from issueforge import contract, engine

    run = _mk_run()
    repo = _repo(
        tmp_path,
        "gate-amended-pins",
        candidate_files={
            "tests/test_new.py": "def test_x():\n    assert 1 == 2\n",
            "tests/conftest.py": "import platformdirs  # noqa: F401\n",
            # gitignore bytecode so collection's regenerated .pyc stays UNTRACKED (as in any real repo)
            # — otherwise _commit's `git add -A` would track a stale .pyc and trip the freeze TOCTOU.
            ".gitignore": "__pycache__/\n*.pyc\n",
        },
    )
    _seed_freeze_evidence(run, repo)
    # M0: freeze with platformdirs pinned 4.10.0.
    m0 = contract.freeze_contract(
        run,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_real_pin({"platformdirs": "4.10.0"}),
        approver=_approve_all,
    )
    assert ("platformdirs", "4.10.0") in {(d, v) for d, v in m0.manifest["external_pins"]}

    # Advance HEAD with a real contract change, then amend to M1 with platformdirs 4.9.0.
    contract_commit = repo.candidate_sha
    new_head = _commit(repo.path, {"tests/test_new.py": "def test_x():\n    assert 1 == 3\n"})
    repo.candidate_sha = new_head
    _seed_freeze_evidence(run, repo, head=new_head)
    diff = _real_diff(repo.path, contract_commit, new_head)
    m1 = contract.amend_contract(
        run,
        reason="#19: re-pin platformdirs after resolving an authorized dependency change",
        diff_text=diff,
        candidate_worktree=repo.path,
        base_sha=repo.base_sha,
        adapter=_adapter(),
        provisioner=_real_pin({"platformdirs": "4.9.0"}),
        approver=_approve_all,
    )
    assert m1.approved is True
    assert ("platformdirs", "4.9.0") in {(d, v) for d, v in m1.manifest["external_pins"]}
    _seed_candidate_worktree(run, repo.path)

    # The candidate is clean under the APPROVED amendment (M1). The engine gate must not false-block it.
    gw = _SpyGateway()
    engine.apply_revision(run, _plan(), gw, approver=_approve_all)
    assert [c for c in gw.calls if c[0] == "update_body"], (
        "the gate false-blocked an M1-valid candidate because it provisioned the ORIGINAL manifest's "
        "pins instead of the active amended manifest's"
    )
