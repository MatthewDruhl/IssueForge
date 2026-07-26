"""Committed PENDING acceptance suite for #145: every pytest invocation in the repo's
harness runs with ``-n auto --dist worksteal``.

The suite has a long tail of 15-35s tests. xdist's default scheduler pre-chunks tests
across workers, so a worker that finishes early idles while another grinds a slow chunk.
``--dist worksteal`` lets idle workers pull remaining tests.

Why this suite exists at all: NOTHING in ``tests/`` reads ``Makefile`` or
``.github/workflows/ci.yml``, so harness drift is invisible to the suite. Pre-build
footprint verification found the concrete instance: the ``Makefile`` has THREE pytest
invocations, not the two the issue originally named. ``make gate`` (``Makefile:17-22``)
does not call the ``test`` target -- it carries its OWN copy at ``Makefile:21``. A change
made only to ``test``/``test-fast`` leaves ``gate`` on the default scheduler and silently
breaks the repo's "green here == green in CI" claim (``Makefile:14-16``).

ANTI-GAMING DESIGN (hardened across three adversarial review rounds, each of which returned
NO-SHIP). The first draft checked flags with a plain substring test over the raw command
line. Seven distinct ways to fake it green were proven and fixed:

1. **Lookalike flags.** ``-n automatic --dist workstealing`` CONTAINS both ``-n auto`` and
   ``--dist worksteal`` as substrings. Commands are now tokenized with ``shlex`` and the
   flag VALUES are compared exactly.
2. **Shell comments.** ``uv run pytest a.py # -n auto --dist worksteal`` satisfied a
   substring check while pytest never received the flags. ``shlex`` strips comments.
3. **Compound commands.** ``pytest a.py && pytest -n auto --dist worksteal b.py`` passed
   as one "invocation" although the FIRST process was unflagged. Commands are split on
   shell operators and each pytest process is validated independently.
4. **Vacuous pass.** "every invocation carries the flags" is trivially true over an empty
   set, so an extractor that matches nothing fakes the suite green.
   ``test_extractor_finds_every_harness_invocation`` is GREEN TODAY (not PENDING) and pins
   at least three Makefile invocations, at least one CI invocation, and that the extracted
   Makefile targets include ``gate``, ``test`` AND ``test-fast``.
5. **Operators without whitespace.** POSIX does not require spaces around ``&&``, and
   ``shlex.split`` leaves the operator glued inside a token, so
   ``pytest a.py&&pytest -n auto --dist worksteal b.py`` parsed as ONE command.
   Tokenization now uses ``shlex.shlex(punctuation_chars="&|;")``.
6. **Bash's ``|&``.** shlex GROUPS consecutive punctuation, so ``|&`` arrives as a single
   token that an explicit operator list missed. Operators are now detected by CHARACTER
   COMPOSITION, which closes the whole class instead of one more spelling.
7. **pytest's ``--`` delimiter.** ``pytest -- -n auto --dist worksteal`` passes NO options;
   pytest treats everything after a bare ``--`` as positional. ``_options_only`` truncates
   there before any flag is credited.

Two structural choices carry the design, both preferring default-deny over enumeration:

- The launch-shape check is an ALLOWLIST. A command is accepted as a pytest invocation only
  when it matches a sanctioned shape (``pytest ...``, ``uv run ... pytest ...``,
  ``python -m pytest ...``). Anything that MENTIONS pytest without matching raises
  ``UnrecognizedPytestInvocation`` and fails loudly, so indirection like
  ``$(PYTEST) tests/a.py`` cannot slip past -- it must be fixed, or the allowlist
  consciously extended.
- Operator detection tests composition, not membership (finding 6). A denylist of bad
  spellings is bottomless: each review round finds spelling N+1.

Every helper takes file TEXT as an argument so the green tests can drive synthetic
fixtures through the exact code path the PENDING tests use on the real files. Without that,
a regression inside the helpers would surface only as an XFAIL and look like the expected
pending state.

The two flag tests were committed PENDING with a literal
``@pytest.mark.xfail(strict=True, reason="PENDING (#145)")`` marker and were flipped to
REQUIRED when #145 built (markers removed; the four harness invocations now carry the
flags). They are live regression gates from here on: a PR that drops a flag reds them.

KNOWN, DELIBERATE COVERAGE LIMITS (filed as post-v1 robustness, not silently dropped):
- Make variable indirection that never spells "pytest" (``$(MAKE) other-target`` recursion)
  is invisible. Indirection that DOES contain the token fails loud (see above).
- The CI reader is line-based (PyYAML is not a dependency of this repo). It follows ``run:``
  scalars and ``run: |`` / ``run: >`` block scalars, but does not parse full YAML step
  structure, and does not follow local composite actions.
- ``docs/poc.md``'s standalone single-file PoC invocation is out of scope per the issue: it
  is not a harness entry point.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Each required flag as (long_option, short_option_or_None, required_value).
REQUIRED_FLAGS = (
    ("--numprocesses", "-n", "auto"),
    ("--dist", None, "worksteal"),
)

# Characters shlex is told to treat as punctuation. A token made up ENTIRELY of these is a
# command separator. Testing composition rather than listing spellings (`&&`, `||`, `;`,
# `|`, `&`, `|&`, `;;`) is deliberate: an explicit list is a denylist, and every review
# round finds spelling N+1. Composition closes the whole class at once.
SHELL_OPERATOR_CHARS = frozenset("&|;")

# A Makefile target header: `name:` at column 0 (optionally with prerequisites).
MAKE_TARGET_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.\-/]+)\s*:(?!=)(?P<rest>.*)$")

# Interpreters that can launch pytest via `-m pytest`.
_PYTHON_NAMES = frozenset({"python", "python3", "python3.12", "python3.13", "python3.14"})


class UnrecognizedPytestInvocation(AssertionError):
    """A command mentions pytest but does not match a sanctioned launch shape.

    Raised rather than ignored so that indirection cannot silently shrink the set of
    invocations the suite validates.
    """


def _flag_label(long_option: str, short_option: str | None, value: str) -> str:
    return f"{short_option or long_option} {value}"


def _is_python_interpreter(token: str) -> bool:
    return Path(token).name in _PYTHON_NAMES


def _is_operator(token: str) -> bool:
    """True when the token is made up entirely of shell operator characters."""
    return bool(token) and all(char in SHELL_OPERATOR_CHARS for char in token)


def _split_on_operators(tokens: list[str]) -> list[list[str]]:
    """Split a tokenized command line into individual commands."""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if _is_operator(token):
            if current:
                segments.append(current)
            current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _pytest_argv(segment: list[str]) -> list[str] | None:
    """Return the pytest argv for one command, or None if it does not launch pytest.

    Raises UnrecognizedPytestInvocation when the command mentions pytest but matches no
    sanctioned launch shape (allowlist, not denylist).
    """
    if not segment:
        return None
    # Case-insensitive: Make variable indirection spells it `$(PYTEST)`, and that must
    # reach the fail-loud path below rather than vanishing from the validated set.
    if not any("pytest" in token.lower() for token in segment):
        return None

    head = segment[0]

    # Shape 1: `pytest ...` / `.venv/bin/pytest ...`
    if Path(head).name == "pytest":
        return segment

    # Shape 2: `uv run [uv flags] pytest ...`
    if head == "uv" and len(segment) > 2 and segment[1] == "run":
        for index, token in enumerate(segment[2:], start=2):
            if Path(token).name == "pytest":
                return segment[index:]
            if token == "-m" and index + 1 < len(segment) and segment[index + 1] == "pytest":
                return segment[index + 1 :]

    # Shape 3: `python -m pytest ...`
    if _is_python_interpreter(head):
        for index, token in enumerate(segment):
            if token == "-m" and index + 1 < len(segment) and segment[index + 1] == "pytest":
                return segment[index + 1 :]

    raise UnrecognizedPytestInvocation(
        f"command mentions pytest but matches no sanctioned launch shape "
        f"(`pytest ...`, `uv run ... pytest ...`, `python -m pytest ...`): {' '.join(segment)}"
    )


def _pytest_argvs(command_line: str) -> list[list[str]]:
    """Return the argv of every pytest process launched by one shell command line."""
    stripped = command_line.strip().lstrip("@-+").strip()
    if not stripped:
        return []
    try:
        # punctuation_chars makes shlex split operators that are NOT surrounded by
        # whitespace: POSIX does not require it, so `pytest a.py&&pytest -n auto ...`
        # would otherwise parse as ONE command and let the flagged second process
        # launder the unflagged first. Restricted to the operator characters so that
        # `$(TEST)` survives as a single token for the failure message.
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars="&|;")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        if "pytest" in stripped.lower():
            raise UnrecognizedPytestInvocation(
                f"command mentions pytest but could not be tokenized: {stripped}"
            ) from None
        return []
    argvs = []
    for segment in _split_on_operators(tokens):
        argv = _pytest_argv(segment)
        if argv is not None:
            argvs.append(argv)
    return argvs


def _join_continuations(lines: list[str]) -> list[str]:
    """Join backslash-continued lines so a wrapped command is validated as a whole."""
    joined: list[str] = []
    buffer = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1].rstrip() + " "
            continue
        joined.append(buffer + line if buffer else line)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def makefile_pytest_invocations(text: str) -> list[tuple[str, list[str]]]:
    """Return ``(target, pytest_argv)`` for every pytest process the Makefile launches."""
    found: list[tuple[str, list[str]]] = []
    current_target = ""
    for raw in _join_continuations(text.splitlines()):
        if raw.startswith("\t"):
            command = raw.strip()
            if command.startswith("#"):
                continue
            for argv in _pytest_argvs(command):
                found.append((current_target, argv))
            continue
        if raw.lstrip().startswith("#"):
            continue
        header = MAKE_TARGET_RE.match(raw)
        if not header:
            continue
        current_target = header.group("name")
        # Inline recipe form: `target: ; command`
        rest = header.group("rest")
        if ";" in rest:
            for argv in _pytest_argvs(rest.split(";", 1)[1]):
                found.append((current_target, argv))
    return found


def ci_pytest_invocations(text: str) -> list[list[str]]:
    """Return the argv of every pytest process the CI workflow launches.

    Handles both inline ``run: <command>`` and block scalars (``run: |`` / ``run: >``).
    """
    found: list[list[str]] = []
    lines = _join_continuations(text.splitlines())
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        line = raw.strip()
        if line.startswith("#"):
            continue
        match = re.search(r"(^|\s)run:\s*(?P<value>.*)$", line)
        if not match:
            continue
        value = match.group("value").strip()
        if value in {"|", ">", "|-", ">-", "|+", ">+"}:
            key_indent = len(raw) - len(raw.lstrip())
            while index < len(lines):
                block_line = lines[index]
                if (
                    block_line.strip()
                    and (len(block_line) - len(block_line.lstrip())) <= key_indent
                ):
                    break
                index += 1
                if block_line.strip().startswith("#"):
                    continue
                found.extend(_pytest_argvs(block_line))
            continue
        found.extend(_pytest_argvs(value))
    return found


def missing_flags(argv: list[str]) -> list[str]:
    """Return the required flags that this pytest argv does not actually pass."""
    missing = []
    for long_option, short_option, value in REQUIRED_FLAGS:
        if not _passes_flag(argv, long_option, short_option, value):
            missing.append(_flag_label(long_option, short_option, value))
    return missing


def _options_only(argv: list[str]) -> list[str]:
    """Drop everything after pytest's ``--`` delimiter.

    pytest treats tokens after a bare ``--`` as POSITIONAL arguments, so
    ``pytest -- -n auto --dist worksteal`` passes no options at all and must not be
    credited with them.
    """
    return argv[: argv.index("--")] if "--" in argv else argv


def _passes_flag(argv: list[str], long_option: str, short_option: str | None, value: str) -> bool:
    options = [long_option] + ([short_option] if short_option else [])
    argv = _options_only(argv)
    for index, token in enumerate(argv):
        for option in options:
            if token == option:
                if index + 1 < len(argv) and argv[index + 1] == value:
                    return True
            elif token == f"{option}={value}":
                return True
            elif short_option and option == short_option and token == f"{short_option}{value}":
                return True
    return False


def describe_offender(source: str, label: str, argv: list[str], missing: list[str]) -> str:
    """Build the actionable failure line for one unflagged pytest invocation."""
    return f"{source} {label} is missing {missing}: {' '.join(argv)}"


def _offenders_message(offenders: list[str]) -> str:
    return "\n".join(offenders)


# ---------------------------------------------------------------------------
# GREEN guards. These pass today and must keep passing. They protect the two
# PENDING tests below from passing vacuously or being faked green.
# ---------------------------------------------------------------------------


def test_extractor_finds_every_harness_invocation() -> None:
    """The extraction cannot silently shrink to nothing.

    Without this, the two PENDING tests would pass vacuously over an empty set. Also pins
    that the ``gate`` target's own pytest copy is seen -- the invocation the original issue
    body missed.
    """
    makefile_invocations = makefile_pytest_invocations(MAKEFILE.read_text())
    ci_invocations = ci_pytest_invocations(CI_WORKFLOW.read_text())

    assert len(makefile_invocations) >= 3, (
        f"expected at least 3 pytest invocations in {MAKEFILE.name}, "
        f"found {len(makefile_invocations)}: {makefile_invocations}"
    )
    assert len(ci_invocations) >= 1, (
        f"expected at least 1 pytest invocation in {CI_WORKFLOW.name}, "
        f"found {len(ci_invocations)}: {ci_invocations}"
    )

    targets = {target for target, _ in makefile_invocations}
    for required in ("test", "test-fast", "gate"):
        assert required in targets, (
            f"Makefile target {required!r} has no pytest invocation in the extracted set "
            f"{sorted(targets)}; `gate` in particular carries its own copy separate from "
            f"`test`, and dropping it from extraction would hide unflagged invocations"
        )


def test_lookalike_flags_are_not_credited() -> None:
    """``-n automatic --dist workstealing`` must NOT satisfy ``-n auto --dist worksteal``.

    A substring check credits both; this is the cheapest way to fake the suite green.
    """
    (argv,) = _pytest_argvs("uv run pytest -n automatic --dist workstealing tests/a.py")
    assert missing_flags(argv) == ["-n auto", "--dist worksteal"]


def test_shell_comment_does_not_supply_flags() -> None:
    """Flags mentioned in a trailing shell comment never reach pytest."""
    (argv,) = _pytest_argvs("uv run pytest tests/a.py # -n auto --dist worksteal")
    assert missing_flags(argv) == ["-n auto", "--dist worksteal"]


def test_each_pytest_process_in_a_compound_command_is_validated() -> None:
    """A flagged second command must not launder an unflagged first one."""
    argvs = _pytest_argvs(
        "uv run pytest tests/a.py && uv run pytest -n auto --dist worksteal tests/b.py"
    )
    assert len(argvs) == 2
    assert missing_flags(argvs[0]) == ["-n auto", "--dist worksteal"]
    assert missing_flags(argvs[1]) == []


def test_operators_without_surrounding_whitespace_still_split() -> None:
    """POSIX does not require spaces around ``&&``; the split must not depend on them.

    Without this, ``pytest a.py&&pytest -n auto --dist worksteal b.py`` parses as ONE
    command and the flagged second process launders the unflagged first.
    """
    argvs = _pytest_argvs("uv run pytest tests/a.py&&uv run pytest -n auto --dist worksteal b.py")
    assert len(argvs) == 2
    assert missing_flags(argvs[0]) == ["-n auto", "--dist worksteal"]
    assert missing_flags(argvs[1]) == []

    semicolon = _pytest_argvs("uv run pytest a.py;uv run pytest -n auto --dist worksteal b.py")
    assert len(semicolon) == 2
    assert missing_flags(semicolon[0]) == ["-n auto", "--dist worksteal"]


@pytest.mark.parametrize("operator", ["&&", "||", ";", "|", "&", "|&", ";;"])
def test_every_operator_spelling_separates_commands(operator: str) -> None:
    """Operator detection is by CHARACTER COMPOSITION, not a list of spellings.

    shlex groups consecutive punctuation, so bash's ``|&`` arrives as one token. An
    explicit spelling list missed it and let a flagged command launder an unflagged one.
    """
    argvs = _pytest_argvs(
        f"uv run pytest a.py{operator}uv run pytest -n auto --dist worksteal b.py"
    )
    assert len(argvs) == 2, f"{operator!r} did not separate the two commands: {argvs}"
    assert missing_flags(argvs[0]) == ["-n auto", "--dist worksteal"]
    assert missing_flags(argvs[1]) == []


def test_flags_after_the_double_dash_delimiter_are_not_credited() -> None:
    """pytest treats tokens after a bare ``--`` as positional, not as options."""
    (argv,) = _pytest_argvs("uv run pytest tests/a.py -- -n auto --dist worksteal")
    assert missing_flags(argv) == ["-n auto", "--dist worksteal"]

    # A `--` AFTER genuine flags must not invalidate them.
    (valid,) = _pytest_argvs("uv run pytest -n auto --dist worksteal -- tests/a.py")
    assert missing_flags(valid) == []


def test_accepted_flag_spellings() -> None:
    """Equivalent spellings pytest genuinely accepts are credited."""
    (argv,) = _pytest_argvs("uv run pytest --numprocesses=auto --dist=worksteal tests/a.py")
    assert missing_flags(argv) == []
    (short,) = _pytest_argvs("python -m pytest -n auto --dist worksteal tests/a.py")
    assert missing_flags(short) == []


def test_commented_recipe_lines_are_not_extracted() -> None:
    """A commented-out recipe line is not a real invocation."""
    makefile = "real:\n\tuv run pytest -n auto --dist worksteal\n\t# uv run pytest -n auto\n"
    invocations = makefile_pytest_invocations(makefile)
    assert len(invocations) == 1
    assert invocations[0][0] == "real"


def test_commented_ci_steps_are_not_extracted() -> None:
    """A commented-out CI step is not a real invocation."""
    workflow = "steps:\n  # - run: uv run pytest -n auto\n  - run: uv run pytest -n auto\n"
    assert len(ci_pytest_invocations(workflow)) == 1


def test_block_scalar_ci_steps_are_extracted() -> None:
    """A pytest command hidden in a ``run: |`` block is still validated."""
    workflow = "steps:\n  - run: |\n      uv run pytest tests/a.py\n  - run: echo done\n"
    (argv,) = ci_pytest_invocations(workflow)
    assert missing_flags(argv) == ["-n auto", "--dist worksteal"]


def test_continued_makefile_command_is_joined_before_validation() -> None:
    """A backslash-wrapped command that IS correctly flagged must not be reported."""
    makefile = "wrapped:\n\tuv run pytest -n auto \\\n\t  --dist worksteal tests/a.py\n"
    ((_target, argv),) = makefile_pytest_invocations(makefile)
    assert missing_flags(argv) == []


def test_unrecognized_pytest_launch_shape_fails_loud() -> None:
    """Indirection that mentions pytest must fail loudly, not vanish from the set.

    An allowlist of sanctioned launch shapes, rather than a denylist of bad ones.
    """
    with pytest.raises(UnrecognizedPytestInvocation) as excinfo:
        makefile_pytest_invocations("parked:\n\t$(PYTEST) tests/a.py\n")
    assert "$(PYTEST) tests/a.py" in str(excinfo.value)


def test_failure_message_names_the_file_and_the_full_command() -> None:
    """A future PR that drops a flag gets an actionable failure, not a bare assert.

    Locks acceptance criterion 4 so the message cannot decay to `assert not offenders`.
    """
    argv = ["pytest", "-n", "auto", "tests/a.py"]
    message = describe_offender("Makefile", "target 'gate'", argv, missing_flags(argv))
    assert "Makefile" in message
    assert "target 'gate'" in message
    assert "pytest -n auto tests/a.py" in message
    assert "--dist worksteal" in message


# ---------------------------------------------------------------------------
# PENDING acceptance tests. These fail today and flip green when #145 builds.
# ---------------------------------------------------------------------------


def test_every_makefile_pytest_invocation_uses_worksteal() -> None:
    """Every Makefile pytest invocation runs with ``-n auto --dist worksteal``.

    Covers all three: ``test-fast`` (Makefile:8), ``test`` (Makefile:12) and ``gate``
    (Makefile:21).
    """
    offenders = [
        describe_offender(MAKEFILE.name, f"target {target!r}", argv, missing)
        for target, argv in makefile_pytest_invocations(MAKEFILE.read_text())
        if (missing := missing_flags(argv))
    ]
    assert not offenders, _offenders_message(offenders)


def test_every_ci_pytest_invocation_uses_worksteal() -> None:
    """The CI workflow's pytest step runs with ``-n auto --dist worksteal``.

    The flags must compose with the existing ``--splits``/``--group`` sharding:
    pytest-split deselects at collection time, before the scheduler distributes.
    """
    offenders = [
        describe_offender(CI_WORKFLOW.name, "step", argv, missing)
        for argv in ci_pytest_invocations(CI_WORKFLOW.read_text())
        if (missing := missing_flags(argv))
    ]
    assert not offenders, _offenders_message(offenders)
