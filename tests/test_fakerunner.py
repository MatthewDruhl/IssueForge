"""Committed PENDING acceptance suite for #6 (S1) — the test FakeRunner allowlist.

FakeRunner is imported inside each test body from ``issueforge.testing`` (its planned home,
not yet built); a module-top import would ERROR at collection instead of xfailing.
"""

import pytest


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_fakerunner_blocks_unforeseen_commands_by_allowlist():
    """The FakeRunner blocks anything not on the read-only allowlist.

    Contract: FakeRunner().run(["totally-made-up-binary", "x"]) -> AssertionError;
    FakeRunner().run(["git", "push", "--force"]) (destructive, not a read-only prefix) ->
    AssertionError; FakeRunner().run(["git", "status"]) (read-only prefix) -> allowed.
    """
    from issueforge.testing import FakeRunner

    with pytest.raises(AssertionError):
        FakeRunner().run(["totally-made-up-binary", "x"])

    with pytest.raises(AssertionError):
        FakeRunner().run(["git", "push", "--force"])

    FakeRunner().run(["git", "status"])


@pytest.mark.xfail(strict=True, reason="PENDING (#6)")
def test_fakerunner_allows_registered_exact_argv_but_not_near_match():
    """A registered exact command is allowed, but a near-match is still rejected.

    Contract: register exact argv ["gh", "pr", "view", "6"] ->
    FakeRunner(registered=[["gh", "pr", "view", "6"]]).run(["gh", "pr", "view", "6"]) is
    allowed; .run(["gh", "pr", "view", "7"]) (near-match, unregistered) -> AssertionError.
    """
    from issueforge.testing import FakeRunner

    FakeRunner(registered=[["gh", "pr", "view", "6"]]).run(["gh", "pr", "view", "6"])

    with pytest.raises(AssertionError):
        FakeRunner(registered=[["gh", "pr", "view", "6"]]).run(["gh", "pr", "view", "7"])
