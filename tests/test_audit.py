"""Acceptance tests for the S2 source-audit completeness lint (`issueforge audit check`)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from issueforge.cli import app


def write_manifest(path: Path, artifacts: list[dict], roots: list[str] | None = None) -> None:
    artifacts = [dict(artifact) for artifact in artifacts]
    for artifact in artifacts:
        if artifact.get("tag") == "harness":
            artifact.setdefault("stages", ["S2"])
            artifact.setdefault("kind", "source")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "source_repository": "example/source",
                "source_revision": "0" * 40,
                "approved_by": "Matt",
                "approved_on": "2026-07-15",
                "approval_scope": "manifest membership and stage dispositions",
                "provenance_ledger": "ledger.md",
                "roots": roots or ["scripts/", "skills/", "tests/", "context/"],
                "artifacts": artifacts,
            }
        )
    )


def write_stage(
    path: Path,
    rows: list[tuple[str, str, str, str, str]],
    authors: list[tuple[str, str, str]] | None = None,
) -> None:
    lines = [
        "# Stage audit",
        "",
        "Approved-by: Matt",
        "Approved-on: 2026-07-15",
        "Approval-scope: manifest membership and stage dispositions",
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
        roots=["scripts/", "skills/", "tests/", "context/"],
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


def test_discovered_test_missing_from_manifest_and_record_fails(tmp_path: Path) -> None:
    """Mode 3: source discovery, not the curated tests list, defines test completeness."""
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "scripts" / "schedule_waves.py").write_text("")
    (source / "tests" / "test_schedule_waves.py").write_text(
        "def test_listed(): pass\n\nclass TestEdges:\n    def test_new_edge(self): pass\n"
    )
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/schedule_waves.py",
                "tag": "harness",
                "test_files": ["tests/test_schedule_waves.py"],
                "tests": ["test_listed"],
            }
        ],
        roots=["scripts/", "skills/", "tests/", "context/"],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            (
                "scripts/schedule_waves.py",
                "test_listed",
                "ported",
                "deterministic engine policy",
                "test_listed",
            )
        ],
    )

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
            str(source),
        ]
    )

    assert result.exit_code == 1
    assert (
        "tests/test_schedule_waves.py::TestEdges::test_new_edge absent from manifest"
        in result.stderr
    )
    assert "scripts/schedule_waves.py::TestEdges::test_new_edge has no disposition" in result.stderr


def test_unknown_disposition_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [{"path": "scripts/a.py", "tag": "harness", "test_files": [], "tests": ["test_a"]}],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [("scripts/a.py", "test_a", "copied", "deterministic engine policy", "test_a")],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "unknown disposition 'copied'" in result.stderr


def test_ported_requires_allowed_reason_and_real_source_test(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [{"path": "scripts/a.py", "tag": "harness", "test_files": [], "tests": ["test_a"]}],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [("scripts/a.py", "test_a", "ported", "", "test_missing")])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "documents invalid reason-class ''" in result.stderr
    assert "maps to unknown source test 'test_missing'" in result.stderr


def test_invalid_reason_class_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [{"path": "scripts/a.py", "tag": "harness", "test_files": [], "tests": ["test_a"]}],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [("scripts/a.py", "test_a", "discarded", "because", "")])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "invalid reason-class 'because'" in result.stderr


def test_missing_table_header_cannot_parse_rows_as_valid(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [{"path": "scripts/a.py", "tag": "harness", "test_files": [], "tests": ["test_a"]}],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    (stages / "S2.md").write_text(
        "# Stage audit\n\nApproved-by: Matt\nApproved-on: 2026-07-15\n\n"
        "| wrong | columns | still | total | five |\n"
        "|---|---|---|---|---|\n"
        "| scripts/a.py | test_a | ported | deterministic engine policy | test_a |\n"
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "missing required test-disposition table header" in result.stderr
    assert "scripts/a.py::test_a has no disposition" in result.stderr


def test_human_approval_is_required(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, [])
    data = json.loads(manifest.read_text())
    data["approved_by"] = ""
    manifest.write_text(json.dumps(data))
    stages = tmp_path / "stages"
    stages.mkdir()
    (stages / "S2.md").write_text("# Stage audit\n")

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "manifest has no human approval" in result.stderr
    assert "stage S2 has no human approval" in result.stderr


def test_stage_only_requires_applicable_artifacts(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/a.py",
                "tag": "harness",
                "stages": ["S2"],
                "test_files": [],
                "tests": ["test_a"],
            },
            {
                "path": "scripts/b.py",
                "tag": "harness",
                "stages": ["S3"],
                "test_files": [],
                "tests": ["test_b"],
            },
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [("scripts/a.py", "test_a", "ported", "deterministic engine policy", "test_a")],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 0
    assert result.stdout.strip() == "OK"


def test_checked_in_s2_audit_and_ci_gate_pass() -> None:
    root = Path(__file__).parents[1]

    result = run(["audit", "check", "S2"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "OK"
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv run issueforge audit check S2" in workflow
    assert "/Users/matthewdruhl/marvin" not in workflow


def test_empty_manifest_and_invalid_stage_mapping_fail(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, [])
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "manifest artifacts must be a nonempty list" in result.stderr

    write_manifest(
        manifest,
        [{"path": "scripts/a.py", "tag": "harness", "stages": [], "test_files": [], "tests": []}],
    )
    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])
    assert "scripts/a.py has invalid stage applicability" in result.stderr


def test_duplicate_and_extra_dispositions_fail(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [{"path": "scripts/a.py", "tag": "harness", "test_files": [], "tests": ["test_a"]}],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [
            ("scripts/a.py", "test_a", "ported", "deterministic engine policy", "test_a"),
            ("scripts/a.py", "test_a", "ported", "deterministic engine policy", "test_a"),
            ("scripts/a.py", "test_extra", "ported", "deterministic engine policy", "test_a"),
        ],
    )

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "scripts/a.py::test_a has duplicate dispositions" in result.stderr
    assert "scripts/a.py::test_extra is not applicable to stage S2" in result.stderr


def test_short_test_name_cannot_cover_two_test_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "scripts" / "a.py").write_text("")
    for name in ("test_one.py", "test_two.py"):
        (source / "tests" / name).write_text("def test_same(): pass\n")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/a.py",
                "tag": "harness",
                "test_files": ["tests/test_one.py", "tests/test_two.py"],
                "tests": ["test_same"],
            }
        ],
        roots=["scripts/", "skills/", "tests/", "context/"],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(
        stages / "S2.md",
        [("scripts/a.py", "test_same", "ported", "deterministic engine policy", "test_same")],
    )

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
            str(source),
        ]
    )

    assert result.exit_code == 1
    assert "tests/test_one.py::test_same absent from manifest" in result.stderr
    assert "tests/test_two.py::test_same absent from manifest" in result.stderr


def test_nested_skill_test_is_a_discovery_candidate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "skills" / "harden" / "tests" / "fixture-project" / "tests"
    nested.mkdir(parents=True)
    (source / "skills" / "harden" / "SKILL.md").write_text("# harden\n")
    (nested / "test_harden.py").write_text("def test_harden(): pass\n")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [{"path": "skills/harden/SKILL.md", "tag": "workspace, not extracted"}],
        roots=["scripts/", "skills/", "tests/", "context/"],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])

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
            str(source),
        ]
    )

    assert result.exit_code == 1
    assert "skills/harden/tests/fixture-project/tests/test_harden.py discovered" in result.stderr


def test_harness_source_cannot_escape_as_self_referencing_test(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/a.py",
                "tag": "harness",
                "kind": "test",
                "test_files": ["scripts/a.py"],
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "scripts/a.py harness test entries cannot declare tests or test_files" in result.stderr


def test_malformed_test_list_reports_instead_of_crashing(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/a.py",
                "tag": "harness",
                "kind": "source",
                "test_files": [],
                "tests": None,
            }
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "scripts/a.py tests must be a unique list" in result.stderr
    assert result.exception.__class__.__name__ == "SystemExit"


def test_unhashable_manifest_fields_report_instead_of_crashing(tmp_path: Path) -> None:
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])
    cases = [
        ("roots", [{"bad": "root"}]),
        ("path", ["bad"]),
        ("tag", ["harness"]),
        ("kind", ["source"]),
        ("stages", [["S2"]]),
        ("tests", [["test_a"]]),
        ("test_files", [["tests/test_a.py"]]),
    ]
    for field, value in cases:
        manifest = tmp_path / f"{field}.json"
        artifact = {
            "path": "scripts/a.py",
            "tag": "harness",
            "kind": "source",
            "stages": ["S2"],
            "test_files": [],
            "tests": [],
        }
        data = {
            "version": 1,
            "source_repository": "example/source",
            "source_revision": "0" * 40,
            "approved_by": "Matt",
            "approved_on": "2026-07-15",
            "approval_scope": "manifest membership and stage dispositions",
            "provenance_ledger": "ledger.md",
            "roots": ["scripts/", "skills/", "tests/", "context/"],
            "artifacts": [artifact],
        }
        if field == "roots":
            data[field] = value
        else:
            artifact[field] = value
        manifest.write_text(json.dumps(data))

        result = run(
            ["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)]
        )

        assert result.exit_code == 1, field
        assert result.exception.__class__.__name__ == "SystemExit", field

    manifest = tmp_path / "root.json"
    manifest.write_text("[]")
    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])
    assert result.exit_code == 1
    assert "manifest root must be an object" in result.stderr


def test_source_path_cannot_be_labeled_as_referenced_harness_test(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"path": "scripts/a.py", "tag": "harness", "kind": "test"},
            {
                "path": "scripts/decoy.py",
                "tag": "harness",
                "kind": "source",
                "stages": ["S3"],
                "test_files": ["scripts/a.py"],
                "tests": [],
            },
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "scripts/a.py harness test path is not a test file" in result.stderr
    assert "scripts/decoy.py test_files must contain only test paths" in result.stderr


def test_invalid_json_and_scalar_types_fail_cleanly(tmp_path: Path) -> None:
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{")

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "cannot read manifest" in result.stderr
    assert result.exception.__class__.__name__ == "SystemExit"

    write_manifest(manifest, [{"path": "scripts/a.py", "tag": "workspace, not extracted"}])
    data = json.loads(manifest.read_text())
    data.update(
        {
            "version": True,
            "source_revision": 1234567890123456789012345678901234567890,
            "approved_by": {"name": "Matt"},
            "approved_on": 20260715,
        }
    )
    manifest.write_text(json.dumps(data))

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "manifest version must be 1" in result.stderr
    assert "manifest source revision is not a full commit SHA" in result.stderr
    assert "manifest has no human approval" in result.stderr


def test_invalid_utf8_manifest_fails_cleanly(tmp_path: Path) -> None:
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"\xff")

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert "cannot read manifest" in result.stderr


def test_invalid_author_reason_class_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, [{"path": "scripts/a.py", "tag": "workspace, not extracted"}])
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [], authors=[("behavior", "invented class", "note")])

    result = run(["audit", "check", "S2", "--manifest", str(manifest), "--stages-dir", str(stages)])

    assert result.exit_code == 1
    assert (
        "author entry 'behavior' documents invalid reason-class 'invented class'" in result.stderr
    )


def test_invalid_test_syntax_fails_cleanly(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "scripts" / "a.py").write_text("")
    (source / "tests" / "test_a.py").write_text("def test_broken(:\n")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {
                "path": "scripts/a.py",
                "tag": "harness",
                "tests": [],
                "test_files": ["tests/test_a.py"],
            },
            {
                "path": "tests/test_a.py",
                "tag": "harness",
                "kind": "test",
            },
        ],
    )
    stages = tmp_path / "stages"
    stages.mkdir()
    write_stage(stages / "S2.md", [])

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
            str(source),
        ]
    )

    assert result.exit_code == 1
    assert "cannot inspect tests/test_a.py" in result.stderr
