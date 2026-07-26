"""Committed PENDING acceptance suite for #146: the observability tests are PARKED out of
the default run until ``observability.py`` is wired into the runtime.

``src/issueforge/observability.py`` has no runtime importer (the ``shaper.py`` and
``contract.py`` hits are dict keys, error strings and docstrings, not imports). Its two test
files cost ~66s of recorded suite time to prove nothing about shipped behavior. The decision
is to keep the code and stop paying the test cost, NOT to delete either.

DESIGN, in one line: assert against the REPOSITORY'S REAL HARNESS, and compare EXACT node-ID
SETS derived at run time.

**The real harness, not raw pytest.** IssueForge's supported entry points are ``make test``,
``make gate`` and CI. A suite that shells out to ``python -m pytest`` proves only that a
config file parses: an implementation could park the tests in ``addopts`` and then add
``--override-ini=addopts=`` to ``make test``, leaving all 86 tests running in the real
default run while the raw check stayed green (``test_harness_flags.py`` would not notice,
because the parallel flags would still be correct). So ``make test`` and ``make test-parked``
are EXECUTED, end to end, with ``PYTEST_ADDOPTS`` set to ``--collect-only``. That runs make's
own prerequisite chain, every preceding recipe line, the real ``uv run`` launch and the real
flags, and stops short only of executing test bodies. A target with a broken prerequisite, a
preceding ``false``, or a ``uv run --project missing-directory`` fails the make invocation and
therefore fails the test.

**Exact sets, not floors.** Every quantity is derived during the run and compared with ``==``:

- The default collection must equal the unrestricted collection MINUS exactly the node IDs
  belonging to the two parked files. Numeric floors would have let hundreds of unrelated
  tests disappear alongside them, and would rot as the suite legitimately changes size.
- ``make test-parked`` must collect exactly the set of IDs those two files define. A ``-k``,
  marker or ``--deselect`` expression exposing most-but-not-all of them fails.

ANTI-GAMING. Fakes that were specifically closed, and what closes each:

1. **Delete the test files.** ``test_the_parked_suites_keep_their_full_test_inventory`` is
   GREEN TODAY and pins the 86 node IDs those two files define as a golden literal set that
   must remain collectable. Deletion, gutting, or renaming tests away all fail it. Growth is
   allowed (superset), shrinkage is not.
2. **Park with too wide a broom.** Closed by the exact
   default-equals-unrestricted-minus-parked comparison, which names every lost ID.
3. **A target that exists but cannot run.** Closed by executing ``make test-parked`` for real
   and requiring exit 0.
4. **Neutralize the parking in the harness instead of honoring it.** ``make gate`` and the CI
   workflow are checked too: their pytest command lines are replayed in collect-only mode and
   must not bring the parked tests back.
5. **Vacuous absence assertions.** ``x not in []`` is trivially true. Three GREEN-TODAY guards
   prove the collection channel returns real IDs, that a failing make target raises rather
   than reporting an empty collection, and that a pytest usage error does the same.
6. **A comment satisfied by unrelated prose elsewhere in the file.** The config annotation is
   read from the comment block ADJACENT TO the ``addopts`` key, not from every ``#`` line in
   ``pyproject.toml``.
7. **The park quietly becoming permanent.**
   ``test_the_park_is_lifted_when_observability_is_wired_into_the_runtime`` is an IMPLICATION,
   not prose: it AST-scans ``src/issueforge`` for a real importer of ``observability`` and
   flips its expectation. Once a runtime importer exists, the same test demands the tests be
   collected again. A wiring PR that leaves the exclusion in place reds the suite.

MEASURED, DELIBERATE LIMITS (stated rather than hidden):

- ``make gate`` and CI are REPLAYED (their pytest argv is re-run in collect-only mode), not
  executed. ``make gate`` also runs boundary lint, two ruff passes and an audit over the whole
  tree; executing it would make this test fail whenever an unrelated file is unformatted, and
  would add tens of seconds. CI cannot be executed at all. Replay still closes the concrete
  vector (a harness invocation that undoes the parking), because that vector lives entirely in
  the argv. The command lines come from ``make -n`` (make's own expansion) rather than from a
  hand-rolled Makefile parser, so variable indirection is resolved before it is read.
- ``make test-fast`` is out of scope: it is the single-file dev loop and requires ``TEST``.
- "``observability.py`` is untouched" is pinned as existence only, not a content hash. A hash
  would block the future PR that wires observability in, which is entitled to edit that file.
- The launch shape ``uv run ... pytest`` IS enforced here for ``test-parked``. The issue's
  amendment mandates it, and ``test_harness_flags.py`` accepts bare ``pytest`` too, so relying
  on that suite alone would leave the constraint unchecked. The parallel flags themselves
  (``-n auto --dist worksteal``) are NOT re-asserted here: that suite already validates them
  for every Makefile invocation, tokenized and value-exact, and a weaker second copy could
  disagree with the canonical one.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PACKAGE_ROOT = REPO_ROOT / "src" / "issueforge"

# The two suites being parked, and the module they exercise.
PARKED_TEST_FILES = ("tests/test_observability.py", "tests/test_observability_unit.py")
OBSERVABILITY_MODULE_NAME = "observability"
OBSERVABILITY_MODULE = PACKAGE_ROOT / f"{OBSERVABILITY_MODULE_NAME}.py"

# The Makefile target that must keep the parked tests runnable on demand.
ON_DEMAND_TARGET = "test-parked"

# The Makefile target that IS the repository's default test run, and the full local gate.
# `test-fast` is excluded on purpose: it is the single-file dev loop and refuses to run
# without TEST.
DEFAULT_TARGET = "test"
GATE_TARGET = "gate"

# Turns any pytest invocation into a collection. Applied through the environment so the REAL
# make recipe can be executed unmodified: pytest reads ini `addopts`, then PYTEST_ADDOPTS,
# then the recipe's own command-line arguments.
COLLECT_ONLY_ADDOPTS = ("--collect-only", "-q", "--color=no", "-p", "no:cacheprovider")

# Neutralizes ini `addopts`, yielding the collection as it would be with nothing parked.
UNRESTRICTED = "--override-ini=addopts="

# Wall clock for one collection subprocess. Collecting the whole tree takes ~0.5s even
# through `uv run`; this is two orders of magnitude of headroom, and it is what stops a
# runaway (an invocation that actually RUNS the 5-minute suite) from hanging the gate.
COLLECTION_TIMEOUT_SECONDS = 120

# `pytest --collect-only -q` prints one `path::nodeid` line per collected test. The part after
# `::` is NOT a restricted token: a parametrize id carries the repr of the parameter, so real
# node IDs in this tree contain spaces, `#`, quotes, brackets and escaped newlines. Only the
# file path is whitespace-free, so that is the only part this pattern constrains.
_NODE_ID_RE = re.compile(r"^(?P<path>\S+\.py)::.+$")

# The last non-empty line of a `-q` collection. Three real shapes:
#   `1134 tests collected in 0.15s`
#   `1078/1134 tests collected (56 deselected) in 0.14s`
#   `no tests collected (1134 deselected) in 0.14s`
# Cross-checking the parsed count against this number is what stops a node-ID shape the
# pattern above does not anticipate from silently shrinking every set comparison in this file.
_COLLECT_SUMMARY_RE = re.compile(
    r"^(?:(?P<count>\d+)(?:/\d+)?\s+tests?\s+collected|no\s+tests\s+collected)\b"
)

# A real node ID from this tree whose parametrize id contains spaces, `#` and escaped
# newlines. Pinned so a future tightening of the pattern above cannot quietly drop this class
# of ID again. Written as a raw string: the backslashes are literal in pytest's output.
WHITESPACE_NODE_ID = (
    r"tests/test_contract.py::test_broken_states_rejected"
    r"[no_tests-# no test functions here\nVALUE = 1\n-no_tests_collected]"
)

# pytest exit statuses this suite knows how to interpret. 0 = tests collected,
# 5 = NO_TESTS_COLLECTED. Anything else (usage error, internal error, config crash) is a
# broken invocation and must raise rather than be read as "successfully excluded".
_OK_COLLECT_EXITS = (0, 5)

# Dropped before a harness command line is REPLAYED. Worker distribution and pytest-split
# sharding change nothing about which tests exist, and replaying them would either nest xdist
# inside an already-parallel run or collect a single shard.
_VALUED_FLAGS_TO_DROP = frozenset({"-n", "--numprocesses", "--dist", "--splits", "--group"})
_PREFIXED_FLAGS_TO_DROP = ("--numprocesses=", "--dist=", "--splits=", "--group=")

# GitHub Actions expressions (`${{ matrix.group }}`) are not shell syntax; collapse them to a
# placeholder token before tokenizing so the argv can be read at all.
_GHA_EXPRESSION_RE = re.compile(r"\$\{\{[^}]*\}\}")

# The exact node IDs the two parked suites define, captured from the tree at authoring time
# (56 from test_observability.py, 30 from test_observability_unit.py). Pinned as a literal so
# that "parked, not abandoned" also protects the CONTENTS: an implementation cannot satisfy
# the collection contract by replacing these suites with trivial stand-ins.
GOLDEN_PARKED_NODE_IDS = frozenset(
    {
        "tests/test_observability.py::test_a_raw_vendor_ai_call_is_not_classified_as_ai_only_the_provider_seam_is",
        "tests/test_observability.py::test_both_classifiers_read_the_shared_marker_tuple_and_it_has_no_public_setter",
        "tests/test_observability.py::test_boundary_categories_are_exactly_the_seven_declared",
        "tests/test_observability.py::test_boundary_markers_equal_the_curated_v1_contract_and_the_ai_marker_is_the_seam",
        "tests/test_observability.py::test_capture_logs_forwards_args_runs_once_and_leaves_no_persistent_handler",
        "tests/test_observability.py::test_classification_is_deterministic_and_performs_no_ai_process_or_network_call",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[AI]",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[DATABASE]",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[FILESYSTEM]",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[HTTP]",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[QUEUE]",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[SUBPROCESS]",
        "tests/test_observability.py::test_classify_diff_flags_each_category_from_added_lines[THIRD_PARTY]",
        "tests/test_observability.py::test_classify_diff_function_local_import_does_not_seed_another_function",
        "tests/test_observability.py::test_classify_diff_ignores_a_boundary_call_present_only_in_context",
        "tests/test_observability.py::test_classify_diff_ignores_removed_lines_and_added_comments",
        "tests/test_observability.py::test_classify_diff_resolves_alias_defined_only_in_unchanged_context",
        "tests/test_observability.py::test_classify_diff_resolves_alias_imported_in_a_different_hunk_of_the_same_file",
        "tests/test_observability.py::test_classify_diff_resolves_context_alias_in_an_indented_body_hunk",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[AI]",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[DATABASE]",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[FILESYSTEM]",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[HTTP]",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[QUEUE]",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[SUBPROCESS]",
        "tests/test_observability.py::test_classify_prospective_flags_exactly_the_one_category_with_marker_evidence[THIRD_PARTY]",
        "tests/test_observability.py::test_classify_prospective_ignores_prose_comments_and_string_literals",
        "tests/test_observability.py::test_classify_prospective_resolves_import_aliases_to_canonical_markers",
        "tests/test_observability.py::test_classify_prospective_suppresses_shadowed_and_reassigned_names",
        "tests/test_observability.py::test_collect_log_evidence_flags_a_sensitive_field_or_canary_leaked_on_the_failure_path",
        "tests/test_observability.py::test_collect_log_evidence_reports_a_missing_required_event_on_a_raising_failure_path",
        "tests/test_observability.py::test_collect_log_evidence_runs_both_paths_and_reports_events_with_no_leak",
        "tests/test_observability.py::test_detect_logger_convention_excludes_standard_logrecord_fields_from_correlation",
        "tests/test_observability.py::test_detect_logger_convention_level_requires_a_traced_logger_receiver",
        "tests/test_observability.py::test_detect_logger_convention_reads_format_from_fmt_kwarg_and_module_constant",
        "tests/test_observability.py::test_detect_logger_convention_reads_target_a_exactly",
        "tests/test_observability.py::test_detect_logger_convention_reads_target_b_differently",
        "tests/test_observability.py::test_detect_logger_convention_returns_no_factory_when_the_target_has_no_logging",
        "tests/test_observability.py::test_filesystem_receiver_resolution_path_method_vs_arbitrary_receiver",
        "tests/test_observability.py::test_g3_flags_every_root_handler_mutation_spelling[annotated-root-addHandler]",
        "tests/test_observability.py::test_g3_flags_every_root_handler_mutation_spelling[bound-root-handlers-append]",
        "tests/test_observability.py::test_g3_flags_every_root_handler_mutation_spelling[chained-getLogger-addHandler]",
        "tests/test_observability.py::test_g3_flags_every_root_handler_mutation_spelling[named-logger-addHandler-ok]",
        "tests/test_observability.py::test_g3_flags_every_root_handler_mutation_spelling[root-handlers-clear]",
        "tests/test_observability.py::test_g3_flags_every_root_handler_mutation_spelling[root-handlers-extend]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[aliased-basicConfig]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[basicConfig]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[bound-root-addHandler]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[named-addHandler-ok]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[named-getLogger-ok]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[root-getLogger]",
        "tests/test_observability.py::test_global_logging_configuration_is_flagged_with_location_but_a_named_logger_is_clean[root-handlers-append]",
        "tests/test_observability.py::test_reconcile_passes_when_the_diff_crosses_only_approved_boundaries",
        "tests/test_observability.py::test_required_event_matches_a_whole_token_not_a_substring",
        "tests/test_observability.py::test_sensitive_values_present_detects_deferred_formatting_and_passes_clean_logs",
        "tests/test_observability.py::test_unanticipated_boundaries_are_a_deterministic_failure_naming_every_new_category",
        "tests/test_observability_unit.py::test_arbitrary_receiver_open_is_not_filesystem",
        "tests/test_observability_unit.py::test_bare_open_is_filesystem_with_open_marker",
        "tests/test_observability_unit.py::test_classify_diff_added_comment_is_not_a_crossing",
        "tests/test_observability_unit.py::test_classify_diff_alias_imported_in_one_hunk_resolves_call_in_another_hunk",
        "tests/test_observability_unit.py::test_classify_diff_cross_hunk_alias_does_not_bleed_across_files",
        "tests/test_observability_unit.py::test_classify_diff_does_not_count_a_call_only_in_context",
        "tests/test_observability_unit.py::test_classify_diff_function_local_import_does_not_seed_other_functions",
        "tests/test_observability_unit.py::test_classify_diff_http_indented_body_with_context_import",
        "tests/test_observability_unit.py::test_classify_diff_indented_body_hunk_resolves_context_alias",
        "tests/test_observability_unit.py::test_classify_diff_multi_file_no_cross_bleed_and_comment_ignored",
        "tests/test_observability_unit.py::test_classify_diff_realistic_git_diff_with_metadata_resolves_context_alias",
        "tests/test_observability_unit.py::test_classify_diff_resolves_alias_defined_in_unchanged_context",
        "tests/test_observability_unit.py::test_constructed_non_path_open_is_not_filesystem",
        "tests/test_observability_unit.py::test_exact_event_satisfies_required_event",
        "tests/test_observability_unit.py::test_format_from_fmt_keyword",
        "tests/test_observability_unit.py::test_format_from_module_constant",
        "tests/test_observability_unit.py::test_g3_flags_every_root_handler_mutation_spelling",
        "tests/test_observability_unit.py::test_g3_named_logger_add_handler_stays_clean",
        "tests/test_observability_unit.py::test_local_def_shadows_builtin_open",
        "tests/test_observability_unit.py::test_logger_info_call_is_a_level",
        "tests/test_observability_unit.py::test_non_logger_info_call_is_not_a_level",
        "tests/test_observability_unit.py::test_non_standard_field_is_the_correlation_key",
        "tests/test_observability_unit.py::test_parameter_shadows_module_name",
        "tests/test_observability_unit.py::test_path_bound_name_write_text_is_filesystem",
        "tests/test_observability_unit.py::test_path_write_text_is_filesystem_with_the_path_marker",
        "tests/test_observability_unit.py::test_reassigned_import_is_not_matched",
        "tests/test_observability_unit.py::test_standard_fields_are_not_a_correlation_key",
        "tests/test_observability_unit.py::test_sub_identifier_does_not_satisfy_required_event",
        "tests/test_observability_unit.py::test_trailing_token_satisfies_required_event",
        "tests/test_observability_unit.py::test_unshadowed_forms_still_flag",
    }
)


class CollectionFailed(AssertionError):
    """A collection subprocess exited with a status this suite cannot interpret.

    Raised rather than swallowed so that a broken pytest configuration, or a make target that
    cannot run, cannot masquerade as a successful exclusion of the parked tests.
    """


@dataclass(frozen=True)
class Collection:
    """The node IDs one collection run reported."""

    node_ids: frozenset[str]

    @property
    def files(self) -> frozenset[str]:
        return frozenset(node_id.split("::", 1)[0] for node_id in self.node_ids)

    def ids_from(self, *test_files: str) -> frozenset[str]:
        wanted = set(test_files)
        return frozenset(n for n in self.node_ids if n.split("::", 1)[0] in wanted)


def _collect_env(extra_addopts: tuple[str, ...]) -> dict[str, str]:
    env = dict(os.environ)
    # The OUTER pytest run must not leak options into the inner one, and an outer `make` must
    # not leak flags (`-n`, `--dry-run`) into the inner one.
    for leaky in ("PYTEST_ADDOPTS", "MAKEFLAGS", "MAKELEVEL", "MFLAGS"):
        env.pop(leaky, None)
    env["PYTEST_ADDOPTS"] = " ".join((*COLLECT_ONLY_ADDOPTS, *extra_addopts))
    # The recipes here run `uv run`, and the OUTER suite is executing out of the very
    # environment `uv run` would sync. Letting a nested launcher rewrite `.venv` underneath a
    # live parallel run is a real way to break unrelated tests. `--no-sync` keeps the launch
    # path intact and only skips the dependency refresh, which the outer run already did.
    env["UV_NO_SYNC"] = "1"
    # The recipes run `-n auto`, and this suite is itself run under `-n auto`. Several OUTER
    # xdist workers can be inside a collection subprocess at the same time, and each would
    # otherwise spawn one inner worker per core. `--collect-only` prevents logical recursion
    # but not that process multiplication. This resolves the inner `auto` to a single worker
    # WITHOUT touching the command line, so the recipe under test still executes verbatim.
    env["PYTEST_XDIST_AUTO_NUM_WORKERS"] = "1"
    return env


def _node_ids(stdout: str) -> frozenset[str]:
    """Read every collected node ID out of a `--collect-only -q` transcript.

    The count pytest reports for itself is treated as the authority: if this parser recovers a
    different number of IDs than pytest says it collected, the parser is wrong and the run
    raises. Every "exactly these tests" comparison in this file is only as exact as this
    function, so it must fail loudly rather than return a subset.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    ids = frozenset(line for line in lines if _NODE_ID_RE.match(line))

    reported: int | None = None
    for line in reversed(lines):
        summary = _COLLECT_SUMMARY_RE.match(line)
        if summary:
            reported = int(summary.group("count") or 0)
            break
    if reported is not None and reported != len(ids):
        raise CollectionFailed(
            f"pytest reported {reported} collected test(s) but this suite's node-ID parser "
            f"recovered {len(ids)}; the collection output has a node-ID shape the parser does "
            f"not handle, so every set comparison built on it would be silently incomplete"
        )
    return ids


def _run(argv: list[str], extra_addopts: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """Run a collection subprocess under a hard wall clock.

    Collection of the whole tree takes well under a second. The timeout exists so that an
    invocation which somehow escapes ``--collect-only`` and starts EXECUTING the suite (five
    minutes, and recursively) fails loudly in seconds instead of hanging the gate.
    """
    try:
        return subprocess.run(
            argv,
            cwd=REPO_ROOT,
            env=_collect_env(extra_addopts),
            capture_output=True,
            text=True,
            check=False,
            timeout=COLLECTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as expired:
        raise CollectionFailed(
            f"`{' '.join(argv)}` did not finish collecting within "
            f"{COLLECTION_TIMEOUT_SECONDS}s; it is running tests rather than collecting them"
        ) from expired


@lru_cache(maxsize=None)
def collect_via_make(target: str, extra_addopts: tuple[str, ...] = ()) -> Collection:
    """EXECUTE a make target with pytest forced into collect-only mode.

    This is the real harness: make's prerequisite chain, every recipe line, the real launcher
    and the real flags all run. Only the test bodies are skipped. A non-zero exit from make is
    a failure, not an empty collection.
    """
    proc = _run(["make", target], extra_addopts)
    if proc.returncode != 0:
        raise CollectionFailed(
            f"`make {target}` exited {proc.returncode} with PYTEST_ADDOPTS="
            f"{_collect_env(extra_addopts)['PYTEST_ADDOPTS']!r}; the target must run end to "
            f"end.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return Collection(_node_ids(proc.stdout))


@lru_cache(maxsize=None)
def collect_via_argv(argv: tuple[str, ...], extra_addopts: tuple[str, ...] = ()) -> Collection:
    """REPLAY a harness command line in collect-only mode.

    Used for the entry points that cannot be executed here: ``make gate`` (whose other steps
    lint the whole tree) and the CI workflow. The vector this closes, a harness invocation
    that undoes the parking on its own command line, lives entirely in the argv.
    """
    proc = _run(list(argv), extra_addopts)
    if proc.returncode not in _OK_COLLECT_EXITS:
        raise CollectionFailed(
            f"`{' '.join(argv)}` exited {proc.returncode} (expected one of "
            f"{_OK_COLLECT_EXITS}).\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return Collection(_node_ids(proc.stdout))


def collect_raw(*args: str) -> Collection:
    """Collect with the running interpreter, for reference sets that must not depend on the
    harness under test."""
    return collect_via_argv((sys.executable, "-m", "pytest", *args))


@lru_cache(maxsize=None)
def make_dry_run(target: str) -> tuple[int, tuple[str, ...]]:
    """Return ``make -n <target>``'s exit status and expanded command lines.

    Reading make's OWN expansion rather than parsing the Makefile means variable indirection
    (``$(PYTEST)``), inline recipes and prerequisites' recipes are all resolved before the
    command line is inspected.
    """
    proc = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        env=_collect_env(()),
        capture_output=True,
        text=True,
        check=False,
        timeout=COLLECTION_TIMEOUT_SECONDS,
    )
    lines = tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return proc.returncode, lines


def pytest_command_lines(lines: tuple[str, ...] | list[str]) -> list[str]:
    """Every command line in the sequence that launches pytest, comments dropped."""
    return [line for line in lines if not line.startswith("#") and "pytest" in line.lower()]


def replayable_argv(command_line: str) -> tuple[str, ...]:
    """Tokenize a harness command line and drop the flags that must not be replayed."""
    tokens = shlex.split(_GHA_EXPRESSION_RE.sub("1", command_line))
    kept: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUED_FLAGS_TO_DROP:
            skip_next = True
            continue
        if token.startswith(_PREFIXED_FLAGS_TO_DROP):
            continue
        kept.append(token)
    return tuple(kept)


def ci_pytest_command_lines(text: str) -> list[str]:
    """Every pytest command line in the CI workflow's ``run:`` steps."""
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        match = re.search(r"(^|\s)run:\s*(?P<value>.+)$", line)
        if match and "pytest" in match.group("value").lower():
            found.append(match.group("value").strip())
    return found


def phony_targets(text: str) -> frozenset[str]:
    """Every target declared on a ``.PHONY:`` line."""
    names: set[str] = set()
    for line in text.splitlines():
        if line.startswith(".PHONY:"):
            names.update(line.split(":", 1)[1].split())
    return frozenset(names)


def runtime_importers_of_observability(package_root: Path) -> list[str]:
    """Return the modules under ``package_root`` that really import ``observability``.

    An AST walk, not a substring search: ``shaper.py`` and ``contract.py`` both contain the
    word in dict keys, error strings and docstrings, and none of those is an import. The
    module cannot import itself into relevance, so it is excluded.
    """
    importers = []
    own_path = (package_root / f"{OBSERVABILITY_MODULE_NAME}.py").resolve()
    for path in sorted(package_root.rglob("*.py")):
        if path.resolve() == own_path:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a syntax error is another suite's problem
            continue
        if _imports_observability(tree) or _dynamically_imports_observability(tree):
            importers.append(str(path.relative_to(package_root)))
    return importers


def _imports_observability(tree: ast.AST) -> bool:
    target = f"issueforge.{OBSERVABILITY_MODULE_NAME}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target or alias.name.startswith(f"{target}."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # `from issueforge.observability import X` / `from .observability import X`
            if module in {target, OBSERVABILITY_MODULE_NAME} or module.startswith(f"{target}."):
                return True
            # `from issueforge import observability` / `from . import observability`
            if module in {"issueforge", ""} and any(
                alias.name == OBSERVABILITY_MODULE_NAME for alias in node.names
            ):
                return True
    return False


def _dynamically_imports_observability(tree: ast.AST) -> bool:
    """Catch ``importlib.import_module("issueforge.observability")`` and friends."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"import_module", "__import__"}:
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.split(".")[-1] == OBSERVABILITY_MODULE_NAME
            ):
                return True
    return False


def addopts_annotation(text: str) -> str:
    """Return the comment block ADJACENT to the pytest ``addopts`` key.

    Scoped deliberately: reading every ``#`` line in the file would let three unrelated
    comments elsewhere satisfy the requirement without annotating the exclusion at all.
    """
    lines = text.splitlines()
    index = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(r"^\s*addopts\s*=", line) and _in_pytest_section(lines, i)
        ),
        None,
    )
    if index is None:
        return ""
    block: list[str] = []
    inline = lines[index].split("#", 1)
    if len(inline) == 2:
        block.append(inline[1])
    for above in range(index - 1, -1, -1):
        if not lines[above].strip().startswith("#"):
            break
        block.append(lines[above].strip().lstrip("#"))
    for below in range(index + 1, len(lines)):
        if not lines[below].strip().startswith("#"):
            break
        block.append(lines[below].strip().lstrip("#"))
    return " ".join(block).lower()


def _in_pytest_section(lines: list[str], index: int) -> bool:
    for above in range(index, -1, -1):
        stripped = lines[above].strip()
        if stripped.startswith("["):
            return stripped == "[tool.pytest.ini_options]"
    return False


def documents_the_on_demand_target(text: str) -> bool:
    """True when a line invokes ``make test-parked`` AND names what it runs.

    An unqualified substring check passes on "never run test-parked", or on the token turning
    up in unrelated prose. The mention has to be tied to the observability suites, in the same
    line or the lines that continue it.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if f"make {ON_DEMAND_TARGET}" not in line:
            continue
        window = " ".join(lines[index : index + 3]).lower()
        if OBSERVABILITY_MODULE_NAME in window:
            return True
    return False


# ---------------------------------------------------------------------------
# GREEN guards. These pass today and must keep passing. They stop the PENDING
# tests below from being satisfied by deletion, by breakage, or vacuously.
# ---------------------------------------------------------------------------


def test_the_make_driven_collection_reports_real_node_ids() -> None:
    """Driving the real `make test` in collect-only mode really does report tests.

    Every "these tests are absent" assertion below is read off this channel. If it silently
    reported nothing, all of them would pass vacuously.
    """
    collection = collect_via_make(DEFAULT_TARGET, (UNRESTRICTED,))
    assert collection.node_ids, (
        f"`make {DEFAULT_TARGET}` in collect-only mode reported no node IDs at all; every "
        f"absence assertion built on it would be vacuous"
    )
    assert len(collection.files) > 10, (
        f"`make {DEFAULT_TARGET}` reported only {sorted(collection.files)}; the channel is "
        f"not seeing the real suite"
    )


def test_the_node_id_parser_keeps_ids_that_contain_whitespace() -> None:
    """A parametrized node ID is not a whitespace-free token, and must survive parsing.

    Parameter reprs put spaces, `#`, quotes, brackets and escaped newlines inside the `[...]`
    of a node ID; this tree has 22 such IDs. A parser that drops them makes every "exactly
    these tests" comparison in this file exact only over an incomplete subset, so unrelated
    tests could vanish from the default run undetected. The count cross-check is the backstop
    that turns any future parser blind spot into a loud failure instead of a quiet one.
    """
    assert _NODE_ID_RE.match(WHITESPACE_NODE_ID), (
        f"the node-ID parser rejects {WHITESPACE_NODE_ID!r}, which is a real ID in this tree"
    )
    everything = collect_raw(UNRESTRICTED).node_ids
    assert WHITESPACE_NODE_ID in everything, (
        f"{WHITESPACE_NODE_ID!r} was not collected. If tests/test_contract.py legitimately "
        f"changed, re-pin WHITESPACE_NODE_ID to another ID whose parameter repr contains "
        f"whitespace; do not delete this guard"
    )
    with_whitespace = {n for n in everything if any(c.isspace() for c in n)}
    assert len(with_whitespace) > 1, (
        f"only {len(with_whitespace)} collected node ID(s) contain whitespace; the parser is "
        f"probably filtering them out again"
    )

    # And the backstop itself must fire: a transcript whose IDs the parser cannot read is a
    # failure, not a smaller collection.
    with pytest.raises(CollectionFailed):
        _node_ids("tests/t.py::a\nnot a node id at all\n2 tests collected in 0.01s\n")


def test_a_broken_collection_raises_instead_of_reading_as_exclusion() -> None:
    """A pytest run that ERRORS must not be mistaken for "those tests are gone".

    Exit 4 (usage error) is the shape a malformed config or a bad path takes. Swallowing it
    would let a repository whose pytest config is simply broken satisfy this contract.
    """
    with pytest.raises(CollectionFailed):
        collect_raw("tests/this_path_does_not_exist.py")


def test_a_make_target_that_cannot_run_raises() -> None:
    """A make target that fails is a failure, not an empty collection.

    This is what makes the on-demand test below sensitive to a broken prerequisite, a
    preceding failing recipe line, or a launcher that cannot start.
    """
    with pytest.raises(CollectionFailed):
        collect_via_make("no-such-target-exists-in-this-makefile")


def test_the_parked_suites_keep_their_full_test_inventory() -> None:
    """Parked is not deleted, and not gutted either.

    Both files must still exist and must still define every one of the 86 tests they defined
    when this suite was written. Growth is fine; quietly replacing the real tests with
    trivial stand-ins is not. `observability.py` itself must survive too.
    """
    for test_file in PARKED_TEST_FILES:
        assert (REPO_ROOT / test_file).is_file(), (
            f"{test_file} was deleted; the issue parks these tests, it does not drop them"
        )
    assert OBSERVABILITY_MODULE.is_file(), (
        f"{OBSERVABILITY_MODULE} was deleted; the decision is to KEEP the code and stop "
        f"paying its test cost until it is wired in"
    )
    available = collect_raw(UNRESTRICTED, *PARKED_TEST_FILES).node_ids
    missing = GOLDEN_PARKED_NODE_IDS - available
    assert not missing, (
        f"{len(missing)} test(s) the parked suites used to define can no longer be "
        f"collected, so coverage was abandoned rather than parked: {sorted(missing)[:5]}"
    )


def test_observability_has_no_runtime_importer_today() -> None:
    """Today nothing in the shipped package imports the observability module.

    This is the premise the whole issue rests on, and the input to the implication test
    below. If it ever stops being true, the park has to lift.
    """
    assert runtime_importers_of_observability(PACKAGE_ROOT) == [], (
        "something in src/issueforge now imports observability; the exclusion must be "
        "removed in that same change"
    )


def test_the_runtime_importer_detector_fires_on_a_real_import(tmp_path: Path) -> None:
    """The importer check is not simply always-false.

    Without this, the implication test would quietly reduce to "always expect parked", and a
    wiring PR that forgot to un-park would sail through.
    """
    package = tmp_path / "issueforge"
    package.mkdir()
    (package / f"{OBSERVABILITY_MODULE_NAME}.py").write_text("VALUE = 1\n")
    spellings = (
        "import issueforge.observability\n",
        "from issueforge.observability import VALUE\n",
        "from issueforge import observability\n",
        "from .observability import VALUE\n",
        "from . import observability\n",
        "import importlib\nmod = importlib.import_module('issueforge.observability')\n",
    )
    for index, source in enumerate(spellings):
        module = package / f"user_{index}.py"
        module.write_text(source)
        assert runtime_importers_of_observability(package) == [module.name], (
            f"the importer detector missed {source.strip()!r}"
        )
        module.unlink()

    # And it must NOT fire on the shapes that exist in the real tree today.
    (package / "decoy.py").write_text(
        '"""Docstring mentioning observability."""\nKEYS = {"observability": 1}\n'
    )
    assert runtime_importers_of_observability(package) == [], (
        "the importer detector fired on a docstring or a dict key, which would make the "
        "premise of this issue look false"
    )


def test_the_dry_run_reports_each_harness_targets_pytest_command() -> None:
    """`make -n` really does hand back the command a target would run.

    The gate check reads its command line this way, so a reader that found nothing would make
    that check vacuous.
    """
    for target in (DEFAULT_TARGET, GATE_TARGET):
        status, lines = make_dry_run(target)
        assert status == 0, f"`make -n {target}` exited {status}"
        commands = pytest_command_lines(lines)
        assert len(commands) == 1, (
            f"expected exactly one pytest command in `make -n {target}`, got {commands}"
        )
        assert "pytest" in replayable_argv(commands[0]), (
            f"the replayable argv for `make {target}` lost its pytest token: {commands[0]}"
        )


def test_the_ci_workflow_still_runs_pytest_directly() -> None:
    """CI's own pytest step is found by the reader the CI check below uses."""
    commands = ci_pytest_command_lines(CI_WORKFLOW.read_text())
    assert len(commands) == 1, (
        f"expected exactly one pytest command in {CI_WORKFLOW.name}, got {commands}"
    )


# ---------------------------------------------------------------------------
# PENDING acceptance tests. These fail today and flip green when #146 builds.
# ---------------------------------------------------------------------------


def test_the_park_is_lifted_when_observability_is_wired_into_the_runtime() -> None:
    """Running the tests the normal way skips the observability tests, and nothing else.

    Written as an if/then rather than a comment: while nothing in the shipped code imports the
    observability module, the real `make test` must not collect its tests, and must lose
    exactly those and no others. The day something does import it, this same test demands the
    tests come back.
    """
    importers = runtime_importers_of_observability(PACKAGE_ROOT)
    default = collect_via_make(DEFAULT_TARGET)
    unrestricted = collect_via_make(DEFAULT_TARGET, (UNRESTRICTED,))
    parked_ids = unrestricted.ids_from(*PARKED_TEST_FILES)
    assert parked_ids, (
        f"the unrestricted collection found no tests in {PARKED_TEST_FILES}; every "
        f"comparison below would be vacuous"
    )

    if importers:
        still_parked = parked_ids - default.node_ids
        assert not still_parked, (
            f"{importers} now import observability at runtime, so the exclusion MUST be "
            f"removed in that same change; {len(still_parked)} of its tests are still parked"
        )
        return

    expected = unrestricted.node_ids - parked_ids
    assert default.node_ids == expected, (
        f"`make {DEFAULT_TARGET}` collected the wrong set. Still present but should be "
        f"parked: {sorted(default.node_ids & parked_ids)[:5]}. Missing but should have been "
        f"kept: {sorted(expected - default.node_ids)[:5]}"
    )


def test_the_gate_and_ci_pytest_runs_do_not_undo_the_parking() -> None:
    """The other two ways the suite gets run also skip the parked tests.

    Parking them in the config and then cancelling that in `make gate` or in CI would leave
    the cost exactly where it was.
    """
    gate_commands = pytest_command_lines(make_dry_run(GATE_TARGET)[1])
    ci_commands = ci_pytest_command_lines(CI_WORKFLOW.read_text())
    assert gate_commands and ci_commands, "no pytest command found for the gate or for CI"

    labelled = [(f"make {GATE_TARGET}", c) for c in gate_commands]
    labelled += [(CI_WORKFLOW.name, c) for c in ci_commands]
    offenders = []
    for label, command in labelled:
        still_there = collect_via_argv(replayable_argv(command)).ids_from(*PARKED_TEST_FILES)
        if still_there:
            offenders.append(f"{label} still collects {len(still_there)} parked tests: {command}")
    assert not offenders, "\n".join(offenders)


def test_make_test_parked_runs_exactly_the_parked_tests_on_demand() -> None:
    """`make test-parked` really runs, and runs precisely the parked tests.

    The target is executed end to end (with the tests collected rather than run), so a
    prerequisite that cannot be built, an earlier recipe line that fails, or a launcher that
    cannot start all fail here. What it collects must match the two suites exactly: not a
    subset chosen by a `-k` expression, and nothing extra.
    """
    status, lines = make_dry_run(ON_DEMAND_TARGET)
    assert status == 0, (
        f"the Makefile has no `{ON_DEMAND_TARGET}` target (`make -n` exited {status}); the "
        f"parked tests must stay runnable on demand"
    )
    assert ON_DEMAND_TARGET in phony_targets(MAKEFILE.read_text()), (
        f"`{ON_DEMAND_TARGET}` is missing from .PHONY, so a file of that name would stop it "
        f"from ever running"
    )

    commands = pytest_command_lines(lines)
    assert len(commands) == 1, (
        f"expected exactly one pytest command in `{ON_DEMAND_TARGET}`, got {commands}"
    )
    tokens = shlex.split(commands[0])
    assert tokens[:2] == ["uv", "run"] and "pytest" in tokens, (
        f"the `{ON_DEMAND_TARGET}` recipe must launch pytest as `uv run ... pytest ...`, the "
        f"shape the issue mandates, got: {commands[0]}"
    )

    collected = collect_via_make(ON_DEMAND_TARGET).node_ids
    expected = collect_raw(UNRESTRICTED, *PARKED_TEST_FILES).node_ids
    assert expected, "reference collection of the parked files is empty; comparison is vacuous"
    assert collected == expected, (
        f"`make {ON_DEMAND_TARGET}` must expose exactly the parked suites. Missing "
        f"{len(expected - collected)}: {sorted(expected - collected)[:5]}. Unexpected extras "
        f"{len(collected - expected)}: {sorted(collected - expected)[:5]}"
    )


def test_the_pytest_config_annotates_the_exclusion_itself() -> None:
    """The exclusion carries a note, right next to it, explaining it is temporary.

    The note must name what is parked and say the pause has to be lifted in the same change
    that wires observability in, so the exclusion cannot quietly become permanent.
    """
    annotation = addopts_annotation(PYPROJECT.read_text())
    assert annotation, (
        "pyproject.toml has no comment adjacent to the pytest `addopts` key; the exclusion "
        "must be annotated where it is written, not somewhere else in the file"
    )
    assert OBSERVABILITY_MODULE_NAME in annotation, (
        f"the comment next to `addopts` does not mention observability: {annotation!r}"
    )
    assert "park" in annotation, (
        f"the comment next to `addopts` must say the tests are PARKED pending integration, "
        f"not abandoned: {annotation!r}"
    )
    assert re.search(r"same (pr|pull request)", annotation), (
        f"the comment next to `addopts` must say the exclusion is removed in the SAME PR "
        f"that wires observability into the runtime: {annotation!r}"
    )


def test_the_on_demand_target_is_documented_for_humans_and_agents() -> None:
    """Both instruction files that list the make targets explain the new one.

    CLAUDE.md and AGENTS.md each enumerate the Makefile targets and tell contributors to run
    tests only through them, so leaving the new target out would make both files wrong. A bare
    mention of the name is not enough: it has to say what the target runs.
    """
    for doc in (CLAUDE_MD, AGENTS_MD):
        assert documents_the_on_demand_target(doc.read_text()), (
            f"{doc.name} does not document `make {ON_DEMAND_TARGET}` as the way to run the "
            f"parked observability suites"
        )
