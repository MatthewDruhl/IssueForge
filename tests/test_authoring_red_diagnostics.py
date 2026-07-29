"""PENDING acceptance suite for #203 — the authoring red contract plus reject diagnostics.

Two independent behaviors are pinned here, both currently unbuilt:

1. ``engine._authoring_prompt`` must, FROM THE TEMPLATE (not an issue echo), tell the author that
   every authored test has to fail before any implementation and that tests passing against the
   current code must not be included. Controls belong to implementation, outside the frozen contract.

2. ``contract.prove_red`` rejections issued AFTER the added set is computed must record the REAL
   ``added_ids`` (today ``contract._reject`` forces ``added_ids=()``), and a step-8 classification
   rejection must name the offending node id in a new ``RedProof.offending_ids`` field (default
   ``()``, present in the persisted ``red_proof`` payload). Accepted proofs carry ``offending_ids``
   empty. ``no_tests_collected`` is exempt (its added set is empty by definition).

Every test is marked ``@pytest.mark.xfail(strict=True, reason="PENDING (#203)")`` — the literal
convention. Each fixture here is a LOCAL copy of the real-seam helpers in ``tests/test_contract.py``
(``_scenario``/``_prove``/``_mk_run``/``_provisioner``/``_adapter``/``_write``); no shared helper is
modified. The ``isolated_state_home`` autouse fixture in ``tests/conftest.py`` points the run store
at a per-test root, so every prove_red persists onto an isolated RunStore.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueforge import contract, engine, store

# ------------------------------------------------------------------- local real-seam fixtures
# Copied from tests/test_contract.py so this suite owns its fixtures; no shared helper is touched.

_CONFIG = 'baseline = ["-m", "pytest"]\nframework = "pytest"\n'
_GIT_ID = ["-c", "user.name=IF Tests", "-c", "user.email=tests@issueforge.invalid"]

_BASE_FILE = "tests/test_base.py"
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
        path.write_text(content)


def _scenario(
    root: Path,
    name: str,
    *,
    candidate_files: dict[str, str],
    base_files: dict[str, str] | None = None,
) -> SimpleNamespace:
    """Build a REAL two-commit repo: a frozen base checkout at ``base_sha`` and a candidate worktree.

    The base commit carries ``base_files`` (default: the two-test green base) plus ``.issueforge.toml``
    and an ``origin`` whose default branch resolves to that commit; ``base_checkout`` is a full copy
    frozen there. The candidate commit overlays ``candidate_files`` as a LATER local sha.
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
    """A host-side provisioner seam: the host interpreter + an ALLOWLIST env, no network denial."""
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


def _mk_run(run_id: str = "run-1") -> str:
    """Mint a buildable, revision-applied run record so prove_red has a real RunStore run to persist."""
    shape = {"classification": "buildable", "write_scope": []}
    record = {"status": "running", "shape": shape, "revision_ledger": {"op-1": "fingerprint"}}
    store.RunStore().apply(run_id, lambda _r: record, create=True)
    return run_id


def _prove(run_id, scen, *, targeted_ids):
    return contract.prove_red(
        run_id,
        targeted_ids=tuple(targeted_ids),
        base_checkout=scen.base_checkout,
        candidate_worktree=scen.candidate_worktree,
        base_sha=scen.base_sha,
        adapter=_adapter(),
        provisioner=_provisioner(),
        secrets=frozenset(),
    )


def _persisted_proof(run_id: str) -> dict:
    """Read the run's persisted ``red_proof`` payload back FROM THE RUN STORE (not the returned proof)."""
    return store.RunStore().read(run_id)["red_proof"]


# ------------------------------------------------------------------- Test 1: authoring prompt


@pytest.mark.xfail(strict=True, reason="PENDING (#203)")
def test_authoring_prompt_forbids_passing_tests():
    """The authoring prompt must tell the author, from its own template, that every test fails first
    and that passing tests are not included in the frozen contract.

    technical (contract): ``engine._authoring_prompt("Fix the widget.", [], ["-m", "pytest"])`` — a
    NEUTRAL issue body that contains NEITHER sentence, since the prompt embeds the body verbatim —
    renders a string containing the exact LF-delimited standalone line
    ``\\nEvery test you author must fail when run now, before any implementation, for the behavioral
    reason in the issue.\\n`` AND the exact LF-delimited standalone line
    ``\\nDo not include tests that pass against the current code; guard or control tests are added
    during implementation, never in this contract.\\n`` (substring checks include the leading and
    trailing newlines). Today neither line exists in the template.
    """
    rendered = engine._authoring_prompt("Fix the widget.", [], ["-m", "pytest"])
    assert (
        "\nEvery test you author must fail when run now, before any implementation, "
        "for the behavioral reason in the issue.\n"
    ) in rendered
    assert (
        "\nDo not include tests that pass against the current code; guard or control tests are "
        "added during implementation, never in this contract.\n"
    ) in rendered


# ------------------------------------------------------------------- Tests 2-3: not_red diagnostics


@pytest.mark.xfail(strict=True, reason="PENDING (#203)")
def test_not_red_rejection_names_offender_and_real_added_set(tmp_path):
    """A ``not_red`` rejection must name the offending (passing) node id and carry the real added set.

    technical (contract): real-seam candidate adds one file with a genuinely failing test and a
    genuinely passing test; targeted == both added ids. ``prove_red`` -> ``accepted is False``,
    ``reason == "not_red"``, ``proof.offending_ids == (<passing node id>,)``,
    ``set(proof.added_ids) == {<failing id>, <passing id>}``. Today ``RedProof`` has no
    ``offending_ids`` field and every rejection carries ``added_ids == ()``.
    """
    red = "tests/test_new.py::test_red"
    ok = "tests/test_new.py::test_ok"
    scen = _scenario(
        tmp_path,
        "not-red",
        candidate_files={
            "tests/test_new.py": "def test_red():\n    assert 1 == 2\n\n\ndef test_ok():\n    assert True\n"
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(red, ok))
    assert rp.accepted is False
    assert rp.reason == "not_red"
    assert rp.offending_ids == (ok,)
    assert set(rp.added_ids) == {red, ok}


@pytest.mark.xfail(strict=True, reason="PENDING (#203)")
def test_persisted_red_proof_payload_carries_diagnostics(tmp_path):
    """The persisted ``red_proof`` manifest payload carries the offender and the real added set.

    technical (contract): same scenario as the not_red test; after the rejection, the run's persisted
    ``red_proof`` payload read back FROM THE RUN STORE has ``offending_ids == [<passing node id>]`` and
    ``sorted(payload["added_ids"]) == sorted([<failing id>, <passing id>])``. Red today.
    """
    red = "tests/test_new.py::test_red"
    ok = "tests/test_new.py::test_ok"
    scen = _scenario(
        tmp_path,
        "not-red-persist",
        candidate_files={
            "tests/test_new.py": "def test_red():\n    assert 1 == 2\n\n\ndef test_ok():\n    assert True\n"
        },
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(red, ok))
    assert rp.accepted is False
    assert rp.reason == "not_red"
    payload = _persisted_proof(run)
    assert payload["offending_ids"] == [ok]
    assert sorted(payload["added_ids"]) == sorted([red, ok])


# ------------------------------------------------------------------- Test 4: missing_targeted_id


@pytest.mark.xfail(strict=True, reason="PENDING (#203)")
def test_missing_targeted_id_records_added_and_keeps_offending_empty(tmp_path):
    """A ``missing_targeted_id`` rejection records the real added set and keeps ``offending_ids`` empty.

    technical (contract): real candidate adds exactly one collectable test;
    ``prove_red(targeted_ids=("tests/test_absent.py::test_nope",), ...)`` -> ``accepted is False``,
    ``reason == "missing_targeted_id"``, ``set(proof.added_ids) == {<the actually-added node id>}``
    (never empty), ``proof.offending_ids == ()``, and the persisted payload has ``offending_ids == []``
    (offending_ids is reserved for classification rejections). Red today.
    """
    scen = _scenario(
        tmp_path,
        "missing-id",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=("tests/test_absent.py::test_nope",))
    assert rp.accepted is False
    assert rp.reason == "missing_targeted_id"
    assert rp.offending_ids == ()
    assert set(rp.added_ids) == {_NEW_ID}
    assert _persisted_proof(run)["offending_ids"] == []


# ------------------------------------------------------------------- Test 5: accept path


@pytest.mark.xfail(strict=True, reason="PENDING (#203)")
def test_accept_path_carries_empty_offending_ids(tmp_path):
    """An accepted proof carries an empty ``offending_ids`` in memory and ``[]`` in the persisted payload.

    technical (contract): the existing accept scenario (one genuinely red added test, targeted ==
    added) -> ``accepted is True``, ``reason == "behavioral_red"``, ``proof.offending_ids == ()``,
    persisted payload ``offending_ids == []``. Red today: the field is absent, so accessing it raises
    AttributeError at the assertion (a call-phase failure, the right reason).
    """
    scen = _scenario(
        tmp_path,
        "accept",
        candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
    )
    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=(_NEW_ID,))
    assert rp.accepted is True
    assert rp.reason == "behavioral_red"
    assert rp.offending_ids == ()
    assert _persisted_proof(run)["offending_ids"] == []


# ------------------------------------------------------------------- Test 6: every post-added reject


@pytest.mark.parametrize(
    "case",
    ["baseline_not_green", "empty_parametrize", "no_candidate_evidence", "xpass", "setup_error"],
)
@pytest.mark.xfail(strict=True, reason="PENDING (#203)")
def test_post_added_rejections_record_real_added_set(tmp_path, monkeypatch, case):
    """Every distinct rejection issued AFTER the added set is computed records the real added set;
    the two classification siblings also name their offender.

    technical (contract): each case is a real-seam scenario whose rejection fires TODAY on the correct
    reason; only the ``added_ids`` (and, for the classification siblings, ``offending_ids``) assertions
    are red today. ``baseline_not_green`` (candidate breaks a preexisting base test and adds a genuine
    red), ``empty_parametrize`` (added test with empty parametrize argvalues, collected id carries the
    ``[NOTSET]`` suffix), and ``no_candidate_evidence`` (candidate report-log suppressed) each ->
    ``set(proof.added_ids) == {<that scenario's real added id>}``. ``xpass`` (the only added test is
    xfail-marked and passes) and ``setup_error`` (the only added test uses a failing fixture) each also
    -> ``proof.offending_ids == (<its node id>,)``. ``no_tests_collected`` is exempt.
    """
    new_id = _NEW_ID
    notset_id = f"{_NEW_ID}[NOTSET]"
    expected_offending = None

    if case == "baseline_not_green":
        scen = _scenario(
            tmp_path,
            "post-baseline",
            candidate_files={
                _BASE_FILE: "def test_base_a():\n    assert True\n\n\ndef test_base_b():\n    assert False\n",
                "tests/test_new.py": "def test_new():\n    assert 1 == 2\n",
            },
        )
        targeted = (new_id,)
        expected_reason = "baseline_not_green"
        expected_added = {new_id}
    elif case == "empty_parametrize":
        scen = _scenario(
            tmp_path,
            "post-empty-param",
            candidate_files={
                "tests/test_new.py": (
                    'import pytest\n\n\n@pytest.mark.parametrize("x", [])\n'
                    "def test_new(x):\n    assert False\n"
                )
            },
        )
        targeted = (new_id,)
        expected_reason = "empty_parametrize"
        expected_added = {notset_id}
    elif case == "no_candidate_evidence":
        scen = _scenario(
            tmp_path,
            "post-no-evidence",
            candidate_files={"tests/test_new.py": "def test_new():\n    assert 1 == 2\n"},
        )
        targeted = (new_id,)
        expected_reason = "no_candidate_evidence"
        expected_added = {new_id}
        # Suppress the candidate report-log exactly as the existing required-suite fixture does:
        # replace the candidate _run_suite Evidence with one carrying report_present=False.
        real_run_suite = contract._run_suite

        def _suppressed(adapter, worktree, provisioner):
            evidence = real_run_suite(adapter, worktree, provisioner)
            if Path(worktree) != Path(scen.candidate_worktree):
                return evidence
            return replace(evidence, nodes=(), report_present=False)

        monkeypatch.setattr(contract, "_run_suite", _suppressed)
    elif case == "xpass":
        scen = _scenario(
            tmp_path,
            "post-xpass",
            candidate_files={
                "tests/test_new.py": (
                    'import pytest\n\n\n@pytest.mark.xfail(reason="p")\n'
                    "def test_new():\n    assert True\n"
                )
            },
        )
        targeted = (new_id,)
        expected_reason = "xpass"
        expected_added = {new_id}
        expected_offending = (new_id,)
    elif case == "setup_error":
        scen = _scenario(
            tmp_path,
            "post-setup-error",
            candidate_files={
                "tests/test_new.py": "def test_new(undefined_fixture_xyz):\n    assert False\n"
            },
        )
        targeted = (new_id,)
        expected_reason = "setup_error"
        expected_added = {new_id}
        expected_offending = (new_id,)
    else:  # pragma: no cover - guards the parametrize list
        raise AssertionError(f"unknown case {case!r}")

    run = _mk_run()
    rp = _prove(run, scen, targeted_ids=targeted)
    assert rp.accepted is False
    assert rp.reason == expected_reason
    if expected_offending is not None:
        assert rp.offending_ids == expected_offending
    assert set(rp.added_ids) == expected_added
