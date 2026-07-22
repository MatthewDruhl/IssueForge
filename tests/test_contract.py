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
