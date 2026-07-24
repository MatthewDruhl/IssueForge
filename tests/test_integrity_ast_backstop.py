"""Committed PENDING acceptance suite for #19 (S13) — AST-backstop acceptance-integrity
guard, ported from MARVIN's ``scripts/check_acceptance_integrity.py`` (DEMOTED here to
defense-in-depth behind S13's other integrity checks).

``issueforge.integrity`` does not exist yet, so every test imports it INSIDE its body and is
``@pytest.mark.xfail(strict=True, reason="PENDING (#19)")`` (an ImportError/ModuleNotFoundError
while pending). This suite pins the SAME public API MARVIN exposes:

    from issueforge import integrity
    integrity.classify_acceptance_change(old_src: str, new_src: str) -> str   # "allowed" | "weakened"
    integrity.is_pending(src: str, test_name: str) -> bool

MARVIN's tests pair every vector with both a pure-core call and a CLI invocation (the CLI
does not exist in IssueForge — S13 exposes the pure functions directly), so each distinct
vector below collapses to ONE ``classify_acceptance_change``/``is_pending`` assertion.

Vectors ported from:
  - marvin/tests/test_acceptance_integrity.py (core weaken + entry mode, issue #491)
  - marvin/tests/test_acceptance_integrity_classes.py (class-based tests, issue #610)
  - marvin/tests/test_check_acceptance_integrity_unit.py (false-allow holes, incl. the 4
    NAMED false-allows: aliased-import skip downgrade, empty-parametrize, decorator
    reorder, and re-marking an active test as pending)

Anti-false-green: the module does not exist yet, so every test is genuinely red today (import
fails before any assertion runs). No lazily-satisfiable asserts: every test pins a concrete
"allowed"/"weakened"/True/False expected value that a wrong-but-plausible implementation
(e.g. a constant-return stub) would fail.

Pending convention: literal per-test ``xfail(strict=True)`` (repo standard).
"""

from __future__ import annotations

import pytest

_PENDING = 'reason="pending: #217 durable fix not built"'
TARGET = "test_second_match_returns_409"

# A committed, pending acceptance test — the baseline most fixtures derive from.
OLD_PENDING = f"""import pytest


@pytest.mark.xfail(strict=True, {_PENDING})
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""

# --- ALLOWED -----------------------------------------------------------------


def test_marker_only_removal_allowed():
    """Removing only the pending xfail marker (assertions byte-identical) is the
    sanctioned Step-4 activation edit -> allowed."""
    from issueforge import integrity

    new_src = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "allowed"


def test_comment_or_whitespace_change_allowed():
    """A comment-only change with assertions identical -> allowed (AST-aware, not textual)."""
    from issueforge import integrity

    new_src = f"""import pytest


@pytest.mark.xfail(strict=True, {_PENDING})
def test_second_match_returns_409():
    # a second concurrent match must be rejected, not double-booked
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "allowed"


def test_xfail_reason_only_edit_allowed():
    """Editing only the xfail reason text (assertions/markers otherwise identical) -> allowed."""
    from issueforge import integrity

    new_src = """import pytest


@pytest.mark.xfail(strict=True, reason="pending: rewritten reason text")
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "allowed"


# --- WEAKEN: assertion tampering ----------------------------------------------


def test_value_change_blocked():
    """Expected value changed (409 -> 200) -> weakened."""
    from issueforge import integrity

    new_src = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 200
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_operator_loosened_blocked():
    """Comparison operator loosened (== 409 -> >= 400) -> weakened."""
    from issueforge import integrity

    new_src = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code >= 400
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_truthy_shortcircuit_blocked():
    """Assertion short-circuited to always-true (`or True`) -> weakened."""
    from issueforge import integrity

    new_src = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409 or True
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_assert_noop_blocked():
    """Real check dropped for a no-op (`assert True`) -> weakened."""
    from issueforge import integrity

    new_src = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert True
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


# --- WEAKEN: deletion / rename -------------------------------------------------


def test_whole_test_deletion_blocked():
    """Deleting the committed acceptance test outright -> weakened."""
    from issueforge import integrity

    new_src = """import pytest


def test_unrelated_helper():
    assert 1 + 1 == 2
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_rename_with_same_body_blocked():
    """Renaming the committed test (same body) so the named test vanishes -> weakened."""
    from issueforge import integrity

    new_src = """import pytest


def test_second_match_renamed():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


# --- WEAKEN: marker downgrade --------------------------------------------------


def test_marker_downgrade_xfail_to_skip_blocked():
    """FALSE-ALLOW #1: xfail(strict=True) downgraded to a permanent skip (not
    removed) -> weakened. This is the exact attack the guard exists to catch."""
    from issueforge import integrity

    new_src = """import pytest


@pytest.mark.skip(reason="flaky, disable for now")
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_marker_strict_true_to_strict_false_blocked():
    """xfail strict=True weakened to strict=False (an XPASS no longer fails) -> weakened."""
    from issueforge import integrity

    new_src = f"""import pytest


@pytest.mark.xfail(strict=False, {_PENDING})
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


# --- WEAKEN: directly-referenced module constant -------------------------------


def test_referenced_module_constant_change_blocked():
    """A module-level constant the assertion directly references changed
    (EXPECTED_STATUS 409 -> 200) while the assert line is textually identical -> weakened."""
    from issueforge import integrity

    old_src = f"""import pytest

EXPECTED_STATUS = 409


@pytest.mark.xfail(strict=True, {_PENDING})
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == EXPECTED_STATUS
"""
    new_src = """import pytest

EXPECTED_STATUS = 200


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == EXPECTED_STATUS
"""
    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"


# --- WEAKEN: the 4 NAMED false-allows (Codex-found holes) ---------------------


def test_false_allow_1_aliased_import_skip_downgrade_blocked():
    """NAMED FALSE-ALLOW #1: pending xfail downgraded to skip via an aliased
    import (`from pytest import mark` + `@mark.skip`) so a literal-chain-only
    detector misses it -> must still be weakened."""
    from issueforge import integrity

    new_src = """from pytest import mark


@mark.skip(reason="disable")
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_false_allow_2_empty_parametrize_blocked():
    """NAMED FALSE-ALLOW #2: pending marker swapped for
    `@pytest.mark.parametrize("case", [])` (empty params collects to nothing);
    body is byte-identical -> must still be weakened."""
    from issueforge import integrity

    new_src = """import pytest


@pytest.mark.parametrize("case", [])
def test_second_match_returns_409(case):
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(OLD_PENDING, new_src) == "weakened"


def test_false_allow_3_decorator_reorder_blocked():
    """NAMED FALSE-ALLOW #3: two non-pending decorators kept but REORDERED while
    the pending xfail is removed -> weakened (decorator application order is
    semantic)."""
    from issueforge import integrity

    old_src = """import pytest


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("case", [1, 2])
@pytest.mark.xfail(strict=True, reason="pending: #217")
def test_second_match_returns_409(case):
    resp = match_expense(expense_id=1, txn_id=case)
    assert resp.status_code == 409
"""
    new_src = """import pytest


@pytest.mark.parametrize("case", [1, 2])
@pytest.mark.usefixtures("db")
def test_second_match_returns_409(case):
    resp = match_expense(expense_id=1, txn_id=case)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"


def test_false_allow_3_decorator_reorder_same_order_marker_removal_allowed():
    """Activation control pairing with the reorder-blocked test above: SAME two
    decorators, SAME order, ONLY the pending xfail removed -> allowed."""
    from issueforge import integrity

    old_src = """import pytest


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("case", [1, 2])
@pytest.mark.xfail(strict=True, reason="pending: #217")
def test_second_match_returns_409(case):
    resp = match_expense(expense_id=1, txn_id=case)
    assert resp.status_code == 409
"""
    new_src = """import pytest


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("case", [1, 2])
def test_second_match_returns_409(case):
    resp = match_expense(expense_id=1, txn_id=case)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(old_src, new_src) == "allowed"


def test_direct_alias_redefinition_blocked():
    """A pending marker bound to a module alias (`P1 = pytest.mark.xfail(...)`)
    kept as `@P1` on the test, but the alias itself REDEFINED to a permanent
    skip -> weakened (redefining the binding a kept decorator relies on cannot
    relax a committed test)."""
    from issueforge import integrity

    old_src = """import pytest

P1 = pytest.mark.xfail(strict=True, reason="pending: #610")


@P1
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    new_src = """import pytest

P1 = pytest.mark.skip(reason="disable")


@P1
def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"


def test_malformed_input_raises_syntax_error():
    """Unparseable source: mirrors MARVIN's pure core EXACTLY — the pure
    ``classify_acceptance_change``/``is_pending`` functions call ``ast.parse``
    with no try/except of their own (only MARVIN's CLI wrapper catches
    ``SyntaxError`` and prints a clean message); the port exposes only the pure
    functions, so a malformed ``new_src`` propagates ``SyntaxError`` unmodified,
    not a swallowed/safe verdict."""
    from issueforge import integrity

    with pytest.raises(SyntaxError):
        integrity.classify_acceptance_change(OLD_PENDING, "def broken(:\n")
    with pytest.raises(SyntaxError):
        integrity.is_pending("def broken(:\n", TARGET)


def test_false_allow_4_remarking_active_test_as_pending_blocked():
    """NAMED FALSE-ALLOW #4: an ACTIVE (unmarked) committed test re-marked
    pending by ADDING xfail(strict=True) — the inverse of the sanctioned
    activation -> weakened."""
    from issueforge import integrity

    old_active = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    new_remarked_pending = OLD_PENDING
    assert integrity.classify_acceptance_change(old_active, new_remarked_pending) == "weakened"


# --- CLASS-BASED (#610) --------------------------------------------------------

_CLASS_PENDING = f"""import json

import pytest
from moto import mock_aws
from unittest.mock import patch

from src.handlers import game


@mock_aws
@patch("src.auth._get_stored_passphrase", return_value="test-passphrase")
class TestGameIdempotency:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_idempotent_replay_keyed_by_turn_id(self, mock_ssm):
        status1, body1 = _parse_response(
            game.handler(_action_event("c1", "open the door", "t-abc"), None)
        )
        assert status1 == 200
        assert body1["narrative"] == "You push the door open."

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_missing_or_empty_turn_id_rejected(self, mock_ssm):
        status, body = _parse_response(
            game.handler(_action_event("c1", "look around"), None)
        )
        assert status == 400
        assert "turn_id" in json.dumps(body)
"""

_CLASS_MARKER_FLIPPED = f"""import json

import pytest
from moto import mock_aws
from unittest.mock import patch

from src.handlers import game


@mock_aws
@patch("src.auth._get_stored_passphrase", return_value="test-passphrase")
class TestGameIdempotency:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_idempotent_replay_keyed_by_turn_id(self, mock_ssm):
        status1, body1 = _parse_response(
            game.handler(_action_event("c1", "open the door", "t-abc"), None)
        )
        assert status1 == 200
        assert body1["narrative"] == "You push the door open."

    def test_missing_or_empty_turn_id_rejected(self, mock_ssm):
        status, body = _parse_response(
            game.handler(_action_event("c1", "look around"), None)
        )
        assert status == 400
        assert "turn_id" in json.dumps(body)
"""


def test_class_method_assertion_weakened_blocked():
    """An expected value changed inside a class method (400 -> 200), disguised
    as a normal activation diff -> weakened."""
    from issueforge import integrity

    new_src = _CLASS_MARKER_FLIPPED.replace("assert status == 400", "assert status == 200")
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


def test_class_method_deleted_blocked():
    """The committed class method deleted outright (sibling method survives so
    the class still parses) -> weakened."""
    from issueforge import integrity

    new_src = f"""import json

import pytest
from moto import mock_aws
from unittest.mock import patch

from src.handlers import game


@mock_aws
@patch("src.auth._get_stored_passphrase", return_value="test-passphrase")
class TestGameIdempotency:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_idempotent_replay_keyed_by_turn_id(self, mock_ssm):
        status1, body1 = _parse_response(
            game.handler(_action_event("c1", "open the door", "t-abc"), None)
        )
        assert status1 == 200
        assert body1["narrative"] == "You push the door open."
"""
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


def test_class_method_marker_downgrade_blocked():
    """The method's pending xfail(strict=True) downgraded to a permanent skip
    (not removed) -> weakened."""
    from issueforge import integrity

    target_marker_and_def = (
        f"@pytest.mark.xfail(strict=True, {_PENDING})\n"
        "    def test_missing_or_empty_turn_id_rejected"
    )
    new_src = _CLASS_PENDING.replace(
        target_marker_and_def,
        '@pytest.mark.skip(reason="flaky, disable for now")\n'
        "    def test_missing_or_empty_turn_id_rejected",
    )
    assert "pytest.mark.skip" in new_src  # fixture self-check
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


def test_class_decorator_removed_blocked():
    """A CLASS-LEVEL decorator (@mock_aws) stripped while every method body and
    every method decorator stays byte-identical -> weakened. The class
    decorator list shapes the environment the assertions run in."""
    from issueforge import integrity

    new_src = _CLASS_PENDING.replace("@mock_aws\n@patch(", "@patch(")
    assert "@mock_aws" not in new_src  # fixture self-check
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


def test_duplicate_method_name_across_classes_not_shadowed_blocked():
    """TestAPI and TestCLI both define the same method name; only TestAPI's copy
    is weakened while TestCLI's stays intact. Methods are keyed per class
    (ClassName::method), so the intact copy must NOT shadow the weakened one -> weakened."""
    from issueforge import integrity

    old_src = f"""import pytest


class TestAPI:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_missing_or_empty_turn_id_rejected(self):
        status, body = _parse_response(
            game.handler(_action_event("c1", "look around"), None)
        )
        assert status == 400


class TestCLI:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_missing_or_empty_turn_id_rejected(self):
        rc, out = _run_cli("act", "look around")
        assert rc == 2
"""
    new_src = old_src.replace("assert status == 400", "assert status == 200")
    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"


def test_class_marker_only_flip_allowed():
    """Removing ONLY the method's pending marker, body byte-identical, sibling
    method untouched -> allowed (the sanctioned class-method activation)."""
    from issueforge import integrity

    assert integrity.classify_acceptance_change(_CLASS_PENDING, _CLASS_MARKER_FLIPPED) == "allowed"


def test_module_level_weaken_not_masked_by_sibling_class_test_blocked():
    """A file with BOTH a module-level test and a class: the module-level test
    is untouched (so a module-only scan would see nothing to flag) while a
    class method's assertion is weakened -> must still be weakened."""
    from issueforge import integrity

    old_src = f"""import pytest


def test_module_level_smoke():
    assert 1 + 1 == 2


class TestGameIdempotency:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def test_missing_or_empty_turn_id_rejected(self, mock_ssm):
        status, body = _parse_response(
            game.handler(_action_event("c1", "look around"), None)
        )
        assert status == 400
"""
    new_src = old_src.replace("assert status == 400", "assert status == 200")
    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"


def test_class_method_assert_noop_blocked():
    """The empirical #610 attack: a class method's real checks neutered to a
    no-op (`assert True`), marker also removed so the file looks like a
    normal activation diff -> weakened."""
    from issueforge import integrity

    new_src = _CLASS_MARKER_FLIPPED.replace(
        """    def test_missing_or_empty_turn_id_rejected(self, mock_ssm):
        status, body = _parse_response(
            game.handler(_action_event("c1", "look around"), None)
        )
        assert status == 400
        assert "turn_id" in json.dumps(body)
""",
        """    def test_missing_or_empty_turn_id_rejected(self, mock_ssm):
        assert True
""",
    )
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


def test_class_method_strict_true_to_strict_false_blocked():
    """A class method's xfail strict=True weakened to strict=False (an XPASS
    no longer fails) -> weakened. Same downgrade vector the module-level
    suite pins, ported into the class-based resolution path."""
    from issueforge import integrity

    target_marker_and_def = (
        f"@pytest.mark.xfail(strict=True, {_PENDING})\n"
        "    def test_missing_or_empty_turn_id_rejected"
    )
    new_src = _CLASS_PENDING.replace(
        target_marker_and_def,
        f"@pytest.mark.xfail(strict=False, {_PENDING})\n"
        "    def test_missing_or_empty_turn_id_rejected",
    )
    assert "strict=False" in new_src  # fixture self-check
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


def test_class_decorator_addition_blocked():
    """A decorator ADDED to the class (methods untouched) — e.g. a smuggled
    class-level skip — is a weakening; the class decorator list shapes the
    environment every method's assertions run in, so an ADDITION is as
    dangerous as a removal."""
    from issueforge import integrity

    new_src = _CLASS_PENDING.replace(
        '@mock_aws\n@patch("src.auth._get_stored_passphrase", return_value="test-passphrase")\nclass TestGameIdempotency:',
        '@mock_aws\n@patch("src.auth._get_stored_passphrase", return_value="test-passphrase")\n@pytest.mark.skip(reason="later")\nclass TestGameIdempotency:',
    )
    assert 'pytest.mark.skip(reason="later")' in new_src  # fixture self-check
    assert integrity.classify_acceptance_change(_CLASS_PENDING, new_src) == "weakened"


_CLASS_LEVEL_PENDING = f"""import pytest


@pytest.mark.xfail(strict=True, {_PENDING})
class TestMatching:

    def test_second_match_returns_409(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""

_CLASS_LEVEL_MARKER_REMOVED = _CLASS_LEVEL_PENDING.replace(
    f"@pytest.mark.xfail(strict=True, {_PENDING})\n", ""
)


def test_class_level_pending_marker_removal_allowed():
    """The pending convention applied on the CLASS (xfail(strict=True) on the
    class marks every method pending): removing ONLY the class-level marker,
    methods byte-identical, is the sanctioned activation -> allowed."""
    from issueforge import integrity

    assert "pytest.mark.xfail" not in _CLASS_LEVEL_MARKER_REMOVED  # fixture self-check
    assert (
        integrity.classify_acceptance_change(_CLASS_LEVEL_PENDING, _CLASS_LEVEL_MARKER_REMOVED)
        == "allowed"
    )


def test_is_pending_qualified_wrong_class_lookup_not_found():
    """A qualified `Class::method` lookup naming a class the method does NOT
    belong to -> False (not found), even though the method exists under its
    real class and IS pending there."""
    from issueforge import integrity

    assert (
        integrity.is_pending(_CLASS_PENDING, "OtherClass::test_missing_or_empty_turn_id_rejected")
        is False
    )


# --- ENTRY MODE (is_pending) ---------------------------------------------------


def test_is_pending_present_and_pending_true():
    """A named test present in src and carrying xfail(strict=True) -> True."""
    from issueforge import integrity

    assert integrity.is_pending(OLD_PENDING, TARGET) is True


def test_is_pending_absent_false():
    """A named test not present in src -> False (not raised)."""
    from issueforge import integrity

    assert integrity.is_pending(OLD_PENDING, "test_does_not_exist") is False


def test_is_pending_present_but_not_pending_false():
    """A named test present but unmarked (no xfail) -> False."""
    from issueforge import integrity

    src = """import pytest


def test_second_match_returns_409():
    resp = match_expense(expense_id=1, txn_id=2)
    assert resp.status_code == 409
"""
    assert integrity.is_pending(src, TARGET) is False


def test_is_pending_class_level_marker_inherited_across_name_forms():
    """A method whose CLASS carries xfail(strict=True) is pending even though
    the method itself is unmarked -- for the bare name, `Class::method`, and
    `Class.method` forms alike (#627)."""
    from issueforge import integrity

    method = "test_second_match_returns_409"
    src = f"""import pytest


@pytest.mark.xfail(strict=True, reason="pending: #217")
class TestMatching:

    def {method}(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409
"""
    assert integrity.is_pending(src, method) is True
    assert integrity.is_pending(src, f"TestMatching::{method}") is True
    assert integrity.is_pending(src, f"TestMatching.{method}") is True


def test_is_pending_ambiguous_bare_name_fails_closed():
    """A bare method name matching methods in several classes is pending only
    if EVERY match is pending (fail-closed on ambiguity); here TestAlpha's copy
    is pending and TestBeta's is active, so the bare-name lookup is False."""
    from issueforge import integrity

    method = "test_missing_or_empty_turn_id_rejected"
    src = f"""import pytest


class TestAlpha:

    @pytest.mark.xfail(strict=True, {_PENDING})
    def {method}(self):
        assert alpha() == 409


class TestBeta:

    def {method}(self):
        assert beta() == 409
"""
    assert integrity.is_pending(src, method) is False


# --- AST-BACKSTOP OFFENDER DETAIL (#13) ----------------------------------------


def test_ast_backstop_class_decorator_weakening_names_qualified_methods():
    """A class-level guard decorator (@mock_aws) stripped from a class holding
    two methods (test_a, test_b) is a weakening (the class decorator list shapes
    the environment every method's assertions run in). The canonical R1 detail
    for an ast_backstop offender is the QUALIFIED ``Class::method`` per affected
    method — NOT a bare ``ClassName``. Today ``integrity._analyze`` returns the
    bare class name for a class-decorator weakening, so the surfaced offender
    detail is ``TestMatching`` instead of ``TestMatching::test_a`` /
    ``TestMatching::test_b``; this LOCKs one offender per affected method,
    each qualified."""
    from issueforge import contract, integrity

    old_src = """import pytest
from moto import mock_aws


@mock_aws
class TestMatching:

    def test_a(self):
        resp = match_expense(expense_id=1, txn_id=2)
        assert resp.status_code == 409

    def test_b(self):
        resp = match_expense(expense_id=3, txn_id=4)
        assert resp.status_code == 409
"""
    # Weaken: strip the class-level guard decorator; every method body and the
    # method decorator list stay byte-identical.
    new_src = old_src.replace("@mock_aws\n", "")
    assert "@mock_aws" not in new_src  # fixture self-check

    assert integrity.classify_acceptance_change(old_src, new_src) == "weakened"

    offenders = contract._ast_backstop_offenders(old_src.encode("utf-8"), new_src.encode("utf-8"))
    # One QUALIFIED offender per affected method — never the bare class name.
    assert set(offenders) == {"TestMatching::test_a", "TestMatching::test_b"}
