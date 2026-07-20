"""Unit tests locking the 8 correctness fixes to observability.py (Codex build-gate findings).

Each defect gets a positive AND a negative case. These exercise the analysis on inputs the
committed acceptance fixtures never reach (arbitrary receivers, reassignment/shadowing,
context-only aliases in diffs, indirect root loggers, non-logger `.info()`, kwarg/const formats,
standard LogRecord fields, and sub-identifier event names).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from issueforge import observability
from issueforge.observability import BoundaryCategory as Cat


def _write(dir_path: Path, name: str, source: str) -> Path:
    path = dir_path / name
    path.write_text(textwrap.dedent(source))
    return path


def _categories(tmp_path: Path, source: str) -> frozenset:
    footprint = _write(tmp_path, "mod.py", source)
    return observability.classify_prospective("", [footprint], tmp_path).categories


# ---------------------------------------------------------------------------
# Defect 1 — filesystem receiver matching (false positive + false negative)
# ---------------------------------------------------------------------------


def test_path_write_text_is_filesystem_with_the_path_marker(tmp_path):
    footprint = _write(tmp_path, "mod.py", "from pathlib import Path\nPath('x').write_text('y')\n")
    verdict = observability.classify_prospective("", [footprint], tmp_path)
    assert Cat.FILESYSTEM in verdict.categories
    assert any(e.marker == "pathlib.Path.write_text" for e in verdict.evidence)


def test_path_bound_name_write_text_is_filesystem(tmp_path):
    source = "from pathlib import Path\np = Path('x')\np.write_text('y')\n"
    assert Cat.FILESYSTEM in _categories(tmp_path, source)


def test_arbitrary_receiver_open_is_not_filesystem(tmp_path):
    assert _categories(tmp_path, "def f(client):\n    client.open()\n") == frozenset()


def test_constructed_non_path_open_is_not_filesystem(tmp_path):
    source = "class Archive:\n    def open(self):\n        return 1\nArchive().open()\n"
    assert Cat.FILESYSTEM not in _categories(tmp_path, source)


def test_bare_open_is_filesystem_with_open_marker(tmp_path):
    footprint = _write(tmp_path, "mod.py", "open('f')\n")
    verdict = observability.classify_prospective("", [footprint], tmp_path)
    assert verdict.categories == frozenset({Cat.FILESYSTEM})
    assert any(e.marker == "open" for e in verdict.evidence)


# ---------------------------------------------------------------------------
# Defect 2 — classify_diff must keep unchanged context for alias resolution
# ---------------------------------------------------------------------------


def test_classify_diff_resolves_alias_defined_in_unchanged_context():
    diff = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,3 @@\n"
        " import subprocess as sp\n"
        " def f():\n"
        '+    sp.run(["x"])\n'
    )
    assert observability.classify_diff(diff).categories == frozenset({Cat.SUBPROCESS})


def test_classify_diff_realistic_git_diff_with_metadata_resolves_context_alias():
    # A full `git diff` — diff --git + index + ---/+++ headers must not corrupt reconstruction,
    # and the alias defined on an UNCHANGED context line must still resolve.
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "index abc1234..def5678 100644\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import subprocess as sp\n"
        " def f():\n"
        '+    sp.run(["x"])\n'
    )
    assert observability.classify_diff(diff).categories == frozenset({Cat.SUBPROCESS})


def test_classify_diff_indented_body_hunk_resolves_context_alias():
    # An indented body fragment (does not parse standalone) must still resolve a context alias
    # without dropping context.
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -5,3 +5,4 @@ def outer():\n"
        "     import sqlite3\n"
        "     x = 1\n"
        '+    sqlite3.connect("d")\n'
    )
    assert observability.classify_diff(diff).categories == frozenset({Cat.DATABASE})


def test_classify_diff_http_indented_body_with_context_import():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import httpx\n"
        " def f():\n"
        '+    httpx.get("http://x")\n'
        "     return 1\n"
    )
    assert observability.classify_diff(diff).categories == frozenset({Cat.HTTP})


def test_classify_diff_multi_file_no_cross_bleed_and_comment_ignored():
    # File A adds a real DB crossing; file B adds only a comment. Result is {DATABASE} — no bleed,
    # and the comment is not a crossing.
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import sqlite3\n"
        " def f():\n"
        '+    sqlite3.connect("d")\n'
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -1,1 +1,2 @@\n"
        " def g():\n"
        "+    # subprocess.run(['x']) discussed only\n"
    )
    assert observability.classify_diff(diff).categories == frozenset({Cat.DATABASE})


def test_classify_diff_does_not_count_a_call_only_in_context():
    # subprocess.run is present only as an UNCHANGED context line -> not an added crossing.
    diff = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,3 +1,4 @@\n"
        " import subprocess\n"
        " def f():\n"
        "     subprocess.run(['x'])\n"
        "+    y = 1\n"
    )
    assert observability.classify_diff(diff).categories == frozenset()


def test_classify_diff_added_comment_is_not_a_crossing():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1,1 +1,2 @@\n"
        " def f():\n"
        "+    # subprocess.run(['x']) is only discussed\n"
    )
    assert observability.classify_diff(diff).categories == frozenset()


# ---------------------------------------------------------------------------
# Defect 3 — shadowing / reassignment must suppress false positives
# ---------------------------------------------------------------------------


def test_parameter_shadows_module_name(tmp_path):
    assert _categories(tmp_path, "def f(subprocess):\n    subprocess.run(['x'])\n") == frozenset()


def test_reassigned_import_is_not_matched(tmp_path):
    source = "import subprocess as sp\nsp = object()\nsp.run()\n"
    assert _categories(tmp_path, source) == frozenset()


def test_local_def_shadows_builtin_open(tmp_path):
    source = "def open(path):\n    return path\nopen('x')\n"
    assert _categories(tmp_path, source) == frozenset()


def test_unshadowed_forms_still_flag(tmp_path):
    assert _categories(tmp_path, "import subprocess as sp\nsp.run(['x'])\n") == frozenset(
        {Cat.SUBPROCESS}
    )


# ---------------------------------------------------------------------------
# Defect 4 — G3 must catch every root-handler mutation spelling
# ---------------------------------------------------------------------------

_G3_FLAGGED = [
    "import logging\nlogging.getLogger().addHandler(h)\n",
    "import logging\nroot = logging.root\nroot.handlers.append(h)\n",
    "import logging\nroot: logging.Logger = logging.getLogger()\nroot.addHandler(h)\n",
    "import logging\nroot = logging.getLogger()\nroot.handlers.extend([h])\n",
    "import logging\nlogging.root.handlers.clear()\n",
]


def test_g3_flags_every_root_handler_mutation_spelling(tmp_path):
    for i, source in enumerate(_G3_FLAGGED):
        path = _write(tmp_path, f"lib{i}.py", source)
        violations = observability.check_global_logging([path])
        assert violations, f"expected a violation for:\n{source}"
        assert all(
            f"lib{i}.py" in v.location and any(ch.isdigit() for ch in v.location)
            for v in violations
        )


def test_g3_named_logger_add_handler_stays_clean(tmp_path):
    source = "import logging\nlog = logging.getLogger(__name__)\nlog.addHandler(h)\n"
    path = _write(tmp_path, "clean.py", source)
    assert observability.check_global_logging([path]) == []


# ---------------------------------------------------------------------------
# Defect 5 — level detection only on a traced logger receiver
# ---------------------------------------------------------------------------


def test_non_logger_info_call_is_not_a_level(tmp_path):
    _write(tmp_path, "app.py", "metrics = object()\nmetrics.info('cache hit')\n")
    convention = observability.detect_logger_convention(tmp_path)
    assert "info" not in convention.levels


def test_logger_info_call_is_a_level(tmp_path):
    _write(
        tmp_path,
        "app.py",
        "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('x')\n",
    )
    convention = observability.detect_logger_convention(tmp_path)
    assert "info" in convention.levels
    assert convention.factory == "logging.getLogger"


# ---------------------------------------------------------------------------
# Defect 6 — format detection: fmt kwarg and module-constant reference
# ---------------------------------------------------------------------------


def test_format_from_fmt_keyword(tmp_path):
    _write(tmp_path, "app.py", "import logging\nlogging.Formatter(fmt='%(message)s')\n")
    assert observability.detect_logger_convention(tmp_path).format == "%(message)s"


def test_format_from_module_constant(tmp_path):
    _write(
        tmp_path,
        "app.py",
        "import logging\nFMT = '%(message)s'\nlogging.Formatter(FMT)\n",
    )
    assert observability.detect_logger_convention(tmp_path).format == "%(message)s"


# ---------------------------------------------------------------------------
# Defect 7 — correlation excludes the standard LogRecord field set
# ---------------------------------------------------------------------------


def test_standard_fields_are_not_a_correlation_key(tmp_path):
    fmt = "%(asctime)s %(pathname)s %(filename)s %(module)s %(funcName)s %(message)s"
    _write(tmp_path, "app.py", f"import logging\nlogging.Formatter('{fmt}')\n")
    assert observability.detect_logger_convention(tmp_path).correlation_key is None


def test_non_standard_field_is_the_correlation_key(tmp_path):
    fmt = "%(asctime)s %(correlation_id)s %(message)s"
    _write(tmp_path, "app.py", f"import logging\nlogging.Formatter('{fmt}')\n")
    assert observability.detect_logger_convention(tmp_path).correlation_key == "correlation_id"


# ---------------------------------------------------------------------------
# Defect 8 — required-event matching is whole-token, not substring
# ---------------------------------------------------------------------------


def _events_for(message: str, required: set[str]) -> frozenset:
    def success():
        import logging

        logging.getLogger("t.unit").info(message)

    def failure():
        return None

    return observability.collect_log_evidence(
        success,
        failure,
        required_events=required,
        sensitive_fields=set(),
        canaries=set(),
    ).events_emitted


def test_sub_identifier_does_not_satisfy_required_event():
    assert "request.failed" not in _events_for("request.failed_extra", {"request.failed"})


def test_trailing_token_satisfies_required_event():
    assert "request.failed" in _events_for("request.failed order=5", {"request.failed"})


def test_exact_event_satisfies_required_event():
    assert "request.failed" in _events_for("request.failed", {"request.failed"})


# --- fix 2b (Codex confirmation-round new finding): cross-hunk alias state ---
# An import in one hunk must resolve a call in ANOTHER hunk of the SAME file, while files stay
# isolated. Regression guard for the per-hunk-isolated-alias-state defect.


def test_classify_diff_alias_imported_in_one_hunk_resolves_call_in_another_hunk():
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1,1 +1,2 @@\n"
        " HEADER = 1\n"
        "+import subprocess as sp\n"
        "@@ -40,2 +41,3 @@\n"
        " def worker():\n"
        "     prepare()\n"
        '+    sp.run(["x"])\n'
    )
    assert observability.classify_diff(diff).categories == frozenset({Cat.SUBPROCESS})


def test_classify_diff_cross_hunk_alias_does_not_bleed_across_files():
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,2 @@\n"
        " HEADER = 1\n"
        "+import subprocess as sp\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n"
        "+++ b/b.py\n"
        "@@ -10,2 +10,3 @@\n"
        " def worker():\n"
        "     prepare()\n"
        '+    sp.run(["x"])\n'
    )
    # b.py never imported sp; the alias from a.py must NOT leak into b.py.
    assert observability.classify_diff(diff).categories == frozenset()


def test_classify_diff_function_local_import_does_not_seed_other_functions():
    # A FUNCTION-LOCAL import (indented) in first() must NOT seed sp for an unrelated second().
    # first() adds only the local import (no boundary call), so the ONLY way SUBPROCESS could appear
    # is the false-positive seed leak; with module-level-only seeding it stays empty. (Discriminating:
    # the buggy indentation-stripping seed would return {SUBPROCESS} here.)
    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def first():\n"
        "     helper()\n"
        "+    import subprocess as sp\n"
        "@@ -20,2 +21,3 @@\n"
        " def second():\n"
        "     prepare()\n"
        '+    sp.run(["x"])\n'
    )
    assert observability.classify_diff(diff).categories == frozenset()
