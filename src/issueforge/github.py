"""The GitHub read side: is an issue open? (issue #8, S4).

``issue_is_open`` shells out to ``gh`` as an argv array (never ``shell=True``); the ``run`` seam is
injectable so the check runs offline in tests. Open -> True, closed -> False; a lookup failure or an
unparseable/unknown state RAISES (never a silent True/False) so a run never proceeds on a bad read.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


def issue_is_open(slug: str, number: int, *, run: Any = subprocess.run) -> bool:
    """Return whether issue ``number`` of ``owner/repo`` ``slug`` is OPEN, via ``gh issue view``."""
    result = run(
        ["gh", "issue", "view", str(number), "--repo", slug, "--json", "state"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh issue view failed for {slug}#{number} (exit {result.returncode}): {result.stderr}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"unparseable gh output for {slug}#{number}: {result.stdout!r}") from error
    state = data.get("state") if isinstance(data, dict) else None
    if state == "OPEN":
        return True
    if state == "CLOSED":
        return False
    raise ValueError(f"unrecognized issue state {state!r} for {slug}#{number}")


def pr_facts(run_id: str) -> dict:
    """The GitHub-authoritative PR/branch/merge facts for a run (default: none, offline).

    This is the injectable reconcile seam ``engine.continue_run`` reads before resuming; tests pass
    their own. Offline it reports no facts, so reconciliation finds nothing to diverge on.
    """
    return {}
