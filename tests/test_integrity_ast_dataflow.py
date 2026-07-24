"""PENDING acceptance suite for issue #19 (S13) — AST-backstop weaken-check
deeper-dataflow and alias-indirection vectors ported from MARVIN's
scripts/check_acceptance_integrity.py (#588, #627, #666) onto IssueForge's
locked API:

    from issueforge import integrity
    integrity.classify_acceptance_change(old_src: str, new_src: str) -> str
        # "allowed" | "weakened"
    integrity.is_pending(src: str, test_name: str) -> bool

``issueforge.integrity`` does not exist yet (S13 is what builds it), so every
test imports it INSIDE its body and carries
``@pytest.mark.xfail(strict=True, reason="PENDING (#19)")`` — an
ImportError/AttributeError while unbuilt, mirroring tests/test_contract.py:6-8.

Ported from (marvin repo, faithful port of the DISTINCT vectors, adapted only
to the classify/is_pending return-value API — MARVIN's CLI names the offending
test/predicate in stdout; IssueForge's pure functions return only
"allowed"/"weakened" or a bool, so assertions check the return value, not a
message string):
  - tests/test_acceptance_integrity_588.py  (deeper data flow: fixture/helper/
    param-default/computed-expression resolution; parametrize is KNOWN-SAFE)
  - tests/test_acceptance_integrity_627.py  (alias-indirection downgrade +
    class-marker entry inheritance across name forms)
  - tests/test_acceptance_integrity_666.py  (AST-scope name-shadowing edges:
    comprehension targets do not leak; a helper's own locals are not followed
    into module scope)

Every fixture pair below reproduces MARVIN's source-string shapes verbatim
(module docstrings there explain WHY each shape is adversarial); only the
pending-marker text embedded IN the fixture source (representing the marker
on the ACCEPTANCE TEST UNDER TEST, not on this suite's own tests) is kept as
in MARVIN — it is fixture content, not this file's own xfail marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TARGET = "test_second_match_returns_409"
SIBLING = "test_first_match_succeeds"

_PENDING_588 = 'reason="pending: #217 durable fix not built"'
_PENDING_627 = 'reason="pending: #217 durable fix not built"'
_PENDING_666 = 'reason="pending: #666 scope fix not built"'

# ============================================================================
# #588 — deeper data-flow resolution (fixture/helper/param-default/computed)
# ============================================================================

# 1. Fixture return value.
OLD_FIXTURE_RETURN = f"""import pytest


@pytest.fixture
def expected():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

NEW_FIXTURE_RETURN = f"""import pytest


@pytest.fixture
def expected():
    return 200


def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

# 2. Parameter default whose VALUE flows in through a helper call.
OLD_PARAM_DEFAULT = f"""import pytest


def _expected_status():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}(expected=_expected_status()):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

NEW_PARAM_DEFAULT = f"""import pytest


def _expected_status():
    return 200


def {TARGET}(expected=_expected_status()):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

# 3. Transitively-computed module value: EXPECTED = base + delta, delta flips.
OLD_COMPUTED_EXPR = f"""import pytest

base = 400
delta = 9
EXPECTED = base + delta


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == EXPECTED
"""

NEW_COMPUTED_EXPR = f"""import pytest

base = 400
delta = 0
EXPECTED = base + delta


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == EXPECTED
"""

# 4. Helper the assertion calls directly.
OLD_HELPER_RETURN = f"""import pytest


def _expected_status():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

NEW_HELPER_RETURN = f"""import pytest


def _expected_status():
    return 200


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

# 5. Class method, helper return (#610 x #588 shared per-test path).
OLD_CLASS_HELPER = f"""import pytest


def _expected_status():
    return 409


class TestMatching:
    @pytest.mark.xfail(strict=True, {_PENDING_588})
    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == _expected_status()
"""

NEW_CLASS_HELPER = f"""import pytest


def _expected_status():
    return 200


class TestMatching:
    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == _expected_status()
"""

# 6. Class method, fixture return (non-helper vector).
OLD_CLASS_FIXTURE_RETURN = f"""import pytest


@pytest.fixture
def expected():
    return 409


class TestMatching:
    @pytest.mark.xfail(strict=True, {_PENDING_588})
    def {TARGET}(self, expected):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == expected
"""

NEW_CLASS_FIXTURE_RETURN = f"""import pytest


@pytest.fixture
def expected():
    return 200


class TestMatching:
    def {TARGET}(self, expected):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == expected
"""

# --- GREEN controls: RESOLVER, not broad fail-closed -----------------------

OLD_UNRELATED_FIXTURE = f"""import pytest


@pytest.fixture
def expected():
    return 409


@pytest.fixture
def other():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

NEW_UNRELATED_FIXTURE = f"""import pytest


@pytest.fixture
def expected():
    return 409


@pytest.fixture
def other():
    return 200


def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

OLD_UNRELATED_HELPER = f"""import pytest


def _expected_status():
    return 409


def _unused_status():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

NEW_UNRELATED_HELPER = f"""import pytest


def _expected_status():
    return 409


def _unused_status():
    return 200


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

OLD_UNRELATED_COMPUTED = f"""import pytest

base = 400
delta = 9
EXPECTED = base + delta

other_base = 400
other_delta = 9
OTHER = other_base + other_delta


@pytest.mark.xfail(strict=True, {_PENDING_588})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == EXPECTED
"""

NEW_UNRELATED_COMPUTED = f"""import pytest

base = 400
delta = 9
EXPECTED = base + delta

other_base = 400
other_delta = 0
OTHER = other_base + other_delta


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == EXPECTED
"""

# --- GREEN guards: parametrize is KNOWN-SAFE --------------------------------

OLD_PARAMETRIZE_MARKER = f"""import pytest


@pytest.mark.parametrize("status", ["merged", "needs-review", "abandoned"])
@pytest.mark.xfail(strict=True, {_PENDING_588})
def test_terminal_status_rejected(status):
    resp = finalize(status=status)
    assert resp.status_code == 409
"""

NEW_PARAMETRIZE_MARKER = """import pytest


@pytest.mark.parametrize("status", ["merged", "needs-review", "abandoned"])
def test_terminal_status_rejected(status):
    resp = finalize(status=status)
    assert resp.status_code == 409
"""

# ============================================================================
# #627 — alias-indirection downgrade + class-marker entry inheritance
# ============================================================================

OLD_CHAIN_ALIAS = f"""import pytest

P1 = pytest.mark.xfail(strict=True, {_PENDING_627})
P2 = P1


@P2
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""

NEW_CHAIN_ROOT_SKIP = OLD_CHAIN_ALIAS.replace(
    f"P1 = pytest.mark.xfail(strict=True, {_PENDING_627})",
    'P1 = pytest.mark.skip(reason="flaky, disable for now")',
)
assert "pytest.mark.skip" in NEW_CHAIN_ROOT_SKIP
assert "P2 = P1" in NEW_CHAIN_ROOT_SKIP

OLD_IMPORTED_ALIAS = f"""import pytest
from markers import PENDING_217


@PENDING_217
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""

NEW_IMPORT_SWAPPED = OLD_IMPORTED_ALIAS.replace(
    "from markers import PENDING_217",
    "from markers import SKIP as PENDING_217",
)
assert "SKIP as PENDING_217" in NEW_IMPORT_SWAPPED

NEW_IMPORT_TO_LOCAL_SKIP = OLD_IMPORTED_ALIAS.replace(
    "from markers import PENDING_217",
    'PENDING_217 = pytest.mark.skip(reason="disable for now")',
)
assert "pytest.mark.skip" in NEW_IMPORT_TO_LOCAL_SKIP

NEW_IMPORT_DELETED = OLD_IMPORTED_ALIAS.replace("\nfrom markers import PENDING_217", "")
assert "from markers" not in NEW_IMPORT_DELETED
assert "@PENDING_217" in NEW_IMPORT_DELETED

OLD_SINGLE_ALIAS = f"""import pytest

PENDING = "pending: #217 durable fix not built"
pending = pytest.mark.xfail(strict=True, reason=PENDING)


@pending
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""

NEW_SINGLE_ALIAS_FLIPPED = f"""import pytest


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""

OLD_IMPORTED_WITH_SIBLING = f"""import pytest
from markers import PENDING_217


@PENDING_217
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409


@pytest.mark.xfail(strict=True, {_PENDING_627})
def {SIBLING}():
    resp = match_expense(expense_id=1, txn_id=1)
    assert resp.status_code == 201
"""

NEW_SIBLING_ACTIVATED = OLD_IMPORTED_WITH_SIBLING.replace(
    f"@pytest.mark.xfail(strict=True, {_PENDING_627})\ndef {SIBLING}", f"def {SIBLING}"
)
assert NEW_SIBLING_ACTIVATED.count("xfail") == 0
assert "@PENDING_217" in NEW_SIBLING_ACTIVATED

OLD_CLASS_LEVEL_PENDING = f"""import pytest


@pytest.mark.xfail(strict=True, {_PENDING_627})
class TestMatching:

    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""

OLD_CLASS_ENV_PLUS_PENDING = f"""import json

import pytest
from moto import mock_aws
from unittest.mock import patch

from src.handlers import game


@mock_aws
@patch("src.auth._get_stored_passphrase", return_value="test-passphrase")
@pytest.mark.xfail(strict=True, {_PENDING_627})
class TestGameIdempotency:

    def {TARGET}(self, mock_ssm):
        status, body = _parse_response(
            game.handler(_action_event("c1", "look around"), None)
        )
        assert status == 400
"""

OLD_CLASS_ALIAS_PENDING = f"""import pytest

PENDING = "pending: #217 durable fix not built"
pending = pytest.mark.xfail(strict=True, reason=PENDING)


@pending
class TestMatching:

    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""

OLD_TWO_CLASSES_ALPHA_PENDING = f"""import pytest


@pytest.mark.xfail(strict=True, {_PENDING_627})
class TestAlpha:

    def {TARGET}(self):
        assert alpha() == 409


class TestBeta:

    def {TARGET}(self):
        assert beta() == 409
"""

CLASS_SKIP_MARKED = f"""import pytest


@pytest.mark.skip(reason="later")
class TestMatching:

    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""

CLASS_XFAIL_NONSTRICT = f"""import pytest


@pytest.mark.xfail(strict=False, {_PENDING_627})
class TestMatching:

    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""

CLASS_IMPORTED_ALIAS_MARKED = f"""import pytest
from markers import PENDING_217


@PENDING_217
class TestMatching:

    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""

# ============================================================================
# #666 — AST-scope name-shadowing edges (comprehension / helper-local)
# ============================================================================

OLD_COMPREHENSION_SHADOW = f"""import pytest


@pytest.fixture
def expected():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    labels = [expected for expected in [1]]
    assert resp.status_code == expected
"""

NEW_COMPREHENSION_SHADOW = f"""import pytest


@pytest.fixture
def expected():
    return 200


def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    labels = [expected for expected in [1]]
    assert resp.status_code == expected
"""

OLD_COMPREHENSION_SHADOW_METHOD = f"""import pytest


@pytest.fixture
def expected():
    return 409


class TestMatching:
    @pytest.mark.xfail(strict=True, {_PENDING_666})
    def {TARGET}(self, expected):
        resp = match_expense(expense_id=1, txn_id=2)
        labels = [expected for expected in [1]]
        assert resp.status_code == expected
"""

NEW_COMPREHENSION_SHADOW_METHOD = f"""import pytest


@pytest.fixture
def expected():
    return 200


class TestMatching:
    def {TARGET}(self, expected):
        resp = match_expense(expense_id=1, txn_id=2)
        labels = [expected for expected in [1]]
        assert resp.status_code == expected
"""

OLD_HELPER_LOCAL_UNRELATED = f"""import pytest

expected = 409
EXPECTED = 409


def _expected_status():
    expected = 409
    return expected


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    _expected_status()
    assert resp.status_code == EXPECTED
"""

NEW_HELPER_LOCAL_UNRELATED = f"""import pytest

expected = 200
EXPECTED = 409


def _expected_status():
    expected = 409
    return expected


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    _expected_status()
    assert resp.status_code == EXPECTED
"""

OLD_HELPER_LOCAL_UNRELATED_METHOD = f"""import pytest

expected = 409
EXPECTED = 409


def _expected_status():
    expected = 409
    return expected


class TestMatching:
    @pytest.mark.xfail(strict=True, {_PENDING_666})
    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        _expected_status()
        assert resp.status_code == EXPECTED
"""

NEW_HELPER_LOCAL_UNRELATED_METHOD = f"""import pytest

expected = 200
EXPECTED = 409


def _expected_status():
    expected = 409
    return expected


class TestMatching:
    def {TARGET}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        _expected_status()
        assert resp.status_code == EXPECTED
"""

OLD_PLAIN_FIXTURE = f"""import pytest


@pytest.fixture
def expected():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

NEW_PLAIN_FIXTURE = f"""import pytest


@pytest.fixture
def expected():
    return 200


def {TARGET}(expected):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == expected
"""

OLD_HELPER_GENUINE_REF = f"""import pytest

THRESHOLD = 409


def _expected_status():
    return THRESHOLD


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

NEW_HELPER_GENUINE_REF = f"""import pytest

THRESHOLD = 200


def _expected_status():
    return THRESHOLD


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

OLD_COMPREHENSION_OVERREACH = f"""import pytest

EXPECTED = 409


@pytest.fixture
def other():
    return 409


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    labels = [other for other in [1]]
    assert resp.status_code == EXPECTED
"""

NEW_COMPREHENSION_OVERREACH = f"""import pytest

EXPECTED = 409


@pytest.fixture
def other():
    return 200


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    labels = [other for other in [1]]
    assert resp.status_code == EXPECTED
"""

# --- R9: MARVIN #666 cross-review regressions (deeper-dataflow scope fail-opens) --
# Ported verbatim from marvin/tests/test_check_acceptance_integrity_unit.py:548-630
# (Finding 1 / Finding 2). Both are fail-opens the first #666 scope fix
# introduced: a genuine module-value change flows into the target's assertion
# but the resolver dropped the name from the dep roots, so a 409 -> 200
# weakening slipped through as "allowed". A resolver with either known
# fail-open passes all the OTHER dataflow tests above; these two close that
# gap. Each must be CAUGHT (classify "weakened").

# Finding 1: a helper that declares `global THRESHOLD` and assigns to it is NOT
# rebinding a helper-local — `THRESHOLD` names the MODULE binding, so it must
# stay a followed ref. The helper body is byte-identical in OLD and NEW; only
# the module-level THRESHOLD value flips 409 -> 200, and the target calls the
# helper, so the change is a deeper-dataflow weakening.
OLD_GLOBAL_HELPER = f"""import pytest

THRESHOLD = 409


def _expected_status():
    global THRESHOLD
    THRESHOLD += 0
    return THRESHOLD


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

NEW_GLOBAL_HELPER = f"""import pytest

THRESHOLD = 200


def _expected_status():
    global THRESHOLD
    THRESHOLD += 0
    return THRESHOLD


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == _expected_status()
"""

# Finding 2: comprehension scope is SEQUENTIAL. In
# `[x for x in [1] if y for y in [2]]` the `if y` clause runs BEFORE the later
# `for y` target binds, so its `y` refers to the ENCLOSING/module `y`, not to
# the comprehension target. `y` appears ONLY inside the comprehension's
# if-clause (never in the assertion directly), so the ONLY path from the test
# to module `y` is through that if-clause reference. A module-level `y` flip
# 409 -> 200 must be CAUGHT. (A resolver that collects ALL generator targets
# before walking any `if` wrongly treats this `y` as bound and lets the change
# slip through — a fail-open.)
OLD_FORWARD_REF_IF = f"""import pytest

y = 409


@pytest.mark.xfail(strict=True, {_PENDING_666})
def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    pairs = [x for x in [1] if y for y in [2]]
    assert resp.status_code == pairs
"""

NEW_FORWARD_REF_IF = f"""import pytest

y = 200


def {TARGET}():
    resp = match_expense(expense_id=1, txn_id=2)
    pairs = [x for x in [1] if y for y in [2]]
    assert resp.status_code == pairs
"""

# --- Real live-tree parametrized acceptance file (repo-local substitute) ---
# MARVIN's #588/#666 live-clean controls read tests/test_check_references_v2.py
# (a MARVIN-only file). IssueForge has no such file; the closest real,
# parametrized acceptance test in THIS repo is
# tests/test_github.py::test_every_op_target_must_be_repo_qualified.
_LIVE_PARAMETRIZED_FILE = Path(__file__).resolve().parent / "test_github.py"
_LIVE_BODY_ANCHOR = "    gw = _SpyGateway()"


def _live_clean_pair() -> tuple[str, str]:
    old_src = _LIVE_PARAMETRIZED_FILE.read_text()
    assert "@pytest.mark.parametrize" in old_src, "live file lost its parametrize shape"
    assert _LIVE_BODY_ANCHOR in old_src, "live file shape changed; refresh the body anchor"
    new_src = old_src.replace(
        _LIVE_BODY_ANCHOR,
        "    # comment-only edit for the #19 live-clean contract\n" + _LIVE_BODY_ANCHOR,
        1,
    )
    assert new_src != old_src, "comment insertion produced no diff"
    return old_src, new_src


# ============================================================================
# #588 tests — WEAKEN: deeper data-flow caught
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_fixture_return_weakening_caught():
    """A @pytest.fixture returning the expected value has its return changed
    409 -> 200; the assertion is byte-identical. Must classify "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_FIXTURE_RETURN, NEW_FIXTURE_RETURN) == "weakened"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_param_default_weakening_caught():
    """The expected value enters through a function parameter DEFAULT whose
    expression is byte-identical but whose helper's return flips 409 -> 200.
    Must classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_PARAM_DEFAULT, NEW_PARAM_DEFAULT) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_computed_expression_weakening_caught():
    """EXPECTED = base + delta is byte-identical, but delta changes 9 -> 0, so
    EXPECTED's value silently drops. Must classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_COMPUTED_EXPR, NEW_COMPUTED_EXPR) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_helper_return_weakening_caught():
    """The assertion calls a helper for its expected value; the call is
    byte-identical but the helper now returns 200 instead of 409. Must
    classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_HELPER_RETURN, NEW_HELPER_RETURN) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_class_method_deeper_dataflow_weakening_caught():
    """CLASS-METHOD coverage: the deeper-dataflow resolver must apply to
    test_* methods of a top-level class, not only module-level functions. The
    method's helper call is byte-identical; the helper's return flips
    409 -> 200. Must classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_CLASS_HELPER, NEW_CLASS_HELPER) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_class_method_fixture_return_weakening_caught():
    """CLASS-METHOD non-helper coverage: the resolver must run the SAME
    shared per-test path for class methods across ALL vectors, not only
    direct helper calls. The expected() fixture flips 409 -> 200. Must
    classify "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_CLASS_FIXTURE_RETURN, NEW_CLASS_FIXTURE_RETURN)
        == "weakened"
    )


# ============================================================================
# #588 tests — ALLOW: resolver-not-fail-closed / parametrize KNOWN-SAFE
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_unrelated_fixture_change_allowed():
    """The target depends on fixture `expected` (kept 409); a DIFFERENT,
    unused fixture `other` flips 409 -> 200 while the target's only change is
    pending-marker removal. Must classify "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_UNRELATED_FIXTURE, NEW_UNRELATED_FIXTURE)
        == "allowed"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_unrelated_helper_change_allowed():
    """The target asserts on `_expected_status()` (kept 409); a DIFFERENT,
    uncalled helper `_unused_status` flips 409 -> 200. Must classify
    "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_UNRELATED_HELPER, NEW_UNRELATED_HELPER)
        == "allowed"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_unrelated_computed_change_allowed():
    """The target reads EXPECTED (kept); an unreferenced OTHER computed value
    flips via other_delta 9 -> 0. Must classify "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_UNRELATED_COMPUTED, NEW_UNRELATED_COMPUTED)
        == "allowed"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_parametrize_marker_only_removal_allowed():
    """A legitimate marker-only activation on a @pytest.mark.parametrize-
    driven test must NOT be treated as unresolvable-dataflow weakening. Must
    classify "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_PARAMETRIZE_MARKER, NEW_PARAMETRIZE_MARKER)
        == "allowed"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_real_parametrized_acceptance_file_stays_allowed_on_benign_change():
    """A comment-only diff on a REAL parametrized acceptance file from the
    live tree (tests/test_github.py) must classify "allowed" — the resolver
    must not red main on incidental edits to real parametrized suites."""
    from issueforge import integrity

    old_src, new_src = _live_clean_pair()
    assert integrity.classify_acceptance_change(old_src, new_src) == "allowed"


# ============================================================================
# #627 tests — WEAKEN: alias-indirection downgrades must be BLOCKED
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_alias_chain_root_redefined_to_skip_blocked():
    """Alias-of-alias evasion: P1 = xfail(strict=True); P2 = P1; @P2, with P1
    redefined to a permanent skip while P2 = P1 and @P2 stay byte-identical.
    Single-level resolution is the pinned contract, so the chain must fail
    CLOSED rather than resolve. Must classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_CHAIN_ALIAS, NEW_CHAIN_ROOT_SKIP) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_imported_alias_import_swap_blocked():
    """`from markers import PENDING_217` becomes
    `from markers import SKIP as PENDING_217` while `@PENDING_217` stays
    textually identical. A change to the import line feeding a kept decorator
    is a decorator change. Must classify "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_IMPORTED_ALIAS, NEW_IMPORT_SWAPPED) == "weakened"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_imported_alias_replaced_by_local_skip_blocked():
    """The import line is replaced by a local
    `PENDING_217 = pytest.mark.skip(...)` binding while `@PENDING_217` stays
    identical. Must classify "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_IMPORTED_ALIAS, NEW_IMPORT_TO_LOCAL_SKIP)
        == "weakened"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_imported_alias_import_deleted_blocked():
    """The `from markers import PENDING_217` line is DELETED outright while
    `@PENDING_217` stays textually identical. A diff-only-ImportFrom-
    replacements implementation would miss a pure removal. Must classify
    "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_IMPORTED_ALIAS, NEW_IMPORT_DELETED) == "weakened"
    )


# ============================================================================
# #627 tests — ALLOW: legitimate alias handling stays permitted
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_single_level_alias_flip_still_allowed():
    """The sanctioned Step-4 flip of a single-level in-file alias (live
    shape: `pending = pytest.mark.xfail(strict=True, reason=PENDING)` then
    `@pending`) — decorator, alias binding, and reason constant all removed,
    body byte-identical. Must classify "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_SINGLE_ALIAS, NEW_SINGLE_ALIAS_FLIPPED)
        == "allowed"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_unchanged_import_with_sibling_activation_still_allowed():
    """An imported-alias-marked test whose decorator AND import line are
    untouched, alongside a sibling test's sanctioned literal-marker
    activation. "Imported aliases are opaque" must not mean any file
    importing a marker always fails. Must classify "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_IMPORTED_WITH_SIBLING, NEW_SIBLING_ACTIVATED)
        == "allowed"
    )


# ============================================================================
# #627 tests — ENTRY (is_pending): alias resolution stays opaque
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    ("src", "test_name"),
    [(OLD_CHAIN_ALIAS, TARGET), (OLD_IMPORTED_ALIAS, TARGET)],
    ids=["alias-chain", "imported-alias"],
)
def test_entry_alias_indirection_not_pending_control(src, test_name):
    """Entry mode must treat BOTH an alias-of-alias-marked test (`@P2` where
    P2 = P1) AND an imported-alias-marked test (`from markers import
    PENDING_217`) as NOT pending. Single-level resolution is the pinned
    contract; imported aliases never count as literal pending markers. Must
    return False for both."""
    from issueforge import integrity

    assert integrity.is_pending(src, test_name) is False


# ============================================================================
# #627 tests — ENTRY (is_pending): class-level marker inheritance
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    "name",
    [f"TestMatching::{TARGET}", f"TestMatching.{TARGET}", TARGET],
    ids=["qualified", "dotted", "bare"],
)
def test_entry_class_level_pending_inherited_all_name_forms(name):
    """A method whose CLASS carries the literal xfail(strict=True) pending
    marker is pending even though the method itself is unmarked — for the
    qualified Class::method, dotted Class.method, and bare-name forms alike.
    Must return True for all three."""
    from issueforge import integrity

    assert integrity.is_pending(OLD_CLASS_LEVEL_PENDING, name) is True


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_entry_class_marker_among_env_decorators_passes():
    """Real-world shape (DandD trigger file): the class-level pending marker
    sits below @mock_aws and @patch in the class decorator list. Entry mode
    must find it there and report the unmarked method pending. Must return
    True."""
    from issueforge import integrity

    assert (
        integrity.is_pending(OLD_CLASS_ENV_PLUS_PENDING, f"TestGameIdempotency::{TARGET}") is True
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_entry_class_marker_via_alias_passes():
    """Class-level pending applied via a single-level in-file alias
    (`pending = pytest.mark.xfail(strict=True, reason=PENDING)` then
    `@pending` on the class). Entry-mode inheritance must mirror weaken-mode
    alias resolution. Must return True."""
    from issueforge import integrity

    assert integrity.is_pending(OLD_CLASS_ALIAS_PENDING, TARGET) is True


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_entry_bare_name_fail_closed_names_only_unmarked_class():
    """Fail-closed discriminator: TestAlpha's copy of the method is pending
    ONLY via its class-level marker; TestBeta's same-named copy is active
    (unmarked, no class marker). A bare-name entry check must fail closed —
    pending only if EVERY match is pending — so the mixed result must return
    False."""
    from issueforge import integrity

    assert integrity.is_pending(OLD_TWO_CLASSES_ALPHA_PENDING, TARGET) is False


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    "base_src",
    [CLASS_SKIP_MARKED, CLASS_XFAIL_NONSTRICT, CLASS_IMPORTED_ALIAS_MARKED],
    ids=["class-skip", "class-xfail-nonstrict", "class-imported-alias"],
)
def test_entry_non_pending_class_marker_not_inherited_control(base_src):
    """Class-marker inheritance applies ONLY to the strict pending
    convention. A class marked with a permanent skip, a non-strict xfail, or
    an opaque IMPORTED alias confers nothing: the unmarked method stays not
    pending. Must return False for all three."""
    from issueforge import integrity

    assert integrity.is_pending(base_src, TARGET) is False


# ============================================================================
# #666 tests — AST-scope name-shadowing edges
# ============================================================================


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    ("old_src", "new_src"),
    [
        (OLD_COMPREHENSION_SHADOW, NEW_COMPREHENSION_SHADOW),
        (OLD_COMPREHENSION_SHADOW_METHOD, NEW_COMPREHENSION_SHADOW_METHOD),
    ],
    ids=["module", "class-method"],
)
def test_comprehension_target_does_not_shadow_fixture(old_src, new_src):
    """A test reads a fixture-injected `expected` (409) in its assertion AND
    contains `[expected for expected in [1]]`. In Py3 the comprehension
    target does not leak into the enclosing scope, so the assertion's
    `expected` is still the fixture; when the fixture flips 409 -> 200 the
    change must be caught for both the module-level function and the
    class-method variant. Must classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
@pytest.mark.parametrize(
    ("old_src", "new_src"),
    [
        (OLD_HELPER_LOCAL_UNRELATED, NEW_HELPER_LOCAL_UNRELATED),
        (OLD_HELPER_LOCAL_UNRELATED_METHOD, NEW_HELPER_LOCAL_UNRELATED_METHOD),
    ],
    ids=["module", "class-method"],
)
def test_helper_local_name_not_followed_to_module_def(old_src, new_src):
    """A helper `_expected_status()` the target calls has a LOCAL `expected =
    409` in its body that collides with an UNRELATED module-level `expected`.
    A legitimate change to that unrelated module `expected` (409 -> 200)
    during a marker-only activation must be ALLOWED for both the
    module-level function and the class-method variant. Must classify
    "allowed"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(old_src, new_src) == "allowed"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_non_shadowed_fixture_change_still_caught():
    """SCOPE-only guarantee (fail-open side): a genuinely-injected fixture
    whose return flips 409 -> 200 (no comprehension shadowing it) is still a
    weakening — the comprehension fix must not stop resolving real fixtures.
    Must classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_PLAIN_FIXTURE, NEW_PLAIN_FIXTURE) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_helper_genuine_module_ref_still_followed():
    """SCOPE-only guarantee (fail-closed side): a helper that GENUINELY reads
    a module value (`return THRESHOLD`, no local rebinding) must still have
    that value followed — THRESHOLD flipping 409 -> 200 stays a weakening.
    Must classify "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_HELPER_GENUINE_REF, NEW_HELPER_GENUINE_REF)
        == "weakened"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_comprehension_internal_names_not_dragged_into_roots():
    """SCOPE-only guarantee (fail-open overreach): the assertion depends only
    on the unchanged module constant EXPECTED; an UNRELATED module-level
    fixture `other`, shadowed only by a comprehension target
    (`[other for other in [1]]`), flips its return 409 -> 200. Neither the
    comprehension's target nor its internal names belong to the test's dep
    roots, so the unrelated change must be ALLOWED. Must classify
    "allowed"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(
            OLD_COMPREHENSION_OVERREACH, NEW_COMPREHENSION_OVERREACH
        )
        == "allowed"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_global_declared_helper_ref_still_followed():
    """R9 (MARVIN #666 cross-review Finding 1): a helper declares
    `global THRESHOLD` and reassigns it (`THRESHOLD += 0`) before returning it.
    That `global` declaration names the MODULE binding, not a helper-local, so
    a resolver must still follow it. The helper body is byte-identical; only
    THRESHOLD's module-level value flips 409 -> 200. A resolver that treats
    any name assigned inside a helper as helper-local (ignoring `global`)
    fails open here even though it passes the other dataflow tests. Must
    classify "weakened"."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(OLD_GLOBAL_HELPER, NEW_GLOBAL_HELPER) == "weakened"


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_forward_ref_comprehension_if_module_name_followed():
    """R9 (MARVIN #666 cross-review Finding 2): comprehension scope is
    SEQUENTIAL. In `[x for x in [1] if y for y in [2]]` the `if y` clause runs
    BEFORE the later `for y` target binds, so that `y` refers to the
    ENCLOSING/module `y`, not the comprehension's own target. `y` appears only
    inside the if-clause (never directly in the assertion), so the only path
    from the test to module `y` is through that reference. A resolver that
    collects ALL generator targets before walking any `if` clause wrongly
    treats this `y` as already bound and fails open even though it passes the
    other comprehension-scoping tests above. Must classify "weakened"."""
    from issueforge import integrity

    assert (
        integrity.classify_acceptance_change(OLD_FORWARD_REF_IF, NEW_FORWARD_REF_IF) == "weakened"
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#19)")
def test_real_parametrized_acceptance_file_stays_clean_666():
    """Live-clean boundary (real tree, #666 scope fix): a comment-only diff
    on the same real parametrized acceptance file
    (tests/test_github.py::test_every_op_target_must_be_repo_qualified) must
    classify "allowed" — the scope fix must not red main."""
    from issueforge import integrity

    old_src, new_src = _live_clean_pair()
    assert integrity.classify_acceptance_change(old_src, new_src) == "allowed"
