"""Committed PENDING acceptance suite for #8 (S4) — the github read side: is the issue open?

``issueforge.github`` does not exist yet, so each test is ``xfail(strict=True)`` (ImportError while
pending). Dual-layer docstrings.

The read-side interface this suite authors (ATDD):
- ``github.issue_is_open(slug, number, *, run=subprocess.run) -> bool`` reports whether issue
  ``number`` of ``owner/repo`` ``slug`` is OPEN. It invokes ``gh`` as an argv array (no shell)
  carrying the slug and number; the ``run`` seam is injectable so the check is offline in tests.
  Open -> True; closed -> False; a lookup failure OR an unparseable/unknown state RAISES (never a
  silent True or False).
"""

from __future__ import annotations

import json
import subprocess

import pytest


def _capturing_run(stdout="", returncode=0):
    calls = []

    def run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return run, calls


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_issue_is_open_invokes_gh_as_argv_with_slug_and_number_no_shell():
    """The open check shells out to gh as an argv array (no shell) carrying the exact repo slug and issue number.

    technical (contract): github.issue_is_open("Owner/Repo", 148, run=<spy returning
    {"state": "OPEN"}>) is True; the spy's argv is a list starting with "gh", contains "148" and
    "Owner/Repo", and the call did not pass shell=True.
    """
    from issueforge import github

    run, calls = _capturing_run(stdout=json.dumps({"state": "OPEN"}))
    assert github.issue_is_open("Owner/Repo", 148, run=run) is True

    (cmd, kwargs) = calls[0]
    assert isinstance(cmd, list) and cmd[0] == "gh"
    assert "148" in cmd and "Owner/Repo" in cmd
    assert kwargs.get("shell") is not True


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_issue_is_open_false_for_a_closed_issue():
    """A closed issue reads as not open.

    technical (contract): github.issue_is_open("Owner/Repo", 148, run=<gh returns
    {"state": "CLOSED"}>) is False.
    """
    from issueforge import github

    run, _ = _capturing_run(stdout=json.dumps({"state": "CLOSED"}))
    assert github.issue_is_open("Owner/Repo", 148, run=run) is False


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_issue_lookup_failure_raises_never_silently_true():
    """A failed gh lookup raises rather than defaulting to open.

    technical (contract): github.issue_is_open("Owner/Repo", 999, run=<gh exits non-zero>)
    raises.
    """
    from issueforge import github

    run, _ = _capturing_run(stdout="", returncode=1)
    with pytest.raises(Exception):
        github.issue_is_open("Owner/Repo", 999, run=run)


@pytest.mark.xfail(strict=True, reason="PENDING (#8)")
def test_unparseable_or_unknown_state_raises_not_a_silent_false():
    """Malformed JSON or an unrecognized state raises, so a run never proceeds on a bad read.

    technical (contract): github.issue_is_open("Owner/Repo", 1, run=<gh returns "not json">)
    raises; github.issue_is_open("Owner/Repo", 1, run=<gh returns {"state": "WEIRD"}>) raises.
    """
    from issueforge import github

    bad_json, _ = _capturing_run(stdout="not json at all")
    with pytest.raises(Exception):
        github.issue_is_open("Owner/Repo", 1, run=bad_json)

    weird, _ = _capturing_run(stdout=json.dumps({"state": "WEIRD"}))
    with pytest.raises(Exception):
        github.issue_is_open("Owner/Repo", 1, run=weird)
