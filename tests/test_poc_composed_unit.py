"""Focused UNIT tests for the composed PoC-D default stage (``engine._poc_composed_stage``, #115).

These sit alongside the committed acceptance contract (``tests/test_poc_integration.py``) and REUSE
its real-seam harness (``_seed_dandd`` / ``_install_seams``) without modifying it, to assert internal
composition guarantees the outcome-level acceptance suite does not pin.
"""

from __future__ import annotations

from test_poc_integration import (
    CONTRACT_PATH,
    EXPECTED_SLUG,
    ISSUE_NUMBER,
    SPEC,
    WRITE_SCOPE_PATH,
    _install_seams,
    _seed_dandd,
)


def test_delivered_record_preserves_the_exact_issue_body(tmp_path, monkeypatch):
    """The delivered ``waiting-for-merge`` record carries the EXACT issue body under ``issue_body``.

    Regression guard for Codex #5 (data loss): the delivery-shape apply overwrites ``record["issue"]``
    with the ``(owner, repo, number)`` tuple ``deliver_pr`` needs, which used to DROP the exact issue
    body seeded for ``run_candidate``. The composed stage must persist the body under a distinct key so
    the issue #115 criterion — "the exact issue body ... are persisted" — holds on the final record.

    technical (contract): with a sentinel body returned by ``read_issue_body``, after
    ``engine.run("DandD#111")`` the delivered record has ``status == "waiting-for-merge"``,
    ``record["issue_body"]`` EQUALS the sentinel body verbatim, and ``record["issue"]`` is the
    ``(owner, repo, number)`` tuple (so the body is preserved SEPARATELY from the delivery tuple).
    """
    _seed_dandd(tmp_path)
    _install_seams(monkeypatch)

    from issueforge import engine, github, store

    # A sentinel body distinct from the harness default, so the assertion proves an exact round-trip
    # rather than coincidental equality. ``files`` / ``contract_paths`` stay the harness paths so the
    # real candidate + readiness path still reaches ready.
    sentinel_body = (
        f"{EXPECTED_SLUG}#{ISSUE_NUMBER} GREET\n\n"
        "exact multi-line body that must survive delivery verbatim <hi sam>"
    )
    monkeypatch.setattr(
        github,
        "read_issue_body",
        lambda slug, number, **_kw: {
            "body": sentinel_body,
            "files": [WRITE_SCOPE_PATH],
            "contract_paths": [CONTRACT_PATH],
        },
        raising=False,
    )

    result = engine.run(SPEC)
    record = store.RunStore().read(result["run_id"])

    assert record["status"] == "waiting-for-merge"
    assert record["issue_body"] == sentinel_body
    owner, repo = EXPECTED_SLUG.split("/")
    assert tuple(record["issue"]) == (owner, repo, ISSUE_NUMBER)
