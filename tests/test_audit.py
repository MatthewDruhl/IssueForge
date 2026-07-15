"""Acceptance tests for the S2 source-audit completeness lint (`issueforge audit check`)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from issueforge.cli import app


def write_manifest(path: Path, artifacts: list[dict], roots: list[str] | None = None) -> None:
    path.write_text(json.dumps({"roots": roots or ["scripts/"], "artifacts": artifacts}))


def write_stage(
    path: Path,
    rows: list[tuple[str, str, str, str, str]],
    authors: list[tuple[str, str, str]] | None = None,
) -> None:
    lines = [
        "# Stage audit",
        "",
        "## Test dispositions",
        "| artifact | test | disposition | reason-class | source |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    if authors is not None:
        lines += [
            "",
            "## Author-supplied entries (no MARVIN prior art)",
            "| behavior | reason-class | note |",
            "|---|---|---|",
        ]
        for a in authors:
            lines.append("| " + " | ".join(a) + " |")
    path.write_text("\n".join(lines) + "\n")


def run(args: list[str]):
    return CliRunner().invoke(app, args)


def test_complete_record_passes(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint", "test_serializes_hotfile"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            ),
            (
                "scripts/schedule_waves.py",
                "test_serializes_hotfile",
                "replaced",
                "deterministic engine policy",
                "new impl",
            ),
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OK"
    assert result.stderr.strip() == ""


def test_undisposed_manifested_test_fails(tmp_path: Path) -> None:
    """Mode 1/3: a manifested harness test with no disposition row is a violation."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint", "test_serializes_hotfile"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    # Half-audited file: only the first test is classified.
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            ),
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "ERROR:" in result.stderr
    assert "scripts/schedule_waves.py::test_serializes_hotfile" in result.stderr


def test_artifact_absent_from_record_fails(tmp_path: Path) -> None:
    """Mode 2: a manifested harness artifact never mentioned in the record is flagged as a whole."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            },
            {
                "path": "scripts/close_run.py",
                "tag": "harness",
                "test_files": ["tests/test_close_run.py"],
                "tests": ["test_guarded_transition"],
            },
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            ),
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    # A whole-artifact omission reads as one artifact-level error, not per-test noise.
    absent = [ln for ln in result.stderr.splitlines() if "scripts/close_run.py" in ln]
    assert len(absent) == 1
    assert "absent from stage record" in absent[0]
    assert "::test_guarded_transition" not in absent[0]


def test_ported_without_source_fails(tmp_path: Path) -> None:
    """Mode 4 (US-11.4): a reused (`ported`) test must map to a source test."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "",
            )
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "scripts/schedule_waves.py::test_groups_disjoint" in result.stderr
    assert "ported" in result.stderr and "source" in result.stderr


def test_replaced_without_reason_fails(tmp_path: Path) -> None:
    """US-11.3: a rewrite (`replaced`/`discarded`) with no documented reason fails."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [("scripts/schedule_waves.py", "test_groups_disjoint", "replaced", "", "")],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "scripts/schedule_waves.py::test_groups_disjoint" in result.stderr
    assert "reason" in result.stderr


def test_reports_every_violation_no_fail_fast(tmp_path: Path) -> None:
    """Report every violation in one pass; do not stop at the first."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint", "test_serializes_hotfile"],
            },
            {
                "path": "scripts/close_run.py",
                "tag": "harness",
                "test_files": ["tests/test_close_run.py"],
                "tests": ["test_guarded_transition"],
            },
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "",
            ),
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    errors = [ln for ln in result.stderr.splitlines() if ln.startswith("ERROR:")]
    assert len(errors) == 3
    joined = "\n".join(errors)
    assert "test_groups_disjoint is ported but names no source" in joined
    assert "test_serializes_hotfile has no disposition" in joined
    assert "scripts/close_run.py absent from stage record" in joined


def test_author_entry_without_reason_fails(tmp_path: Path) -> None:
    """US-11.2: an author-supplied net-new behavior must be classified with a reason."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            )
        ],
        authors=[("parent-epic update", "", "merged_runner missing epic step")],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "parent-epic update" in result.stderr
    assert "reason" in result.stderr


def test_author_entry_with_reason_passes(tmp_path: Path) -> None:
    """A complete record plus a reasoned author-supplied entry is clean."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            )
        ],
        authors=[("parent-epic update", "new engine policy", "merged_runner missing epic step")],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OK"


def _mode5_fixture(tmp_path: Path):
    """A source tree with one manifested and one unmanifested script; a complete record."""
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    (src / "scripts" / "schedule_waves.py").write_text("# manifested\n")
    (src / "scripts" / "prune_plan_files.py").write_text("# NOT in manifest\n")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            }
        ],
        roots=["scripts/"],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            )
        ],
    )
    return src, manifest, stages


def test_mode5_flags_unmanifested_candidate(tmp_path: Path) -> None:
    """Mode 5: a candidate under a declared root but absent from the manifest is flagged."""
    src, manifest, stages = _mode5_fixture(tmp_path)

    result = run(
        [
            "audit",
            "check",
            "S2",
            "--manifest",
            str(manifest),
            "--stages-dir",
            str(stages),
            "--source-root",
            str(src),
        ]
    )

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "scripts/prune_plan_files.py" in result.stderr
    assert "manifest" in result.stderr
    # It does not falsely flag the manifested artifact.
    assert "scripts/schedule_waves.py discovered" not in result.stderr


def test_mode5_is_opt_in(tmp_path: Path) -> None:
    """Without --source-root, discovery does not run and the checked-in record alone passes."""
    _src, manifest, stages = _mode5_fixture(tmp_path)

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OK"


def test_workspace_artifact_needs_no_disposition(tmp_path: Path) -> None:
    """A `workspace, not extracted` artifact is excluded from required dispositions."""
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_groups_disjoint"],
            },
            {
                "path": "skills/marvin_start/SKILL.md",
                "tag": "workspace, not extracted",
                "test_files": [],
                "tests": ["test_quiz_flow"],
            },
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_groups_disjoint",
                "ported",
                "deterministic engine policy",
                "test_groups_disjoint",
            )
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OK"
    assert "test_quiz_flow" not in result.stderr
