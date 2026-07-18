"""Load and validate ``.issueforge.toml`` from a repository's VERIFIED committed Git object.

The configuration the engine runs on is read from a commit, never from an untracked or
dirty working-tree copy: a dirty edit cannot change what an origin-based worktree executes.
``baseline`` is a required argv array (US-4.1); ``acceptance``/``lint``/``build``,
``contract_paths`` and ``sensitive_fields`` are optional (G8). Every command field is
argv-validated, not just the baseline (G1). Loud failure names the offending field; all
violations are reported at once (no fail-fast).
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_COMMAND_FIELDS = ("baseline", "acceptance", "lint", "build")
_LIST_FIELDS = ("contract_paths", "sensitive_fields")
_STRING_FIELDS = ("framework", "reporter")
_CONFIG_FILE = ".issueforge.toml"


@dataclass(frozen=True)
class Violation:
    """One config defect, attributed to the field that caused it."""

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


class ConfigError(Exception):
    """A ``.issueforge.toml`` that could not be loaded or validated.

    Carries every ``Violation`` found in one pass so a caller can report them all.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__("; ".join(str(v) for v in violations))


@dataclass(frozen=True)
class Config:
    """A validated build configuration resolved from a committed config object."""

    baseline: list[str]
    acceptance: list[str] | None = None
    lint: list[str] | None = None
    build: list[str] | None = None
    contract_paths: list[str] | None = field(default=None)
    sensitive_fields: list[str] | None = field(default=None)
    framework: str | None = None
    reporter: str | None = None


def load_config(repo: Path, *, ref: str | None = None) -> Config:
    """Return the validated ``Config`` read from ``ref`` (default HEAD) in ``repo``.

    Raises ``ConfigError`` if no committed config object exists, if it is malformed, or if
    any command field is not an argv array.
    """
    text = _read_committed_config(Path(repo), ref or "HEAD")

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([Violation(_CONFIG_FILE, f"invalid TOML: {exc}")]) from exc

    violations: list[Violation] = []

    if "baseline" not in raw:
        violations.append(Violation("baseline", "missing required argv array"))

    for name in _COMMAND_FIELDS:
        if name in raw:
            violations.extend(_validate_argv(name, raw[name]))

    for name in _LIST_FIELDS:
        if name in raw:
            violations.extend(_validate_string_list(name, raw[name]))

    for name in _STRING_FIELDS:
        if name in raw and not isinstance(raw[name], str):
            violations.append(Violation(name, "must be a string"))

    if violations:
        raise ConfigError(violations)

    return Config(
        baseline=list(raw["baseline"]),
        acceptance=_optional(raw, "acceptance"),
        lint=_optional(raw, "lint"),
        build=_optional(raw, "build"),
        contract_paths=_optional(raw, "contract_paths"),
        sensitive_fields=_optional(raw, "sensitive_fields"),
        framework=raw.get("framework"),
        reporter=raw.get("reporter"),
    )


def _read_committed_config(repo: Path, ref: str) -> str:
    """Read ``.issueforge.toml`` from the committed object at ``ref`` (never the worktree)."""
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{_CONFIG_FILE}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ConfigError(
            [
                Violation(
                    _CONFIG_FILE,
                    f"no committed {_CONFIG_FILE} object at {ref} "
                    "(an untracked or uncommitted file is not a configuration)",
                )
            ]
        )
    return result.stdout


def _validate_argv(name: str, value: object) -> list[Violation]:
    if isinstance(value, str):
        return [
            Violation(
                name,
                "must be an argv array (a list of string arguments), not a shell string",
            )
        ]
    if not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
        return [Violation(name, "must be an argv array (a list of string arguments)")]
    return []


def _validate_string_list(name: str, value: object) -> list[Violation]:
    if not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
        return [Violation(name, "must be a list of strings")]
    return []


def _optional(raw: dict, name: str) -> list[str] | None:
    return list(raw[name]) if name in raw else None
