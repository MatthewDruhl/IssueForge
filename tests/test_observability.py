"""Committed PENDING acceptance suite for #10 (S8) — the observability policy.

The S8 surfaces this suite authors do not exist yet (``issueforge.observability`` is a new module),
so each test FAILS while pending — an ``ImportError`` for the missing module or an ``AttributeError``
for a missing attribute — and is therefore ``@pytest.mark.xfail(strict=True, reason="PENDING (#10)")``.
Every import of a pending surface lives INSIDE its test so a module-top import cannot turn the whole
file into a collection ERROR. Dual-layer docstrings: plain behavior first, then the
``technical (contract):`` golden values.

Design decisions resolved with the human before authoring (2026-07-20):
- **The boundary marker set is a FIXED, curated, module-level tuple** (``BOUNDARY_MARKERS``), a
  documented v1 ceiling — not a rule engine, not user-configurable. ``GOLDEN_MARKERS`` below IS the
  contract; the module must equal it exactly. A marker not in the tuple is simply not detected.
- **The "third-party service" boundary** (the one category with no clean stdlib marker) is a curated
  SDK tuple (boto3/botocore/stripe/twilio/sendgrid/googleapiclient).
- **The AI boundary is the S7 provider seam** ``issueforge.providers.invoke`` — NEVER a vendor product
  name (S7's no-vendor-name test bans those tokens in engine source). A raw vendor call (e.g.
  ``openai.chat.completions.create()``) is therefore NOT classified as AI in v1 (it is a different
  violation, forbidden by S7); this is an honest, tested consequence of the marker ceiling.

Scope of S8 (this slice OWNS the DETERMINISTIC evidence APIs S9 consumes and S15 enforces; it exercises
no AI judgment): the two boundary classifiers + reconcile + the marker tuple, the G3 no-global-logging
static check, the target logger-convention detector, and the runtime log-capture evidence collector +
sensitive-field exclusion predicate. OUT of scope: the observability VERDICT itself (US-6.7 — S9's),
redaction of IssueForge's own artifacts (S4), and the enforcement call site (S15). The general "is this
diagnosable elsewhere" judgment stays the AI reviewer's and is NOT in this deterministic module.

The S8 interface this suite authors (ATDD), all in ``issueforge.observability``:

- ``BoundaryCategory`` — an enum whose members are EXACTLY HTTP, DATABASE, SUBPROCESS, FILESYSTEM,
  QUEUE, THIRD_PARTY, AI.
- ``BOUNDARY_MARKERS: dict[BoundaryCategory, tuple[str, ...]]`` — the fixed, curated marker tuple
  backing BOTH classifiers; equal to ``GOLDEN_MARKERS``. No public setter/register/configure; not read
  from the environment.
- ``BoundaryEvidence(category, location, marker)`` and ``BoundaryVerdict(categories: frozenset,
  evidence: tuple)`` — the structured result both classifiers return.
- ``classify_prospective(issue_text, footprint_paths, repo_root) -> BoundaryVerdict`` — PRE-AUTHORING:
  deterministic AST match of the boundary-crossing calls ALREADY PRESENT in the footprint files against
  ``BOUNDARY_MARKERS``, resolving import aliases to canonical names; the issue PROSE never adds a
  category, and marker text in a comment or string literal never matches.
- ``classify_diff(diff_text) -> BoundaryVerdict`` — over a unified diff; only ADDED (``+``) code lines
  count (a removed boundary is not a crossing; an added comment/string is not a crossing).
- ``reconcile(approved, actual) -> None`` — returns None when ``actual.categories`` ⊆
  ``approved.categories``; else raises ``UnanticipatedBoundary`` NAMING every newly-crossed category. Its
  signature is exactly ``(approved, actual)`` — there is no override parameter (the halt is
  deterministic and not waivable; S15 owns enforcement).
- ``UnanticipatedBoundary`` — the deterministic-failure exception (its own class, not ValueError).
- ``GlobalLoggingViolation(location, message)`` and ``check_global_logging(source_paths)`` — G3: flags
  library code that installs global logging configuration (``basicConfig``, a no-arg root
  ``getLogger()``, root-handler mutation) resolving import aliases; a named ``getLogger(__name__)`` and a
  named logger's own ``addHandler`` are clean. Each violation's ``location`` names the file and line.
- ``LoggerConvention(factory, levels: frozenset, format, correlation_key)`` and
  ``detect_logger_convention(repo_root) -> LoggerConvention`` — reads the TARGET's logging conventions
  from its source (US-6.9: detected, never imposed). A target with no logging yields factory=None.
- ``capture_logs(fn, *args, **kwargs) -> tuple[result, list[logging.LogRecord]]`` — forward args to
  ``fn``, run it exactly once, return its result and every record it emitted, and remove any handler it
  installed (no persistent handler/level change; repeated captures do not duplicate).
- ``sensitive_values_present(records, sensitive: set[str]) -> set[str]`` — the exclusion predicate over
  each record's FULLY-FORMATTED message (``record.getMessage()``, so ``%s`` args are resolved).
- ``LogEvidence(events_emitted: frozenset, leaked: frozenset)`` and
  ``collect_log_evidence(success_path, failure_path, *, required_events, sensitive_fields, canaries)`` —
  the authoritative evidence: run BOTH callables capturing logs (the failure_path MAY raise; its
  pre-exception records still count and the exception does not propagate), report which required events
  emitted across both paths and which sensitive/canary values leaked (leaked MUST be empty to pass).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# The fixed v1 marker contract. The module's BOUNDARY_MARKERS must equal this exactly.
GOLDEN_MARKERS = {
    "SUBPROCESS": (
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "pty.spawn",
    ),
    "FILESYSTEM": (
        "open",
        "io.open",
        "os.open",
        "os.remove",
        "os.unlink",
        "os.mkdir",
        "os.makedirs",
        "pathlib.Path.open",
        "pathlib.Path.read_text",
        "pathlib.Path.read_bytes",
        "pathlib.Path.write_text",
        "pathlib.Path.write_bytes",
        "pathlib.Path.unlink",
        "pathlib.Path.mkdir",
        "shutil.copy",
        "shutil.move",
        "shutil.rmtree",
        "tempfile.NamedTemporaryFile",
        "tempfile.mkstemp",
    ),
    "HTTP": (
        "socket.socket",
        "socket.create_connection",
        "http.client.HTTPConnection",
        "http.client.HTTPSConnection",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "requests.request",
        "requests.Session",
        "httpx.get",
        "httpx.post",
        "httpx.request",
        "httpx.Client",
        "aiohttp.ClientSession",
    ),
    "DATABASE": (
        "sqlite3.connect",
        "psycopg.connect",
        "psycopg2.connect",
        "pymysql.connect",
        "asyncpg.connect",
        "sqlalchemy.create_engine",
    ),
    "QUEUE": (
        "queue.Queue",
        "queue.SimpleQueue",
        "multiprocessing.Queue",
        "pika.BlockingConnection",
        "kombu.Connection",
        "celery.Celery",
    ),
    "THIRD_PARTY": (
        "boto3.client",
        "boto3.resource",
        "botocore.session.Session",
        "stripe.Charge",
        "stripe.PaymentIntent",
        "twilio.rest.Client",
        "sendgrid.SendGridAPIClient",
        "googleapiclient.discovery.build",
    ),
    "AI": ("issueforge.providers.invoke",),
}

# One representative crossing per category: (source, the exact golden marker it must match).
CATEGORY_SNIPPETS = {
    "SUBPROCESS": ("import subprocess\ndef f():\n    subprocess.run(['ls'])\n", "subprocess.run"),
    "DATABASE": ("import sqlite3\ndef f():\n    sqlite3.connect('x.db')\n", "sqlite3.connect"),
    "HTTP": ("import httpx\ndef f():\n    httpx.get('http://x')\n", "httpx.get"),
    "FILESYSTEM": ("def f():\n    open('data.txt', 'w')\n", "open"),
    "QUEUE": ("import queue\ndef f():\n    queue.Queue()\n", "queue.Queue"),
    "THIRD_PARTY": ("import boto3\ndef f():\n    boto3.client('s3')\n", "boto3.client"),
    "AI": (
        "from issueforge.providers import invoke\ndef f():\n    invoke(p, 'hi', cwd='.', timeout=1, run_id='r')\n",
        "issueforge.providers.invoke",
    ),
}


def _write(dir_path: Path, name: str, source: str) -> Path:
    path = dir_path / name
    path.write_text(textwrap.dedent(source))
    return path


def _added_diff(added_lines: list[str]) -> str:
    body = "".join("+" + line + "\n" for line in added_lines)
    return f"--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,{1 + len(added_lines)} @@\n def keep():\n{body}"


# ---------------------------------------------------------------------------
# Boundary categories and the fixed, curated marker tuple
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_boundary_categories_are_exactly_the_seven_declared(isolated_state_home):
    """The boundary categories are a closed set of exactly seven: HTTP, database, subprocess, filesystem, queue, third-party service, and AI.

    technical (contract): set(observability.BoundaryCategory.__members__) == {"HTTP","DATABASE",
    "SUBPROCESS","FILESYSTEM","QUEUE","THIRD_PARTY","AI"}.
    """
    from issueforge import observability

    assert set(observability.BoundaryCategory.__members__) == set(GOLDEN_MARKERS)


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_boundary_markers_equal_the_curated_v1_contract_and_the_ai_marker_is_the_seam(
    isolated_state_home,
):
    """The marker set is exactly the fixed, curated v1 contract; the AI marker is the provider seam alone, with no vendor product name.

    technical (contract): {c.name: markers for c, markers in observability.BOUNDARY_MARKERS.items()} ==
    GOLDEN_MARKERS (exact); observability.BOUNDARY_MARKERS[AI] == ("issueforge.providers.invoke",); no
    marker anywhere contains a banned vendor token {codex,claude,anthropic,openai,chatgpt,gemini,copilot,
    ollama}.
    """
    from issueforge import observability

    by_name = {c.name: markers for c, markers in observability.BOUNDARY_MARKERS.items()}
    assert by_name == GOLDEN_MARKERS
    assert observability.BOUNDARY_MARKERS[observability.BoundaryCategory.AI] == (
        "issueforge.providers.invoke",
    )

    banned = {"codex", "claude", "anthropic", "openai", "chatgpt", "gemini", "copilot", "ollama"}
    all_markers = " ".join(
        m for markers in observability.BOUNDARY_MARKERS.values() for m in markers
    ).lower()
    assert not any(token in all_markers for token in banned)


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_both_classifiers_read_the_shared_marker_tuple_and_it_has_no_public_setter(
    tmp_path, isolated_state_home, monkeypatch
):
    """Both classifiers consult the shared marker tuple rather than hardcoding detections, and the tuple exposes no user-facing way to reconfigure it.

    technical (contract): with BOUNDARY_MARKERS monkeypatched to a custom mapping whose SUBPROCESS
    markers are ("zzz.novel_call",), classify_prospective over a file calling zzz.novel_call(...) and
    classify_diff over a diff adding zzz.novel_call(...) BOTH flag SUBPROCESS (they read the attr); and
    the module exposes no set_markers/register_marker/configure/add_marker function.
    """
    from issueforge import observability

    cat = observability.BoundaryCategory
    custom = {c: (() if c is not cat.SUBPROCESS else ("zzz.novel_call",)) for c in cat}
    monkeypatch.setattr(observability, "BOUNDARY_MARKERS", custom)

    footprint = _write(tmp_path, "novel.py", "import zzz\ndef f():\n    zzz.novel_call()\n")
    assert (
        cat.SUBPROCESS in observability.classify_prospective("", [footprint], tmp_path).categories
    )
    assert (
        cat.SUBPROCESS
        in observability.classify_diff(_added_diff(["    zzz.novel_call()"])).categories
    )

    public = {name for name in dir(observability) if not name.startswith("_")}
    assert not (public & {"set_markers", "register_marker", "configure", "add_marker"})


# ---------------------------------------------------------------------------
# classify_prospective: exact per-category detection, aliases, negatives
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
@pytest.mark.parametrize("category", list(CATEGORY_SNIPPETS))
def test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence(
    category, tmp_path, isolated_state_home
):
    """Each of the seven boundaries is flagged in ISOLATION — a footprint that crosses exactly one boundary yields exactly that one category, with evidence naming the file and the concrete matched marker (so a classifier that just returns all seven fails).

    technical (contract): a footprint containing only the representative call for <category> yields
    verdict.categories == frozenset({<category>}); exactly one BoundaryEvidence, whose category is
    <category>, whose location names the file, and whose marker equals the category's golden marker
    (a member of BOUNDARY_MARKERS[<category>]).
    """
    from issueforge import observability

    source, marker = CATEGORY_SNIPPETS[category]
    footprint = _write(tmp_path, "mod.py", source)
    verdict = observability.classify_prospective("some issue text", [footprint], tmp_path)

    expected = observability.BoundaryCategory[category]
    assert verdict.categories == frozenset({expected})
    matching = [e for e in verdict.evidence if e.category is expected]
    assert len(matching) == 1
    assert "mod.py" in matching[0].location
    assert matching[0].marker == marker
    assert marker in observability.BOUNDARY_MARKERS[expected]


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_classify_prospective_resolves_import_aliases_to_canonical_markers(
    tmp_path, isolated_state_home
):
    """Detection follows canonical names through import aliases, not raw text — an aliased subprocess, an aliased database import, and an aliased provider import are all still detected.

    technical (contract): a footprint with `import subprocess as sp; sp.run(...)`,
    `from sqlite3 import connect as db; db(...)`, and `from issueforge import providers as p;
    p.invoke(...)` yields categories == {SUBPROCESS, DATABASE, AI}.
    """
    from issueforge import observability

    source = """
        import subprocess as sp
        from sqlite3 import connect as db
        from issueforge import providers as p

        def f():
            sp.run(['ls'])
            db('x.db')
            p.invoke(prof, 'hi', cwd='.', timeout=1, run_id='r')
    """
    footprint = _write(tmp_path, "aliased.py", source)
    verdict = observability.classify_prospective("", [footprint], tmp_path)

    cat = observability.BoundaryCategory
    assert verdict.categories == frozenset({cat.SUBPROCESS, cat.DATABASE, cat.AI})


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_classify_prospective_ignores_prose_comments_and_string_literals(
    tmp_path, isolated_state_home
):
    """The trigger is executable code only: marker text in a comment or a string literal, and boundary words in the issue prose, never add a category.

    technical (contract): classify_prospective("This feature calls an HTTP API and a database", [a
    footprint whose only marker text is `# subprocess.run(...)` in a comment and "httpx.get" inside a
    string literal, with real code that is pure arithmetic]) yields categories == empty set.
    """
    from issueforge import observability

    source = """
        # subprocess.run(['danger']) is discussed here but not called
        NOTE = "we might use httpx.get or sqlite3.connect someday"

        def add(a, b):
            return a + b
    """
    footprint = _write(tmp_path, "pure.py", source)
    verdict = observability.classify_prospective(
        "This feature calls an HTTP API and a database", [footprint], tmp_path
    )
    assert verdict.categories == frozenset()


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_a_raw_vendor_ai_call_is_not_classified_as_ai_only_the_provider_seam_is(
    tmp_path, isolated_state_home
):
    """The AI boundary is the provider seam, not a vendor SDK: a raw vendor AI call is not flagged as AI (in v1 it is a different, S7-forbidden violation), while the provider seam is the sole AI trigger.

    technical (contract): a footprint calling `openai.chat.completions.create(...)` yields categories
    that does NOT contain AI (and, since no vendor marker exists, is the empty set); a footprint calling
    issueforge.providers.invoke(...) yields categories == {AI}.
    """
    from issueforge import observability

    cat = observability.BoundaryCategory

    vendor = _write(
        tmp_path,
        "vendor.py",
        "import openai\ndef f():\n    openai.chat.completions.create(model='x')\n",
    )
    assert cat.AI not in observability.classify_prospective("", [vendor], tmp_path).categories
    assert observability.classify_prospective("", [vendor], tmp_path).categories == frozenset()

    seam = _write(tmp_path, "seam.py", CATEGORY_SNIPPETS["AI"][0])
    assert observability.classify_prospective("", [seam], tmp_path).categories == frozenset(
        {cat.AI}
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_classification_is_deterministic_and_performs_no_ai_process_or_network_call(
    tmp_path, isolated_state_home, monkeypatch
):
    """Classification is a pure, repeatable, I/O-free function — it makes no AI call, launches no subprocess, and opens no socket — so it can be regression-tested.

    technical (contract): classify_prospective on the same footprint twice returns an EQUAL
    BoundaryVerdict (categories AND evidence); with observability's own provider seam, issueforge.process.run,
    subprocess.run, and socket.socket all monkeypatched to raise, classify_prospective and classify_diff
    still produce verdicts; and the AST of classify_prospective/classify_diff contains no call to
    invoke/process.run/subprocess.*/socket.*.
    """
    import ast

    from issueforge import observability, process, providers

    footprint = _write(tmp_path, "mod.py", CATEGORY_SNIPPETS["SUBPROCESS"][0])
    first = observability.classify_prospective("", [footprint], tmp_path)
    second = observability.classify_prospective("", [footprint], tmp_path)
    assert first.categories == second.categories
    assert first.evidence == second.evidence

    def _boom(*a, **k):
        raise AssertionError("classification must not perform I/O or call the AI")

    monkeypatch.setattr(providers, "invoke", _boom)
    monkeypatch.setattr(process, "run", _boom)
    import socket
    import subprocess

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    assert observability.classify_prospective("", [footprint], tmp_path).categories
    assert isinstance(
        observability.classify_diff(_added_diff(["    import os"])), observability.BoundaryVerdict
    )

    tree = ast.parse(Path(observability.__file__).read_text(encoding="utf-8"))
    targets = {"classify_prospective", "classify_diff"}
    forbidden = ("invoke", "process.run", "subprocess.run", "subprocess.Popen", "socket.socket")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                name = ast.unparse(call.func)
                assert not any(name.endswith(f) or name == f for f in forbidden), (
                    f"{node.name} calls {name}"
                )


# ---------------------------------------------------------------------------
# classify_diff: added lines only, all seven categories, negatives
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
@pytest.mark.parametrize("category", list(CATEGORY_SNIPPETS))
def test_classify_diff_flags_each_category_from_added_lines(category, isolated_state_home):
    """Each boundary is detected from a diff's ADDED lines alone, for every category, with the concrete matched marker.

    technical (contract): a unified diff whose added lines are the representative snippet for <category>
    yields categories == frozenset({<category>}) with a BoundaryEvidence whose marker is the category's
    golden marker.
    """
    from issueforge import observability

    source, marker = CATEGORY_SNIPPETS[category]
    added = [line for line in textwrap.dedent(source).splitlines() if line.strip()]
    verdict = observability.classify_diff(_added_diff(["    " + line for line in added]))

    expected = observability.BoundaryCategory[category]
    assert verdict.categories == frozenset({expected})
    assert any(e.category is expected and e.marker == marker for e in verdict.evidence)


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_classify_diff_ignores_removed_lines_and_added_comments(isolated_state_home):
    """A removed boundary is not a crossing, and marker text added only in a comment is not a crossing.

    technical (contract): classify_diff over a diff that DELETES a `subprocess.run(...)` line and ADDS
    only a comment line `# subprocess.run stays discussed` returns categories == empty set.
    """
    from issueforge import observability

    diff = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,3 +1,3 @@\n"
        " def f():\n"
        "-    subprocess.run(['ls'])\n"
        "+    # subprocess.run stays discussed\n"
        "     return 1\n"
    )
    assert observability.classify_diff(diff).categories == frozenset()


# ---------------------------------------------------------------------------
# reconcile: unanticipated boundary = deterministic, fully-named, non-overridable
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_reconcile_passes_when_the_diff_crosses_only_approved_boundaries(isolated_state_home):
    """Reconciliation passes when the actual diff crosses only boundaries the approved verdict already anticipated.

    technical (contract): approved.categories = {SUBPROCESS, FILESYSTEM}, actual.categories =
    {SUBPROCESS} -> reconcile(approved, actual) returns None.
    """
    from issueforge import observability

    cat = observability.BoundaryCategory
    approved = observability.BoundaryVerdict(frozenset({cat.SUBPROCESS, cat.FILESYSTEM}), ())
    actual = observability.BoundaryVerdict(frozenset({cat.SUBPROCESS}), ())
    assert observability.reconcile(approved, actual) is None


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_unanticipated_boundaries_are_a_deterministic_failure_naming_every_new_category(
    isolated_state_home,
):
    """Any boundary the diff crosses that the approved verdict never anticipated halts the run as a deterministic failure that names EVERY newly-crossed category, is its own exception type, and offers no override route.

    technical (contract): approved.categories = {SUBPROCESS}, actual.categories = {SUBPROCESS, HTTP,
    DATABASE} -> reconcile raises observability.UnanticipatedBoundary whose message names both "http" and
    "database" but NOT "subprocess"; UnanticipatedBoundary is not a ValueError/TypeError; and
    inspect.signature(reconcile).parameters keys == ["approved", "actual"] (no override parameter, so
    reconcile(approved, actual, override=True) raises TypeError).
    """
    import inspect

    from issueforge import observability

    cat = observability.BoundaryCategory
    approved = observability.BoundaryVerdict(frozenset({cat.SUBPROCESS}), ())
    actual = observability.BoundaryVerdict(frozenset({cat.SUBPROCESS, cat.HTTP, cat.DATABASE}), ())

    with pytest.raises(observability.UnanticipatedBoundary) as excinfo:
        observability.reconcile(approved, actual)
    message = str(excinfo.value).lower()
    assert "http" in message and "database" in message
    assert "subprocess" not in message
    assert not isinstance(excinfo.value, (ValueError, TypeError))

    assert list(inspect.signature(observability.reconcile).parameters) == ["approved", "actual"]
    with pytest.raises(TypeError):
        observability.reconcile(approved, actual, override=True)


# ---------------------------------------------------------------------------
# G3: libraries never install global logging configuration
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
@pytest.mark.parametrize(
    "source, flagged",
    [
        ("import logging\nlogging.basicConfig(level=logging.INFO)\n", True),
        ("import logging as lg\nlg.basicConfig()\n", True),
        ("import logging\nlog = logging.getLogger()\n", True),
        (
            "import logging\nroot = logging.getLogger()\nroot.addHandler(logging.StreamHandler())\n",
            True,
        ),
        ("import logging\nlogging.root.handlers.append(logging.StreamHandler())\n", True),
        ("import logging\nlog = logging.getLogger(__name__)\n", False),
        (
            "import logging\nlog = logging.getLogger(__name__)\nlog.addHandler(logging.NullHandler())\n",
            False,
        ),
    ],
    ids=[
        "basicConfig",
        "aliased-basicConfig",
        "root-getLogger",
        "bound-root-addHandler",
        "root-handlers-append",
        "named-getLogger-ok",
        "named-addHandler-ok",
    ],
)
def test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean(
    source, flagged, tmp_path, isolated_state_home
):
    """Library code must never install global logging configuration: basicConfig (even aliased), a no-argument root getLogger, and any mutation of the root logger's handlers are flagged with a file-and-line location, while a named logger and its own addHandler are clean.

    technical (contract): check_global_logging over a file with <source> returns a non-empty list of
    GlobalLoggingViolation when flagged else empty; each violation's location contains the file name and
    a line number.
    """
    from issueforge import observability

    path = _write(tmp_path, "lib.py", source)
    violations = observability.check_global_logging([path])
    assert bool(violations) is flagged
    if flagged:
        assert all(isinstance(v, observability.GlobalLoggingViolation) for v in violations)
        assert all(
            "lib.py" in v.location and any(ch.isdigit() for ch in v.location) for v in violations
        )


# ---------------------------------------------------------------------------
# Logger-convention detector: exact, detected from the target, not imposed
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_detect_logger_convention_reads_target_a_exactly(tmp_path, isolated_state_home):
    """The detector reads the target's own logging conventions exactly — factory, the exact levels used, the installed format, and the correlation field.

    technical (contract): a target using logging.getLogger(__name__), logger.info and logger.error, with
    logging.Formatter("%(asctime)s %(correlation_id)s %(message)s") installed, yields
    LoggerConvention(factory="logging.getLogger", levels==frozenset({"info","error"}),
    format=="%(asctime)s %(correlation_id)s %(message)s", correlation_key="correlation_id").
    """
    from issueforge import observability

    _write(
        tmp_path,
        "app.py",
        """
        import logging
        logger = logging.getLogger(__name__)
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s %(correlation_id)s %(message)s"))
        def handle():
            logger.info("start")
            logger.error("boom")
        """,
    )
    convention = observability.detect_logger_convention(tmp_path)
    assert convention.factory == "logging.getLogger"
    assert convention.levels == frozenset({"info", "error"})
    assert convention.format == "%(asctime)s %(correlation_id)s %(message)s"
    assert convention.correlation_key == "correlation_id"


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_detect_logger_convention_reads_target_b_differently(tmp_path, isolated_state_home):
    """A different target yields a different convention — different exact levels, a different exact format, and no correlation field — proving the result tracks the target rather than a hardcoded default.

    technical (contract): a target using logger.warning and logger.debug with
    logging.Formatter("%(levelname)s:%(message)s") yields levels==frozenset({"warning","debug"}),
    format=="%(levelname)s:%(message)s", correlation_key is None (and "info" not in levels).
    """
    from issueforge import observability

    _write(
        tmp_path,
        "svc.py",
        """
        import logging
        logger = logging.getLogger(__name__)
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
        def run():
            logger.warning("careful")
            logger.debug("details")
        """,
    )
    convention = observability.detect_logger_convention(tmp_path)
    assert convention.levels == frozenset({"warning", "debug"})
    assert convention.format == "%(levelname)s:%(message)s"
    assert convention.correlation_key is None


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_detect_logger_convention_returns_no_factory_when_the_target_has_no_logging(
    tmp_path, isolated_state_home
):
    """A target that does no logging at all yields a convention with no factory — the detector reports absence rather than a hardcoded default.

    technical (contract): a target whose only source is pure arithmetic yields a LoggerConvention with
    factory is None and levels == frozenset().
    """
    from issueforge import observability

    _write(tmp_path, "calc.py", "def add(a, b):\n    return a + b\n")
    convention = observability.detect_logger_convention(tmp_path)
    assert convention.factory is None
    assert convention.levels == frozenset()


# ---------------------------------------------------------------------------
# Runtime log capture + sensitive predicate + evidence collector
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_capture_logs_forwards_args_runs_once_and_leaves_no_persistent_handler(isolated_state_home):
    """Capturing logs forwards arguments to the callable, runs it exactly once, returns its result and the records it emitted, and installs no lasting handler (a second capture does not see duplicates).

    technical (contract): with fn(a, b, *, tag) that appends to a call-count list and logs f"{tag}:a" and
    f"{tag}:b", capture_logs(fn, 3, 4, tag="run1") returns (7, records) where the call-count list has one
    entry and the two messages are "run1:a","run1:b"; a second capture_logs(fn, 1, 1, tag="run2") sees
    exactly its own two records (no run1 duplicates), and logging.getLogger("target.capture").handlers is
    unchanged across the two captures.
    """
    import logging

    from issueforge import observability

    calls = []

    def fn(a, b, *, tag):
        calls.append(tag)
        log = logging.getLogger("target.capture")
        log.info("%s:a", tag)
        log.error("%s:b", tag)
        return a + b

    handlers_before = list(logging.getLogger("target.capture").handlers)
    result, records = observability.capture_logs(fn, 3, 4, tag="run1")
    assert result == 7
    assert calls == ["run1"]
    assert [r.getMessage() for r in records] == ["run1:a", "run1:b"]

    _, records2 = observability.capture_logs(fn, 1, 1, tag="run2")
    assert [r.getMessage() for r in records2] == ["run2:a", "run2:b"]
    assert logging.getLogger("target.capture").handlers == handlers_before


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_sensitive_values_present_detects_deferred_formatting_and_passes_clean_logs(
    isolated_state_home,
):
    """The exclusion predicate resolves logging's deferred %-formatting before checking, so a secret passed as a %s argument is still caught; clean logs report none even when the sensitive set is non-empty.

    technical (contract): for a record emitted as logger.info("token=%s", "SECRET-xyz"),
    sensitive_values_present(records, {"SECRET-xyz","TOKEN-abc"}) == {"SECRET-xyz"} (matched via
    getMessage()); for a record with neither value, it returns set().
    """
    import logging

    from issueforge import observability

    def leaky():
        logging.getLogger("target.leak").info("token=%s for user", "SECRET-xyz")

    def clean():
        logging.getLogger("target.clean").info("processing %s", "an-innocent-request")

    _, leaked_records = observability.capture_logs(leaky)
    _, clean_records = observability.capture_logs(clean)
    assert observability.sensitive_values_present(leaked_records, {"SECRET-xyz", "TOKEN-abc"}) == {
        "SECRET-xyz"
    }
    assert (
        observability.sensitive_values_present(clean_records, {"SECRET-xyz", "TOKEN-abc"}) == set()
    )


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_collect_log_evidence_runs_both_paths_and_reports_events_with_no_leak(isolated_state_home):
    """The authoritative evidence run actually executes both the success and the failure callable, capturing the events each emits, and reports no leak when the sensitive/canary sets appear nowhere — even though those sets are non-empty.

    technical (contract): success_path and failure_path each append a distinct token to a shared list
    (proving they ran) and log events built from their inputs; with required_events =
    {"request.received","request.succeeded","request.failed"} emitted across the two paths, and
    sensitive_fields={"111-22-3333"} / canaries={"CANARY-9"} that neither path logs, collect_log_evidence
    returns events_emitted == required_events and leaked == frozenset(); the shared list shows both paths
    ran exactly once.
    """
    import logging

    from issueforge import observability

    ran = []

    def success():
        ran.append("success")
        log = logging.getLogger("target.evidence")
        log.info("request.received")
        log.info("request.succeeded")

    def failure():
        ran.append("failure")
        log = logging.getLogger("target.evidence")
        log.info("request.received")
        log.error("request.failed")

    evidence = observability.collect_log_evidence(
        success,
        failure,
        required_events={"request.received", "request.succeeded", "request.failed"},
        sensitive_fields={"111-22-3333"},
        canaries={"CANARY-9"},
    )
    assert ran == ["success", "failure"]
    assert evidence.events_emitted == frozenset(
        {"request.received", "request.succeeded", "request.failed"}
    )
    assert evidence.leaked == frozenset()


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_collect_log_evidence_reports_a_missing_required_event_on_a_raising_failure_path(
    isolated_state_home,
):
    """If a required event never emits, the evidence shows it missing — and this holds even when the failure path raises after logging, because the collector captures the pre-exception records and does not propagate the error.

    technical (contract): success_path logs "request.received"; failure_path logs "request.received"
    then RAISES RuntimeError (it never logs "request.failed"); with required_events =
    {"request.received","request.failed"}, collect_log_evidence does not raise, and its events_emitted
    contains "request.received" but NOT "request.failed".
    """
    import logging

    from issueforge import observability

    def success():
        logging.getLogger("target.miss").info("request.received")

    def failure():
        logging.getLogger("target.miss").info("request.received")
        raise RuntimeError("path failed before logging request.failed")

    evidence = observability.collect_log_evidence(
        success,
        failure,
        required_events={"request.received", "request.failed"},
        sensitive_fields=set(),
        canaries=set(),
    )
    assert "request.received" in evidence.events_emitted
    assert "request.failed" not in evidence.events_emitted


@pytest.mark.xfail(strict=True, reason="PENDING (#10)")
def test_collect_log_evidence_flags_a_sensitive_field_or_canary_leaked_on_the_failure_path(
    isolated_state_home,
):
    """A contract-listed sensitive field or a seeded canary that leaks into the logs fails the required verdict — including a leak on the failure path (where a raw error dump is most likely) — the deterministic sensitive-data exclusion the override can never waive.

    technical (contract): success_path logs cleanly ("request.received"); failure_path logs
    "request.failed", the sensitive value "111-22-3333", and the canary "CANARY-FAIL", then RAISES; with
    sensitive_fields={"111-22-3333"} and canaries={"CANARY-FAIL"}, collect_log_evidence returns
    leaked == frozenset({"111-22-3333","CANARY-FAIL"}).
    """
    import logging

    from issueforge import observability

    def success():
        logging.getLogger("target.leaky").info("request.received")

    def failure():
        log = logging.getLogger("target.leaky")
        log.error("request.failed ssn=%s", "111-22-3333")
        log.error("dump CANARY-FAIL")
        raise RuntimeError("boom")

    evidence = observability.collect_log_evidence(
        success,
        failure,
        required_events={"request.received", "request.failed"},
        sensitive_fields={"111-22-3333"},
        canaries={"CANARY-FAIL"},
    )
    assert evidence.leaked == frozenset({"111-22-3333", "CANARY-FAIL"})
