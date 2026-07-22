"""Committed PENDING acceptance suite for #6 (S1) — the verification adapter interface.

The ``issueforge.adapters.*`` symbols are imported inside each test body (they do not exist
yet); a module-top import would ERROR at collection instead of xfailing. The tiny helpers
below touch no IssueForge code, so they live at module top.
"""

import sys
import textwrap

import pytest


class _FakeToolchain:
    """A toolchain seam whose reporter version differs from the host interpreter."""

    def version(self, name: str) -> str:
        return "9.9.9"


def _if_truthy(value):
    if value:
        return True
    return False


def test_verification_adapter_protocol_declares_six_signatures():
    """The adapter Protocol declares all six operations with their signatures.

    Contract: get_protocol_members(VerificationAdapter) (or equivalent) is exactly
    {probe, provision_environment, canonical_collect, classify,
    discover_contract_dependencies, validate_invocation}; provision_environment's signature
    includes frozen_deps defaulting to None.
    """
    import inspect

    from issueforge.adapters.base import VerificationAdapter

    try:
        from typing import get_protocol_members

        members = set(get_protocol_members(VerificationAdapter))
    except ImportError:
        members = set(VerificationAdapter.__protocol_attrs__)

    assert members == {
        "probe",
        "provision_environment",
        "canonical_collect",
        "classify",
        "discover_contract_dependencies",
        "validate_invocation",
    }
    assert getattr(VerificationAdapter, "_is_runtime_protocol", False) is True

    sig = inspect.signature(VerificationAdapter.provision_environment)
    assert sig.parameters["frozen_deps"].default is None


def test_pytest_adapter_defers_only_the_s12_s13_operations():
    """probe is implemented; the S12/S13 operations remain declared-but-deferred.

    Contract: PytestAdapter().probe(toolchain) returns a real result; discover_contract_dependencies
    (S12) and validate_invocation (S13) raise NotImplementedError (declared, deferred, never a
    silent stub). provision_environment/canonical_collect/classify are NOT asserted deferred here —
    S6 implements them, and their behavior is pinned by the S6 tests below; this test must not
    regress once S6 lands.
    """
    from issueforge.adapters.pytest_adapter import PytestAdapter

    adapter = PytestAdapter()
    assert adapter.probe(_FakeToolchain()) is not None

    deferred = [
        lambda: adapter.discover_contract_dependencies(None),
        lambda: adapter.validate_invocation(None),
    ]
    for call in deferred:
        with pytest.raises(NotImplementedError):
            call()


def test_pytest_probe_reads_supplied_toolchain_and_pins_reporter_version():
    """probe reads the toolchain it is handed and pins that reporter's exact version.

    Contract: a toolchain seam whose reporter version is 9.9.9 (!= host),
    PytestAdapter().probe(toolchain) -> .reporter_version == "9.9.9" (from the seam, not the
    host importlib.metadata) and .capabilities equals the adapter's declared capabilities.
    """
    from issueforge.adapters.pytest_adapter import PytestAdapter

    probe = PytestAdapter().probe(_FakeToolchain())

    assert probe.reporter_version == "9.9.9"
    assert probe.capabilities == PytestAdapter.CAPABILITIES


def test_registry_keys_on_both_framework_and_reporter():
    """Adapter selection keys on both framework and reporter, not framework alone and not language.

    Contract: registry.resolve(framework="pytest", reporter=<pytest reporter>) -> the pytest
    adapter; resolve(framework="unittest", reporter=<pytest reporter>) -> unresolved; and
    resolve(framework="pytest", reporter="nonesuch") -> unresolved.
    """
    from issueforge.adapters.base import registry
    from issueforge.adapters.pytest_adapter import PytestAdapter

    hit = registry.resolve(framework=PytestAdapter.framework, reporter=PytestAdapter.reporter)
    assert hit is not None
    assert hit.framework == "pytest"

    assert registry.resolve(framework="unittest", reporter=PytestAdapter.reporter) is None
    assert registry.resolve(framework=PytestAdapter.framework, reporter="nonesuch") is None


def test_error_outcome_propagates_through_predicate_inversions():
    """An error Outcome propagates through every boolean inversion, it can never read as "no".

    Contract: over the inversion forms bool(Outcome.BROKEN), not Outcome.BROKEN, and
    if Outcome.BROKEN: each raises TypeError; never returns False.
    """
    from issueforge.adapters.base import Outcome

    forms = [
        lambda: bool(Outcome.BROKEN),
        lambda: not Outcome.BROKEN,
        lambda: _if_truthy(Outcome.BROKEN),
    ]
    for form in forms:
        with pytest.raises(TypeError):
            form()


# =========================================================================================
# S6 (#12) — provision_environment / canonical_collect / classify (red vs BROKEN).
#
# Every symbol below (BaselineStatus, the real classify/canonical_collect/provision_environment
# behavior) lands in S6 and does not exist yet, so each test imports inside its body and is
# @pytest.mark.xfail(strict=True, reason="PENDING (#12)"). The classify state matrix is exercised
# with synthetic phase records for exhaustive coverage; test_verify.py grounds the load-bearing
# cases (GREEN, ALL_SKIPPED, NO_TESTS_COLLECTED, BEHAVIORAL_RED) in REAL pytest runs.
# =========================================================================================


def _rec(nodeid: str, when: str, outcome: str) -> dict:
    """One report-log phase record (the vocabulary classify must understand)."""
    return {"nodeid": nodeid, "when": when, "outcome": outcome, "longrepr": None}


# ------------------------------------------------- the S6 operations on the portable Protocol


def test_verification_adapter_protocol_declares_the_s6_signatures():
    """S6 widens the portable Protocol (base.py), not just the pytest adapter, so any adapter must
    implement the real signatures — a stale ``classify(native_events)`` Protocol cannot linger.

    Contract: inspect.signature(VerificationAdapter.classify) declares the keyword parameters
    exit_code, timed_out, report_present, and expected_ids; provision_environment declares a
    provisioner parameter (in addition to frozen_deps); these are the signatures the S6 behavioral
    tests call through the pytest adapter.
    """
    import inspect

    from issueforge.adapters.base import VerificationAdapter

    classify_params = set(inspect.signature(VerificationAdapter.classify).parameters)
    assert {"exit_code", "timed_out", "report_present", "expected_ids"} <= classify_params

    provision_params = set(inspect.signature(VerificationAdapter.provision_environment).parameters)
    assert "provisioner" in provision_params


# --------------------------------------------------------------- the closed run-level enum


def test_baseline_status_is_a_closed_ten_member_enum_separate_from_outcome():
    """classify's run-level verdict is a closed 10-member BaselineStatus enum, distinct from the
    per-node Outcome, and GREEN is the sole success member.

    Contract: {m.name for m in BaselineStatus} == exactly {GREEN, BEHAVIORAL_RED,
    COLLECTION_ERROR, NO_TESTS_COLLECTED, ALL_SKIPPED, USAGE_ERROR, INTERNAL_ERROR, TIMEOUT,
    LAUNCH_FAILED, BROKEN}; BaselineStatus is not Outcome; GREEN is not a member of Outcome.
    """
    # amended 9->10 for #58: BROKEN run-level state (Matt-approved 2026-07-21)
    from issueforge.adapters.base import BaselineStatus, Outcome

    assert {m.name for m in BaselineStatus} == {
        "GREEN",
        "BEHAVIORAL_RED",
        "COLLECTION_ERROR",
        "NO_TESTS_COLLECTED",
        "ALL_SKIPPED",
        "USAGE_ERROR",
        "INTERNAL_ERROR",
        "TIMEOUT",
        "LAUNCH_FAILED",
        "BROKEN",
    }
    assert BaselineStatus is not Outcome
    assert "GREEN" not in {m.name for m in Outcome}


def test_classify_returns_a_status_record_never_a_passed_failed_pair():
    """classify returns a structured result carrying a BaselineStatus + counts + per-node records,
    never a bare (passed, failed) pair.

    Contract: over two passing nodes a,b with expected_ids {a,b} and exit 0, the result exposes
    .status is BaselineStatus.GREEN, .collected == 2, .executed == 2, .report_present is True, and
    .nodes is a sequence of records each with (nodeid, phase, outcome, longrepr) — not a 2-tuple
    of ints.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    records = [_rec("t::a", "call", "passed"), _rec("t::b", "call", "passed")]
    res = PytestAdapter().classify(
        records, exit_code=0, timed_out=False, report_present=True, expected_ids={"t::a", "t::b"}
    )
    assert res.status is BaselineStatus.GREEN
    assert res.collected == 2 and res.executed == 2
    assert res.report_present is True
    assert res.exit_code == 0
    node = res.nodes[0]
    assert hasattr(node, "nodeid") and hasattr(node, "phase")
    assert hasattr(node, "outcome") and hasattr(node, "longrepr")


# ------------------------------------------------- GREEN is a conjunction, never a residual


def test_green_requires_every_expected_node_present_and_passed():
    """GREEN = exit 0 AND collected>0 AND executed>0 AND every EXPECTED id present with outcome
    passed. A missing expected node is not green even at exit 0.

    Contract: exit 0, records only [a:call:passed], but expected_ids {a, b} (b vanished from the
    run) -> status is NOT GREEN (b was expected but never present-and-passed).
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [_rec("t::a", "call", "passed")],
        exit_code=0,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert res.status is not BaselineStatus.GREEN


def test_an_incomplete_report_at_exit_zero_is_not_green():
    """An exit-0 run whose report is INCOMPLETE (an expected node has no terminal record) is not
    green — report completeness is fused into the verdict, not just the exit code.

    Contract: exit 0, report_present=True, but records only [a:call:passed] with expected_ids
    {a, b} (b's records absent — a truncated/incomplete report) -> status is NOT GREEN. Also an
    empty report at exit 0 with a non-empty expected set is not green.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    adapter = PytestAdapter()
    incomplete = adapter.classify(
        [_rec("t::a", "call", "passed")],
        exit_code=0,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert incomplete.status is not BaselineStatus.GREEN

    empty = adapter.classify(
        [], exit_code=0, timed_out=False, report_present=True, expected_ids={"t::a"}
    )
    assert empty.status is not BaselineStatus.GREEN


def test_all_skipped_at_exit_zero_is_all_skipped_not_green():
    """Exit 0 with every node skipped (no call phase executed) is ALL_SKIPPED, never GREEN.

    Contract: exit 0, records [a:setup:skipped, b:setup:skipped] (executed==0, collected==2) ->
    status is BaselineStatus.ALL_SKIPPED.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [_rec("t::a", "setup", "skipped"), _rec("t::b", "setup", "skipped")],
        exit_code=0,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert res.status is BaselineStatus.ALL_SKIPPED


# ------------------------------------------- BROKEN vs RED: distinct diagnostics, never merged


def test_zero_collected_is_no_tests_collected_a_distinct_state():
    """Zero collected (pytest exit 5) is NO_TESTS_COLLECTED — a third state, neither GREEN nor
    BEHAVIORAL_RED.

    Contract: exit 5, no records, collected 0 -> status is BaselineStatus.NO_TESTS_COLLECTED,
    and it is neither GREEN nor BEHAVIORAL_RED.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [], exit_code=5, timed_out=False, report_present=True, expected_ids=set()
    )
    assert res.status is BaselineStatus.NO_TESTS_COLLECTED
    assert res.status not in {BaselineStatus.GREEN, BaselineStatus.BEHAVIORAL_RED}


def test_collection_error_and_internal_error_are_never_collapsed():
    """A collection error (exit 2) and an internal error (exit 3) are DISTINCT diagnostics, never
    folded into one anomaly.

    Contract: classify(..., exit_code=2) -> COLLECTION_ERROR; classify(..., exit_code=3) ->
    INTERNAL_ERROR; the two statuses differ.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    adapter = PytestAdapter()
    collection = adapter.classify(
        [], exit_code=2, timed_out=False, report_present=False, expected_ids=set()
    )
    internal = adapter.classify(
        [], exit_code=3, timed_out=False, report_present=False, expected_ids=set()
    )
    assert collection.status is BaselineStatus.COLLECTION_ERROR
    assert internal.status is BaselineStatus.INTERNAL_ERROR
    assert collection.status is not internal.status


def test_usage_error_exit_four_is_its_own_status():
    """A usage error (exit 4) classifies as USAGE_ERROR, not conflated with a collection error.

    Contract: classify(..., exit_code=4) -> status is BaselineStatus.USAGE_ERROR.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [], exit_code=4, timed_out=False, report_present=False, expected_ids=set()
    )
    assert res.status is BaselineStatus.USAGE_ERROR


def test_a_failed_call_at_exit_one_is_behavioral_red():
    """A failing node at exit 1 is BEHAVIORAL_RED, with the failed node carried as an Outcome.FAILED
    record.

    Contract: exit 1, records [a:call:passed, b:call:failed] -> status is BEHAVIORAL_RED, and the
    node record for b has outcome Outcome.FAILED.
    """
    from issueforge.adapters.base import BaselineStatus, Outcome
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [_rec("t::a", "call", "passed"), _rec("t::b", "call", "failed")],
        exit_code=1,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert res.status is BaselineStatus.BEHAVIORAL_RED
    b = [n for n in res.nodes if n.nodeid == "t::b" and n.phase == "call"][0]
    assert b.outcome is Outcome.FAILED


# ----------------------------------- evidence fusion: absence is signal (timeout / launch)


def test_timeout_flag_fuses_to_timeout_even_though_a_report_is_absent():
    """A timeout is fused from the engine-side timed_out flag; the report is absent and that
    absence is expected signal, not an anomaly.

    Contract: timed_out=True, report_present=False, exit_code=-9 -> status is BaselineStatus.TIMEOUT
    (the timeout flag dominates the raw negative exit code).
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [], exit_code=-9, timed_out=True, report_present=False, expected_ids=set()
    )
    assert res.status is BaselineStatus.TIMEOUT


def test_no_report_without_a_timeout_is_launch_failed():
    """A missing report with no timeout and no captured exit is LAUNCH_FAILED — the process never
    produced evidence, distinct from a timeout.

    Contract: timed_out=False, report_present=False, exit_code=None -> status is
    BaselineStatus.LAUNCH_FAILED, kept distinct from TIMEOUT.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [], exit_code=None, timed_out=False, report_present=False, expected_ids=set()
    )
    assert res.status is BaselineStatus.LAUNCH_FAILED


# --------------------------------------------- canonical_collect: the source of expected_ids


def test_canonical_collect_returns_the_exact_repo_relative_ids_deterministically(tmp_path):
    """canonical_collect returns the EXACT repository-relative node-id set of a real tree,
    identically across repeated calls, plus selection metadata — the frozen expected-id set.

    Contract: a target with test_a and test_b; canonical_collect yields ids EXACTLY equal to
    {'tests/test_target.py::test_a', 'tests/test_target.py::test_b'} (repo-relative, no absolute
    path prefix); a second call returns the identical tuple (stable, not merely one sort order);
    and the collection carries selection/command metadata for the freeze.
    """
    from types import SimpleNamespace

    from issueforge.adapters.pytest_adapter import PytestAdapter

    repo = tmp_path / "collect"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_target.py").write_text(
        textwrap.dedent(
            """
            def test_a():
                assert True

            def test_b():
                assert True
            """
        )
    )
    adapter = PytestAdapter()
    invocation = SimpleNamespace(
        worktree=repo, interpreter=sys.executable, command=["-m", "pytest"]
    )
    first = adapter.canonical_collect(invocation)
    assert set(first.ids) == {
        "tests/test_target.py::test_a",
        "tests/test_target.py::test_b",
    }
    # Stable across repeated collection (byte-identical id tuple), and selection metadata present.
    second = adapter.canonical_collect(invocation)
    assert tuple(first.ids) == tuple(second.ids)
    assert getattr(first, "selection", None) is not None


# ------------------------------------------------ provision_environment: the authoritative env


def test_provision_environment_delegates_to_the_provisioner_seam(tmp_path):
    """provision_environment builds the authoritative handle through an injectable provisioner
    seam, and reports network OFF.

    Contract: an injected provisioner records the (worktree, frozen_deps) it was called with and
    returns a known handle; adapter.provision_environment(worktree, frozen_deps,
    provisioner=fake) returns that handle, with .network False.
    """
    from types import SimpleNamespace

    from issueforge.adapters.pytest_adapter import PytestAdapter

    calls = []

    def _fake(worktree, frozen_deps=None):
        calls.append((worktree, frozen_deps))
        return SimpleNamespace(
            interpreter=sys.executable, env={}, artifact_dir=tmp_path / "a", network=False
        )

    handle = PytestAdapter().provision_environment(
        tmp_path / "wt", {"pytest": "8.3"}, provisioner=_fake
    )
    assert calls == [(tmp_path / "wt", {"pytest": "8.3"})]
    assert handle.network is False
    assert handle.interpreter == sys.executable


# ======================================================================
# Issue #58 — S6 hardening: adversarial tests reproducing the 14 Codex-found
# defects the happy-path S6 suite missed. Each is PENDING until the #58 build.
# ======================================================================


# ===== #58 defect #10 =====
def test_authoritative_run_network_is_denied_at_os_level(tmp_path):
    """The authoritative baseline RUN executes with OS-level network denial (a container with
    ``--network none``), enforced by the executor — not merely recorded on the handle, NOT fakeable
    by an in-interpreter ``socket`` stub, NOT satisfiable by a merely-broken environment, and NOT
    able to pass VACUOUSLY when outbound DNS/port 53 happens to be blocked.

    Denial is proven DETERMINISTICALLY against a HOST TCP listener this test owns — never an external
    address that might be firewalled (a firewalled external target would make the egress probe fail
    for the WRONG reason and let the gate pass without ``--network none`` denying anything). The
    listener binds an ephemeral port on the host and is advertised at the host's ROUTABLE IP (not
    ``127.0.0.1``: under ``--network none`` the container keeps its OWN loopback, so a loopback target
    would fail even WITH a network and prove nothing — the routable host IP is reachable from the
    host-run control but unreachable from the OS-denied container).

    Attribution is pinned by TWO baseline tests, each spawning a ``-S`` child (site disabled, so an
    injected ``sitecustomize`` / in-interpreter ``socket`` monkeypatch cannot reach either child):
    ``test_local_compute`` does pure local work (must PASS — proves the interpreter + subprocess
    machinery run inside the authoritative run) and ``test_child_egress`` connects to the host
    listener (must FAIL only because the OS denied egress). A broken env (invalid interpreter, patched
    ``subprocess.run``, missing stdlib) fails BOTH children, so the compute node stops passing and the
    assertion fails — a broken environment can never masquerade as network denial. Only real OS-level
    denial makes egress fail while compute still passes.

    The POSITIVE CONTROL is a hard ASSERTION, never a skip: the host-run baseline (network on) MUST
    reach the listener (GREEN). That establishes the listener is live and the probe connects, so the
    later egress FAILURE can only be the OS denial — not a dead port. The ONLY sanctioned skip is a
    genuinely absent docker daemon; a blocked port can no longer masquerade as a silent skip-pass.
    IssueForge CI (ubuntu-latest) has docker, so the gate RUNS and is enforced there.

    technical (contract): the target has two ``-S``-child tests — ``test_local_compute`` runs
    ``print(2 + 2)`` and asserts stdout "4"; ``test_child_egress`` runs
    ``socket.create_connection((HOST_IP, PORT), timeout=5).close()`` against the host listener and
    asserts returncode 0. Under ``_allowed_provisioner`` (network=True, host interpreter) run_baseline
    -> BaselineStatus.GREEN (both pass, listener reached). Under ``provisioner=None`` (the REAL default
    authoritative path, docker ``--network none``) run_baseline -> BaselineStatus.BEHAVIORAL_RED,
    executed == 2, the ``test_local_compute`` call node PASSED and the ``test_child_egress`` call node
    FAILED (the container cannot reach the routable host IP).
    """
    import os
    import socket
    import subprocess
    import threading
    from pathlib import Path
    from types import SimpleNamespace

    from issueforge import verify
    from issueforge.adapters.base import BaselineStatus, Outcome
    from issueforge.adapters.pytest_adapter import PytestAdapter

    # The ONLY sanctioned skip: the docker daemon is genuinely unreachable, so the OS-level denial
    # mechanism does not exist here. A blocked port is NOT a reason to skip — denial is proven against
    # a host listener we control, so there is no external-egress dependency to skip on.
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        info = None
    if info is None or info.returncode != 0:
        pytest.skip(
            "docker daemon unreachable; OS-level network denial cannot be enforced/verified"
        )

    # The host's ROUTABLE outbound IP (the interface the kernel would egress on). A UDP "connect"
    # sends no packet; it just selects the local address. This is the address the container is denied.
    _probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _probe.connect(("8.8.8.8", 80))
        host_ip = _probe.getsockname()[0]
    finally:
        _probe.close()

    # A real host TCP listener on an ephemeral port, accepting for the duration of BOTH runs.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    accepting = True

    def _serve():
        while accepting:
            try:
                conn, _ = listener.accept()
                conn.close()
            except OSError:
                break

    server = threading.Thread(target=_serve, daemon=True)
    server.start()
    try:
        # Two -S children: a local-compute probe that must PASS and an egress probe (to the host
        # listener) that must FAIL under denial. HOST/PORT are baked in as literals and handed to the
        # egress child via its environment (kept out of the -c source, robust to quoting).
        repo = tmp_path / "netdeny"
        (repo / "tests").mkdir(parents=True)
        target_src = f"HOST = {host_ip!r}\nPORT = {port}\n" + textwrap.dedent(
            """
                import os
                import subprocess
                import sys

                def test_local_compute():
                    # Pure local work in a -S child: proves the interpreter + subprocess machinery
                    # run. A broken authoritative env fails THIS too, so denial cannot be confused
                    # with it.
                    result = subprocess.run(
                        [sys.executable, "-S", "-c", "print(2 + 2)"],
                        capture_output=True,
                        text=True,
                    )
                    assert result.returncode == 0 and result.stdout.strip() == "4"

                def test_child_egress():
                    # Connect to the HOST listener in a -S child: reachable from the host-run control,
                    # unreachable from the --network none container. Only OS-level denial makes THIS
                    # fail while test_local_compute still passes.
                    probe = (
                        "import os, socket; "
                        "socket.create_connection("
                        "(os.environ['NETDENY_HOST'], int(os.environ['NETDENY_PORT'])), "
                        "timeout=5).close()"
                    )
                    result = subprocess.run(
                        [sys.executable, "-S", "-c", probe],
                        env={**os.environ, "NETDENY_HOST": HOST, "NETDENY_PORT": str(PORT)},
                    )
                    assert result.returncode == 0
                """
        )
        (repo / "tests" / "test_target.py").write_text(target_src)
        baseline = ["-m", "pytest"]
        adapter = PytestAdapter()

        def _allowed_provisioner(worktree, frozen_deps=None):
            env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
            return SimpleNamespace(
                interpreter=sys.executable,
                env=env,
                artifact_dir=Path(worktree).parent / "if-allowed",
                network=True,
            )

        # POSITIVE CONTROL (a hard assertion, never a skip): the host-run baseline (network on) MUST
        # reach the host listener — both children pass, so the run is GREEN. This proves the listener
        # is live and the probe connects, so a later egress FAILURE can only be OS-level denial, not a
        # dead port. If this ever fails, the environment cannot attribute denial and the test FAILS
        # loudly rather than passing vacuously.
        control = verify.run_baseline(
            repo, baseline, adapter=adapter, provisioner=_allowed_provisioner, timeout=120
        )
        assert control.status is BaselineStatus.GREEN, (
            f"host-run control could not reach the host listener at {host_ip}:{port} "
            f"(status={control.status!r}); cannot attribute the denied run to OS-level denial"
        )

        # THE DENIAL: the REAL default authoritative path runs under docker --network none, so the
        # SAME egress probe cannot reach the routable host listener while local compute still succeeds.
        # Requiring the compute node PASSED and the egress node FAILED (not merely "not GREEN") means a
        # broken environment — which fails compute too — can never masquerade as network denial.
        authoritative = verify.run_baseline(
            repo, baseline, adapter=adapter, provisioner=None, timeout=600
        )
        assert authoritative.status is BaselineStatus.BEHAVIORAL_RED
        assert authoritative.executed == 2
        calls = {n.nodeid: n for n in authoritative.nodes if n.phase == "call"}
        compute = next((n for nid, n in calls.items() if "test_local_compute" in nid), None)
        egress = next((n for nid, n in calls.items() if "test_child_egress" in nid), None)
        assert compute is not None and compute.outcome is Outcome.PASSED
        assert egress is not None and egress.outcome is Outcome.FAILED
    finally:
        accepting = False
        listener.close()
        server.join(timeout=5)


# ===== #58 defect #11 =====
def test_default_provisioning_builds_target_dep_env_and_artifact_dir(tmp_path):
    """Default provisioning must build the target's dependency environment: install the frozen
    manifest so a pinned dep the baseline imports is importable in the authoritative interpreter,
    and create the artifact directory it hands back (never a computed-but-never-made phantom path).

    technical (contract): PytestAdapter().provision_environment(worktree, {"platformdirs":
    "4.10.0"}) on the DEFAULT path (no injected provisioner) returns a handle whose
    .artifact_dir is a real EXISTING directory (Path(handle.artifact_dir).is_dir() is True) — the
    current impl computes Path(state_root())/"artifacts"/<uuid> but never creates it, so this is
    False today. The authoritative interpreter (handle.interpreter, != sys.executable) carries the
    frozen manifest: running it as `-c "import platformdirs, sys; sys.stdout.write(
    platformdirs.__version__)"` exits 0 and prints exactly "4.10.0". platformdirs is NOT a
    transitive dependency of pytest/pytest-reportlog, so its importability in the SEPARATE venv
    proves the frozen manifest was installed there, not leaked from the host interpreter.
    """
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path

    from issueforge.adapters.pytest_adapter import PytestAdapter

    worktree = tmp_path / "target"
    worktree.mkdir()

    handle = PytestAdapter().provision_environment(worktree, {"platformdirs": "4.10.0"})

    # The artifact dir the handle advertises must actually exist (created + verified), never a
    # path that was computed and handed back without being made.
    artifact_dir = Path(handle.artifact_dir)
    assert artifact_dir.is_dir(), f"artifact_dir advertised but never created: {artifact_dir}"

    # The authoritative interpreter is a SEPARATE venv that must carry the frozen manifest; a
    # dep that is not a pytest transitive proves the manifest was installed, not host-leaked.
    interpreter = str(handle.interpreter)
    assert interpreter != _sys.executable, "authoritative run used the HOST interpreter"
    proc = _sp.run(
        [
            interpreter,
            "-c",
            "import platformdirs, sys; sys.stdout.write(platformdirs.__version__)",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"frozen dep not importable in authoritative env: {proc.stderr}"
    assert proc.stdout.strip() == "4.10.0"


# ======================================================================
# PR #60 hardening — adversarial tests for the 11 GREEN-safety gaps Codex
# found in the S6 hardening build. PENDING until the fix closes them.
# ======================================================================


# ===== #58 hardening (PR#60 Codex gap #7) =====
def test_green_forbids_an_unexpected_ghost_node_that_passes():
    """GREEN means collection and execution RECONCILE: exactly the expected node-ids ran and
    passed, no strangers. A report that carries a passing record for a node nobody collected
    (a "ghost") is a reconciliation failure — the run executed something outside the frozen
    expected set — so it must not green even though every expected node itself passed.

    technical (contract): classify with expected_ids={t::a} over records
    [t::a:call:passed, t::ghost:call:passed] at exit 0, report_present=True, timed_out=False
    -> status is NOT BaselineStatus.GREEN. The unexpected passing t::ghost (collected==2 but
    expected=={t::a}) forbids green because GREEN checks the expected set is a passing SUBSET,
    not that the executed set equals the expected set. Today this returns GREEN.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [_rec("t::a", "call", "passed"), _rec("t::ghost", "call", "passed")],
        exit_code=0,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a"},
    )
    assert res.status is not BaselineStatus.GREEN


# ===== #58 hardening (PR#60 Codex gap #8) =====
def test_behavioral_red_requires_a_complete_report_and_no_infra_failure():
    """BEHAVIORAL_RED is a whole-report property: a genuine behavioral red baseline needs a COMPLETE
    report (every expected node ran to a terminal record) whose only failure is an expected
    call-phase failure. A single expected call failure is NOT sufficient. If some expected node
    never executed, the report is incomplete and the run is BROKEN, not a trustworthy red. If the
    failing node ALSO has a setup/teardown (infrastructure) failure, the run is contaminated by
    infra breakage and is BROKEN, not a clean behavioral red. Today classify latches onto the first
    expected call:failed record and mislabels both truncated and infra-contaminated reports as
    BEHAVIORAL_RED.

    technical (contract):
      adapter = PytestAdapter()
      Case A (incomplete report): classify(records=[a:call:failed], exit_code=1, timed_out=False,
        report_present=True, expected_ids={a, b}) -> status is BaselineStatus.BROKEN (b never
        produced any terminal record; the report is incomplete), and is NOT BEHAVIORAL_RED.
      Case B (mixed call + teardown failure): classify(records=[a:call:failed, a:teardown:failed,
        b:call:passed], exit_code=1, timed_out=False, report_present=True, expected_ids={a, b})
        -> status is BaselineStatus.BROKEN (a's teardown/setup-infrastructure failure contaminates
        the baseline), and is NOT BEHAVIORAL_RED.
      A complete report with an expected call failure and no infra failure remains BEHAVIORAL_RED
      (see test_a_failed_call_at_exit_one_is_behavioral_red) — this test only forbids the two
      unsafe reports from being mislabeled.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    adapter = PytestAdapter()

    # Case A: expected {a, b} but only a:call:failed — b never executed (a truncated/incomplete
    # report). One expected call failure is not a complete, trustworthy red baseline.
    incomplete = adapter.classify(
        [_rec("t::a", "call", "failed")],
        exit_code=1,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert incomplete.status is BaselineStatus.BROKEN
    assert incomplete.status is not BaselineStatus.BEHAVIORAL_RED

    # Case B: a fails in the call phase AND in teardown (infra breakage); b passes and the report is
    # otherwise complete. The infra failure contaminates the baseline -> BROKEN, not a clean red.
    mixed = adapter.classify(
        [
            _rec("t::a", "call", "failed"),
            _rec("t::a", "teardown", "failed"),
            _rec("t::b", "call", "passed"),
        ],
        exit_code=1,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert mixed.status is BaselineStatus.BROKEN
    assert mixed.status is not BaselineStatus.BEHAVIORAL_RED


# ===== #58 hardening (PR#60 second round — gap D: setup-only node counted complete) =====
def test_behavioral_red_requires_every_expected_node_to_reach_a_call_phase():
    """BEHAVIORAL_RED requires every expected node to have reached a call phase (or a terminal
    lifecycle). A node that produced ONLY a non-call record — e.g. ``setup:passed`` with no
    following call — never ran its test body, so the report is INCOMPLETE and the run is BROKEN, not
    a trustworthy behavioral red. The current completeness check only asks whether each expected node
    produced ANY record, so a setup-only node is wrongly counted as complete and the run latches onto
    the other node's call failure as BEHAVIORAL_RED.

    technical (contract): classify(records=[a:call:failed, b:setup:passed], exit_code=1,
    timed_out=False, report_present=True, expected_ids={a, b}) -> status is BaselineStatus.BROKEN
    (b never reached a call/terminal lifecycle), and is NOT BEHAVIORAL_RED. Current impl: b's
    setup:passed record satisfies the "produced a record" check, so status is BEHAVIORAL_RED.
    """
    from issueforge.adapters.base import BaselineStatus
    from issueforge.adapters.pytest_adapter import PytestAdapter

    res = PytestAdapter().classify(
        [_rec("t::a", "call", "failed"), _rec("t::b", "setup", "passed")],
        exit_code=1,
        timed_out=False,
        report_present=True,
        expected_ids={"t::a", "t::b"},
    )
    assert res.status is BaselineStatus.BROKEN
    assert res.status is not BaselineStatus.BEHAVIORAL_RED
