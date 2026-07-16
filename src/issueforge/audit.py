"""Source-audit completeness lint (issue #4, slice S2).

Validates a per-stage provenance record against the checked-in extraction manifest,
following MARVIN's validator house style: report every violation, no fail-fast.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from datetime import date
from pathlib import Path

DEFAULT_MANIFEST = Path("docs/provenance/extraction-manifest.json")
DEFAULT_STAGES_DIR = Path("docs/provenance/stages")

_DISPOSITION_HEADER = ("artifact", "test", "disposition", "reason-class", "source")
_AUTHOR_HEADER = ("behavior", "reason-class", "note")

_DISPOSITIONS = {"ported", "replaced", "discarded"}
_REASON_CLASSES = {
    "deterministic engine policy",
    "AI judgment",
    "human approval",
    "MARVIN-specific behavior to discard",
}
_AUTHOR_REASON_CLASSES = _REASON_CLASSES | {"new engine policy"}

# Per declared root, the glob that identifies candidate build-harness artifacts.
_ROOT_GLOBS = {
    "scripts/": ("*.py",),
    "skills/": ("*/SKILL.md", "*/*.py", "*/scripts/*.py", "**/test_*.py"),
    "tests/": ("test_*.py",),
    "context/": ("*.md",),
}
_REQUIRED_ROOTS = set(_ROOT_GLOBS)
_APPROVAL_SCOPE = "manifest membership and stage dispositions"
_STAGE = re.compile(r"S(?:[1-9]|1[0-9]|2[0-5])\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(Path(manifest_path).read_text())


def _discover_candidates(source_root: Path, roots: list[str]) -> list[str]:
    """Repo-relative paths of harness-shaped files found under the declared roots."""
    found: list[str] = []
    for root in roots:
        base = Path(source_root) / root
        if not base.is_dir():
            continue
        for pattern in _ROOT_GLOBS.get(root, ("*.py",)):
            for path in sorted(base.glob(pattern)):
                if path.is_file():
                    found.append(str(path.relative_to(source_root)))
    return sorted(set(found))


def _parse_table(record_text: str, header: tuple[str, ...]) -> list[dict]:
    """Parse the markdown table whose header matches `header` into a list of row dicts."""
    rows: list[dict] = []
    lines = record_text.splitlines()
    try:
        start = next(
            index
            for index, line in enumerate(lines)
            if tuple(cell.strip() for cell in line.strip().strip("|").split("|")) == header
        )
    except StopIteration:
        return rows
    for line in lines[start + 1 :]:
        line = line.strip()
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(header):
            break
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def _discover_tests(path: Path) -> list[str]:
    """Return pytest-style function names, including methods in test classes."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            found.append(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and child.name.startswith("test_"):
                    found.append(f"{node.name}::{child.name}")
    return found


def _valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _approved(record: dict) -> bool:
    return bool(
        isinstance(record.get("approved_by"), str)
        and record["approved_by"]
        and _valid_date(record.get("approved_on"))
        and isinstance(record.get("approval_scope"), str)
        and record["approval_scope"] == _APPROVAL_SCOPE
    )


def _stage_approved(record_text: str) -> bool:
    fields = {
        key.strip().lower(): value.strip()
        for line in record_text.splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    return bool(
        fields.get("approved-by")
        and _valid_date(fields.get("approved-on"))
        and fields.get("approval-scope") == _APPROVAL_SCOPE
    )


def _unique_strings(value: object, *, allow_empty: bool = True) -> bool:
    return bool(
        isinstance(value, list)
        and (allow_empty or value)
        and all(isinstance(item, str) and item for item in value)
        and len(value) == len(set(value))
    )


def _test_path(path: str) -> bool:
    return path.endswith(".py") and (
        path.startswith("tests/test_") or (path.startswith("skills/") and "/tests/test_" in path)
    )


def _manifest_violations(manifest: object) -> list[str]:
    violations: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest root must be an object"]
    roots = manifest.get("roots")
    artifacts = manifest.get("artifacts")
    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        violations.append("manifest version must be 1")
    if not isinstance(manifest.get("source_repository"), str) or not manifest["source_repository"]:
        violations.append("manifest has no source repository")
    revision = manifest.get("source_revision")
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        violations.append("manifest source revision is not a full commit SHA")
    if not _unique_strings(roots, allow_empty=False):
        violations.append("manifest roots must be a nonempty unique list")
    elif not _REQUIRED_ROOTS.issubset(roots) or any(root not in _ROOT_GLOBS for root in roots):
        violations.append("manifest roots do not match the declared discovery roots")
    if not isinstance(manifest.get("provenance_ledger"), str) or not manifest["provenance_ledger"]:
        violations.append("manifest has no provenance ledger")
    if not isinstance(artifacts, list) or not artifacts:
        violations.append("manifest artifacts must be a nonempty list")
        return violations

    paths = [artifact.get("path") for artifact in artifacts if isinstance(artifact, dict)]
    if len(paths) != len(artifacts) or not _unique_strings(paths, allow_empty=False):
        violations.append("manifest artifact paths must be nonempty and unique")
    referenced_tests = {
        test_file
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("tag") == "harness"
        and isinstance(artifact.get("test_files"), list)
        for test_file in artifact["test_files"]
        if isinstance(test_file, str)
    }
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        path = artifact.get("path", "<missing>")
        tag = artifact.get("tag")
        if not isinstance(tag, str) or tag not in {"harness", "workspace, not extracted"}:
            violations.append(f"{path} has invalid tag '{tag}'")
            continue
        if tag != "harness":
            continue
        kind = artifact.get("kind")
        if not isinstance(kind, str) or kind not in {"source", "test"}:
            violations.append(f"{path} has invalid harness kind '{kind}'")
        stages = artifact.get("stages")
        if not _unique_strings(stages, allow_empty=False) or any(
            not _STAGE.fullmatch(stage) for stage in stages if isinstance(stage, str)
        ):
            violations.append(f"{path} has invalid stage applicability")
        if kind == "test":
            if artifact.get("tests") not in (None, []) or artifact.get("test_files") not in (
                None,
                [],
            ):
                violations.append(f"{path} harness test entries cannot declare tests or test_files")
            if not isinstance(path, str) or not _test_path(path):
                violations.append(f"{path} harness test path is not a test file")
            elif path not in referenced_tests:
                violations.append(f"{path} is a harness test not referenced by a harness artifact")
            continue
        tests = artifact.get("tests")
        test_files = artifact.get("test_files")
        if not _unique_strings(tests):
            violations.append(f"{path} tests must be a unique list")
        if not _unique_strings(test_files):
            violations.append(f"{path} test_files must be a unique list")
        elif any(not _test_path(test_file) for test_file in test_files):
            violations.append(f"{path} test_files must contain only test paths")
    return violations


def check_stage(
    stage: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    stages_dir: Path = DEFAULT_STAGES_DIR,
    source_root: Path | None = None,
) -> list[str]:
    """Return the list of completeness violations for `stage` (empty list = clean)."""
    try:
        loaded_manifest = _load_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"cannot read manifest: {exc}"]
    violations = _manifest_violations(loaded_manifest)
    manifest = loaded_manifest if isinstance(loaded_manifest, dict) else {}
    try:
        record_text = (Path(stages_dir) / f"{stage}.md").read_text()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read stage {stage}: {exc}"]
    dispositions = _parse_table(record_text, _DISPOSITION_HEADER)
    by_key = {(row["artifact"], row["test"]): row for row in dispositions}
    mentioned = {row["artifact"] for row in dispositions}

    if not _approved(manifest):
        violations.append("manifest has no human approval")
    if not _stage_approved(record_text):
        violations.append(f"stage {stage} has no human approval")
    header_missing = "| " + " | ".join(_DISPOSITION_HEADER) + " |" not in record_text
    if header_missing:
        violations.append("missing required test-disposition table header")

    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    harness = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("tag") == "harness"
        and artifact.get("kind") == "source"
        and isinstance(artifact.get("path"), str)
        and _unique_strings(artifact.get("stages"), allow_empty=False)
        and _unique_strings(artifact.get("tests"))
        and _unique_strings(artifact.get("test_files"))
    ]
    applicable: list[dict] = []
    for artifact in harness:
        if artifact.get("stages") is not None and stage not in artifact["stages"]:
            continue
        applicable.append(artifact)
        path = artifact["path"]
        if path not in mentioned:
            if header_missing:
                violations.extend(
                    f"{path}::{test} has no disposition" for test in artifact["tests"]
                )
            else:
                violations.append(f"{path} absent from stage record")
            continue
        for test in artifact["tests"]:
            row = by_key.get((path, test))
            if row is None:
                violations.append(f"{path}::{test} has no disposition")
                continue
            disposition = row["disposition"]
            if disposition not in _DISPOSITIONS:
                violations.append(f"{path}::{test} has unknown disposition '{disposition}'")
            if row["reason-class"] not in _REASON_CLASSES:
                violations.append(
                    f"{path}::{test} documents invalid reason-class '{row['reason-class']}'"
                )
            if disposition == "ported":
                if not row["source"]:
                    violations.append(f"{path}::{test} is ported but names no source test")
                elif row["source"] not in artifact["tests"]:
                    violations.append(
                        f"{path}::{test} maps to unknown source test '{row['source']}'"
                    )

    expected = {(artifact["path"], test) for artifact in applicable for test in artifact["tests"]}
    counts: dict[tuple[str, str], int] = {}
    for row in dispositions:
        key = (row["artifact"], row["test"])
        counts[key] = counts.get(key, 0) + 1
        if key not in expected:
            violations.append(f"{key[0]}::{key[1]} is not applicable to stage {stage}")
    for key, count in counts.items():
        if count > 1:
            violations.append(f"{key[0]}::{key[1]} has duplicate dispositions")

    for entry in _parse_table(record_text, _AUTHOR_HEADER):
        if entry["reason-class"] not in _AUTHOR_REASON_CLASSES:
            violations.append(
                f"author entry '{entry['behavior']}' documents invalid reason-class "
                f"'{entry['reason-class']}'"
            )

    if source_root is not None:
        manifested = {
            path
            for artifact in artifacts
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str)
            for path in [
                artifact["path"],
                *(
                    [item for item in artifact.get("test_files", []) if isinstance(item, str)]
                    if isinstance(artifact.get("test_files"), list)
                    else []
                ),
            ]
        }
        roots = manifest.get("roots", [])
        if not _unique_strings(roots, allow_empty=False):
            roots = []
        for candidate in _discover_candidates(source_root, roots):
            if candidate not in manifested:
                violations.append(
                    f"{candidate} discovered under a declared root but absent from manifest "
                    "(classify harness vs workspace)"
                )
        for artifact in harness:
            declared = set(artifact["tests"])
            for test_file in artifact["test_files"]:
                path = Path(source_root) / test_file
                if not path.is_file():
                    violations.append(f"{test_file} named by {artifact['path']} does not exist")
                    continue
                try:
                    discovered_tests = _discover_tests(path)
                except (OSError, UnicodeError, SyntaxError) as exc:
                    violations.append(f"cannot inspect {test_file}: {exc}")
                    continue
                for test in discovered_tests:
                    identifier = f"{test_file}::{test}"
                    short_is_safe = len(artifact["test_files"]) == 1 and test in declared
                    if identifier not in declared and not short_is_safe:
                        violations.append(f"{test_file}::{test} absent from manifest")
                        if artifact in applicable and not any(
                            (artifact["path"], candidate) in by_key
                            for candidate in (test, identifier)
                        ):
                            violations.append(f"{artifact['path']}::{test} has no disposition")
        revision = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if revision.returncode != 0:
            violations.append("could not verify source checkout revision")
        elif revision.stdout.strip() != manifest.get("source_revision"):
            violations.append("source checkout revision does not match manifest")

    return violations
