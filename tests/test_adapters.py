"""Committed PENDING acceptance suite for #6 (S1) — the verification adapter interface.

The ``issueforge.adapters.*`` symbols are imported inside each test body (they do not exist
yet); a module-top import would ERROR at collection instead of xfailing. The tiny helpers
below touch no IssueForge code, so they live at module top.
"""

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


def test_pytest_adapter_implements_probe_only_this_slice():
    """This slice implements only probe on the pytest adapter; the other five are not built.

    Contract: PytestAdapter().probe(toolchain) returns a real result; each of
    provision_environment/canonical_collect/classify/discover_contract_dependencies/
    validate_invocation raises NotImplementedError (declared, deferred), never a silent stub.
    """
    from issueforge.adapters.pytest_adapter import PytestAdapter

    adapter = PytestAdapter()
    assert adapter.probe(_FakeToolchain()) is not None

    deferred = [
        lambda: adapter.provision_environment(None),
        lambda: adapter.canonical_collect(None),
        lambda: adapter.classify(None),
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
