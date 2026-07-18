"""Committed PENDING acceptance suite for #6 (S1) — the committed-object config loader.

``issueforge.config`` is imported inside each test body (it does not exist yet), so the
ImportError is the expected xfail rather than a collection ERROR. The Git helpers below use
only the standard library, so they live at module top.
"""

import subprocess
from pathlib import Path

import pytest

_GIT_ID = ["-c", "user.name=IssueForge Tests", "-c", "user.email=tests@issueforge.invalid"]


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)


def _write(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)


def _commit(repo: Path, name: str, content: str, message: str = "c") -> str:
    _write(repo, name, content)
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)
    subprocess.run(["git", "-C", str(repo), *_GIT_ID, "commit", "-qm", message], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return head.stdout.strip()


def test_baseline_shell_string_rejected_attributed_to_field_with_valid_control(tmp_path):
    """A shell string for baseline is rejected and blamed on baseline, while a valid config loads.

    Contract: committed .issueforge.toml with baseline = "pytest | tee out" -> config error
    whose offending field is baseline (argv/shell-string violation); committed control
    baseline = ["pytest"] -> loads successfully.
    """
    import re

    from issueforge.config import ConfigError, load_config

    bad = tmp_path / "bad"
    _init_repo(bad)
    _commit(bad, ".issueforge.toml", 'baseline = "pytest | tee out"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(bad)
    violation = next(v for v in exc.value.violations if v.field == "baseline")
    assert re.search(r"shell|argv|array|list|string", str(violation), re.IGNORECASE)

    control = tmp_path / "control"
    _init_repo(control)
    _commit(control, ".issueforge.toml", 'baseline = ["pytest"]\n')
    assert load_config(control).baseline == ["pytest"]


def test_missing_baseline_rejected_as_missing_required_with_valid_control(tmp_path):
    """A config with no baseline is rejected as a missing required field, not just any failure.

    Contract: committed .issueforge.toml with no baseline key -> missing-required error
    identifying baseline; committed control with only baseline = ["pytest"] -> loads.
    """
    import re

    from issueforge.config import ConfigError, load_config

    bad = tmp_path / "bad"
    _init_repo(bad)
    _commit(bad, ".issueforge.toml", 'acceptance = ["pytest"]\n')
    with pytest.raises(ConfigError) as exc:
        load_config(bad)
    violation = next(v for v in exc.value.violations if v.field == "baseline")
    assert re.search(r"missing|required", str(violation), re.IGNORECASE)

    control = tmp_path / "control"
    _init_repo(control)
    _commit(control, ".issueforge.toml", 'baseline = ["pytest"]\n')
    assert load_config(control).baseline == ["pytest"]


def test_every_command_field_is_argv_validated(tmp_path):
    """The argv-array rule covers every command field, not just baseline.

    Contract: for field in {baseline, acceptance, lint, build}, a committed config where
    field = "a | b" (shell string, other commands valid argv) -> a config error naming field.
    """
    from issueforge.config import ConfigError, load_config

    valid = {
        "baseline": 'baseline = ["pytest"]',
        "acceptance": 'acceptance = ["pytest", "-k", "accept"]',
        "lint": 'lint = ["ruff", "check"]',
        "build": 'build = ["make"]',
    }
    for field in ["baseline", "acceptance", "lint", "build"]:
        repo = tmp_path / f"repo_{field}"
        _init_repo(repo)
        lines = dict(valid)
        lines[field] = f'{field} = "a | b"'
        _commit(repo, ".issueforge.toml", "\n".join(lines.values()) + "\n")
        with pytest.raises(ConfigError) as exc:
            load_config(repo)
        assert field in {v.field for v in exc.value.violations}


def test_untracked_only_config_rejected_as_missing_committed_object(tmp_path):
    """A config that was never committed is rejected as an absent committed object.

    Contract: a repo with an untracked-only .issueforge.toml -> a domain config error
    identifying that no committed .issueforge.toml exists; a committed-config control -> loads.
    """
    from issueforge.config import ConfigError, load_config

    bad = tmp_path / "bad"
    _init_repo(bad)
    _commit(bad, "README.md", "seed\n")
    _write(bad, ".issueforge.toml", 'baseline = ["pytest"]\n')  # untracked only
    with pytest.raises(ConfigError) as exc:
        load_config(bad)
    assert "commit" in str(exc.value).lower()

    control = tmp_path / "control"
    _init_repo(control)
    _commit(control, ".issueforge.toml", 'baseline = ["pytest"]\n')
    assert load_config(control).baseline == ["pytest"]


def test_config_read_from_specified_verified_commit_not_head_or_worktree(tmp_path):
    """Config comes from the specified verified commit, beating both a divergent HEAD and tree.

    Contract: commit A sets baseline = ["from-A"]; a later HEAD commit sets ["from-HEAD"];
    the working tree is edited to ["from-worktree"]; load bound to commit A -> baseline ==
    ["from-A"] (not from-HEAD, not from-worktree).
    """
    from issueforge.config import load_config

    repo = tmp_path / "repo"
    _init_repo(repo)
    sha_a = _commit(repo, ".issueforge.toml", 'baseline = ["from-A"]\n', "A")
    _commit(repo, ".issueforge.toml", 'baseline = ["from-HEAD"]\n', "HEAD")
    _write(repo, ".issueforge.toml", 'baseline = ["from-worktree"]\n')

    assert load_config(repo, ref=sha_a).baseline == ["from-A"]


def test_optional_fields_parse_with_exact_committed_values(tmp_path):
    """All optional fields parse to exactly the committed values.

    Contract: a committed config with acceptance = ["A"], lint = ["L"], build = ["B"],
    contract_paths = ["src/"], sensitive_fields = ["token"] -> the loaded config exposes each
    of those exact arrays.
    """
    from issueforge.config import load_config

    repo = tmp_path / "repo"
    _init_repo(repo)
    content = (
        'baseline = ["pytest"]\n'
        'acceptance = ["A"]\n'
        'lint = ["L"]\n'
        'build = ["B"]\n'
        'contract_paths = ["src/"]\n'
        'sensitive_fields = ["token"]\n'
    )
    _commit(repo, ".issueforge.toml", content)

    cfg = load_config(repo)
    assert cfg.acceptance == ["A"]
    assert cfg.lint == ["L"]
    assert cfg.build == ["B"]
    assert cfg.contract_paths == ["src/"]
    assert cfg.sensitive_fields == ["token"]


def test_optional_fields_may_be_omitted(tmp_path):
    """Optional fields may be omitted; a baseline-only config loads with defined absences.

    Contract: a committed config with only baseline = ["pytest"] -> loads successfully;
    acceptance/lint/build/contract_paths/sensitive_fields are defined absent/default values
    (None or []), not an error.
    """
    from issueforge.config import load_config

    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit(repo, ".issueforge.toml", 'baseline = ["pytest"]\n')

    cfg = load_config(repo)
    assert cfg.baseline == ["pytest"]
    assert cfg.acceptance in (None, [])
    assert cfg.lint in (None, [])
    assert cfg.build in (None, [])
    assert cfg.contract_paths in (None, [])
    assert cfg.sensitive_fields in (None, [])
