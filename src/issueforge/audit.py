"""Source-audit completeness lint (issue #4, slice S2).

Validates a per-stage provenance record against the checked-in extraction manifest,
following MARVIN's validator house style: report every violation, no fail-fast.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MANIFEST = Path("docs/provenance/extraction-manifest.json")
DEFAULT_STAGES_DIR = Path("docs/provenance/stages")

_DISPOSITION_HEADER = ("artifact", "test", "disposition", "reason-class", "source")
_AUTHOR_HEADER = ("behavior", "reason-class", "note")

# Per declared root, the glob that identifies candidate build-harness artifacts.
_ROOT_GLOBS = {
    "scripts/": "*.py",
    "skills/": "**/SKILL.md",
    "tests/": "test_*.py",
    "context/": "*.md",
}


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(Path(manifest_path).read_text())


def _discover_candidates(source_root: Path, roots: list[str]) -> list[str]:
    """Repo-relative paths of harness-shaped files found under the declared roots."""
    found: list[str] = []
    for root in roots:
        base = Path(source_root) / root
        if not base.is_dir():
            continue
        for path in sorted(base.glob(_ROOT_GLOBS.get(root, "*.py"))):
            if path.is_file():
                found.append(str(path.relative_to(source_root)))
    return found


def _parse_table(record_text: str, header: tuple[str, ...]) -> list[dict]:
    """Parse the markdown table whose header matches `header` into a list of row dicts."""
    rows: list[dict] = []
    for line in record_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(header):
            continue
        if tuple(cells) == header:
            continue
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def check_stage(
    stage: str,
    manifest_path: Path = DEFAULT_MANIFEST,
    stages_dir: Path = DEFAULT_STAGES_DIR,
    source_root: Path | None = None,
) -> list[str]:
    """Return the list of completeness violations for `stage` (empty list = clean)."""
    violations: list[str] = []
    manifest = _load_manifest(manifest_path)
    record_text = (Path(stages_dir) / f"{stage}.md").read_text()
    dispositions = _parse_table(record_text, _DISPOSITION_HEADER)
    by_key = {(row["artifact"], row["test"]): row for row in dispositions}
    mentioned = {row["artifact"] for row in dispositions}

    for artifact in manifest.get("artifacts", []):
        if artifact.get("tag") != "harness":
            continue
        path = artifact["path"]
        if path not in mentioned:
            violations.append(f"{path} absent from stage record")
            continue
        for test in artifact.get("tests", []):
            row = by_key.get((path, test))
            if row is None:
                violations.append(f"{path}::{test} has no disposition")
                continue
            disposition = row["disposition"]
            if disposition == "ported" and not row["source"]:
                violations.append(f"{path}::{test} is ported but names no source test")
            elif disposition in ("replaced", "discarded") and not row["reason-class"]:
                violations.append(f"{path}::{test} is {disposition} but documents no reason")

    for entry in _parse_table(record_text, _AUTHOR_HEADER):
        if not entry["reason-class"]:
            violations.append(f"author entry '{entry['behavior']}' documents no reason")

    if source_root is not None:
        manifested = {artifact["path"] for artifact in manifest.get("artifacts", [])}
        for candidate in _discover_candidates(source_root, manifest.get("roots", [])):
            if candidate not in manifested:
                violations.append(
                    f"{candidate} discovered under a declared root but absent from manifest "
                    "(classify harness vs workspace)"
                )

    return violations
