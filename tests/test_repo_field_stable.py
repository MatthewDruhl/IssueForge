"""Committed PENDING acceptance suite for #207 — the manifest ``repo`` field keeps ONE stable type
(the registry alias string) for a run's whole lifetime.

Live evidence 2026-07-29, run-ba96e3f0cdc6 (the M1 delivery run): before delivery the manifest
carried ``repo: "DandD"`` (the registry alias, as every parked run shows); after the deliver stage the
same field read ``repo: ["MatthewDruhl", "DandD"]``. Cause: the readiness/delivery step overwrites the
alias with ``issue_ref[:2]`` (an ``(owner, name)`` tuple → JSON list) at ``engine.py:1404``, purely so
``github.deliver_pr`` can pass ``record["repo"]`` to the gh gateway (``default_branch``/``open_pr`` call
``_slug(repo)`` = ``f"{repo[0]}/{repo[1]}"``, which needs an owner/name pair). ``slug``/``issue_ref``
already carry owner/name, so the overwrite is redundant AND a type break.

Two tests, reusing the #129/#140 real-seams harness (a full ``engine.run("DandD#111")`` to
``waiting-for-merge`` with a recording gateway):

- ``test_full_run_delivers_and_targets_correct_repo`` — UNMARKED live green guard. Passes TODAY and must
  keep passing after the fix. It proves the harness runs end-to-end (so the ``xfail`` below cannot mask a
  setup crash) and pins delivery correctness: exactly one PR, opened against ``MatthewDruhl/DandD``. It
  also catches the NAIVE fix — dropping the ``repo`` overwrite without fixing ``deliver_pr`` would feed
  the alias ``"DandD"`` to ``_slug`` → ``"D/a"`` and red this guard.
- ``test_repo_field_stays_alias_string`` — the invariant, committed PENDING via the literal
  ``@pytest.mark.xfail(strict=True, reason="PENDING (#207)")`` marker. Red TODAY at ``record["repo"] ==
  "DandD"`` (it is ``["MatthewDruhl", "DandD"]``); flips green when the fix leaves ``repo`` as the alias
  and ``deliver_pr`` derives owner/name from ``record["issue"]``.

Deliberately no DB-constraint / concurrency test: #207 is a within-run type-stability invariant with one
manifest writer and no cross-request dimension, so the Step 3 invariant lens does not apply.
"""

from __future__ import annotations

import pytest

from test_poc_real_seams import (
    EXPECTED_SLUG,
    SPEC,
    _install_seams,
    _open_pr_count,
    _seed_dandd,
)

PENDING = "PENDING (#207)"


def _open_pr_repo_slug(gateways: list) -> str:
    """The owner/name slug the single recorded ``open_pr`` call targeted, via the gateway's own
    ``_slug`` rule (``f"{repo[0]}/{repo[1]}"``). For the buggy/correct owner-name pair this is
    ``"MatthewDruhl/DandD"``; for a bare alias string ``"DandD"`` it degrades to ``"D/a"``."""
    calls = [kw for gw in gateways for name, kw in gw.calls if name == "open_pr"]
    assert len(calls) == 1, f"expected exactly one open_pr call, got {len(calls)}"
    repo = calls[0]["repo"]
    return f"{repo[0]}/{repo[1]}"


@pytest.mark.slow
def test_full_run_delivers_and_targets_correct_repo(tmp_path, monkeypatch):
    """A full ``issueforge run DandD#111`` reaches ``waiting-for-merge``, opens exactly one PR, and
    opens it against ``MatthewDruhl/DandD``.

    technical (contract): after ``engine.run("DandD#111")`` the record status is ``waiting-for-merge``,
    ``_open_pr_count == 1``, and the recorded ``open_pr`` repo slugs to ``EXPECTED_SLUG``. This is the
    live harness proof (so the ``xfail`` invariant test below cannot pass by masking a setup crash) and
    the discriminating control against a naive fix that drops the ``repo`` overwrite but still hands
    ``deliver_pr`` the alias string.
    """
    _seed_dandd(tmp_path)
    handles = _install_seams(monkeypatch, scope_return=["src/dandd/greet.py"])

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert _open_pr_count(handles.gateways) == 1
    assert _open_pr_repo_slug(handles.gateways) == EXPECTED_SLUG


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="PENDING (#207)")
def test_repo_field_stays_alias_string(tmp_path, monkeypatch):
    """After delivery the persisted ``repo`` is STILL the registry alias string, never converted to an
    ``[owner, name]`` list; owner/name is preserved in ``issue``.

    technical (contract): after ``engine.run("DandD#111")`` reaches ``waiting-for-merge``,
    ``record["repo"] == "DandD"`` and is a ``str``; ``record["issue"][:2] == ["MatthewDruhl", "DandD"]``.
    Red today: the deliver path overwrites ``repo`` with ``issue_ref[:2]`` so it reads
    ``["MatthewDruhl", "DandD"]``.
    """
    _seed_dandd(tmp_path)
    _install_seams(monkeypatch, scope_return=["src/dandd/greet.py"])

    from issueforge import engine, store

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert isinstance(record["repo"], str)
    assert record["repo"] == "DandD"
    assert list(record["issue"][:2]) == ["MatthewDruhl", "DandD"]
