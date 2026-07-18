import subprocess
from pathlib import Path

from typer.testing import CliRunner

from issueforge.cli import app

_GIT_ID = ["-c", "user.name=IssueForge Tests", "-c", "user.email=tests@issueforge.invalid"]


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # click >= 8.2 always separates stderr
        return CliRunner()


def _commit_config(repo: Path, content: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".issueforge.toml").write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", ".issueforge.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), *_GIT_ID, "commit", "-qm", "cfg"], check=True)


def test_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_config_check_valid_prints_full_resolved_plan_from_runtime(tmp_path, monkeypatch):
    """config check on a valid repo prints the complete resolved plan from live data, exit 0.

    Contract: a config with distinguishable baseline/acceptance/lint/build values and a probe
    reporter version 9.9.9 -> exit 0; stdout contains each resolved argv array, the selected
    adapter key, the capabilities, and 9.9.9 (values sourced from the loaded config + probe).
    """

    class _FakeProbe:
        reporter_version = "9.9.9"
        capabilities = "PROBED_CAPS_9"

    monkeypatch.setattr(
        "issueforge.adapters.pytest_adapter.PytestAdapter.probe",
        lambda self, toolchain=None: _FakeProbe(),
    )

    repo = tmp_path / "repo"
    _commit_config(
        repo,
        'baseline = ["BASELINE_CMD"]\n'
        'acceptance = ["ACCEPTANCE_CMD"]\n'
        'lint = ["LINT_CMD"]\n'
        'build = ["BUILD_CMD"]\n',
    )

    result = _runner().invoke(app, ["config", "check", str(repo)])

    assert result.exit_code == 0
    for token in ("BASELINE_CMD", "ACCEPTANCE_CMD", "LINT_CMD", "BUILD_CMD"):
        assert token in result.stdout
    assert "pytest" in result.stdout
    assert "PROBED_CAPS_9" in result.stdout
    assert "9.9.9" in result.stdout


def test_config_check_malformed_committed_exit1_stderr_names_field_no_traceback(tmp_path):
    """config check on a committed bad config fails cleanly on stderr, naming the field.

    Contract: a committed config with baseline = "pytest | tee" -> exit 1; stderr names
    baseline (shell-string violation); stdout is empty; stderr has no traceback.
    """
    repo = tmp_path / "repo"
    _commit_config(repo, 'baseline = "pytest | tee"\n')

    result = _runner().invoke(app, ["config", "check", str(repo)])

    assert result.exit_code == 1
    assert "baseline" in result.stderr
    assert result.stdout == ""
    assert "Traceback (most recent call last)" not in result.stderr


def test_config_check_reports_every_violation_as_distinct_diagnostics(tmp_path):
    """config check reports every violation at once as distinct diagnostics, no fail-fast.

    Contract: a committed config with baseline = "a | b" AND lint = "c | d" -> exit 1; stderr
    carries two distinct diagnostics, one naming baseline and one naming lint; stdout empty.
    """
    repo = tmp_path / "repo"
    _commit_config(repo, 'baseline = "a | b"\nlint = "c | d"\n')

    result = _runner().invoke(app, ["config", "check", str(repo)])

    assert result.exit_code == 1
    assert "baseline" in result.stderr
    assert "lint" in result.stderr
    assert result.stdout == ""
