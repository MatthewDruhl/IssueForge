"""Committed PENDING acceptance suite for #16 (S10) — AI-authored acceptance tests + the
DETERMINISTIC red proof. The load-bearing integrity control: a run produces AI-authored
acceptance tests PLUS machine-checked evidence that they collected, executed, and FAILED in the
call phase on a healthy baseline at a bound sha, and REFUSES to proceed otherwise.

``issueforge.contract`` does not exist yet, so every test imports it INSIDE its body and is
``@pytest.mark.xfail(strict=True, reason="PENDING (#16)")`` (an AttributeError/ImportError while
pending). The git/target/store scaffolding below touches no unbuilt IssueForge code, so it lives
at module top. Dual-layer docstrings: a plain-English behavior line first, then the
``technical (contract):`` golden values.

Governing design (mirrors test_shaper.py / test_verify.py): assert OBSERVABLE behavior through
REAL seams. Each scenario is a REAL two-commit git repo (a base checkout at ``base_sha`` and a
contract-candidate worktree) whose authored test file genuinely produces the target condition; the
red proof really provisions (a host-side injected provisioner), collects (``canonical_collect``),
executes (report-log), and classifies (``classify``). NO hand-fed NodeRecord/Evidence. Every
verdict, record, pause, and redaction is read back through ``store.RunStore().read()`` /
``replay_events()`` / the persisted artifacts. The scenario shapes below were validated against a
real host pytest run before the contract was frozen, so each authored test reaches its intended
phase/outcome (e.g. empty ``parametrize`` collects a ``[NOTSET]`` node skipped at setup, a non-
strict XPASS carries ``wasxfail``, a call-phase ImportError surfaces as a ModuleNotFoundError).

The S10 interface this suite AUTHORS (ATDD):

``issueforge.contract``:
- ``prove_red(run_id, *, targeted_ids, base_checkout, candidate_worktree, base_sha, adapter,
  provisioner=None, store=None, secrets=frozenset()) -> RedProof`` — the deterministic red proof.
  Ordered, each rejection PAUSING the run with its SPECIFIC reason (a red is never the else branch):
  (1) verify ``base_sha`` against ``base_checkout``'s committed origin-default HEAD — a mismatch is
  ``"sha_mismatch"``; (2) the base suite is GREEN at ``base_sha`` (a red base is
  ``"baseline_not_green"``); (3) ``BASE_IDS = canonical_collect(base_checkout).ids`` and
  ``CANDIDATE_IDS = canonical_collect(candidate_worktree).ids`` — a candidate whose collection hard-
  errors is ``"import_error"`` (an ImportError at collection) / ``"collection_error"`` (syntax/config)
  / ``"no_tests_collected"`` (nothing collected); (4) ``select_baseline`` — a vanished base id is
  ``"base_id_disappeared"``; (5) a targeted id that REUSES a base id is ``"reused_base_id"``; (6) run
  EVERY id in ``BASE_IDS`` at the candidate — still-green required (author breakage is
  ``"baseline_not_green"``); (7) the collection-IDENTITY check ``set(ADDED) == set(targeted_ids)``
  (SET EQUALITY, not a count; a same-cardinality mismatch still fails) — else ``"missing_targeted_id"``;
  (8) the call-phase discrimination over EVERY targeted id — each must be a call-phase behavioral
  failure whose exception is NOT an ImportError. A non-red targeted outcome rejects with its reason:
  ``"import_error"`` (call-phase ImportError, invalid at every phase), ``"setup_error"``,
  ``"teardown_error"``, ``"all_skipped"``, ``"xpass"``, ``"empty_parametrize"``, or ``"not_red"``
  (a passing targeted test). Only when ALL targeted ids are valid call-phase reds does it ACCEPT
  ``"behavioral_red"``.
  ACCEPT: run stays ``running``, ``record["red_proof"]`` persisted, a ``red_proof`` event
  ``outcome="accepted"``. REJECT: run -> ``paused``; a ``red_proof`` event ``outcome="rejected"`` with
  ``reason``; a redacted diagnostic still persisted (the failure path).
  ``RedProof`` (frozen): ``accepted: bool``, ``reason: str``, ``records: tuple[RedRecord, ...]``,
  ``base_sha: str``, ``added_ids: tuple[str, ...]``.
  ``RedRecord`` (frozen): ``nodeid: str``, ``exception_type: str`` (the ACTUAL raised type, never
  coerced), ``assertion_line: int | None`` (the real source line), ``message: str`` (REDACTED via
  S4's writer).
- ``author_tests(run_id, *, author, existing_ids, dispositions, store=None) -> object`` — the
  authoring entry. Calls ``engine.enter_authoring`` FIRST (deriving ``revision_applied`` from the
  run's applied-revision ledger) and refuses (propagates ``state.IllegalTransition``) unless the run
  is buildable AND revision-applied — authoring NOTHING and NEVER invoking ``author``. It then refuses
  (pauses, ``author`` uninvoked) when any id in ``existing_ids`` lacks a keep/revise/supersede
  disposition (discover-before-authoring). Only on a buildable, revision-applied run with complete
  dispositions does it invoke ``author`` and record an ``authoring`` event.
- ``reject_false_green(test_source) -> str | None`` — the suite-level anti-false-green discipline
  (ported from ``validate_pending_markers``' catalogue): a reason string when a "blocked" test is a
  false green (asserts only a passing/zero exit, or is missing the keyword OR the offending test
  name); ``None`` only when it asserts a NON-zero exit AND a keyword AND the offending test name.
- ``example_reproduced_verbatim(issue_example, fixtures) -> bool`` — the verbatim-example fixture
  rule: True only when EXACTLY ONE fixture reproduces ``issue_example`` byte-for-byte.

``issueforge.adapters.pytest_adapter.PytestAdapter``:
- ``select_baseline(base_ids, candidate_ids) -> BaselineSelection`` — ``added`` (sorted
  candidate-minus-base, COMPUTED not declared), ``missing`` (base ids absent from candidate), ``ok``
  (== ``missing`` empty). NEVER a ``collected(base) - new_ids`` subtraction.
  ``BaselineSelection`` (frozen): ``added: tuple``, ``missing: tuple``, ``ok: bool``.

``issueforge.engine`` (existing S9 seam, reused, not rebuilt):
- ``enter_authoring(run_id, *, revision_applied)`` — the buildable + revision-applied gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import store
from issueforge.paths import state_root

# The baseline command as it appears in .issueforge.toml, minus the interpreter — the runner
# supplies the interpreter from the PROVISIONED environment, never the candidate's.
_CONFIG = 'baseline = ["-m", "pytest"]\nframework = "pytest"\n'
_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]

# The standard base suite: TWO genuinely-green tests, so "runs EVERY base id" and the
# no-silent-subtraction guarantees are exercised (a one-base-id fixture could not distinguish an
# implementation that runs only the first base id).
_BASE_FILE = "tests/test_base.py"
_BASE_A = "tests/test_base.py::test_base_a"
_BASE_B = "tests/test_base.py::test_base_b"
_BASE_IDS = (_BASE_A, _BASE_B)
_BASE_FILES = {
    _BASE_FILE: "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert True\n"
}

_NEW_ID = "tests/test_new.py::test_new"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *_GIT_ID, *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))


def _scenario(
    root: Path,
    name: str,
    *,
    candidate_files: dict[str, str],
    base_files: dict[str, str] | None = None,
    remove_from_candidate: tuple[str, ...] = (),
    base_local_red_head: bool = False,
) -> SimpleNamespace:
    """Build a REAL two-commit repo: a frozen base checkout at ``base_sha`` and a candidate worktree.

    The base commit carries ``base_files`` (default: the two-test green base) plus ``.issueforge.toml``
    and an ``origin`` whose default branch resolves to that commit; ``base_checkout`` is a full copy
    frozen there. The candidate commit overlays ``candidate_files`` (and deletes
    ``remove_from_candidate``) — the AI-authored contract candidate — as a LATER local sha, so a test
    can prove the red binds to the verified origin-default ``base_sha``, not the candidate's local HEAD.

    ``base_local_red_head=True`` advances ``base_checkout``'s LOCAL ``HEAD`` to a commit that reddens
    the base suite while leaving ``refs/remotes/origin/main`` at the green ``base_sha`` — so a proof
    that reads the base checkout's working tree / local ``HEAD`` (instead of checking out the bound
    origin-default sha) sees a red base, while a correct proof stays green.
    """
    base_files = dict(_BASE_FILES if base_files is None else base_files)
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(_CONFIG)
    _write(repo, base_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "remote", "add", "origin", "git@github.com:Owner/IssueForge.git")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    base_checkout = root / f"{name}-base"
    shutil.copytree(repo, base_checkout)
    if base_local_red_head:
        # Redden the base suite on base_checkout's LOCAL HEAD only; origin/main stays at base_sha.
        (base_checkout / _BASE_FILE).write_text(
            "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert False\n"
        )
        _git(base_checkout, "add", "-A")
        _git(base_checkout, "commit", "-qm", "local-red-head")
    for rel in remove_from_candidate:
        (repo / rel).unlink()
    _write(repo, candidate_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate")
    candidate_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return SimpleNamespace(
        base_checkout=base_checkout,
        candidate_worktree=repo,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )


def _provisioner():
    """A host-side provisioner seam: the host interpreter + an ALLOWLIST env, no network denial.

    It carries no ``denies_network`` marker, so the red proof runs on the host (fast, no docker),
    exactly like test_verify.py's ``_clean_provisioner`` — never a copy of the candidate's environ.
    Plugin autoload is disabled so a developer's installed pytest plugins cannot alter collection.
    """
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


def _adapter():
    from issueforge.adapters.pytest_adapter import PytestAdapter

    return PytestAdapter()


def _mk_run(run_id: str = "run-1", *, buildable: bool = True, revision_applied: bool = True) -> str:
    """Mint a run record so the red proof / authoring has a real RunStore run to persist onto.

    A buildable, revision-applied run is ``status == "running"`` with a ``shape`` whose
    ``classification == "buildable"`` and a ``revision_ledger`` present (S20's applied-revision
    marker). Toggling the flags produces the ineligible variants the authoring gate must refuse.
    """
    shape = {"classification": "buildable" if buildable else "blocked", "write_scope": []}
    record = {"status": "running", "shape": shape}
    if revision_applied:
        record["revision_ledger"] = {"op-1": "fingerprint"}
    store.RunStore().apply(run_id, lambda _r: record, create=True)
    return run_id


def _prove(run_id, scen, *, targeted_ids, base_sha=None, secrets=frozenset()):
    from issueforge import contract

    return contract.prove_red(
        run_id,
        targeted_ids=tuple(targeted_ids),
        base_checkout=scen.base_checkout,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha if base_sha is None else base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        secrets=frozenset(secrets),
    )


def _run_files() -> list[Path]:
    """Every persisted file under the isolated state root's ``runs`` tree."""
    runs = Path(state_root()) / "runs"
    return [p for p in runs.rglob("*") if p.is_file()] if runs.exists() else []


def _assert_rejected(run_id, rp, reason):
    """A uniform rejection contract applied to EVERY reject path: the returned verdict, the paused
    run status, and a persisted ``red_proof`` event carrying outcome ``rejected`` with the SAME
    specific reason — so no rejection can quietly leave the run running or omit its reason."""
    assert rp.accepted is False
    assert rp.reason == reason
    assert store.RunStore().read(run_id)["status"] == "paused"
    events = store.RunStore().replay_events(run_id)
    assert any(
        e.get("transition") == "red_proof"
        and e.get("outcome") == "rejected"
        and e.get("reason") == reason
        for e in events
    ), f"no rejected red_proof event with reason {reason!r}"


# =============================================================== A. Collection identity (set equality)


def test_collection_identity_ties_targeted_added_and_executed(tmp_path):
    """Acceptance requires the targeted set, the computed ADDED set, and the executed/recorded set to
    be IDENTICAL — proven by identity across TWO new ids, not a count.

    technical (contract): a candidate authoring two genuine reds (test_x, test_y); targeted_ids ==
    {x, y}. prove_red -> accepted True, reason "behavioral_red", set(added_ids) == {x, y}, and the
    record node-id set == {x, y} (targeted == added == recorded).
    """
    x = "tests/test_new.py::test_x"
    y = "tests/test_new.py::test_y"
    scen = _scenario(
        tmp_path,
        "identity",
        candidate_files={
            "tests/test_new.py": "def test_x():\n    assert 1 == 2\n\n\ndef test_y():\n    assert 2 == 3\n"
        },
    )
    rp = _prove(_mk_run(), scen, targeted_ids=(x, y))
    assert rp.accepted is True
    assert rp.reason == "behavioral_red"
    assert set(rp.added_ids) == {x, y}
    assert {r.nodeid for r in rp.records} == {x, y}


def test_same_cardinality_id_mismatch_rejected(tmp_path):
    """A SAME-SIZE targeted/ADDED mismatch is rejected — equality is by identity, never cardinality.

    technical (contract): the candidate authors ids {x, y} while targeted_ids is {x, z} (both size 2,
    different members). prove_red -> rejected, reason "missing_targeted_id"; a count-based
    ``len(ADDED) == len(targeted)`` implementation would wrongly accept.
    """
    x = "tests/test_new.py::test_x"
    z = "tests/test_new.py::test_z"
    scen = _scenario(
        tmp_path,
        "same-card",
        candidate_files={
            "tests/test_new.py": "def test_x():\n    assert 1 == 2\n\n\ndef test_y():\n    assert 2 == 3\n"
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(x, z))
    _assert_rejected(run, rp, "missing_targeted_id")


def test_missing_targeted_id_rejected_even_when_collected_nonzero(tmp_path):
    """A targeted id the candidate never collects is rejected — a nonzero count is not identity.

    technical (contract): the candidate authors a DIFFERENT test than targeted (collected == 1,
    nonzero); targeted_ids names an absent id. prove_red -> rejected, reason "missing_targeted_id".
    """
    scen = _scenario(
        tmp_path,
        "missing-id",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=("tests/test_new.py::test_absent",))
    _assert_rejected(run, rp, "missing_targeted_id")


# =============================================================== B. Call-phase discrimination


@pytest.mark.parametrize(
    "name, files, exc",
    [
        (
            "attr_local_module",
            {
                "pkgmod.py": "thing = object()\n",
                "tests/test_new.py": "from pkgmod import thing\n\n\ndef test_new():\n    thing.no_such_symbol_xyz()\n",
            },
            "AttributeError",
        ),
        (
            "notimplemented",
            {"tests/test_new.py": "def test_new():\n    raise NotImplementedError\n"},
            "NotImplementedError",
        ),
        (
            "assertion",
            {"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
            "AssertionError",
        ),
    ],
)
def test_call_phase_behavioral_failure_is_valid_red(tmp_path, name, files, exc):
    """ANY call-phase behavioral failure on a SUCCESSFULLY-IMPORTED module is a VALID red — phase-
    based, NOT whitelisted to AssertionError (the genuine-TDD trap).

    technical (contract): the module under test imports cleanly; the missing behavior is exercised
    THROUGH it and fails in the CALL phase via AttributeError (on a local application module),
    NotImplementedError, or a plain assertion. Each -> accepted True, reason "behavioral_red", and
    the record's exception_type is the ACTUAL raised type (never coerced to AssertionError).
    """
    scen = _scenario(tmp_path, f"valid-{name}", candidate_files=files)
    rp = _prove(_mk_run(), scen, targeted_ids=(_NEW_ID,))
    assert rp.accepted is True
    assert rp.reason == "behavioral_red"
    assert any(r.nodeid == _NEW_ID and r.exception_type == exc for r in rp.records)


def test_collection_import_error_is_invalid(tmp_path):
    """A COLLECTION-phase ImportError (the authored module is not importable) is INVALID red.

    technical (contract): the authored file imports a nonexistent module at top level, so
    --collect-only errors (exit 2) with an ImportError, distinct from a syntax error. prove_red ->
    rejected, reason "import_error".
    """
    scen = _scenario(
        tmp_path,
        "collect-import",
        candidate_files={
            "tests/test_new.py": "import module_absent_xyz9\n\n\ndef test_new():\n    assert False\n"
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "import_error")


def test_call_phase_import_error_is_invalid(tmp_path):
    """A CALL-phase ImportError is STILL invalid — import errors are invalid at EVERY phase.

    technical (contract): the authored test raises ImportError from its body (the module under test
    is not importable at call time). pytest marks the call "failed", but the discriminator excludes
    ImportError BY TYPE (it does not whitelist AssertionError). prove_red -> rejected, reason
    "import_error" (the earlier "call-phase ImportError is VALID" overshoot is WRONG).
    """
    scen = _scenario(
        tmp_path,
        "call-import",
        candidate_files={
            "tests/test_new.py": 'def test_new():\n    raise ImportError("cannot import feature")\n'
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "import_error")


def test_syntax_error_collection_is_invalid(tmp_path):
    """A syntax error at collection is INVALID red — distinguished from an import error.

    technical (contract): the authored file has a syntax error, so --collect-only errors (exit 2)
    with a SyntaxError, not an ImportError. prove_red -> rejected, reason "collection_error".
    """
    scen = _scenario(
        tmp_path,
        "syntax",
        candidate_files={"tests/test_new.py": "def test_new(:\n    pass\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "collection_error")


def test_missing_fixture_setup_error_is_invalid(tmp_path):
    """A missing-fixture SETUP-phase error is INVALID — the failure is not in the call phase.

    technical (contract): the authored test requests an undefined fixture, so pytest errors in SETUP.
    prove_red -> rejected, reason "setup_error".
    """
    scen = _scenario(
        tmp_path,
        "setup-error",
        candidate_files={
            "tests/test_new.py": "def test_new(undefined_fixture_xyz):\n    assert False\n"
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "setup_error")


def test_teardown_failure_with_passing_call_is_invalid(tmp_path):
    """A TEARDOWN-phase failure with a PASSING call is INVALID — infra breakage, not a behavioral red.

    technical (contract): the authored test's call PASSES but its fixture raises during teardown, so
    the only failure is in the teardown phase. prove_red -> rejected, reason "teardown_error" (never
    accepted off the passing call).
    """
    scen = _scenario(
        tmp_path,
        "teardown",
        candidate_files={
            "tests/test_new.py": (
                "import pytest\n\n\n@pytest.fixture\ndef f():\n    yield\n    raise RuntimeError('td')\n\n\n"
                "def test_new(f):\n    assert True\n"
            )
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "teardown_error")


def test_passing_targeted_test_is_not_red(tmp_path):
    """An ordinary PASSING authored test is not a red and is rejected — red is never the else branch.

    technical (contract): the authored targeted test passes (assert True). prove_red -> rejected,
    reason "not_red" (a passing call is not a behavioral failure).
    """
    scen = _scenario(
        tmp_path,
        "passing",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert True\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "not_red")


def test_mixed_targeted_outcomes_reject_when_any_is_not_red(tmp_path):
    """EVERY targeted id must be a valid call-phase red — one red plus one pass is rejected.

    technical (contract): two targeted tests, one a genuine call-phase red and one PASSING. An
    ``any(call failure)`` implementation would accept; prove_red requires ALL targeted units to be red
    -> rejected, reason "not_red".
    """
    red = "tests/test_new.py::test_red"
    ok = "tests/test_new.py::test_ok"
    scen = _scenario(
        tmp_path,
        "mixed",
        candidate_files={
            "tests/test_new.py": "def test_red():\n    assert 1 == 2\n\n\ndef test_ok():\n    assert True\n"
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(red, ok))
    _assert_rejected(run, rp, "not_red")


# =============================================================== C. Baseline-still-green by ID-set


def test_every_base_id_runs_at_candidate_second_break_is_caught(tmp_path):
    """The baseline runs EVERY base id at the candidate — a break in the SECOND base id is caught.

    technical (contract): the candidate keeps test_base_a green but rewrites test_base_b to fail while
    adding a red. An implementation that runs only the first base id would miss the break; prove_red ->
    rejected, reason "baseline_not_green".
    """
    scen = _scenario(
        tmp_path,
        "second-break",
        candidate_files={
            _BASE_FILE: "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert False\n",
            "tests/test_new.py": "def test_new():\n    assert 1 == 2\n",
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "baseline_not_green")


def test_base_suite_red_at_bound_sha_is_refused(tmp_path):
    """A red BASE suite at the bound sha refuses — a healthy baseline at ``base_sha`` is required.

    technical (contract): the base commit itself carries a failing test (the baseline is not green at
    ``base_sha``), and the candidate adds a red. prove_red -> rejected, reason "baseline_not_green"
    (the red proof requires a healthy baseline at the bound sha before authoring).
    """
    scen = _scenario(
        tmp_path,
        "base-red",
        base_files={
            _BASE_FILE: "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert False\n"
        },
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "baseline_not_green")


def test_base_id_disappeared_is_hard_failure(tmp_path):
    """A preexisting base id that DISAPPEARED at the candidate is a hard failure, not a green baseline.

    technical (contract): the candidate DELETES the base test file (BASE_IDS not subset-of
    CANDIDATE_IDS) while adding a red. prove_red -> rejected, reason "base_id_disappeared".
    """
    scen = _scenario(
        tmp_path,
        "disappeared",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
        remove_from_candidate=(_BASE_FILE,),
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, "base_id_disappeared")


def test_authored_test_reusing_base_id_is_hard_failure(tmp_path):
    """An authored test that REUSES a preexisting base id is a hard failure — caught, NEVER subtracted.

    technical (contract): targeted_ids names the preexisting ``_BASE_A`` (a reuse, not a genuinely new
    id) while the other base id ``_BASE_B`` stays protected. prove_red -> rejected, reason
    "reused_base_id" — the reused id is rejected, not silently removed from the protected baseline set
    (a naive candidate-minus-new subtraction is unsound and must not be used).
    """
    scen = _scenario(
        tmp_path,
        "reused",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_BASE_A,))
    _assert_rejected(run, rp, "reused_base_id")


def test_select_baseline_added_is_computed_not_declared(tmp_path):
    """The adapter's baseline selection COMPUTES ADDED = candidate - base and flags a disappeared id.

    technical (contract): select_baseline(("a::t","b::t"), ("a::t","b::t","c::t")) -> added ==
    ("c::t",), missing == (), ok is True; select_baseline(("a::t","b::t"), ("a::t","c::t")) -> missing
    == ("b::t",), ok is False. ADDED is computed, never declared or a candidate-minus-new subtraction.
    """
    adapter = _adapter()
    keep = adapter.select_baseline(("a::t", "b::t"), ("a::t", "b::t", "c::t"))
    assert tuple(keep.added) == ("c::t",)
    assert tuple(keep.missing) == ()
    assert keep.ok is True
    drop = adapter.select_baseline(("a::t", "b::t"), ("a::t", "c::t"))
    assert tuple(drop.missing) == ("b::t",)
    assert drop.ok is False


# =============================================================== D. Third-state rejections


@pytest.mark.parametrize(
    "name, body, reason",
    [
        ("no_tests", "# no test functions here\nVALUE = 1\n", "no_tests_collected"),
        (
            "all_skipped",
            'import pytest\n\n\ndef test_new():\n    pytest.skip("later")\n',
            "all_skipped",
        ),
        (
            "xpass",
            'import pytest\n\n\n@pytest.mark.xfail(reason="p")\ndef test_new():\n    assert True\n',
            "xpass",
        ),
        (
            "empty_param",
            'import pytest\n\n\n@pytest.mark.parametrize("x", [])\ndef test_new(x):\n    assert False\n',
            "empty_parametrize",
        ),
    ],
)
def test_broken_states_rejected(tmp_path, name, body, reason):
    """The BROKEN third states — zero collected, all-skipped, XPASS, empty parametrize — are REJECTED.

    technical (contract): each candidate authors the named broken shape (XPASS carries pytest's
    ``wasxfail``; empty parametrize collects a ``[NOTSET]`` node skipped at setup). prove_red ->
    rejected with the state's own token; NONE is ever accepted as red.
    """
    scen = _scenario(tmp_path, f"broken-{name}", candidate_files={"tests/test_new.py": body})
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    _assert_rejected(run, rp, reason)


# =============================================================== E. Red-evidence record (accuracy)


def test_red_records_carry_accurate_type_line_and_message(tmp_path):
    """Each canonical record carries the ACCURATE exception type, the real source line, and the message.

    technical (contract): two targeted assertion reds on the EXACT source lines 2 and 7 of the
    authored file, one with an explicit message. Each record has exception_type "AssertionError";
    by_id[a].assertion_line == 2 and by_id[b].assertion_line == 7 (fabricated or constant line numbers
    fail); and the explicitly-messaged failure's message contains its source fragment "boom detail".
    """
    a = "tests/test_new.py::test_a"
    b = "tests/test_new.py::test_b"
    scen = _scenario(
        tmp_path,
        "record-accuracy",
        candidate_files={
            # Lines: 1 def test_a / 2 assert (a) / 3-4 blank / 5 def test_b / 6 x=5 / 7 assert (b).
            "tests/test_new.py": (
                'def test_a():\n    assert 1 == 2, "boom detail"\n\n\n'
                "def test_b():\n    x = 5\n    assert x == 6\n"
            )
        },
    )
    rp = _prove(_mk_run(), scen, targeted_ids=(a, b))
    by_id = {r.nodeid: r for r in rp.records}
    assert by_id[a].exception_type == "AssertionError"
    assert by_id[b].exception_type == "AssertionError"
    assert by_id[a].assertion_line == 2
    assert by_id[b].assertion_line == 7
    assert "boom detail" in by_id[a].message


def test_red_proof_persisted_as_permanent_manifest_artifact(tmp_path):
    """The red proof is a PERMANENT manifest artifact plus an append-only event.

    technical (contract): after an accepted proof, RunStore().read(run)["red_proof"] holds the verdict
    (accepted True, reason "behavioral_red", base_sha, records) and replay_events(run) has a
    "red_proof" event with outcome "accepted".
    """
    scen = _scenario(
        tmp_path,
        "persist",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    _prove(run, scen, targeted_ids=(_NEW_ID,))
    manifest = store.RunStore().read(run)
    assert manifest["red_proof"]["accepted"] is True
    assert manifest["red_proof"]["reason"] == "behavioral_red"
    assert manifest["red_proof"]["base_sha"] == scen.base_sha
    events = store.RunStore().replay_events(run)
    assert any(
        e.get("transition") == "red_proof" and e.get("outcome") == "accepted" for e in events
    )


def test_red_verdict_is_fully_rederivable(tmp_path):
    """The per-unit verdict is RE-DERIVABLE: proving the same candidate commit twice yields the SAME
    complete canonical records.

    technical (contract): two prove_red runs over the same candidate produce identical verdict, reason,
    base_sha, and the same full per-unit records (nodeid, exception_type, assertion_line, message) —
    fidelity from re-derivability, not from storing raw text once.
    """
    scen = _scenario(
        tmp_path,
        "rederive",
        candidate_files={"tests/test_new.py": 'def test_new():\n    assert 1 == 2, "detail"\n'},
    )
    first = _prove(_mk_run("run-a"), scen, targeted_ids=(_NEW_ID,))
    second = _prove(_mk_run("run-b"), scen, targeted_ids=(_NEW_ID,))
    assert (first.accepted, first.reason, first.base_sha) == (
        second.accepted,
        second.reason,
        second.base_sha,
    )

    def _canon(rp):
        return sorted((r.nodeid, r.exception_type, r.assertion_line, r.message) for r in rp.records)

    assert _canon(first) == _canon(second)


def test_permanent_manifest_holds_canonical_record_not_raw_dump(tmp_path):
    """The WHOLE permanent manifest holds only the canonical record — never a raw pytest output dump.

    technical (contract): after an accepted proof, each record["red_proof"]["records"] entry has
    exactly the keys {nodeid, exception_type, assertion_line, message}, and the ENTIRE manifest text
    contains no raw pytest section banner ("=" * 8 or "short test summary info"). Raw output, if kept
    at all, is an expiring redacted artifact, never the permanent manifest.
    """
    scen = _scenario(
        tmp_path,
        "canonical",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    _prove(run, scen, targeted_ids=(_NEW_ID,))
    manifest = store.RunStore().read(run)
    entry = next(r for r in manifest["red_proof"]["records"] if r["nodeid"] == _NEW_ID)
    assert set(entry) == {"nodeid", "exception_type", "assertion_line", "message"}
    manifest_text = (Path(state_root()) / "runs" / run / "manifest.json").read_text()
    assert "========" not in manifest_text
    assert "short test summary info" not in manifest_text


# =============================================================== F. SHA-binding (real verification)


def test_red_evidence_is_sha_bound(tmp_path):
    """An accepted red is SHA-BOUND to the verified base sha in both the verdict and the manifest.

    technical (contract): prove_red(base_sha=<verified origin-default sha>) -> RedProof.base_sha == that
    sha and RunStore().read(run)["red_proof"]["base_sha"] == that sha, and it differs from the
    candidate's local HEAD (the build forks from the verified sha, not a stale local head).
    """
    scen = _scenario(
        tmp_path,
        "sha-bound",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    assert rp.base_sha == scen.base_sha
    assert rp.base_sha != scen.candidate_sha
    assert store.RunStore().read(run)["red_proof"]["base_sha"] == scen.base_sha


def test_wrong_base_sha_is_refused_not_echoed(tmp_path):
    """A ``base_sha`` that does not match the base checkout's verified origin-default is REFUSED, not
    echoed — the sha binding is verified against real git state, defeating an echo-the-argument stub.

    technical (contract): prove_red is given a base_sha that is NOT the base checkout's HEAD (here the
    candidate's local sha). prove_red -> rejected, reason "sha_mismatch" (an implementation that copies
    the base_sha argument into the result without verifying git state would wrongly accept).
    """
    scen = _scenario(
        tmp_path,
        "sha-mismatch",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,), base_sha=scen.candidate_sha)
    _assert_rejected(run, rp, "sha_mismatch")


def test_baseline_bound_to_origin_sha_not_base_checkout_local_head(tmp_path):
    """The base baseline runs at the BOUND origin-default sha, not the base checkout's local HEAD.

    technical (contract): the base checkout's LOCAL HEAD reddens the base suite while
    refs/remotes/origin/main stays at the green ``base_sha``; the candidate is a normal red. A proof
    that runs the base checkout's working tree / local HEAD would see a red base (baseline_not_green);
    a correct proof checks out the bound origin sha (green) -> accepted True, reason "behavioral_red".
    """
    scen = _scenario(
        tmp_path,
        "origin-bound",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
        base_local_red_head=True,
    )
    rp = _prove(_mk_run(), scen, targeted_ids=(_NEW_ID,))
    assert rp.accepted is True
    assert rp.reason == "behavioral_red"


# =============================================================== G. Redaction canary (both paths)


def test_redaction_canary_success_path(tmp_path):
    """SUCCESS path: the secret in the captured red message is redacted IN the canonical record and
    absent from every persisted file.

    technical (contract): an accepted red whose assertion message embeds SECRET, proved with
    secrets={SECRET}. The persisted record["red_proof"]["records"] entry for ``_NEW_ID`` has a message
    containing "[REDACTED]" and NOT the secret; SECRET appears in ZERO files under the run tree.
    """
    secret = "SECRET_sk_live_canary_success_9x8y7z"
    scen = _scenario(
        tmp_path,
        "redact-ok",
        candidate_files={
            "tests/test_new.py": f'def test_new():\n    assert 1 == 2, "{secret} boom"\n'
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,), secrets={secret})
    assert rp.accepted is True
    entry = next(
        r for r in store.RunStore().read(run)["red_proof"]["records"] if r["nodeid"] == _NEW_ID
    )
    assert "[REDACTED] boom" in entry["message"]
    assert secret not in entry["message"]
    texts = [p.read_text(errors="replace") for p in _run_files()]
    assert all(secret not in t for t in texts)


def test_redaction_canary_failure_path(tmp_path):
    """FAILURE path: a REJECTED proof still PERSISTS a redacted diagnostic — the secret is captured,
    redacted, and absent everywhere (not vacuously absent because nothing was written).

    technical (contract): a call-phase ImportError whose message embeds SECRET (rejected
    "import_error"), proved with secrets={SECRET}. The captured diagnostic is redacted IN PLACE: some
    persisted file contains the source-specific surviving fragment "[REDACTED] not importable" (proving
    the real secret-bearing message was captured and redacted, not a constant "[REDACTED]" written
    elsewhere), and SECRET appears in ZERO files under the run tree.
    """
    secret = "SECRET_sk_live_canary_failure_1a2b3c"
    scen = _scenario(
        tmp_path,
        "redact-fail",
        candidate_files={
            "tests/test_new.py": f'def test_new():\n    raise ImportError("{secret} not importable")\n'
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,), secrets={secret})
    _assert_rejected(run, rp, "import_error")
    texts = [p.read_text(errors="replace") for p in _run_files()]
    assert any("[REDACTED] not importable" in t for t in texts)
    assert all(secret not in t for t in texts)


# =============================================================== H. Discover-before-authoring


def test_authoring_refuses_when_existing_test_undisposed_and_authors_nothing(tmp_path):
    """Authoring refuses when any existing contract test lacks a disposition — and invokes no author.

    technical (contract): author_tests on a buildable, revision-applied run with a known existing id
    but an EMPTY dispositions map pauses (status "paused"), records NO "authoring" event, and NEVER
    invokes the injected author (call count stays 0).
    """
    from issueforge import contract

    run = _mk_run()
    calls = {"n": 0}

    def author(*a, **k):
        calls["n"] += 1
        return "authored"

    with pytest.raises(Exception):
        contract.author_tests(run, author=author, existing_ids=(_BASE_A,), dispositions={})
    assert calls["n"] == 0
    assert store.RunStore().read(run)["status"] == "paused"
    assert all(e.get("transition") != "authoring" for e in store.RunStore().replay_events(run))


# =============================================================== I. Authoring gate


def test_authoring_refuses_unless_buildable_and_revision_applied(tmp_path):
    """Authoring runs ONLY behind a buildable, revision-applied run — otherwise it refuses, authoring
    nothing and invoking no author.

    technical (contract): author_tests on a NON-buildable run and on a buildable-but-not-revision-
    applied run each raises state.IllegalTransition, records no "authoring" event, and never invokes
    the author.
    """
    from issueforge import contract, state

    calls = {"n": 0}

    def author(*a, **k):
        calls["n"] += 1
        return "authored"

    for rid, kw in (("run-nb", dict(buildable=False)), ("run-nr", dict(revision_applied=False))):
        run = _mk_run(rid, **kw)
        with pytest.raises(state.IllegalTransition):
            contract.author_tests(run, author=author, existing_ids=(), dispositions={})
        assert all(e.get("transition") != "authoring" for e in store.RunStore().replay_events(run))
    assert calls["n"] == 0


def test_authoring_legal_case_invokes_author_and_records_event(tmp_path):
    """A buildable, revision-applied run with complete dispositions PASSES the gate: it invokes the
    author once and records exactly one authoring event.

    technical (contract): author_tests on a buildable, revision-applied run whose one existing id has
    a "keep" disposition invokes the injected author exactly once and records one "authoring" event
    (the legal path, not just the refusals).
    """
    from issueforge import contract

    run = _mk_run()
    calls = {"n": 0}

    def author(*a, **k):
        calls["n"] += 1
        return "authored"

    contract.author_tests(
        run, author=author, existing_ids=(_BASE_A,), dispositions={_BASE_A: "keep"}
    )
    assert calls["n"] == 1
    events = store.RunStore().replay_events(run)
    assert sum(1 for e in events if e.get("transition") == "authoring") == 1


def test_authoring_refuses_on_invalid_disposition_value(tmp_path):
    """A disposition VALUE outside keep/revise/supersede is not a valid disposition — authoring refuses.

    technical (contract): author_tests on a buildable, revision-applied run whose existing id maps to
    an invalid value ("banana") pauses (status "paused"), records NO "authoring" event, and never
    invokes the author — a mere ``existing_ids <= dispositions.keys()`` check would wrongly proceed.
    """
    from issueforge import contract

    run = _mk_run()
    calls = {"n": 0}

    def author(*a, **k):
        calls["n"] += 1
        return "authored"

    with pytest.raises(Exception):
        contract.author_tests(
            run, author=author, existing_ids=(_BASE_A,), dispositions={_BASE_A: "banana"}
        )
    assert calls["n"] == 0
    assert store.RunStore().read(run)["status"] == "paused"
    assert all(e.get("transition") != "authoring" for e in store.RunStore().replay_events(run))


# =============================================================== J. Verbatim-example fixture


@pytest.mark.parametrize(
    "fixtures, expected",
    [
        ({"f.txt": "label: value\n"}, True),  # exactly one exact match
        ({"f.txt": "label: value\n", "other.txt": "unrelated\n"}, True),  # one exact + unrelated
        ({"f.txt": "label:\nvalue\n"}, False),  # re-shaped, not verbatim
        ({}, False),  # zero matches
        (
            {"a.txt": "label: value\n", "b.txt": "label: value\n"},
            False,
        ),  # two matches, not exactly one
        ({"f.txt": "label: value"}, False),  # newline-sensitive: missing trailing newline
    ],
)
def test_verbatim_example_fixture_exactly_one_byte_for_byte(tmp_path, fixtures, expected):
    """A concrete issue example is reproduced by EXACTLY ONE fixture, byte-for-byte.

    technical (contract): example_reproduced_verbatim("label: value\\n", fixtures) is True only for a
    single exact byte match; a re-shaped, missing-newline, absent, or duplicated fixture is False.
    """
    from issueforge import contract

    assert contract.example_reproduced_verbatim("label: value\n", fixtures) is expected


# =============================================================== K. Suite-level anti-false-green


@pytest.mark.parametrize(
    "source, rejected",
    [
        # A full-strength blocked test: non-zero exit AND keyword AND offending test name.
        (
            "def test_blocked():\n"
            "    r = run()\n"
            "    assert r.returncode != 0\n"
            '    assert "weaken" in r.stderr\n'
            '    assert "test_offender" in r.stderr\n',
            False,
        ),
        # Only asserts it failed (no keyword, no name) — satisfied by the script not existing.
        ("def test_blocked():\n    r = run()\n    assert r.returncode != 0\n", True),
        # Asserts a PASSING/zero exit — a bare exit-0 false green.
        ("def test_blocked():\n    r = run()\n    assert r.returncode == 0\n", True),
        # Has the non-zero exit and keyword but MISSING the offending test name.
        (
            'def test_blocked():\n    r = run()\n    assert r.returncode != 0\n    assert "weaken" in r.stderr\n',
            True,
        ),
        # Keyword/name only in a comment, not asserted — must not count.
        (
            "def test_blocked():\n    r = run()\n    assert r.returncode != 0\n    # weaken test_offender\n",
            True,
        ),
    ],
)
def test_reject_false_green_catalogue(tmp_path, source, rejected):
    """reject_false_green enforces the discipline across syntactic variants, not a two-point lookup.

    technical (contract): reject_false_green returns None ONLY for a test asserting a non-zero exit AND
    a keyword AND the offending test name; every weaker shape (only-failed, exit-0, missing-name,
    comment-only) returns a non-empty reason string.
    """
    from issueforge import contract

    result = contract.reject_false_green(source)
    if rejected:
        assert isinstance(result, str) and result
    else:
        assert result is None


# ===================================================================================================
# S11 (#17) — Independent semantic review of the red contract.
#
# S10 proves the tests executed and FAILED in the call phase at a bound sha. It CANNOT prove the red
# corresponds to the NAMED missing behavior, that the tests cover the issue, or that a golden is
# semantically weak (``run cmd -> exit 0`` passes every syntactic check). S11 adds that irreducibly
# semantic gate: an independent, FRESH session that reviews against the REAL candidate worktree with
# the LITERAL proof command, is fail-loud on empty/non-zero/timeout, binds its verdict to the REAL
# candidate HEAD, runs a bounded batched-round protocol on its OWN under-lock counter, records an
# explicit override with full provenance, and retains a fully-redacted structured audit packet.
#
# The gate — ``contract.review_red_contract`` — consumes an ACCEPTED S10 red proof on the SAME run
# (proof precedes review), then owns the deterministic half. Between blocking rounds it drives a fix
# -> RE-PROVE (the full S10 predicate set, minting NEW sha-bound red evidence) -> RE-REVIEW loop
# through injected ``fixer``/``reprove`` seams, so "any change to a test re-runs the proof" is part of
# the round protocol. The reviewer is an INJECTED seam (like ``shaper._confirm``): each round it
# returns an ``_Outcome`` carrying a REAL ``providers.AIResult`` (produced by launching the fake
# provider CLI through the guarded subprocess seam IN THE CANDIDATE WORKTREE, so fail-loud, real-
# worktree execution, session identity, and redaction are exercised against a real child) plus the
# AI's structured judgment (``correspondence``, ``findings``). The reviewer here ASSERTS the packet it
# was handed (real worktree, byte-exact proof command, ALL materialized inputs present with exact
# BYTES before it runs, the expected-reason ``contract`` AND the observed ``red_evidence``, the
# network-off provisioner marker, and the exhaustive-pass instruction), so a gate that fails to supply
# any of them is caught rather than rubber-stamped.
#
# PENDING (#17): every test below is xfail(strict) until S11 is built; each flips to required then.
# ===================================================================================================

# Canaries: a synthetic secret seeded into the review packet (reviewer stdout, echoed inputs, captured
# stderr). A field MARKER sits next to each canary so a test can prove the field was copied INTO the
# S11 packet (marker present) AND then redacted (canary absent, ``[REDACTED]`` present).
_CANARY_RESPONSE = "canary-resp-AAA111"
_CANARY_INPUT = "canary-input-BBB222"
_CANARY_STDERR = "canary-stderr-CCC333"
_MARK_RESPONSE = "RESPFIELD"
_MARK_INPUT = "INPUTFIELD"
_MARK_STDERR = "STDERRFIELD"

_AUTH_SESSION = "authoring-session-deadbeef"
_PROOF_CMD = "python -m pytest tests/test_new.py::test_x -x"
# A byte-fragile command: leading/trailing whitespace, doubled spaces, quotes, a backslash, and a
# trailing newline, so a ``.strip()`` / ``shlex.join(shlex.split(...))`` normalization would CHANGE it.
_PROOF_CMD_FRAGILE = '  python  -m pytest "tests/test_new.py::test_x[a b]" -k \\ansi -x \n'
# The instruction fragment that marks the exhaustive first pass (vs a confirmation round).
_EXHAUSTIVE = "enumerate ALL"

_NEW_X = "tests/test_new.py::test_x"
_RED_A = "tests/test_new.py::test_x call-phase FAILED: AssertionError (assert 1 == 2)\n"
_CONTRACT_A = "S11 contract: test_x must FAIL because the missing behavior raises AssertionError\n"


def _default_inputs(red_evidence: str = _RED_A, contract: str = _CONTRACT_A) -> dict:
    return {
        "diff": "diff --git a/tests/test_new.py b/tests/test_new.py\n+def test_x(): assert 1 == 2\n",
        "contract": contract,
        "manifest": '{"red_proof": {"reason": "behavioral_red"}}\n',
        "red_evidence": red_evidence,
    }


def _red_scenario(root: Path, name: str = "review") -> SimpleNamespace:
    """A REAL two-commit repo whose candidate authors one genuine call-phase red (``test_x``)."""
    return _scenario(
        root,
        name,
        candidate_files={"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"},
    )


def _seed_proof(run_id: str, scen: SimpleNamespace) -> None:
    """Seed an ACCEPTED S10 red proof on ``run_id`` — the precondition the review consumes.

    The review gate requires an accepted proof on the SAME run (proof precedes review); tests that are
    not about the proof->review linkage seed it here so a gate that correctly checks the precondition
    is exercised rather than tripped. The C1/C2 test runs the REAL ``prove_red`` instead.
    """
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "red_proof": {
                "accepted": True,
                "reason": "behavioral_red",
                "base_sha": scen.base_sha,
                "head_sha": scen.candidate_sha,
                "added_ids": [_NEW_X],
                "records": [],
            }
        },
    )


def _review_run(scen: SimpleNamespace, run_id: str = "run-1") -> str:
    run = _mk_run(run_id)
    _seed_proof(run, scen)
    return run


def _review_profile(script: Path, start: list[str]):
    """A Profile whose executable runs the fake provider CLI through the real subprocess seam."""
    from issueforge.config import Profile

    return Profile(
        name="reviewer-cli",
        executable=[sys.executable, str(script)],
        start=start,
        resume=["--out", "resumed", "{prompt}"],
        auth=["--exit", "0"],
    )


def _start_argv(spec: dict) -> list[str]:
    """Build the fake-provider start template from a round spec (stdout lines, stderr lines, exit,
    sleep). No ``--out`` line means EMPTY stdout — the fail-loud empty-output case."""
    argv: list[str] = []
    for line in spec.get("out", ()):
        argv += ["--out", line]
    for line in spec.get("err", ()):
        argv += ["--err", line]
    argv += ["--exit", str(spec.get("exit", 0)), "--sleep", str(spec.get("sleep", 0.0)), "{prompt}"]
    return argv


class _Outcome(SimpleNamespace):
    """One round's reviewer return: a real AIResult plus the AI's semantic judgment. ``result`` drives
    fail-loud + independence + redaction; ``correspondence``/``findings`` are the semantic half."""


def _make_reviewer(
    script: Path,
    run_id: str,
    specs: list[dict],
    *,
    scen: SimpleNamespace | None = None,
    dest: Path | None = None,
    expect_inputs: dict | None = None,
    secrets=frozenset(),
    shared_session: str | None = None,
    proof_command: str = _PROOF_CMD,
    require_exhaustive: bool = False,
):
    """An injected reviewer returning one ``_Outcome`` per round (last spec repeats).

    It records every packet and every AIResult (``reviewer.packets`` / ``reviewer.results``) and — the
    strong observable — ASSERTS the packet the gate handed it AT CALL TIME (before returning):

    - the real candidate ``worktree``;
    - the byte-exact ``proof_command`` (compared as bytes);
    - EVERY materialized input already present on disk under ``dest`` with EXACT BYTES (a gate that
      materializes lazily, partially, or with CRLF drift is caught here, during the review);
    - both the observed ``red_evidence`` and the expected-reason ``contract`` (so the semantic
      observed-vs-expected judgment is bound to BOTH inputs, not hand-fed);
    - the network-off provisioner marker;
    - the enumerate-ALL instruction on the first pass.

    It launches the fake provider IN THE CANDIDATE WORKTREE (fresh session unless ``shared_session``
    forces the equal-session path), and emits its full findings set ONLY when the packet carries the
    exhaustive instruction, so a gate whose prompt says "stop at the first" is caught.
    """
    from issueforge import providers

    packets: list = []
    results: list = []
    captures: list = []
    captured_dirs: list = []
    box = {"i": 0}

    def reviewer(packet):
        packets.append(packet)
        if scen is not None:
            assert Path(packet["worktree"]) == Path(scen.candidate_worktree), (
                "not the real worktree"
            )
        assert packet["proof_command"].encode() == proof_command.encode(), (
            "proof command not byte-exact"
        )
        prov = packet.get("provisioner")
        assert prov is not None and getattr(prov, "network", True) is False, (
            "network-off marker missing"
        )
        inputs = packet["inputs"]
        if expect_inputs is not None:
            assert set(inputs) == set(expect_inputs) | {"proof_command", "head_sha"}
            for key, value in expect_inputs.items():
                path = Path(inputs[key])
                assert path.exists(), f"{key} not materialized before review"
                if dest is not None:
                    # Bounded to exactly two layouts (direct child OR one run-owned subdir of dest);
                    # rejects arbitrary nesting and lexical traversal like ``dest/../outside``. The
                    # strong per-run-subdir pin lives in the #82 item-2 tests, not this shared helper.
                    assert path.parent == Path(dest) or path.parent.parent == Path(dest), (
                        f"{key} not directly under dest or a run-owned subdir of dest"
                    )
                assert path.read_bytes() == value.encode(), f"{key} bytes not exact"
        # Snapshot the materialized input bytes AT CALL TIME so tests can assert content even after the
        # gate removes the run-owned inputs on return (item-2 cleanup); one dict per round.
        captures.append({name: Path(p).read_bytes() for name, p in inputs.items()})
        # Snapshot the LIVE directory facts (symlink-ness, resolved parent) while the dir still exists,
        # so the item-2 tests can reject a symlinked run dir that escapes dest (a lexical post-return
        # check cannot see it once removed). ``parents`` is the set of every input's parent directory.
        dir_parents = {Path(p).parent for p in inputs.values()}
        one_parent = Path(inputs["diff"]).parent
        captured_dirs.append(
            {
                "parents": dir_parents,
                "owned": one_parent,
                "is_symlink": one_parent.is_symlink(),
                "resolved": one_parent.resolve(),
            }
        )
        exhaustive = _EXHAUSTIVE in packet.get("instruction", "")
        if require_exhaustive and box["i"] == 0:
            assert exhaustive, "first pass did not carry the exhaustive-enumeration instruction"

        spec = specs[min(box["i"], len(specs) - 1)]
        box["i"] += 1
        profile = _review_profile(script, _start_argv(spec))
        cwd = Path(packet["worktree"]) if scen is not None else Path.cwd()
        result = providers.invoke(
            profile,
            "review prompt",
            cwd=cwd,
            timeout=spec.get("timeout", 5.0),
            run_id=run_id,
            secrets=frozenset(secrets),
            role="review",
            session=shared_session if spec.get("shared") else None,
        )
        results.append(result)
        findings = tuple(spec.get("findings", ()))
        if findings and not exhaustive:
            findings = findings[:1]
        return _Outcome(
            result=result, correspondence=spec.get("correspondence", True), findings=findings
        )

    reviewer.packets = packets
    reviewer.results = results
    reviewer.captured = captures
    reviewer.captured_dirs = captured_dirs
    reviewer.calls = box
    return reviewer


def _review(
    run_id,
    scen,
    *,
    reviewer,
    dest: Path,
    authoring_session_id: str = _AUTH_SESSION,
    proof_command: str = _PROOF_CMD,
    secrets=frozenset(),
    max_rounds=None,
    inputs=None,
    reprove=None,
    fixer=None,
    provisioner=None,
):
    """One call into the S11 gate; centralized so a signature change touches one place (cf. ``_prove``).

    The gate binds to the REAL HEAD of ``scen.candidate_worktree`` (never a caller-supplied sha),
    materializes ``review_inputs`` to ``dest`` before each review, and drives the bounded round loop
    (fix -> reprove -> re-review) through the injected ``fixer``/``reprove`` seams.
    """
    from issueforge import contract

    return contract.review_red_contract(
        run_id,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha,
        proof_command=proof_command,
        reviewer=reviewer,
        authoring_session_id=authoring_session_id,
        materialize_dest=Path(dest),
        review_inputs=inputs if inputs is not None else _default_inputs(),
        provisioner=provisioner if provisioner is not None else _provisioner(),
        secrets=frozenset(secrets),
        max_rounds=max_rounds,
        reprove=reprove,
        fixer=fixer,
    )


def _events(run_id, transition):
    return [e for e in store.RunStore().replay_events(run_id) if e.get("transition") == transition]


def _state_files() -> list[Path]:
    return [p for p in Path(state_root()).rglob("*") if p.is_file()]


# ------------------------------------------------------------------- L. Session independence (US-9.5)


def test_review_session_bound_to_airesult_and_recorded(tmp_path, fake_provider_script):
    """The reviewer session is FRESH (not the authoring session) and the recorded id is the ACTUAL
    reviewer AIResult's session — not an arbitrary string the gate could invent.

    technical (contract): review_red_contract -> ContractReview.reviewer_session_id equals the injected
    reviewer's real AIResult.session_id (role "review"), differs from _AUTH_SESSION, and is recorded
    identically on the manifest ``contract_review`` block AND a ``review`` event.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["APPROVE"], "correspondence": True}],
        scen=scen,
        dest=dest,
    )
    review = _review(run, scen, reviewer=reviewer, dest=dest)
    real = reviewer.results[-1]
    assert real.role == "review"
    assert review.reviewer_session_id == real.session_id != _AUTH_SESSION
    block = store.RunStore().read(run)["contract_review"]
    assert block["reviewer_session_id"] == real.session_id
    assert block["authoring_session_id"] == _AUTH_SESSION
    assert any(e.get("session_id") == real.session_id for e in _events(run, "review"))


def test_equal_reviewer_session_rejected_fail_loud(tmp_path, fake_provider_script):
    """A review whose session equals the authoring session is REJECTED (US-9.5) — and the rejection is
    fail-loud: the run pauses, NO ``done``/accepted verdict is persisted, and the failure records BOTH
    equal session ids.

    technical (contract): a reviewer forced to reuse _AUTH_SESSION -> review_red_contract raises
    providers.SharedSessionError; afterward the run status is "paused", the manifest has no
    ``contract_review`` verdict "done", and a failed ``review`` event records the reviewer session id
    equal to the authoring session id.
    """
    from issueforge import providers

    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["APPROVE"], "correspondence": True, "shared": True}],
        scen=scen,
        dest=tmp_path / "pkt",
        shared_session=_AUTH_SESSION,
    )
    with pytest.raises(providers.SharedSessionError):
        _review(run, scen, reviewer=reviewer, dest=tmp_path / "pkt")
    record = store.RunStore().read(run)
    assert record["status"] == "paused"
    assert record.get("contract_review", {}).get("verdict") != "done"
    failed = [e for e in _events(run, "review") if e.get("confirmed") is False or e.get("failed")]
    assert failed, "no failed review event recorded on the equal-session path"
    assert any(
        e.get("session_id") == _AUTH_SESSION and e.get("authoring_session_id") == _AUTH_SESSION
        for e in failed
    ), "failed event must record the equal (reviewer == authoring) session ids"


# ------------------------------------------------- M. Execution capability: materialize + verbatim cmd


def test_inputs_materialized_in_run_owned_subdir_then_removed(tmp_path, fake_provider_script):
    """EVERY review input is written with EXACT BYTES BEFORE the reviewer runs (S7, no network), inside a
    RUN-OWNED temp subdir UNDER ``materialize_dest`` (not dest directly) — proven inside the reviewer
    callback — and after ``review_red_contract`` returns, every materialized input file is REMOVED, so no
    secret-bearing input lingers on disk (#82 item 2).

    technical (contract): the reviewer (via ``expect_inputs``) asserts each input exists byte-exact under
    dest or a run-owned subdir; the test then asserts ALL six inputs share ONE common parent directory
    whose parent is dest and which is not dest itself, the captured head_sha equals the real candidate
    HEAD, that whole run-owned dir no longer exists after the gate returns (no advertised OR stray file
    survives), and a SECOND review gets a DISTINCT run-owned dir (a fixed shared dir would collide).
    """
    scen = _red_scenario(tmp_path)
    dest = tmp_path / "pkt"
    inputs = _default_inputs()

    def run_once(run_id):
        run = _review_run(scen, run_id)
        reviewer = _make_reviewer(
            fake_provider_script,
            run,
            [{"out": ["APPROVE"]}],
            scen=scen,
            dest=dest,
            expect_inputs=inputs,
        )
        _review(run, scen, reviewer=reviewer, dest=dest, inputs=inputs)
        return reviewer

    rev1 = run_once("run-1")
    got1 = rev1.packets[0]["inputs"]
    # ALL six inputs share ONE run-owned parent directory, directly under dest and never dest itself.
    parents = {Path(p).parent for p in got1.values()}
    assert len(parents) == 1, "inputs scattered across multiple directories"
    owned = parents.pop()
    assert owned.parent == dest and owned != dest, "inputs not in a single run-owned subdir of dest"
    # The run dir is a REAL, non-symlink directory whose RESOLVED parent is dest (captured live — a
    # ``dest/run -> /outside`` symlink would pass the lexical checks above while leaking outside dest).
    dfacts = rev1.captured_dirs[0]
    assert dfacts["parents"] == {owned}, "inputs not all under one live parent during the review"
    assert dfacts["is_symlink"] is False, "run dir is a symlink (possible escape from dest)"
    assert dfacts["resolved"].parent == dest.resolve(), "run dir resolves outside dest"
    # Byte-exact content captured DURING the callback (files are gone by now).
    assert rev1.captured[0]["proof_command"] == _PROOF_CMD.encode()
    assert rev1.captured[0]["head_sha"].decode().strip() == scen.candidate_sha
    # The whole run-owned dir is removed on return — no advertised file OR stray backup survives.
    assert not owned.exists(), "run-owned input dir lingered after the review"
    for name, path in got1.items():
        assert not Path(path).exists(), f"{name} lingered on disk after the review"
    # No stray path survives ANYWHERE under dest (a leaked ``dest/backup/red_evidence`` would be caught).
    assert list(dest.rglob("*")) == [], "the review left stray paths under dest"
    # A SECOND review gets a DISTINCT run-owned dir (a fixed shared dir would collide across runs).
    rev2 = run_once("run-2")
    owned2 = Path(rev2.packets[0]["inputs"]["diff"]).parent
    assert owned2 != owned, "materialization dir not distinct per review (fixed shared dir)"
    assert not owned2.exists(), "second run-owned input dir lingered after the review"


def test_proof_command_byte_exact_never_normalized(tmp_path, fake_provider_script):
    """The literal proof command reaches the reviewer and the materialized file BYTE-FOR-BYTE — a
    whitespace/quote/backslash/newline-fragile command survives unchanged (no strip, no re-quote).

    technical (contract): with proof_command=_PROOF_CMD_FRAGILE, packet["proof_command"] and the
    materialized proof_command file both equal _PROOF_CMD_FRAGILE as BYTES (a normalization would
    alter the doubled spaces / trailing newline and fail).
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["APPROVE"]}],
        scen=scen,
        dest=dest,
        proof_command=_PROOF_CMD_FRAGILE,
    )
    _review(run, scen, reviewer=reviewer, dest=dest, proof_command=_PROOF_CMD_FRAGILE)
    assert reviewer.packets[0]["proof_command"].encode() == _PROOF_CMD_FRAGILE.encode()
    # Captured at review time (the materialized input is removed on return, #82 item 2).
    assert reviewer.captured[0]["proof_command"] == _PROOF_CMD_FRAGILE.encode()


def test_review_runs_against_real_worktree_with_network_off_marker(tmp_path, fake_provider_script):
    """The reviewer is given EXECUTION CAPABILITY against the REAL candidate worktree under a
    network-off provisioner marker (network denial itself is S6/S15's hermetic-env contract).

    technical (contract): the packet's ``worktree`` == scen.candidate_worktree and its ``provisioner``
    carries ``network is False``; the reviewer child is launched with cwd == the candidate worktree
    (a diff-only gate that never supplies a worktree fails the packet assertions in _make_reviewer).
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["APPROVE"], "correspondence": True}],
        scen=scen,
        dest=dest,
    )
    _review(run, scen, reviewer=reviewer, dest=dest)
    packet = reviewer.packets[0]
    assert Path(packet["worktree"]) == Path(scen.candidate_worktree)
    assert packet["provisioner"].network is False


# ------------------------------------------------------------------------------ N. Fail-loud (from S7)


@pytest.mark.parametrize(
    "name, spec, cause",
    [
        ("empty_output", {"out": [], "correspondence": True}, "empty_output"),
        ("nonzero_exit", {"out": ["APPROVE"], "exit": 1, "correspondence": True}, "nonzero_exit"),
        (
            "timeout",
            {"out": ["APPROVE"], "sleep": 5.0, "timeout": 0.3, "correspondence": True},
            "timeout",
        ),
    ],
)
def test_failed_invocation_full_contract(tmp_path, fake_provider_script, name, spec, cause):
    """Empty output OR non-zero exit OR timeout = FAILED review, NEVER a pass — even when the reviewer
    CLAIMS correspondence. The failure is fully recorded: exact ``blocking:1``, run paused, a failed
    ``review`` event naming the SPECIFIC cause, and a retained packet; never ``done``, never
    ``blocking:0``.

    technical (contract): a reviewer whose AIResult status is FAILED but whose outcome claims
    correspondence=True, max_rounds=1 -> verdict == "blocking:1", accepted False, run status "paused",
    a failed review event whose ``cause`` distinguishes empty_output / nonzero_exit / timeout, and
    ContractReview.packet_path exists.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(fake_provider_script, run, [spec], scen=scen, dest=dest)
    review = _review(run, scen, reviewer=reviewer, dest=dest, max_rounds=1)
    assert review.accepted is False
    assert review.verdict == "blocking:1"
    record = store.RunStore().read(run)
    assert record["status"] == "paused"
    assert record["contract_review"]["verdict"] != "done"
    failed = [e for e in _events(run, "review") if e.get("failed")]
    assert failed and failed[-1].get("cause") == cause, "failed event must name the specific cause"
    assert Path(review.packet_path).exists()


# --------------------------------------------------------- O. Semantic correspondence (core deliverable)


def test_correspondence_true_no_findings_is_done_but_findings_block(tmp_path, fake_provider_script):
    """``done`` requires BOTH correspondence AND zero findings: a reviewer that AFFIRMS correspondence
    yet still returns a blocking finding does NOT pass (a gate of ``accepted = correspondence`` fails).

    technical (contract): correspondence=True, findings=() -> "done", accepted True; correspondence=True
    with findings=("weak-golden",), max_rounds=1 -> "blocking:1", accepted False.
    """
    scen = _red_scenario(tmp_path)
    ok_run = _review_run(scen, "ok-run")
    ok_rev = _make_reviewer(
        fake_provider_script,
        ok_run,
        [{"out": ["APPROVE"], "correspondence": True, "findings": ()}],
        scen=scen,
        dest=tmp_path / "ok",
    )
    ok = _review(ok_run, scen, reviewer=ok_rev, dest=tmp_path / "ok")
    assert ok.verdict == "done" and ok.accepted is True

    blk_run = _review_run(scen, "blk-run")
    blk_rev = _make_reviewer(
        fake_provider_script,
        blk_run,
        [{"out": ["APPROVE"], "correspondence": True, "findings": ("weak-golden",)}],
        scen=scen,
        dest=tmp_path / "blk",
        require_exhaustive=True,
    )
    blk = _review(blk_run, scen, reviewer=blk_rev, dest=tmp_path / "blk", max_rounds=1)
    assert blk.verdict == "blocking:1" and blk.accepted is False


@pytest.mark.parametrize(
    "name, red_evidence, contract_reason, finding",
    [
        (
            "wrong_reason",
            "tests/test_new.py::test_x FAILED: KeyError (unrelated)\n",
            "test_x must fail with AssertionError for the missing behavior\n",
            "red-mismatch",
        ),
        (
            "weak_golden",
            "tests/test_new.py::test_x cmd -> exit 0 (assert returncode == 0)\n",
            "test_x must assert a NON-zero exit, not exit 0\n",
            "weak-golden",
        ),
        (
            "coverage_gap",
            "tests/test_new.py::test_y FAILED (a different behavior)\n",
            "the suite must cover test_x, the issue's behavior\n",
            "coverage-gap",
        ),
    ],
)
def test_semantic_failure_is_blocking_from_distinct_inputs(
    tmp_path, fake_provider_script, name, red_evidence, contract_reason, finding
):
    """A red-for-the-wrong-reason, a weak golden, or a coverage gap is ``blocking`` — NOT a rubber
    stamp — and the reviewer must actually RECEIVE BOTH the observed red evidence AND the expected
    behavioral reason (contract). A gate that forwards neither, or only one, is caught.

    technical (contract): each case supplies DISTINCT red_evidence + contract; the injected reviewer
    asserts (via expect_inputs) it received both exact bytes; with correspondence=False and one
    finding, max_rounds=1 -> verdict "blocking:1", accepted False, the finding token persisted.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    inputs = _default_inputs(red_evidence, contract_reason)
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["REJECT"], "correspondence": False, "findings": (finding,)}],
        scen=scen,
        dest=dest,
        expect_inputs=inputs,
    )
    review = _review(run, scen, reviewer=reviewer, dest=dest, inputs=inputs, max_rounds=1)
    assert review.accepted is False
    assert review.verdict == "blocking:1"
    assert finding in store.RunStore().read(run)["contract_review"]["findings"]


def test_missing_correspondence_is_blocking_one(tmp_path, fake_provider_script):
    """A healthy invocation that does NOT affirm correspondence and names no explicit finding is still
    blocking — a bare OK can never substitute for the semantic judgment; the gate synthesizes the
    missing-correspondence finding.

    technical (contract): status OK, correspondence=False, findings=(), max_rounds=1 -> verdict
    "blocking:1", accepted False, and a "missing-correspondence" finding persisted on the block.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["OK"], "correspondence": False, "findings": ()}],
        scen=scen,
        dest=dest,
    )
    review = _review(run, scen, reviewer=reviewer, dest=dest, max_rounds=1)
    assert review.verdict == "blocking:1" and review.accepted is False
    assert "missing-correspondence" in store.RunStore().read(run)["contract_review"]["findings"]


# ------------------------------------------------------- P. Sha-binding, staleness, evidence currency


def test_verdict_bound_to_real_candidate_head_not_echoed(tmp_path, fake_provider_script):
    """The verdict binds to the REAL resolved HEAD of the candidate worktree — not a caller-supplied
    value, and distinct from the base sha (a gate that binds to base or echoes an argument fails).

    technical (contract): ContractReview.head_sha == scen.candidate_sha (the real git HEAD),
    != scen.base_sha, and the manifest contract_review block records that same head_sha.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["APPROVE"], "correspondence": True}],
        scen=scen,
        dest=dest,
    )
    review = _review(run, scen, reviewer=reviewer, dest=dest)
    assert review.head_sha == scen.candidate_sha != scen.base_sha
    assert store.RunStore().read(run)["contract_review"]["head_sha"] == scen.candidate_sha


@pytest.mark.parametrize(
    "name, manifest, head, expected",
    [
        ("current", {"contract_review": {"verdict": "done", "head_sha": "h"}}, "h", True),
        ("stale_head", {"contract_review": {"verdict": "done", "head_sha": "old"}}, "new", False),
        ("blocking", {"contract_review": {"verdict": "blocking:1", "head_sha": "h"}}, "h", False),
        ("no_block", {}, "h", False),
        ("malformed", {"contract_review": {"head_sha": "h"}}, "h", False),
    ],
)
def test_currency_predicate_only_current_done_matching_head(name, manifest, head, expected):
    """``red_evidence_current`` is True ONLY for a ``done`` verdict whose recorded head equals the
    current head; a stale head, a non-``done`` verdict, an absent block, or a malformed block is False.

    technical (contract): red_evidence_current(manifest, head) matches the parametrized expectation
    across current / stale-head / blocking / missing-block / malformed manifests.
    """
    from issueforge import contract

    assert contract.red_evidence_current(manifest, head) is expected


def test_require_current_evidence_refuses_stale(tmp_path):
    """S11 owns a currency CONSUMER guard a downstream gate (S12 freeze) calls: it refuses stale or
    non-current evidence, so a stale red proof can never be reused. (S12's freeze itself is out of
    scope here; this is the guard it will call.)

    technical (contract): require_current_evidence(manifest, head) returns None for a current done
    manifest and RAISES (ValueError/StaleEvidenceError) for a stale-head, blocking, or missing block.
    """
    from issueforge import contract

    current = {"contract_review": {"verdict": "done", "head_sha": "h"}}
    assert contract.require_current_evidence(current, "h") is None
    for bad in (
        {"contract_review": {"verdict": "done", "head_sha": "old"}},
        {"contract_review": {"verdict": "blocking:1", "head_sha": "h"}},
        {},
    ):
        with pytest.raises((ValueError, Exception)):
            contract.require_current_evidence(bad, "h")


def test_test_change_reproves_and_rebinds_c1_stale_c2_current(tmp_path, fake_provider_script):
    """A change to a test RE-RUNS the full S10 predicate set and mints NEW sha-bound red evidence, in
    the RIGHT order: prove (C1) precedes review (C1); a mutation advances to C2; a real re-prove (C2)
    precedes the review (C2). C1's review is no longer current at C2, C2's is, and the currency guard
    refuses the stale C1 evidence.

    technical (contract): run1 = prove_red(C1) accepted, then review -> head_sha == sha(C1). Mutate the
    test file + commit (C2). run2 = prove_red(C2) accepted, then review -> head_sha == sha(C2) != sha(C1).
    red_evidence_current(C1_manifest, sha(C2)) is False; red_evidence_current(C2_manifest, sha(C2)) is
    True; require_current_evidence(C1_manifest, sha(C2)) raises.
    """
    from issueforge import contract

    scen = _red_scenario(tmp_path, "rerun")
    # C1: PROVE then REVIEW on the same run (proof precedes review).
    run1 = _mk_run("run-c1")
    assert _prove(run1, scen, targeted_ids=(_NEW_X,)).accepted
    d1 = tmp_path / "c1"
    rev1 = _make_reviewer(
        fake_provider_script,
        run1,
        [{"out": ["APPROVE"], "correspondence": True}],
        scen=scen,
        dest=d1,
    )
    r1 = _review(run1, scen, reviewer=rev1, dest=d1)
    assert r1.head_sha == scen.candidate_sha

    # Mutate the authored test -> a NEW candidate head (the contract was rewritten).
    (scen.candidate_worktree / "tests/test_new.py").write_text("def test_x():\n    assert 3 == 4\n")
    _git(scen.candidate_worktree, "add", "-A")
    _git(scen.candidate_worktree, "commit", "-qm", "mutate test_x")
    c2 = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
    assert c2 != scen.candidate_sha
    scen2 = SimpleNamespace(
        candidate_worktree=scen.candidate_worktree,
        base_checkout=scen.base_checkout,
        base_sha=scen.base_sha,
        candidate_sha=c2,
    )
    # C2: RE-PROVE (the full S10 predicate set) then REVIEW on run2.
    run2 = _mk_run("run-c2")
    assert _prove(run2, scen2, targeted_ids=(_NEW_X,)).accepted
    d2 = tmp_path / "c2"
    rev2 = _make_reviewer(
        fake_provider_script,
        run2,
        [{"out": ["APPROVE"], "correspondence": True}],
        scen=scen2,
        dest=d2,
    )
    r2 = _review(run2, scen2, reviewer=rev2, dest=d2)
    assert r2.head_sha == c2 != scen.candidate_sha

    c1_manifest = store.RunStore().read(run1)
    c2_manifest = store.RunStore().read(run2)
    assert contract.red_evidence_current(c1_manifest, c2) is False
    assert contract.red_evidence_current(c2_manifest, c2) is True
    with pytest.raises((ValueError, Exception)):
        contract.require_current_evidence(c1_manifest, c2)


# -------------------------------------------------------------- Q. Batched round protocol + counter


def test_blocking_then_reproved_confirmation_is_done(tmp_path, fake_provider_script):
    """The batched contract: a blocking first pass, then a FIX -> RE-PROVE (new head) -> confirmation
    that consumes evidence DERIVED FROM A RE-VERIFIED PROOF -> ``done``, two rounds. Between rounds the
    ``reprove`` seam returns an ACCEPTED ``RedProof`` bound to the ADVANCED head (never a bare string);
    the confirmation reviews the NEW head with evidence rendered from that proof, never the stale one
    (#82 item 1).

    technical (contract): round 1 blocks; the injected ``fixer`` mutates + commits the worktree (new
    head); ``reprove`` (asserted CALLED once with the new head) returns RedProof(accepted=True,
    reason "behavioral_red", base_sha == review base_sha, head_sha == the new head); round 2 is clean
    -> verdict "done", accepted True, rounds == 2; the round-2 packet's head_sha == the new head and
    differs from round 1's, AND its materialized red_evidence == the proof's one record rendered as
    "<nodeid> <type>: <message>" (an independent literal golden, pinned separately by the renderer test).
    """
    from issueforge import contract

    scen = _red_scenario(tmp_path, "conf")
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    calls = {"fix": 0, "reprove": 0, "reprove_head": None}

    def fixer():
        calls["fix"] += 1
        (scen.candidate_worktree / "tests/test_new.py").write_text(
            "def test_x():\n    assert 5 == 6\n"
        )
        _git(scen.candidate_worktree, "add", "-A")
        _git(scen.candidate_worktree, "commit", "-qm", "fix round")

    def reprove(head_sha):
        calls["reprove"] += 1
        calls["reprove_head"] = head_sha
        proof = contract.RedProof(
            accepted=True,
            reason="behavioral_red",
            records=(
                contract.RedRecord(
                    nodeid=_NEW_X,
                    exception_type="AssertionError",
                    assertion_line=2,
                    message="assert 5 == 6",
                ),
            ),
            base_sha=scen.base_sha,
            added_ids=(_NEW_X,),
            head_sha=head_sha,
        )
        return proof

    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [
            {"out": ["REJECT"], "correspondence": False, "findings": ("a", "b")},
            {"out": ["APPROVE"], "correspondence": True, "findings": ()},
        ],
        scen=scen,
        dest=dest,
        require_exhaustive=True,
    )
    review = _review(
        run, scen, reviewer=reviewer, dest=dest, max_rounds=2, fixer=fixer, reprove=reprove
    )
    assert review.verdict == "done" and review.accepted is True
    assert review.rounds == 2
    assert calls["fix"] == 1 and calls["reprove"] == 1, "fix/reprove seam not driven between rounds"
    new_head = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
    assert calls["reprove_head"] == new_head, "reprove must be called with the advanced head"
    assert reviewer.packets[1]["head_sha"] == new_head != reviewer.packets[0]["head_sha"]
    # Independent golden (NOT via the production seam): the confirmation red_evidence is rendered from
    # the verified proof's one record as "<nodeid> <type>: <message>" (see the dedicated renderer test).
    assert (
        reviewer.captured[1]["red_evidence"] == f"{_NEW_X} AssertionError: assert 5 == 6".encode()
    )


def test_red_evidence_from_proof_renders_records_independently():
    """``contract.red_evidence_from_proof`` renders a verified proof's records to the confirmation
    red_evidence text, pinned to a LITERAL expected string (not the seam itself), so the reproved-
    confirmation contract cannot be satisfied by a renderer that returns "" or a constant (#82 item 1).

    technical (contract): a RedProof with two records renders each as "<nodeid> <type>: <message>",
    newline-joined, byte-for-byte — with the exact literal expected value.
    """
    from issueforge import contract

    proof = contract.RedProof(
        accepted=True,
        reason="behavioral_red",
        records=(
            contract.RedRecord(
                nodeid="tests/test_a.py::test_one",
                exception_type="AssertionError",
                assertion_line=3,
                message="assert 1 == 2",
            ),
            contract.RedRecord(
                nodeid="tests/test_b.py::test_two",
                exception_type="ValueError",
                assertion_line=None,
                message="boom",
            ),
        ),
        base_sha="b" * 40,
        added_ids=("tests/test_a.py::test_one",),
        head_sha="h" * 40,
    )
    assert contract.red_evidence_from_proof(proof) == (
        "tests/test_a.py::test_one AssertionError: assert 1 == 2\n"
        "tests/test_b.py::test_two ValueError: boom"
    )


@pytest.mark.parametrize(
    "defect",
    ["not_accepted", "bound_to_old_head", "wrong_base_sha", "wrong_reason", "bare_string"],
)
def test_reprove_unverified_or_misbound_proof_is_fail_loud(tmp_path, fake_provider_script, defect):
    """Between rounds the gate REQUIRES a re-verified accepted ``RedProof`` bound to the ADVANCED head. A
    reprove that returns an unaccepted proof, a proof bound to the OLD head, a wrong base_sha, a wrong
    reason (not ``behavioral_red``), or a BARE STRING (not a ``RedProof``) is FAIL-LOUD. The gate must
    STILL have advanced the head and CALLED reprove against that advanced head (the #82 exploit surface:
    fabricated evidence at a genuinely advanced candidate head), then REJECT it: the run pauses with a
    persisted ``blocking`` contract_review that is never accepted/done, and the CONFIRMATION reviewer is
    never invoked (reviewer ran exactly once) (#82 item 1).

    technical (contract): round 1 blocks (finding "a"); the fixer advances the head; ``reprove`` is
    called exactly once with the NEW head (calls prove reprove ran at head != old_head) and returns the
    parametrized defective value -> ``review_red_contract`` returns accepted False / verdict startswith
    "blocking:" OR raises, but NEVER reaches a second reviewer call (len(reviewer.packets) == 1);
    afterward run status == "paused", the persisted contract_review verdict startswith "blocking", and
    its accepted flag is not True.
    """
    from issueforge import contract

    scen = _red_scenario(tmp_path, "badreprove")
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    old_head = scen.candidate_sha
    calls = {"fix": 0, "reprove": 0, "reprove_head": None}

    def fixer():
        calls["fix"] += 1
        (scen.candidate_worktree / "tests/test_new.py").write_text(
            "def test_x():\n    assert 5 == 6\n"
        )
        _git(scen.candidate_worktree, "add", "-A")
        _git(scen.candidate_worktree, "commit", "-qm", "fix")

    def reprove(head_sha):
        calls["reprove"] += 1
        calls["reprove_head"] = head_sha
        if defect == "bare_string":
            return "fabricated red evidence not backed by any proof\n"
        return contract.RedProof(
            accepted=(defect != "not_accepted"),
            reason=("import_error" if defect == "wrong_reason" else "behavioral_red"),
            records=(
                contract.RedRecord(
                    nodeid=_NEW_X,
                    exception_type="AssertionError",
                    assertion_line=2,
                    message="assert 5 == 6",
                ),
            ),
            base_sha=("0" * 40 if defect == "wrong_base_sha" else scen.base_sha),
            added_ids=(_NEW_X,),
            head_sha=(old_head if defect == "bound_to_old_head" else head_sha),
        )

    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [
            {"out": ["REJECT"], "correspondence": False, "findings": ("a",)},
            {"out": ["APPROVE"], "correspondence": True, "findings": ()},  # must NOT be reached
        ],
        scen=scen,
        dest=dest,
        require_exhaustive=True,
    )
    try:
        review = _review(
            run, scen, reviewer=reviewer, dest=dest, max_rounds=2, fixer=fixer, reprove=reprove
        )
        assert review.accepted is False
        assert review.verdict.startswith("blocking:")
    except Exception:
        # Fail-loud MAY raise (of ANY type) instead of returning a blocking verdict; the type is not
        # prescribed. The strong post-conditions below are the real teeth, not the exception class.
        pass

    # The gate genuinely reached AND exercised the reprove seam at the ADVANCED head before rejecting.
    assert calls["fix"] == 1 and calls["reprove"] == 1, (
        "gate must advance + call reprove before reject"
    )
    new_head = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
    assert calls["reprove_head"] == new_head != old_head, (
        "reprove must run against the advanced head"
    )
    # The confirmation reviewer never ran — a fabricated reprove cannot reach the confirmation round.
    assert len(reviewer.packets) == 1, "confirmation reviewer must not run on an unverified reprove"
    # A real fail-loud block is persisted; the run is never left accepted/done.
    record = store.RunStore().read(run)
    assert record["status"] == "paused"
    block = record.get("contract_review", {})
    assert block.get("verdict", "").startswith("blocking"), "no blocking contract_review persisted"
    assert block.get("accepted") is not True, "gate persisted accepted state on a rejected reprove"


@pytest.mark.parametrize("path_kind", ["success", "fail_loud", "exception", "reviewer_raises"])
def test_materialized_inputs_removed_on_all_exit_paths(tmp_path, fake_provider_script, path_kind):
    """The run-owned materialized input dir is removed in a ``finally`` on EVERY exit path — each path's
    outcome is FORCED and asserted: a clean ``done``, a fail-loud ``blocking`` return, an exception AFTER
    the reviewer returns (shared-session raise), AND an exception raised INSIDE the reviewer callback (so
    the cleanup provably wraps the reviewer invocation, not just result validation). On every path the
    six inputs are proven materialized BYTE-EXACT in ONE real, non-symlink run-owned subdir of dest, and
    afterward that dir is gone and NO stray path survives anywhere under dest (#82 item 2).

    technical (contract): success -> accepted True / verdict "done"; fail_loud (empty output) -> accepted
    False / verdict startswith "blocking:"; exception -> raises SharedSessionError; reviewer_raises ->
    the reviewer callback raises RuntimeError after materialization. In every case the captured inputs
    equal their expected bytes, share one live non-symlink parent whose resolved parent is dest, and
    after the gate returns/raises that dir is absent and ``dest.rglob('*')`` is empty.
    """
    from issueforge import providers

    scen = _red_scenario(tmp_path, "cleanup")
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    inputs = _default_inputs()
    if path_kind == "fail_loud":
        specs = [{"out": [], "correspondence": True}]  # empty output -> fail-loud blocking:1
        shared = None
    elif path_kind == "exception":
        specs = [{"out": ["APPROVE"], "correspondence": True, "shared": True}]
        shared = _AUTH_SESSION
    else:  # success or reviewer_raises
        specs = [{"out": ["APPROVE"], "correspondence": True}]
        shared = None
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        specs,
        scen=scen,
        dest=dest,
        shared_session=shared,
        expect_inputs=inputs,
    )

    if path_kind == "reviewer_raises":
        base = reviewer

        def raising(packet):
            base(packet)  # gate already materialized; record packet/captured/dirs, then raise
            raise RuntimeError("reviewer callback boom")

        raising.packets = base.packets
        raising.captured = base.captured
        raising.captured_dirs = base.captured_dirs
        reviewer = raising

    if path_kind == "exception":
        with pytest.raises(providers.SharedSessionError):
            _review(run, scen, reviewer=reviewer, dest=dest, inputs=inputs, max_rounds=1)
    elif path_kind == "reviewer_raises":
        with pytest.raises(RuntimeError):
            _review(run, scen, reviewer=reviewer, dest=dest, inputs=inputs, max_rounds=1)
    else:
        review = _review(run, scen, reviewer=reviewer, dest=dest, inputs=inputs, max_rounds=1)
        if path_kind == "success":
            assert review.accepted is True and review.verdict == "done"
        else:  # fail_loud
            assert review.accepted is False and review.verdict.startswith("blocking:")

    # Non-vacuous: all six inputs were materialized BYTE-EXACT (captured at call time) on THIS path.
    assert reviewer.captured, "reviewer never received materialized inputs"
    got_bytes = dict(reviewer.captured[0])
    assert got_bytes.pop("head_sha").decode().strip() == scen.candidate_sha
    assert got_bytes == {
        "diff": inputs["diff"].encode(),
        "contract": inputs["contract"].encode(),
        "manifest": inputs["manifest"].encode(),
        "red_evidence": inputs["red_evidence"].encode(),
        "proof_command": _PROOF_CMD.encode(),
    }, "inputs not materialized byte-exact on this exit path"

    got = reviewer.packets[0]["inputs"]
    parents = {Path(p).parent for p in got.values()}
    assert len(parents) == 1, "inputs scattered across multiple directories"
    owned = parents.pop()
    assert owned.parent == dest and owned != dest, "inputs not in a single run-owned subdir of dest"
    # Live directory facts captured during the callback: a real, non-symlink dir resolving inside dest.
    dfacts = reviewer.captured_dirs[0]
    assert dfacts["is_symlink"] is False, "run dir is a symlink (possible escape from dest)"
    assert dfacts["resolved"].parent == dest.resolve(), "run dir resolves outside dest"
    # The whole run-owned dir is removed on THIS exit path; NO stray path survives anywhere under dest.
    assert not owned.exists(), f"run-owned input dir lingered after the {path_kind} path"
    assert list(dest.rglob("*")) == [], f"stray paths under dest after the {path_kind} path"


def test_first_pass_exhaustive_all_findings_ordered_in_packet(tmp_path, fake_provider_script):
    """The first pass carries the exhaustive-enumeration instruction and records ALL findings in order
    (it does not stop at the first). Without that instruction the reviewer truncates — so the gate is
    obligated to send it.

    technical (contract): a single blocking round carrying three findings, max_rounds=1 with the
    reviewer requiring the exhaustive instruction -> verdict "blocking:3", the persisted
    contract_review ``findings`` == the exact ordered list, and the audit packet echoes the response.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [
            {
                "out": ["REJECT one two three"],
                "correspondence": False,
                "findings": ("f1", "f2", "f3"),
            }
        ],
        scen=scen,
        dest=dest,
        require_exhaustive=True,
    )
    review = _review(run, scen, reviewer=reviewer, dest=dest, max_rounds=1)
    assert review.verdict == "blocking:3"
    assert list(store.RunStore().read(run)["contract_review"]["findings"]) == ["f1", "f2", "f3"]
    assert "REJECT one two three" in Path(review.packet_path).read_text()


def _valid_reproof(scen):
    """A reprove seam (post-#82) returning an ACCEPTED ``RedProof`` bound to the advanced head — the
    valid replacement for the old bare-``_RED_A``-string stub, now that the reprove seam must yield a
    re-verified proof rather than a raw evidence string (#82 item 1)."""
    from issueforge import contract

    def reprove(head_sha):
        return contract.RedProof(
            accepted=True,
            reason="behavioral_red",
            records=(
                contract.RedRecord(
                    nodeid=_NEW_X,
                    exception_type="AssertionError",
                    assertion_line=2,
                    message="assert 1 == 2",
                ),
            ),
            base_sha=scen.base_sha,
            added_ids=(_NEW_X,),
            head_sha=head_sha,
        )

    return reprove


def test_persistent_blocking_stops_at_default_two_rounds_counted(tmp_path, fake_provider_script):
    """``contract_review_rounds`` defaults to 2: a persistently-blocking reviewer is invoked EXACTLY
    twice (two distinct fresh sessions, two review events), the counter persists 2, and the run pauses
    — never a fabricated count from a single call.

    technical (contract): a reviewer that blocks every round, a fixer/reprove supplied, max_rounds=None
    -> the reviewer callback ran exactly 2 times with 2 distinct session ids, 2 ``review`` events,
    manifest ``contract_review_rounds`` == 2, ContractReview.rounds == 2, verdict starts "blocking:",
    run status "paused".
    """
    scen = _red_scenario(tmp_path, "persist")
    run = _review_run(scen)
    dest = tmp_path / "pkt"

    def fixer():
        (scen.candidate_worktree / "tests/test_new.py").write_text(
            "def test_x():\n    assert 7 == 8\n"
        )
        _git(scen.candidate_worktree, "add", "-A")
        _git(scen.candidate_worktree, "commit", "-qm", "fix")

    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["REJECT"], "correspondence": False, "findings": ("x",)}],
        scen=scen,
        dest=dest,
    )
    review = _review(
        run,
        scen,
        reviewer=reviewer,
        dest=dest,
        max_rounds=None,
        fixer=fixer,
        reprove=_valid_reproof(scen),
    )
    assert len(reviewer.results) == 2
    assert len({r.session_id for r in reviewer.results}) == 2
    assert len(_events(run, "review")) == 2
    record = store.RunStore().read(run)
    assert record["contract_review_rounds"] == 2 and review.rounds == 2
    assert review.verdict.startswith("blocking:") and record["status"] == "paused"


def test_reopen_only_on_new_blocking_finding(tmp_path, fake_provider_script):
    """Above the default bound, a confirmation round REOPENS the review only when it introduces a NEW
    blocking finding; a confirmation that repeats the SAME finding does not reopen — it terminates
    blocked (paused, exactly two rounds recorded), never a fabricated ``done``.

    technical (contract): with max_rounds=4 and a reviewer whose rounds repeat the SAME finding "a",
    the review stops after round 2 (no new finding): exactly 2 calls, verdict starts "blocking:", run
    paused, 2 review events. With rounds [block("a"), block("b"), clean] it reopens on the new "b" and
    reaches done at round 3.
    """
    scen = _red_scenario(tmp_path, "reopen")

    def mk_fixer(sc):
        def fixer():
            p = sc.candidate_worktree / "tests/test_new.py"
            p.write_text(p.read_text() + "\n# touch\n")
            _git(sc.candidate_worktree, "add", "-A")
            _git(sc.candidate_worktree, "commit", "-qm", "fix")

        return fixer

    same_run = _review_run(scen, "same")
    same_rev = _make_reviewer(
        fake_provider_script,
        same_run,
        [{"out": ["REJECT"], "correspondence": False, "findings": ("a",)}],
        scen=scen,
        dest=tmp_path / "same",
    )
    same = _review(
        same_run,
        scen,
        reviewer=same_rev,
        dest=tmp_path / "same",
        max_rounds=4,
        fixer=mk_fixer(scen),
        reprove=_valid_reproof(scen),
    )
    assert len(same_rev.results) == 2, (
        "a repeated finding must not reopen past the confirmation round"
    )
    assert same.verdict.startswith("blocking:")
    assert store.RunStore().read(same_run)["status"] == "paused"
    assert len(_events(same_run, "review")) == 2

    scen2 = _red_scenario(tmp_path, "reopen2")
    new_run = _review_run(scen2, "newf")
    new_rev = _make_reviewer(
        fake_provider_script,
        new_run,
        [
            {"out": ["REJECT"], "correspondence": False, "findings": ("a",)},
            {"out": ["REJECT"], "correspondence": False, "findings": ("b",)},
            {"out": ["APPROVE"], "correspondence": True, "findings": ()},
        ],
        scen=scen2,
        dest=tmp_path / "newf",
    )
    review = _review(
        new_run,
        scen2,
        reviewer=new_rev,
        dest=tmp_path / "newf",
        max_rounds=4,
        fixer=mk_fixer(scen2),
        reprove=_valid_reproof(scen2),
    )
    assert review.verdict == "done" and review.rounds == 3


def test_counter_incremented_through_store_apply_and_isolated(
    tmp_path, fake_provider_script, monkeypatch
):
    """The round counter is incremented CUMULATIVELY, one-per-round, THROUGH ``RunStore.apply`` (the
    under-lock write primitive) — a lock-free write or a constant assignment is caught — and the gate
    does NOT touch ``review_rounds`` / ``repair_attempts`` (US-6 counters owned by S14, downstream).

    technical (contract): seed contract_review_rounds=10, review_rounds=5, repair_attempts=7; a spy on
    RunStore.apply counts how many apply calls RAISED contract_review_rounds by exactly 1. A two-round
    review -> exactly 2 such counter-incrementing apply calls, final counter == 12 (cumulative, not
    reset to 2) and an int; review_rounds still 5 and repair_attempts still 7 (untouched).
    """
    from issueforge import store as store_mod

    scen = _red_scenario(tmp_path, "counter")
    run = _review_run(scen)
    store.RunStore().apply(
        run, lambda _r: {"contract_review_rounds": 10, "review_rounds": 5, "repair_attempts": 7}
    )
    real_apply = store_mod.RunStore.apply
    seen = {"prev": 10}
    inc = {"n": 0}

    def spy(self, run_id, transform, **kwargs):
        merged = real_apply(self, run_id, transform, **kwargs)
        cur = merged.get("contract_review_rounds")
        if isinstance(cur, int) and cur == seen["prev"] + 1:
            inc["n"] += 1
        if isinstance(cur, int):
            seen["prev"] = cur
        return merged

    monkeypatch.setattr(store_mod.RunStore, "apply", spy)

    def fixer():
        (scen.candidate_worktree / "tests/test_new.py").write_text(
            "def test_x():\n    assert 9 == 10\n"
        )
        _git(scen.candidate_worktree, "add", "-A")
        _git(scen.candidate_worktree, "commit", "-qm", "fix")

    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["REJECT"], "correspondence": False, "findings": ("x",)}],
        scen=scen,
        dest=tmp_path / "pkt",
    )
    _review(
        run,
        scen,
        reviewer=reviewer,
        dest=tmp_path / "pkt",
        max_rounds=2,
        fixer=fixer,
        reprove=_valid_reproof(scen),
    )
    record = store.RunStore().read(run)
    assert type(record["contract_review_rounds"]) is int
    assert record["contract_review_rounds"] == 12  # cumulative from the seeded 10, not reset to 2
    assert inc["n"] == 2, "counter must be incremented once per round through the under-lock apply"
    assert record["review_rounds"] == 5 and record["repair_attempts"] == 7


# ------------------------------------------------------- R. Override + verdict vocabulary (US-5.4)


def test_override_records_full_provenance_and_updates_outcome(tmp_path, fake_provider_script):
    """A failed review may be explicitly overridden; the override RECORDS who/why/when (a PARSEABLE
    timestamp), the NEW verdict, the verdict it OVERRODE, the reviewed head, and the reviewer
    session/provider/method — and updates the manifest outcome. An override is REFUSED when there is no
    failed review to override (no review block, or an already-successful ``done`` review).

    technical (contract): after a blocking review, override_contract_review(run, by="matt",
    reason="verified by hand", verdict="done", method="human") records an ``override`` event carrying
    by/reason/verdict plus overrode=="blocking:1", head_sha==candidate head, reviewer_session_id (the
    real reviewer session), provider=="reviewer-cli", method=="human", and a datetime.fromisoformat-
    parseable ``when``; the manifest contract_review outcome becomes "done". Overriding a run with no
    failed review, or one whose review already succeeded, raises ValueError.
    """
    from datetime import datetime

    from issueforge import contract

    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["REJECT"], "correspondence": False, "findings": ("a",)}],
        scen=scen,
        dest=dest,
    )
    _review(run, scen, reviewer=reviewer, dest=dest, max_rounds=1)
    contract.override_contract_review(
        run, by="matt", reason="verified by hand", verdict="done", method="human"
    )
    ev = _events(run, "override")[-1]
    assert ev["by"] == "matt" and ev["reason"] == "verified by hand" and ev["verdict"] == "done"
    assert ev["overrode"] == "blocking:1"
    assert ev["head_sha"] == scen.candidate_sha
    assert ev["reviewer_session_id"] == reviewer.results[-1].session_id
    assert ev["provider"] == "reviewer-cli" and ev["method"] == "human"
    datetime.fromisoformat(ev["when"])  # parseable timestamp, not a truthy token
    assert store.RunStore().read(run)["contract_review"]["outcome"] == "done"

    # No failed review to override -> refused.
    clean = _review_run(scen, "no-failed")
    with pytest.raises(ValueError):
        contract.override_contract_review(
            clean, by="matt", reason="x", verdict="done", method="human"
        )
    # An already-successful review -> nothing to override -> refused.
    ok_run = _review_run(scen, "already-done")
    ok_rev = _make_reviewer(
        fake_provider_script,
        ok_run,
        [{"out": ["APPROVE"], "correspondence": True}],
        scen=scen,
        dest=tmp_path / "okd",
    )
    _review(ok_run, scen, reviewer=ok_rev, dest=tmp_path / "okd")
    with pytest.raises(ValueError):
        contract.override_contract_review(
            ok_run, by="matt", reason="x", verdict="done", method="human"
        )


def test_override_is_not_a_silent_retry(tmp_path, fake_provider_script):
    """An override is a first-class recorded EVENT, never a re-invocation of the reviewer: exactly one
    new ``override`` event, and no new ``review`` event, transcript, reviewer session, or round.

    technical (contract): snapshot the review-event count, transcript files, recorded reviewer session,
    and contract_review_rounds; override_contract_review adds exactly one ``override`` event and leaves
    the review-event count, transcript set, recorded reviewer session id, and round counter unchanged.
    """
    from issueforge import contract

    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [{"out": ["REJECT"], "correspondence": False, "findings": ("a",)}],
        scen=scen,
        dest=dest,
    )
    _review(run, scen, reviewer=reviewer, dest=dest, max_rounds=1)
    reviews_before = len(_events(run, "review"))
    transcripts_before = {p.name for p in store.run_dir(run).glob("transcript-*")}
    before = store.RunStore().read(run)
    rounds_before = before["contract_review_rounds"]
    session_before = before["contract_review"]["reviewer_session_id"]
    contract.override_contract_review(run, by="matt", reason="ok", verdict="done", method="human")
    after = store.RunStore().read(run)
    assert len(_events(run, "override")) == 1
    assert len(_events(run, "review")) == reviews_before
    assert {p.name for p in store.run_dir(run).glob("transcript-*")} == transcripts_before
    assert after["contract_review_rounds"] == rounds_before
    assert after["contract_review"]["reviewer_session_id"] == session_before


@pytest.mark.parametrize(
    "verdict, legal",
    [
        ("done", True),
        ("blocking:2", True),
        ("skipped:provider-unavailable", True),
        ("blocking", False),  # count required
        ("blocking:0", False),  # count must be positive
        ("blocking:-1", False),  # non-negative decimal only
        ("blocking:1x", False),  # trailing junk
        ("skipped", False),  # reason required
        ("skipped:   ", False),  # whitespace-only reason
        ("skipped:line1\nline2", False),  # one-line reason only
        ("approved", False),  # out of vocabulary
        ("", False),
    ],
)
def test_valid_contract_verdict_boundaries(verdict, legal):
    """The verdict vocabulary is exactly ``done`` / ``blocking:<positive int>`` / ``skipped:<non-empty
    one-line reason>`` — a zero/negative/junk count, a bare keyword, a whitespace-only or multi-line
    skip reason, or an unknown token is rejected (ported from ``_validate_cross_review``).

    technical (contract): valid_contract_verdict(v) matches the parametrized legality across the
    boundary cases.
    """
    from issueforge import contract

    assert contract.valid_contract_verdict(verdict) is legal


def test_terminal_verdict_enforced_on_terminal_write(tmp_path):
    """The vocabulary is ENFORCED when the review is finalized at TERMINAL status — not merely a
    standalone predicate. Finalizing with an out-of-vocabulary or malformed verdict is refused and
    leaves the run non-terminal; a valid verdict finalizes.

    technical (contract): finalize_review(run, "done") drives the run to a terminal status with the
    recorded verdict; finalize_review(run, "approved") and finalize_review(run, "blocking:0") each raise
    ValueError, write no terminal verdict, and leave the run status non-terminal.
    """
    from issueforge import contract
    from issueforge.state import TERMINAL

    terminal_values = {s.value for s in TERMINAL}
    run = _mk_run()
    for bad in ("approved", "blocking:0"):
        with pytest.raises(ValueError):
            contract.finalize_review(run, bad)
    assert store.RunStore().read(run)["status"] not in terminal_values
    contract.finalize_review(run, "done")
    record = store.RunStore().read(run)
    assert record["status"] in terminal_values
    assert record["contract_review"]["verdict"] == "done"


# ------------------------------------------------------------ S. Audit packet + redaction canary


@pytest.mark.parametrize("path_kind", ["success", "failure"])
def test_full_review_packet_structured_and_retained(tmp_path, fake_provider_script, path_kind):
    """The full review packet is retained as a STRUCTURED auditable artifact — every named field of the
    round is reconstructible — on BOTH the success and the failure path (a FAILED review still persists
    its packet).

    technical (contract): after a review, ContractReview.packet_path exists under the run dir and its
    text contains the reviewed head sha, the literal proof command, the reviewer provider name, the
    reviewer session id, the invocation status, the reviewer stdout AND stderr, the verdict, and every
    materialized input name — for a completed (status OK) AND a failed (non-zero) invocation. Not a
    one-token ledger field.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    exit_code = 0 if path_kind == "success" else 1
    out_marker, err_marker = "PACKET-OUT-42", "PACKET-ERR-42"
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [
            {
                "out": [f"APPROVE {out_marker}"],
                "err": [err_marker],
                "exit": exit_code,
                "correspondence": True,
            }
        ],
        scen=scen,
        dest=dest,
    )
    review = _review(run, scen, reviewer=reviewer, dest=dest, max_rounds=1)
    packet = Path(review.packet_path)
    assert packet.exists() and packet.parent == store.run_dir(run)
    body = packet.read_text()
    real = reviewer.results[-1]
    for token in (
        scen.candidate_sha,
        _PROOF_CMD,
        "reviewer-cli",
        real.session_id,
        out_marker,
        err_marker,
        review.verdict,
        "diff",
        "contract",
        "manifest",
        "red_evidence",
    ):
        assert token in body, f"packet missing {token!r}"


@pytest.mark.parametrize(
    "path_kind", ["success", "failure_nonzero", "failure_empty", "failure_timeout"]
)
def test_redaction_canary_copied_into_packet_then_redacted(
    tmp_path, fake_provider_script, path_kind
):
    """A synthetic secret seeded into each review-packet field (reviewer response, ECHOED input,
    captured stderr) is copied INTO the S11 packet and then redacted — the field MARKER proves the
    field was copied in, the ``[REDACTED]`` marker proves it was scrubbed, and the raw canary appears
    in ZERO persisted artifacts. Holds on the success path AND every failure branch (where the raw
    stderr is exactly where a leak hides).

    technical (contract): with a canary+marker in stdout, the red_evidence input, and stderr, and all
    three canaries declared secrets: the S11 packet exists; NO file under the state root contains any
    raw canary; and the packet contains each field marker (RESPFIELD/INPUTFIELD/STDERRFIELD) — proving
    the fields were copied in — alongside ``[REDACTED]`` — proving they were scrubbed — for a completed
    review AND non-zero / empty-output / timeout failures.
    """
    scen = _red_scenario(tmp_path)
    run = _review_run(scen)
    dest = tmp_path / "pkt"
    resp = f"{_MARK_RESPONSE} {_CANARY_RESPONSE}"
    err = f"{_MARK_STDERR} {_CANARY_STDERR}"
    spec = {
        "success": {"out": [resp], "err": [err], "exit": 0},
        "failure_nonzero": {"out": [resp], "err": [err], "exit": 1},
        "failure_empty": {"out": [], "err": [err], "exit": 0},
        "failure_timeout": {"out": [resp], "err": [err], "sleep": 5.0, "timeout": 0.3},
    }[path_kind]
    spec["correspondence"] = True
    secrets = frozenset({_CANARY_RESPONSE, _CANARY_INPUT, _CANARY_STDERR})
    reviewer = _make_reviewer(
        fake_provider_script,
        run,
        [spec],
        scen=scen,
        dest=dest,
        secrets=secrets,
    )
    inputs = _default_inputs(f"{_MARK_INPUT} call-phase FAILED: {_CANARY_INPUT}\n")
    review = _review(
        run, scen, reviewer=reviewer, dest=dest, secrets=secrets, max_rounds=1, inputs=inputs
    )
    packet = Path(review.packet_path)
    assert packet.exists()
    for path in _state_files():
        text = path.read_text(errors="ignore")
        for canary in (_CANARY_RESPONSE, _CANARY_INPUT, _CANARY_STDERR):
            assert canary not in text, f"{canary} leaked into {path} on the {path_kind} path"
    body = packet.read_text()
    assert "[REDACTED]" in body, "packet never carried the redacted fields"
    # The input field is copied into the packet on every path; response/stderr on paths that produced them.
    assert _MARK_INPUT in body, "input field not copied into the packet"
    if path_kind != "failure_empty":
        assert _MARK_RESPONSE in body, "response field not copied into the packet"
    assert _MARK_STDERR in body, "stderr field not copied into the packet"


# =====================================================================================
# S12 (#18) — freeze / manifest gate (Group B) + boundary mutations (Group C).
#
# The FREEZE composes the protected contract boundary from the adapter's discovered closure,
# hashes every protected file's COMMITTED blob, mirrors ``engine.apply_revision`` (approver BEFORE
# any write, rejection mutates nothing, approval appends one event + persists the manifest), and
# consumes ``require_current_evidence``. Every symbol below (``freeze_contract``, the adapter's
# ``discover_contract_dependencies``/``ContractClosure``/``DiscoveryError``) lands in S12 and does
# not exist yet, so each test imports inside its body and is
# @pytest.mark.xfail(strict=True, reason="PENDING (#18)").
#
# Assumed public API this suite AUTHORS (the contract /spec-dev implements to):
#   issueforge.contract.freeze_contract(
#       run_id, *, candidate_worktree, base_sha, adapter, provisioner, approver,
#       user_added_paths=(), secrets=frozenset(), store=None) -> FreezeResult
#     - resolves contract_commit = the real committed HEAD of candidate_worktree;
#     - refuses (ValueError) unless require_current_evidence(record, HEAD) holds and the run's
#       accepted S10 red_proof is bound to that HEAD;
#     - runs adapter.discover_contract_dependencies over the candidate collection in the PROVISIONED
#       hermetic env, so external pins/plugins resolve from the provisioned interpreter (a version
#       present there is what gets pinned) — never the parent process (D5 2026-07-22); a DiscoveryError
#       propagates as a named freeze refusal (nothing persisted);
#     - composes contract_paths per the schema formula (test_files ∪ fixture_closure ∪
#       (test_body_imports − write_scope) ∪ {selected config} ∪ {.issueforge.toml} ∪ user_added),
#       stored paths verbatim (symlinks never collapsed);
#     - refuses (ValueError naming it) any protected-category path that lexically collides with the
#       write scope; records test_body_imports ∩ write_scope in excluded_sut;
#     - hashes each protected path's committed blob (a symlink -> its resolved in-repo target's
#       committed blob) into dep_hashes; records symlink targets in symlinks;
#     - persists ONE canonical manifest artifact (sorted keys, UTF-8, no self hash) via the store's
#       redacting write_artifact, shows the approver the EXACT persisted bytes, and on approval
#       appends a ``freeze`` event carrying {"approved": true, "manifest_hash": sha256(bytes)}.
#   FreezeResult: .approved (bool), .manifest (dict schema or None), .manifest_hash (str or None),
#       .artifact_path (Path or None).
# =====================================================================================

_MANIFEST_ARTIFACT = "contract-manifest.json"


def _approve_all(_payload) -> bool:
    return True


def _reject_all(_payload) -> bool:
    return False


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _committed_blob(repo, ref: str) -> bytes:
    """The raw committed blob bytes of ``ref`` (e.g. ``<commit>:tests/helpers.py``) — the independent
    symlink-aware hashing oracle for dep_hashes."""
    result = subprocess.run(["git", "-C", str(repo), "show", ref], capture_output=True)
    return result.stdout


def _canonical_bytes(obj) -> bytes:
    """The canonical manifest serialization the freeze must emit: sorted keys, compact separators,
    ensure_ascii=False, UTF-8, no trailing newline."""
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _collect_ids(worktree) -> tuple[str, ...]:
    """The sorted/dedup canonical node-id set of ``worktree`` via the real adapter seam — the
    independent oracle for manifest ``collected_ids``."""
    adapter = _adapter()
    handle = adapter.provision_environment(worktree, None, provisioner=_provisioner())
    invocation = SimpleNamespace(
        worktree=Path(worktree),
        interpreter=handle.interpreter,
        command=["-m", "pytest"],
        env=getattr(handle, "env", None),
    )
    return tuple(sorted(set(adapter.canonical_collect(invocation).ids)))


def _seed_freeze_proof(
    run_id,
    scen,
    *,
    added_ids=(_NEW_X,),
    accepted=True,
    reason="behavioral_red",
    head=None,
    base_sha=None,
):
    head = scen.candidate_sha if head is None else head
    base = scen.base_sha if base_sha is None else base_sha
    first = added_ids[0] if added_ids else ""
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "red_proof": {
                "accepted": accepted,
                "reason": reason,
                "base_sha": base,
                "head_sha": head,
                "added_ids": list(added_ids),
                "records": [
                    {
                        "nodeid": first,
                        "exception_type": "AssertionError",
                        "assertion_line": 2,
                        "message": "assert 1 == 2",
                    }
                ],
            }
        },
    )


def _seed_done_review(run_id, scen, *, head=None, outcome="done", verdict="done"):
    head = scen.candidate_sha if head is None else head
    store.RunStore().apply(
        run_id,
        lambda _r: {
            "contract_review": {
                "verdict": verdict,
                "outcome": outcome,
                "head_sha": head,
                "reviewer_session_id": "rev-1",
                "authoring_session_id": "auth-1",
                "provider": "cli",
                "findings": [],
            }
        },
    )


def _fscen(root, name="freeze", *, extra=None):
    """A REAL two-commit repo whose candidate authors one genuine red (``test_x``) plus ``extra``."""
    files = {"tests/test_new.py": "def test_x():\n    assert 1 == 2\n"}
    if extra:
        files.update(extra)
    return _scenario(root, name, candidate_files=files)


def _write_scope(paths):
    return [{"op": "edit", "path": p, "justification": "sut"} for p in paths]


def _freeze_run(scen, *, write_scope=(), run_id="run-1", added_ids=(_NEW_X,), review_head=None):
    """Mint a buildable run seeded with an accepted S10 proof + a done contract-review bound to the
    candidate HEAD, plus the approved write scope — the precondition the freeze consumes."""
    run = _mk_run(run_id)
    ws = _write_scope(write_scope)
    store.RunStore().apply(run, lambda r: {"shape": {**r["shape"], "write_scope": ws}})
    _seed_freeze_proof(run, scen, added_ids=added_ids)
    _seed_done_review(run, scen, head=review_head)
    return run


def _freeze(run, scen, *, approver=None, user_added_paths=(), secrets=frozenset()):
    from issueforge import contract

    return contract.freeze_contract(
        run,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        approver=_approve_all if approver is None else approver,
        user_added_paths=tuple(user_added_paths),
        secrets=frozenset(secrets),
    )


def _freeze_prov(run, scen, provisioner, *, user_added_paths=(), secrets=frozenset()):
    """Freeze driving discovery through a caller-supplied PROVISIONER — used by the pin tests so
    external identities resolve from a REAL provisioned interpreter (D5 2026-07-22: pins/plugins
    resolve in the provisioned hermetic env, never the parent process)."""
    from issueforge import contract

    return contract.freeze_contract(
        run,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha,
        adapter=_adapter(),
        provisioner=provisioner,
        approver=_approve_all,
        user_added_paths=tuple(user_added_paths),
        secrets=frozenset(secrets),
    )


def _real_pin_provisioner(pins):
    """A provisioner that builds a REAL, SEPARATE venv with ``pins`` installed (the DEFAULT provision
    path, interpreter != sys.executable), so discovery resolves external identity from the PROVISIONED
    interpreter — a version present in that venv is what gets pinned, never a parent-process
    ``importlib.metadata`` reading. Autoload stays ON so entry-point plugins load in that env."""

    def _provision(worktree, frozen_deps=None):
        merged = dict(pins)
        if frozen_deps:
            merged.update(frozen_deps)
        return _adapter().provision_environment(worktree, merged)

    return _provision


def _pin_set(manifest):
    """Normalize ``manifest['external_pins']`` to a set of ``(dist, version)`` pairs (dict or
    pair-list) — the shape-tolerant oracle for the provisioned pins."""
    raw = manifest["external_pins"]
    if isinstance(raw, dict):
        return {(str(k), str(v)) for k, v in raw.items()}
    return {(str(d), str(v)) for d, v in raw}


def _collect_ids_cmd(worktree, command):
    """The sorted/dedup canonical node-id set of ``worktree`` collected with an EXPLICIT command —
    the oracle for 'what a hardcoded ``-m pytest`` would collect' vs the configured baseline."""
    adapter = _adapter()
    handle = adapter.provision_environment(worktree, None, provisioner=_provisioner())
    invocation = SimpleNamespace(
        worktree=Path(worktree),
        interpreter=handle.interpreter,
        command=list(command),
        env=getattr(handle, "env", None),
    )
    return tuple(sorted(set(adapter.canonical_collect(invocation).ids)))


def _freeze_events(run):
    return _events(run, "freeze")


def _manifest_artifact_path(run):
    from issueforge.paths import run_dir

    return run_dir(run) / _MANIFEST_ARTIFACT


def _no_freeze_writes(run):
    """No freeze event, no manifest artifact — the atomic-refusal invariant."""
    return not _freeze_events(run) and not _manifest_artifact_path(run).exists()


# =============================================================== Group B — freeze / manifest


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_manifest_records_every_frozen_field_populated(tmp_path):
    """On approval the manifest freezes EVERY schema field, each populated from the scenario — the
    contract commit, per-file hashes, the discovered closure, config, command arrays, collected ids,
    red evidence, review verdict, and write scope.

    technical: every schema key present AND non-empty (no placeholder value).
    """
    scen = _fscen(tmp_path, extra={"tests/helpers.py": "H = 1\n"})
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    m = res.manifest
    for key in (
        "contract_commit",
        "contract_paths",
        "dep_hashes",
        "symlinks",
        "external_pins",
        "excluded_sut",
        "test_config",
        "command",
        "collected_ids",
        "red_evidence",
        "contract_review",
        "write_scope",
    ):
        assert key in m, f"missing manifest key {key!r}"
    assert m["contract_commit"] == scen.candidate_sha
    assert m["contract_paths"] and m["dep_hashes"]
    assert m["collected_ids"] and m["red_evidence"] and m["contract_review"]


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
@pytest.mark.parametrize(
    "mutate",
    ["verdict_not_accepted", "stale_head", "missing_review_block", "stale_red", "chain_mismatch"],
)
def test_freeze_refuses_noncurrent_review_evidence(tmp_path, mutate):
    """The freeze refuses on each distinct currency failure and leaves artifact/event/run-state
    untouched.

    technical: each of {verdict != accepted, stale head, missing review block, stale red evidence,
    evidence-chain mismatch} -> ValueError; no manifest artifact, no freeze event, status unchanged.
    """
    scen = _fscen(tmp_path, name=f"noncurrent-{mutate}")
    run = _freeze_run(scen)
    if mutate == "verdict_not_accepted":
        _seed_done_review(run, scen, outcome="blocking:1", verdict="blocking:1")
    elif mutate == "stale_head":
        _seed_done_review(run, scen, head="deadbeef")
    elif mutate == "missing_review_block":
        store.RunStore().apply(run, lambda r: {k: v for k, v in r.items()})  # no-op keep
        # Remove the review block by rewriting the record without it.
        rec = store.RunStore().read(run)
        rec.pop("contract_review", None)
        store.RunStore().apply(run, lambda _r: {"contract_review": None})
    elif mutate == "stale_red":
        _seed_freeze_proof(run, scen, head="deadbeef")
    elif mutate == "chain_mismatch":
        _seed_freeze_proof(run, scen, accepted=False, reason="not_red")

    before = store.RunStore().read(run)["status"]
    with pytest.raises(ValueError):
        _freeze(run, scen)
    assert _no_freeze_writes(run)
    assert store.RunStore().read(run)["status"] == before


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_binds_contract_commit_ancestor_of_implementation_independent_oracle(tmp_path):
    """The frozen contract commit is the real committed HEAD — an ancestor of a later implementation
    commit — verified against an independent git oracle, and it is distinct from the base sha.

    technical: manifest["contract_commit"] == the oracle HEAD == scen.candidate_sha != base_sha, and
    is a merge-base ancestor of a later impl commit.
    """
    scen = _fscen(tmp_path, name="ancestor")
    run = _freeze_run(scen)
    oracle_head = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
    res = _freeze(run, scen)
    assert res.manifest["contract_commit"] == oracle_head == scen.candidate_sha
    assert res.manifest["contract_commit"] != scen.base_sha
    # A later implementation commit: the frozen contract commit is its ancestor.
    (scen.candidate_worktree / "impl.py").write_text("X = 1\n")
    _git(scen.candidate_worktree, "add", "-A")
    _git(scen.candidate_worktree, "commit", "-qm", "impl")
    impl = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
    anc = subprocess.run(
        [
            "git",
            "-C",
            str(scen.candidate_worktree),
            "merge-base",
            "--is-ancestor",
            oracle_head,
            impl,
        ]
    )
    assert anc.returncode == 0


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_rejects_dirty_protected_file_never_freezes_uncommitted_bytes(tmp_path):
    """An uncommitted change to a protected file makes the freeze refuse — bytes are frozen from the
    committed blob, never the dirty worktree.

    technical: modify committed tests/test_new.py uncommitted, freeze -> refuses naming the dirty
    path; nothing persisted.
    """
    scen = _fscen(tmp_path, name="dirty")
    run = _freeze_run(scen)
    (scen.candidate_worktree / "tests/test_new.py").write_text(
        "def test_x():\n    assert 1 == 2  # dirty\n"
    )
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, scen)
    assert "tests/test_new.py" in str(excinfo.value)
    assert _no_freeze_writes(run)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_dep_hashes_domain_equals_contract_paths_exact_blob_hashes(tmp_path):
    """Every protected in-repo file has an exact committed-blob sha256, and the hash-map domain equals
    contract_paths exactly.

    technical: set(dep_hashes) == set(contract_paths); each value == sha256 of the committed blob
    computed by an independent oracle.
    """
    scen = _fscen(tmp_path, name="dephash", extra={"tests/helpers.py": "H = 1\n"})
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    m = res.manifest
    assert set(m["dep_hashes"]) == set(m["contract_paths"])
    for path, digest in m["dep_hashes"].items():
        target = m.get("symlinks", {}).get(path, path)
        oracle = _sha256(_committed_blob(scen.candidate_worktree, f"{scen.candidate_sha}:{target}"))
        assert digest == oracle, f"hash mismatch for {path}"


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_two_sets_disjoint_after_normalization_alias_collision_named(tmp_path):
    """The contract set and write scope share no path after normalization; a path ALIAS is caught, not
    passed as distinct.

    technical: a write_scope alias ``tests/../tests/helpers.py`` of a discovered contract path -> named
    collision + freeze refusal; otherwise contract_paths ∩ write_scope == ∅, both non-empty.
    """
    scen = _fscen(tmp_path, name="alias", extra={"tests/helpers.py": "H = 1\n"})
    run = _freeze_run(scen, write_scope=("tests/../tests/helpers.py",))
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, scen)
    assert "helpers.py" in str(excinfo.value)
    assert _no_freeze_writes(run)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
@pytest.mark.parametrize(
    "category, path, extra",
    [
        ("fixture_helper", "tests/helpers.py", {"tests/helpers.py": "H = 1\n"}),
        ("test_module", "tests/test_new.py", None),
        ("selected_config", "pytest.ini", {"pytest.ini": "[pytest]\n"}),
        ("issueforge_toml", ".issueforge.toml", None),
        ("user_added", "extra/thing.py", {"extra/thing.py": "T = 1\n"}),
    ],
)
def test_freeze_fails_when_any_protected_category_is_in_write_scope(
    tmp_path, category, path, extra
):
    """A path proposed as BOTH a contract input and an implementation target fails the freeze, naming
    it — for every protected category.

    technical: each of {fixture helper, test module, selected config, .issueforge.toml, user-added}
    placed in write_scope -> named freeze failure; never silently dropped.
    """
    files = {"tests/helpers.py": "H = 1\n"}
    if extra:
        files.update(extra)
    scen = _fscen(tmp_path, name=f"cat-{category}", extra=files)
    user_added = ("extra/thing.py",) if category == "user_added" else ()
    run = _freeze_run(scen, write_scope=(path,))
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, scen, user_added_paths=user_added)
    assert path in str(excinfo.value)
    assert _no_freeze_writes(run)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_does_not_silently_sanitize_collisions(tmp_path):
    """The freeze never rewrites the sets to force disjointness; a real overlap fails rather than being
    edited away.

    technical: an overlapping write_scope (the discovered helper) -> ValueError; neither set had an
    entry deleted to fake disjointness (the freeze produced no manifest).
    """
    scen = _fscen(tmp_path, name="nosanitize", extra={"tests/helpers.py": "H = 1\n"})
    run = _freeze_run(scen, write_scope=("tests/helpers.py",))
    with pytest.raises(ValueError):
        _freeze(run, scen)
    assert _no_freeze_writes(run)
    # The approved write scope on the record is preserved verbatim (nothing silently deleted).
    ws = [e["path"] for e in store.RunStore().read(run)["shape"]["write_scope"]]
    assert ws == ["tests/helpers.py"]


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_excludes_in_scope_sut_surfaces_it_and_build_proceeds(tmp_path):
    """A test-body import IN the write scope is excluded from the frozen set, RECORDED as an editable
    SUT, and the build proceeds; editing it is allowed.

    technical: app/calc.py in test_body_imports ∩ write_scope -> not in contract_paths, in
    manifest["excluded_sut"]; freeze succeeds; a later edit of app/calc.py passes the write-scope check.
    """
    from issueforge import engine

    scen = _fscen(
        tmp_path,
        name="excluded-sut",
        extra={
            "app/calc.py": "def calc():\n    return 1\n",
            "tests/test_new.py": "from app.calc import calc\n\n\ndef test_x():\n    assert calc() == 2\n",
        },
    )
    run = _freeze_run(scen, write_scope=("app/calc.py",))
    res = _freeze(run, scen)
    assert "app/calc.py" in res.manifest["excluded_sut"]
    assert "app/calc.py" not in res.manifest["contract_paths"]
    diff = "--- a/app/calc.py\n+++ b/app/calc.py\n"
    assert engine.enforce_write_scope(run, diff) == []


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_protects_test_body_import_not_in_scope(tmp_path):
    """A test-body import NOT in the write scope stays protected (an under-scoped SUT is frozen).

    technical: app/other.py in test_body_imports, not in write_scope -> in contract_paths + dep_hashes.
    """
    scen = _fscen(
        tmp_path,
        name="body-protected",
        extra={
            "app/other.py": "def other():\n    return 1\n",
            "tests/test_new.py": "from app.other import other\n\n\ndef test_x():\n    assert other() == 2\n",
        },
    )
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    assert "app/other.py" in res.manifest["contract_paths"]
    assert "app/other.py" in res.manifest["dep_hashes"]


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_protects_directly_imported_oracle_outside_scope(tmp_path):
    """An oracle imported directly in a test body but kept OUT of the write scope is protected; its edit
    is caught.

    technical: tests/oracle.py in test_body_imports, not in write_scope -> in contract_paths; an edit
    mismatches its frozen hash.
    """
    scen = _fscen(
        tmp_path,
        name="oracle",
        extra={
            "tests/oracle.py": "GOLD = 42\n",
            "tests/test_new.py": "from oracle import GOLD\n\n\ndef test_x():\n    assert GOLD == 0\n",
        },
    )
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    frozen = res.manifest["dep_hashes"]["tests/oracle.py"]
    (scen.candidate_worktree / "tests/oracle.py").write_text("GOLD = 999\n")
    assert _sha256((scen.candidate_worktree / "tests/oracle.py").read_bytes()) != frozen


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_helpers_bypass_caught_via_frozen_hash(tmp_path):
    """After approval, mutating ONLY helpers.py is caught because its S12-frozen hash mismatches.

    technical: freeze -> mutate tests/helpers.py -> the working bytes' sha256 != manifest["dep_hashes"]
    entry (the bypass is detectable before build).
    """
    scen = _fscen(tmp_path, name="bypass", extra={"tests/helpers.py": "H = 1\n"})
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    frozen = res.manifest["dep_hashes"]["tests/helpers.py"]
    (scen.candidate_worktree / "tests/helpers.py").write_text("H = 999\n")
    assert _sha256((scen.candidate_worktree / "tests/helpers.py").read_bytes()) != frozen


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_write_scope_exactly_preserved(tmp_path):
    """The frozen write scope is EXACTLY the normalized approved shape["write_scope"].

    technical: manifest["write_scope"] == the normalized approved paths (none dropped/replaced).
    """
    scen = _fscen(tmp_path, name="ws-preserved")
    run = _freeze_run(scen, write_scope=("app/impl.py", "app/impl2.py"))
    res = _freeze(run, scen)
    assert tuple(sorted(res.manifest["write_scope"])) == ("app/impl.py", "app/impl2.py")


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_contract_paths_match_composition_formula_user_can_only_add(tmp_path):
    """The protected boundary equals the composition formula. A conftest-reached helper enters via
    fixture_closure and stays protected even when the user's .issueforge.toml contract list omits it,
    and a user_added path is included — user config can only ADD, never remove a discovered path.

    technical: tests/conftest.py imports tests/helpers.py (so helpers.py is in fixture_closure, not
    merely a committed file). A narrower contract_paths=["tests/test_new.py"] does NOT drop helpers.py;
    a user_added extra/thing.py is present; the test module + conftest are present. Because the helper
    is genuinely fixture-reached, this exercises the composition formula (an 'all committed - SUT' impl
    would also pass this positive case — the negative test below is the discriminator).
    """
    scen = _fscen(
        tmp_path,
        name="composition",
        extra={
            "tests/conftest.py": "from helpers import H  # noqa: F401\n",
            "tests/helpers.py": "H = 1\n",
            "extra/thing.py": "T = 1\n",
            ".issueforge.toml": (
                'baseline = ["-m", "pytest"]\nframework = "pytest"\n'
                'contract_paths = ["tests/test_new.py"]\n'
            ),
        },
    )
    run = _freeze_run(scen)
    res = _freeze(run, scen, user_added_paths=("extra/thing.py",))
    paths = set(res.manifest["contract_paths"])
    assert "tests/helpers.py" in paths  # fixture-reached, omitted by config, still protected
    assert "tests/conftest.py" in paths
    assert "extra/thing.py" in paths  # user-added
    assert "tests/test_new.py" in paths


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_contract_paths_exclude_unreached_committed_files(tmp_path):
    """A committed file reached by NO collection route — not a test, not in the fixture graph, not a
    test-body import — is NOT in the protected boundary. The closure base is the discovered graph, not
    'every committed file', and naming such an unreached file in the write scope is not a contradiction.

    technical: docs/UNRELATED.md and orphan.py are committed but imported/collected by nothing, so both
    are ABSENT from contract_paths. A pure 'all committed - write_scope' (subtraction) impl would
    PROTECT docs/UNRELATED.md and FAIL here; orphan.py named in the write scope raises NO fixture-closure
    contradiction (it is not in fixture_closure) and stays out of contract_paths.
    """
    scen = _fscen(
        tmp_path,
        name="closure-base",
        extra={
            "docs/UNRELATED.md": "# unrelated\n",
            "orphan.py": "ORPHAN = 1\n",
        },
    )
    run = _freeze_run(scen, write_scope=("orphan.py",))
    res = _freeze(run, scen)
    paths = set(res.manifest["contract_paths"])
    assert (
        "docs/UNRELATED.md" not in paths
    )  # unreached -> a subtraction impl would wrongly protect it
    assert "orphan.py" not in paths


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_collected_ids_exact_bound_to_collection(tmp_path):
    """The frozen collected-id set equals the adapter's collection exactly (sorted, dedup).

    technical: manifest["collected_ids"] == the sorted/dedup canonical_collect result; an arbitrary id
    is absent.
    """
    scen = _fscen(tmp_path, name="collected-ids")
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    assert tuple(res.manifest["collected_ids"]) == _collect_ids(scen.candidate_worktree)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_red_evidence_is_the_exact_s10_proof(tmp_path):
    """The frozen red evidence is the EXACT S10 proof — accepted, failing, bound to base/head, with
    added_ids == the collected targeted ids — proven by running the REAL prove_red.

    technical: manifest["red_evidence"] carries accepted True, reason "behavioral_red",
    base_sha == scen.base_sha, added_ids == [_NEW_X] (the real proof, not a dummy string).
    """
    scen = _red_scenario(tmp_path, "red-evidence")
    run = _mk_run("run-1")
    proof = _prove(run, scen, targeted_ids=(_NEW_X,))
    assert proof.accepted
    _seed_done_review(run, scen)
    res = _freeze(run, scen)
    ev = res.manifest["red_evidence"]
    assert ev["accepted"] is True
    assert ev["reason"] == "behavioral_red"
    assert ev["base_sha"] == scen.base_sha
    assert list(ev["added_ids"]) == [_NEW_X]


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_command_arrays_exact_ordering_and_boundaries(tmp_path):
    """Every .issueforge.toml command array is frozen exactly, preserving order and argument
    boundaries.

    technical: baseline ["pytest", "-q", "--maxfail=1"] frozen byte-identical as a list-of-lists; a
    truncated or flattened form fails.
    """
    scen = _fscen(
        tmp_path,
        name="command",
        extra={
            ".issueforge.toml": (
                'baseline = ["pytest", "-q", "--maxfail=1"]\nframework = "pytest"\n'
            )
        },
    )
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    assert ("pytest", "-q", "--maxfail=1") in {tuple(c) for c in res.manifest["command"]}


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
@pytest.mark.parametrize(
    "form, files, marker",
    [
        (
            "pyproject",
            {
                "pyproject.toml": '[tool.pytest.ini_options]\npython_files = ["check_pyproject.py"]\n'
            },
            "check_pyproject.py",
        ),
        ("pytest_ini", {"pytest.ini": "[pytest]\npython_files = check_ini.py\n"}, "check_ini.py"),
        ("tox_ini", {"tox.ini": "[pytest]\npython_files = check_tox.py\n"}, "check_tox.py"),
        (
            "setup_cfg",
            {"setup.cfg": "[tool:pytest]\npython_files = check_cfg.py\n"},
            "check_cfg.py",
        ),
        (
            "precedence",
            {
                "pytest.ini": "[pytest]\npython_files = check_ini.py\n",
                "setup.cfg": "[tool:pytest]\npython_files = check_cfg.py\n",
            },
            "check_ini.py",  # pytest.ini wins over setup.cfg
        ),
    ],
)
def test_freeze_config_four_forms_and_precedence(tmp_path, form, files, marker):
    """Each pytest config form is frozen with its exact adapter-supplied values, and precedence picks
    the right one.

    technical: manifest["test_config"] reflects the WINNING form's values (its distinctive
    python_files marker is present).
    """
    import json

    scen = _fscen(tmp_path, name=f"config-{form}", extra=files)
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    assert marker in json.dumps(res.manifest["test_config"])


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_config_and_issueforge_toml_are_hashed_boundary_members(tmp_path):
    """The selected config file and .issueforge.toml are protected paths with content hashes; editing
    either after approval is detected.

    technical: both paths in contract_paths + dep_hashes; a byte change to either mismatches the frozen
    hash.
    """
    scen = _fscen(tmp_path, name="config-hashed", extra={"pytest.ini": "[pytest]\n"})
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    for path in (".issueforge.toml", "pytest.ini"):
        assert path in res.manifest["contract_paths"]
        assert path in res.manifest["dep_hashes"]
    frozen = res.manifest["dep_hashes"]["pytest.ini"]
    (scen.candidate_worktree / "pytest.ini").write_text("[pytest]\naddopts = -q\n")
    assert _sha256((scen.candidate_worktree / "pytest.ini").read_bytes()) != frozen


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_external_pins_resolve_from_provisioned_env_and_flow_into_hash(tmp_path):
    """External pins resolve from the PROVISIONED hermetic interpreter (D5), not the parent process,
    and they are part of the manifest hash: the SAME conftest import pinned at two DIFFERENT provisioned
    versions yields two different pins and two different manifest hashes.

    technical: a conftest importing ``platformdirs`` frozen at 4.10.0 vs 4.9.1 in a REAL separate venv
    -> external_pins carries ('platformdirs','4.10.0') vs ('platformdirs','4.9.1') AND the two
    manifest_hashes differ. platformdirs is NOT a pytest/pytest-reportlog transitive, so an in-process
    parent reading of importlib.metadata (the same version — or absent — for both runs) cannot produce
    these two distinct pins: a parent-monkeypatch impl fails this.
    """
    extra = {"tests/conftest.py": "import platformdirs  # noqa: F401\n"}
    scen1 = _fscen(tmp_path, name="pin-v1", extra=extra)
    scen2 = _fscen(tmp_path, name="pin-v2", extra=extra)
    run1 = _freeze_run(scen1, run_id="run-v1")
    run2 = _freeze_run(scen2, run_id="run-v2")
    res1 = _freeze_prov(run1, scen1, _real_pin_provisioner({"platformdirs": "4.10.0"}))
    res2 = _freeze_prov(run2, scen2, _real_pin_provisioner({"platformdirs": "4.9.1"}))
    assert ("platformdirs", "4.10.0") in _pin_set(res1.manifest)
    assert ("platformdirs", "4.9.1") in _pin_set(res2.manifest)
    assert res1.manifest_hash != res2.manifest_hash


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_collects_via_configured_invocation_not_hardcoded_dash_m_pytest(tmp_path):
    """Freeze collects the contract via the repo's CONFIGURED .issueforge.toml baseline, not a
    hardcoded ``-m pytest``. A baseline that path-filters collection to ``tests`` must EXCLUDE a
    root-level test a bare ``-m pytest`` would have collected.

    technical: baseline=["-m","pytest","tests"]; extra_tests/test_extra.py is collected by a hardcoded
    ["-m","pytest"] (proven against the real adapter) but NOT by the configured baseline -> its id is
    absent from manifest['collected_ids'] and manifest['collected_ids'] equals the CONFIGURED
    collection. A hardcoded ``-m pytest`` impl would include the extra id and fail both assertions.
    """
    extra_id = "extra_tests/test_extra.py::test_extra"
    scen = _fscen(
        tmp_path,
        name="configured-collection",
        extra={
            "extra_tests/test_extra.py": "def test_extra():\n    assert True\n",
            ".issueforge.toml": 'baseline = ["-m", "pytest", "tests"]\nframework = "pytest"\n',
        },
    )
    default_ids = _collect_ids_cmd(scen.candidate_worktree, ["-m", "pytest"])
    configured_ids = _collect_ids_cmd(scen.candidate_worktree, ["-m", "pytest", "tests"])
    assert extra_id in default_ids  # a hardcoded -m pytest WOULD collect the root-level test
    assert extra_id not in configured_ids
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    assert extra_id not in res.manifest["collected_ids"]  # freeze used the CONFIGURED baseline
    assert set(res.manifest["collected_ids"]) == set(configured_ids)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_pins_externally_autoloaded_plugin_from_provisioned_env(tmp_path):
    """An entry-point pytest plugin present in the PROVISIONED venv is loaded during discovery
    (autoload ON in the hermetic env) and its distribution is pinned in external_pins — even though no
    test file imports it.

    technical: pytest-timeout==2.3.1 installed in a REAL separate venv; discovery loads it via its
    entry point -> ('pytest-timeout','2.3.1') in external_pins. pytest-timeout 2.3.1 declares
    ``pytest>=7.0.0``, so its plugin-reached transitive closure includes pytest (pinned 8.3.4 in the
    venv) -> ('pytest','8.3.4') is ALSO in external_pins. An UNRELATED decoy dist (wcwidth) is also
    installed but is not imported, not a plugin, and not a transitive of the closure, so its name is
    ABSENT. A provisioner that sets PYTEST_DISABLE_PLUGIN_AUTOLOAD (like the host _provisioner) — or a
    parent-process resolver — never loads the plugin and omits the pin; an impl that discovers the
    entry-point plugin owner but does NOT traverse its transitive deps omits pytest and fails the exact
    (pytest, 8.3.4) assertion; an impl that inventories the whole provisioned environment (not the
    plugin closure) includes the decoy 'wcwidth' and fails the absence assertion.
    """
    scen = _fscen(tmp_path, name="ext-plugin")
    run = _freeze_run(scen)
    res = _freeze_prov(
        run,
        scen,
        _real_pin_provisioner({"pytest-timeout": "2.3.1", "pytest": "8.3.4", "wcwidth": "0.8.2"}),
    )
    pins = _pin_set(res.manifest)
    assert ("pytest-timeout", "2.3.1") in pins
    assert ("pytest", "8.3.4") in pins  # plugin-reached transitive (pytest-timeout -> pytest)
    assert "wcwidth" not in {d for d, _ in pins}  # decoy: installed but unreached -> not in closure


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_pins_external_to_external_transitive_deps_from_provisioned_env(tmp_path):
    """External dependency edges are followed transitively AND versioned from the provisioned env: a
    conftest importing an external dist pins that dist AND its own external dependencies
    (external->external), each at the EXACT version present in the provisioned interpreter.

    technical: conftest imports ``markdown_it`` (markdown-it-py) frozen at 3.0.0, which really depends
    on ``mdurl`` (pinned 0.1.2 in the provisioned venv) -> external_pins restricted to the dists under
    test == {('markdown-it-py','3.0.0'), ('mdurl','0.1.2')} EXACTLY, and an UNRELATED decoy dist
    (wcwidth, installed but not imported and not a transitive of markdown-it-py/mdurl) is ABSENT. A
    resolver that stops at the directly-imported dist (or a fixed packages_distributions patch) omits
    the transitive 'mdurl'; an impl that records mdurl's version from the parent interpreter or a
    stale/arbitrary value fails the exact (dist, version) assertion; an impl that inventories the whole
    provisioned environment (not the import closure) includes the decoy 'wcwidth' and fails the absence
    assertion.
    """
    scen = _fscen(
        tmp_path,
        name="ext-transitive",
        extra={"tests/conftest.py": "import markdown_it  # noqa: F401\n"},
    )
    run = _freeze_run(scen)
    res = _freeze_prov(
        run,
        scen,
        _real_pin_provisioner({"markdown-it-py": "3.0.0", "mdurl": "0.1.2", "wcwidth": "0.8.2"}),
    )
    pins = _pin_set(res.manifest)
    under_test = {(d, v) for (d, v) in pins if d in {"markdown-it-py", "mdurl"}}
    assert under_test == {("markdown-it-py", "3.0.0"), ("mdurl", "0.1.2")}
    assert "wcwidth" not in {d for d, _ in pins}  # decoy: installed but unreached -> not in closure


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_pins_every_owner_of_a_multi_dist_namespace(tmp_path):
    """When one namespace package is provided by MORE THAN ONE distribution, every distinct owning
    distribution is pinned at its EXACT provisioned version — a namespace-maps-to-one-dist assumption
    is wrong, and versions come from the provisioned env.

    technical: sphinxcontrib.applehelp and sphinxcontrib.devhelp are two DIFFERENT dists sharing the
    PEP420 ``sphinxcontrib`` namespace, pinned 1.0.8 and 1.0.6 in the provisioned venv; a conftest
    importing both -> external_pins restricted to those owners ==
    {('sphinxcontrib-applehelp','1.0.8'), ('sphinxcontrib-devhelp','1.0.6')} EXACTLY, and an UNRELATED
    decoy dist (wcwidth, installed but not imported and not a transitive of either owner) is ABSENT.
    Mapping the namespace to a single owner drops one; an impl recording a version from the parent
    interpreter or a stale/arbitrary value fails the exact (dist, version) assertion; an impl that
    inventories the whole provisioned environment (not the import closure) includes the decoy 'wcwidth'
    and fails the absence assertion.
    """
    scen = _fscen(
        tmp_path,
        name="ns-multi-owner",
        extra={
            "tests/conftest.py": (
                "import sphinxcontrib.applehelp  # noqa: F401\n"
                "import sphinxcontrib.devhelp  # noqa: F401\n"
            )
        },
    )
    run = _freeze_run(scen)
    res = _freeze_prov(
        run,
        scen,
        _real_pin_provisioner(
            {
                "sphinxcontrib-applehelp": "1.0.8",
                "sphinxcontrib-devhelp": "1.0.6",
                # applehelp's __init__ hard-imports sphinx; provision it so the
                # conftest import resolves and collection completes (mirrors the
                # committed discover-level sibling test_discover_namespace_...).
                # sphinx is NOT in the closure -> asserted absent from the pins.
                "sphinx": "9.1.0",
                "wcwidth": "0.8.2",
            }
        ),
    )
    pins = _pin_set(res.manifest)
    under_test = {
        (d, v) for (d, v) in pins if d in {"sphinxcontrib-applehelp", "sphinxcontrib-devhelp"}
    }
    assert under_test == {
        ("sphinxcontrib-applehelp", "1.0.8"),
        ("sphinxcontrib-devhelp", "1.0.6"),
    }
    assert "wcwidth" not in {d for d, _ in pins}  # decoy: installed but unreached -> not in closure


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_handles_mixed_in_repo_and_external_namespace_package(tmp_path):
    """A namespace shared by an IN-REPO submodule and an EXTERNAL site-packages submodule splits by
    provenance: the in-repo part is protected as a file (fixture_closure) and the external part is
    pinned.

    technical: in-repo sphinxcontrib/local_ns.py + external sphinxcontrib.applehelp (pinned 1.0.8 in the
    provisioned venv) under the PEP420 ``sphinxcontrib`` namespace; a conftest importing both ->
    'sphinxcontrib/local_ns.py' in contract_paths (in-repo, protected) AND the external_pins restricted
    to that dist == {('sphinxcontrib-applehelp','1.0.8')} EXACTLY, and an UNRELATED decoy dist (wcwidth,
    installed but not imported and not a transitive of sphinxcontrib-applehelp) is ABSENT. Treating the
    whole namespace as external drops the in-repo file; treating it all as in-repo misses the pin; an
    impl recording the version from the parent interpreter or a stale/arbitrary value fails the exact
    (dist, version) assertion; an impl that inventories the whole provisioned environment (not the
    import closure) includes the decoy 'wcwidth' and fails the absence assertion.
    """
    scen = _fscen(
        tmp_path,
        name="ns-mixed",
        extra={
            "sphinxcontrib/local_ns.py": "LOCAL = 1\n",
            "tests/conftest.py": (
                "import sphinxcontrib.applehelp  # noqa: F401\n"
                "from sphinxcontrib import local_ns  # noqa: F401\n"
            ),
        },
    )
    run = _freeze_run(scen)
    res = _freeze_prov(
        run,
        scen,
        # sphinx provisioned because applehelp's __init__ hard-imports it; it is
        # NOT in the closure and is asserted absent from the pins below.
        _real_pin_provisioner(
            {"sphinxcontrib-applehelp": "1.0.8", "sphinx": "9.1.0", "wcwidth": "0.8.2"}
        ),
    )
    assert "sphinxcontrib/local_ns.py" in set(res.manifest["contract_paths"])
    pins = _pin_set(res.manifest)
    under_test = {(d, v) for (d, v) in pins if d == "sphinxcontrib-applehelp"}
    assert under_test == {("sphinxcontrib-applehelp", "1.0.8")}
    assert "wcwidth" not in {d for d, _ in pins}  # decoy: installed but unreached -> not in closure


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_reject_writes_nothing_atomic(tmp_path):
    """On rejection nothing is written — no manifest, no event, no frozen state, no partial closure.

    technical: approver -> False -> result.approved False, no artifact, no freeze event, status
    unchanged.
    """
    scen = _fscen(tmp_path, name="reject")
    run = _freeze_run(scen)
    before = store.RunStore().read(run)["status"]
    res = _freeze(run, scen, approver=_reject_all)
    assert res.approved is False
    assert _no_freeze_writes(run)
    assert store.RunStore().read(run)["status"] == before


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_approver_called_before_any_write(tmp_path):
    """The approver is consulted BEFORE the first store/event write.

    technical: at approver call-time there is no freeze event and no manifest artifact yet.
    """
    scen = _fscen(tmp_path, name="before-write")
    run = _freeze_run(scen)
    seen = {}

    def approver(_payload):
        seen["events"] = list(_freeze_events(run))
        seen["artifact"] = _manifest_artifact_path(run).exists()
        return True

    _freeze(run, scen, approver=approver)
    assert seen["events"] == []
    assert seen["artifact"] is False


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_approver_bound_to_exact_persisted_manifest(tmp_path):
    """The exact bytes shown to the approver equal the immutable persisted artifact bytes, and the
    event's manifest hash is the sha256 of those same bytes — no show-A-persist-B.

    technical: captured callback bytes == persisted artifact bytes AND
    event["manifest_hash"] == sha256(persisted bytes).
    """
    scen = _fscen(tmp_path, name="bound-bytes")
    run = _freeze_run(scen)
    captured = {}

    def approver(payload):
        captured["bytes"] = bytes(payload)
        return True

    _freeze(run, scen, approver=approver)
    persisted = _manifest_artifact_path(run).read_bytes()
    assert captured["bytes"] == persisted
    event = _freeze_events(run)[-1]
    assert event["manifest_hash"] == _sha256(persisted)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_callback_once_decision_controls_transition_no_default_path(tmp_path):
    """The callback is called exactly once for both outcomes; its boolean controls the transition;
    omission/exception cannot freeze.

    technical: count == 1; True -> a freeze event exists, False -> none; a raising approver -> no
    freeze event.
    """
    scen = _fscen(tmp_path, name="callback-once")

    # True path: exactly one call, freeze event recorded.
    run_t = _freeze_run(scen, run_id="run-true")
    calls_t = {"n": 0}

    def approve(_p):
        calls_t["n"] += 1
        return True

    _freeze(run_t, scen, approver=approve)
    assert calls_t["n"] == 1
    assert _freeze_events(run_t)

    # False path: exactly one call, no freeze event.
    run_f = _freeze_run(scen, run_id="run-false")
    calls_f = {"n": 0}

    def reject(_p):
        calls_f["n"] += 1
        return False

    _freeze(run_f, scen, approver=reject)
    assert calls_f["n"] == 1
    assert not _freeze_events(run_f)

    # Raising approver: no freeze.
    run_r = _freeze_run(scen, run_id="run-raise")

    def boom(_p):
        raise RuntimeError("approver blew up")

    with pytest.raises(RuntimeError):
        _freeze(run_r, scen, approver=boom)
    assert not _freeze_events(run_r)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_event_carries_decision_and_manifest_hash(tmp_path):
    """The approval event records the approver's decision AND the exact manifest hash.

    technical: a freeze event {"approved": true, "manifest_hash": H} with H == sha256(persisted bytes).
    """
    scen = _fscen(tmp_path, name="event-hash")
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    event = _freeze_events(run)[-1]
    assert event["approved"] is True
    persisted = _manifest_artifact_path(run).read_bytes()
    assert event["manifest_hash"] == _sha256(persisted) == res.manifest_hash


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_manifest_is_canonical_bytes_verified_from_persisted(tmp_path):
    """The persisted manifest artifact is canonical (sorted keys, UTF-8), carries NO self-referential
    hash field, and the freeze event's manifest_hash is sha256 of those exact persisted bytes.

    technical: persisted bytes == the canonical serialization of the parsed object;
    "manifest_hash" not in the parsed object; event["manifest_hash"] == sha256(persisted).
    """
    import json

    scen = _fscen(tmp_path, name="canonical")
    run = _freeze_run(scen)
    _freeze(run, scen)
    persisted = _manifest_artifact_path(run).read_bytes()
    parsed = json.loads(persisted)
    assert "manifest_hash" not in parsed
    assert persisted == _canonical_bytes(parsed)
    event = _freeze_events(run)[-1]
    assert event["manifest_hash"] == _sha256(persisted)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_content_hashes_exact_both_versions(tmp_path):
    """Each frozen hash is the content hash of the committed blob; two scenarios differing by one byte
    have different, independently-verified digests.

    technical: S vs S' (helpers.py ±1 byte) -> each dep_hashes["tests/helpers.py"] == sha256 of that
    version's committed blob (not merely unequal).
    """
    scen_a = _fscen(tmp_path, name="ver-a", extra={"tests/helpers.py": "H = 1\n"})
    scen_b = _fscen(tmp_path, name="ver-b", extra={"tests/helpers.py": "H = 2\n"})
    run_a = _freeze_run(scen_a, run_id="run-a")
    run_b = _freeze_run(scen_b, run_id="run-b")
    res_a = _freeze(run_a, scen_a)
    res_b = _freeze(run_b, scen_b)
    oracle_a = _sha256(
        _committed_blob(scen_a.candidate_worktree, f"{scen_a.candidate_sha}:tests/helpers.py")
    )
    oracle_b = _sha256(
        _committed_blob(scen_b.candidate_worktree, f"{scen_b.candidate_sha}:tests/helpers.py")
    )
    assert res_a.manifest["dep_hashes"]["tests/helpers.py"] == oracle_a
    assert res_b.manifest["dep_hashes"]["tests/helpers.py"] == oracle_b
    assert oracle_a != oracle_b


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_manifest_is_permanent_addressable_artifact(tmp_path):
    """The manifest persists as a permanent artifact a fresh reader retrieves unchanged; a later freeze
    of a different run does not overwrite it.

    technical: persist, re-read the artifact bytes+hash unchanged after a second freeze of another run.
    """
    scen1 = _fscen(tmp_path, name="perm-1")
    scen2 = _fscen(tmp_path, name="perm-2")
    run1 = _freeze_run(scen1, run_id="run-perm-1")
    run2 = _freeze_run(scen2, run_id="run-perm-2")
    res1 = _freeze(run1, scen1)
    bytes_before = _manifest_artifact_path(run1).read_bytes()
    _freeze(run2, scen2)
    bytes_after = _manifest_artifact_path(run1).read_bytes()
    assert bytes_after == bytes_before
    assert _sha256(bytes_after) == res1.manifest_hash


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_redaction_removes_only_secrets_preserves_command_structure(tmp_path):
    """Secret redaction blanks only secret material; the executable command structure survives and two
    materially-different commands do not both collapse to [REDACTED].

    technical: a secret in the command -> [REDACTED] while the array shape/args remain; two different
    commands stay distinguishable in their manifests.
    """
    scen1 = _fscen(
        tmp_path,
        name="redact-1",
        extra={
            ".issueforge.toml": (
                'baseline = ["pytest", "-o", "cache_dir=a-SEKRET"]\nframework = "pytest"\n'
            )
        },
    )
    scen2 = _fscen(
        tmp_path,
        name="redact-2",
        extra={
            ".issueforge.toml": (
                'baseline = ["pytest", "-o", "cache_dir=b-SEKRET"]\nframework = "pytest"\n'
            )
        },
    )
    run1 = _freeze_run(scen1, run_id="run-redact-1")
    run2 = _freeze_run(scen2, run_id="run-redact-2")
    res1 = _freeze(run1, scen1, secrets={"SEKRET"})
    res2 = _freeze(run2, scen2, secrets={"SEKRET"})
    import json

    dumped1 = json.dumps(res1.manifest["command"])
    assert "SEKRET" not in dumped1
    assert "[REDACTED]" in dumped1
    assert "pytest" in dumped1  # command structure preserved
    assert res1.manifest["command"] != res2.manifest["command"]  # still distinguishable


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_freeze_fails_closed_on_incomplete_discovery_atomic(tmp_path):
    """If discovery cannot resolve an import, the freeze refuses entirely, naming it — no manifest,
    event, or frozen state.

    technical: discovery raises DiscoveryError("nonexistent_pkg_xyz") -> freeze refusal names it;
    nothing persisted.
    """
    from issueforge.adapters.pytest_adapter import DiscoveryError

    scen = _fscen(
        tmp_path,
        name="incomplete-discovery",
        extra={"tests/conftest.py": "import nonexistent_pkg_xyz  # noqa: F401\n"},
    )
    run = _freeze_run(scen)
    with pytest.raises((DiscoveryError, ValueError)) as excinfo:
        _freeze(run, scen)
    assert "nonexistent_pkg_xyz" in str(excinfo.value)
    assert _no_freeze_writes(run)


# =============================================================== Group C — boundary mutations


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_deleted_file_reads_as_empty_module_delta_names_every_nodeid(tmp_path):
    """A deleted contract test file reads as an empty module — the deletion delta contains EVERY
    node-id it declared, not a generic missing-file error.

    technical: a committed file with two node-ids, deleted from the worktree before freeze -> the
    refusal names BOTH node-ids; nothing persisted.
    """
    scen = _fscen(
        tmp_path,
        name="deleted-delta",
        extra={
            "tests/test_del.py": "def test_a():\n    assert False\n\n\ndef test_b():\n    assert False\n"
        },
    )
    run = _freeze_run(scen)
    (scen.candidate_worktree / "tests/test_del.py").unlink()
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, scen)
    message = str(excinfo.value)
    assert "test_a" in message and "test_b" in message
    assert _no_freeze_writes(run)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_partial_deletion_of_a_node_is_detected(tmp_path):
    """Deleting ONE of two test functions is detected via collected-id loss.

    technical: commit test_pair.py with test_a + test_b, remove test_b in the worktree -> the missing
    id is detected; freeze refuses naming it.
    """
    scen = _fscen(
        tmp_path,
        name="partial-del",
        extra={
            "tests/test_pair.py": "def test_a():\n    assert False\n\n\ndef test_b():\n    assert False\n"
        },
    )
    run = _freeze_run(scen)
    (scen.candidate_worktree / "tests/test_pair.py").write_text("def test_a():\n    assert False\n")
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, scen)
    assert "test_b" in str(excinfo.value)
    assert _no_freeze_writes(run)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
@pytest.mark.parametrize(
    "kind, path, extra",
    [
        ("helper", "tests/helpers.py", {"tests/helpers.py": "H = 1\n"}),
        (
            "plugin",
            "plug.py",
            {"pytest.ini": "[pytest]\naddopts = -p plug\n", "plug.py": "PU = 1\n"},
        ),
        ("issueforge_toml", ".issueforge.toml", None),
        ("config", "pytest.ini", {"pytest.ini": "[pytest]\n"}),
    ],
)
def test_deletion_of_nontest_protected_file_refuses(tmp_path, kind, path, extra):
    """Deleting a protected non-test file has a defined missing-file representation and refusal per
    class.

    technical: each of {helper, plugin, .issueforge.toml, selected config} deleted before freeze ->
    named refusal; nothing persisted.
    """
    scen = _fscen(tmp_path, name=f"del-nontest-{kind}", extra=extra)
    run = _freeze_run(scen)
    (scen.candidate_worktree / path).unlink()
    with pytest.raises(ValueError) as excinfo:
        _freeze(run, scen)
    assert path in str(excinfo.value)
    assert _no_freeze_writes(run)


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_rename_detected_old_deleted_new_no_silent_identity(tmp_path):
    """Renaming a protected file shows the old path recorded and the new path does NOT silently inherit
    the old identity.

    technical: freeze records tests/helpers.py; after renaming to tests/helper_new.py in the worktree,
    the new path is not a member of the frozen contract_paths (no inherited identity) and the old path
    remains the recorded member.
    """
    scen = _fscen(tmp_path, name="rename", extra={"tests/helpers.py": "H = 1\n"})
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    assert "tests/helpers.py" in res.manifest["contract_paths"]
    # Rename in the worktree: the frozen manifest never grants the new path the old identity.
    (scen.candidate_worktree / "tests/helpers.py").rename(
        scen.candidate_worktree / "tests/helper_new.py"
    )
    assert "tests/helper_new.py" not in res.manifest["contract_paths"]


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
def test_generated_file_authoritative_snapshot_is_frozen_commit_blob(tmp_path):
    """A generated helper present at collection is frozen from the COMMITTED snapshot; a later byte
    change is measured against that snapshot.

    technical: a committed tests/gen_helper.py -> dep_hashes value == sha256 of the commit blob; a
    post-approval byte change mismatches it.
    """
    scen = _fscen(tmp_path, name="generated", extra={"tests/gen_helper.py": "GEN = 1\n"})
    run = _freeze_run(scen)
    res = _freeze(run, scen)
    frozen = res.manifest["dep_hashes"]["tests/gen_helper.py"]
    oracle = _sha256(
        _committed_blob(scen.candidate_worktree, f"{scen.candidate_sha}:tests/gen_helper.py")
    )
    assert frozen == oracle
    (scen.candidate_worktree / "tests/gen_helper.py").write_text("GEN = 999\n")
    assert _sha256((scen.candidate_worktree / "tests/gen_helper.py").read_bytes()) != frozen


def _symlink_scenario(root, name, *, link_rel, target_rel, target_content=None, cyclic=False):
    """A REAL two-commit repo with a committed in-repo symlink ``link_rel`` -> ``target_rel``.

    Mirrors ``_scenario``'s origin/HEAD setup so the freeze's currency + HEAD resolution work. When
    ``target_content`` is None the target is not created (a broken link). ``cyclic`` makes the target
    a symlink back to the link (a cycle).
    """
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(_CONFIG)
    _write(repo, dict(_BASE_FILES))
    (repo / "tests/test_new.py").write_text("def test_x():\n    assert 1 == 2\n")
    link_path = repo / link_rel
    target_path = repo / target_rel
    link_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if cyclic:
        os.symlink(os.path.relpath(link_path, target_path.parent), target_path)
    elif target_content is not None:
        target_path.write_text(target_content)
    os.symlink(os.path.relpath(target_path, link_path.parent), link_path)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "remote", "add", "origin", "git@github.com:Owner/IssueForge.git")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    base_checkout = root / f"{name}-base"
    shutil.copytree(repo, base_checkout, symlinks=True)
    candidate_sha = base_sha
    return SimpleNamespace(
        base_checkout=base_checkout,
        candidate_worktree=repo,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#18)")
@pytest.mark.parametrize(
    "case", ["in_repo", "outside_repo", "broken", "cyclic", "retarget_outside"]
)
def test_symlink_boundary_behavior(tmp_path, case):
    """An in-repo symlink is frozen by its RESOLVED in-repo target's committed blob while keeping its
    own contract path and recording the link target; broken, cyclic, outside-repo, and retargeted-
    outside links fail closed.

    technical: in_repo -> dep_hashes[link] == sha256(committed blob of the resolved in-repo target),
    link in contract_paths, symlinks[link] recorded; every other case -> freeze refusal, nothing
    persisted.
    """
    link_rel = "tests/link_helper.py"
    if case == "in_repo":
        scen = _symlink_scenario(
            tmp_path,
            "sym-in",
            link_rel=link_rel,
            target_rel="tests/real_helper.py",
            target_content="H = 1\n",
        )
        run = _freeze_run(scen, run_id="run-sym-in")
        res = _freeze(run, scen)
        assert link_rel in res.manifest["contract_paths"]
        assert link_rel in res.manifest["symlinks"]
        oracle = _sha256(
            _committed_blob(scen.candidate_worktree, f"{scen.candidate_sha}:tests/real_helper.py")
        )
        assert res.manifest["dep_hashes"][link_rel] == oracle
        return

    if case == "outside_repo":
        outside = tmp_path / "outside_target.py"
        outside.write_text("H = 1\n")
        scen = _symlink_scenario(
            tmp_path,
            "sym-out",
            link_rel=link_rel,
            target_rel="tests/real_helper.py",
            target_content="H = 1\n",
        )
        # Retarget the committed link to point outside the repo, then commit that.
        (scen.candidate_worktree / link_rel).unlink()
        os.symlink(str(outside), scen.candidate_worktree / link_rel)
        _git(scen.candidate_worktree, "add", "-A")
        _git(scen.candidate_worktree, "commit", "-qm", "retarget-outside")
        scen.candidate_sha = _git(scen.candidate_worktree, "rev-parse", "HEAD").stdout.strip()
        run = _freeze_run(scen, run_id="run-sym-out")
    elif case == "retarget_outside":
        outside = tmp_path / "retarget_target.py"
        outside.write_text("H = 2\n")
        scen = _symlink_scenario(
            tmp_path,
            "sym-retarget",
            link_rel=link_rel,
            target_rel="tests/real_helper.py",
            target_content="H = 1\n",
        )
        (scen.candidate_worktree / link_rel).unlink()
        os.symlink(
            os.path.relpath(outside, (scen.candidate_worktree / link_rel).parent),
            scen.candidate_worktree / link_rel,
        )
        run = _freeze_run(scen, run_id="run-sym-retarget")
    elif case == "broken":
        scen = _symlink_scenario(
            tmp_path,
            "sym-broken",
            link_rel=link_rel,
            target_rel="tests/missing_target.py",
            target_content=None,
        )
        run = _freeze_run(scen, run_id="run-sym-broken")
    else:  # cyclic
        scen = _symlink_scenario(
            tmp_path,
            "sym-cyclic",
            link_rel=link_rel,
            target_rel="tests/cycle_target.py",
            cyclic=True,
        )
        run = _freeze_run(scen, run_id="run-sym-cyclic")

    with pytest.raises(ValueError):
        _freeze(run, scen)
    assert _no_freeze_writes(run)
