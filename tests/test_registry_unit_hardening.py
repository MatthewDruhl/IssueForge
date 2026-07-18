"""Unit coverage for the S3 hardening fixes from the Codex build gate (#7).

These exercise robustness paths beyond the frozen acceptance suite: slug garbage-rejection,
subdirectory-of-a-repo rejection, a probe that raises, and a corrupt registry file. Each is a
clean refusal (RegistryError / exit 1 with a traceback-free stderr), never a stack trace.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from issueforge.cli import app
from issueforge.registry import RegistryError, repo_slug


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@github.com:Owner/Repo.git", "Owner/Repo"),
        ("https://github.com/Owner/Repo.git", "Owner/Repo"),
        ("https://github.com/Owner/Repo/", "Owner/Repo"),
        ("ssh://git@github.com/Owner/Repo", "Owner/Repo"),
        ("https://github.com/Owner/Repo.git?ref=x", "Owner/Repo"),
        ("Owner/Repo", "Owner/Repo"),
    ],
)
def test_repo_slug_normalizes_supported_forms(url, expected):
    assert repo_slug(url) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "ftp://garbage/Owner/Repo",
        "/srv/Owner/Repo",
        "./relative/path",
        "~/Owner/Repo",
        "not-a-remote",
        "https://github.com/OnlyOwner",
    ],
)
def test_repo_slug_raises_on_garbage_rather_than_manufacturing(bad):
    with pytest.raises(ValueError):
        repo_slug(bad)


def test_register_refuses_a_subdirectory_of_a_repo(make_git_repo, isolated_state_home):
    """A path INSIDE a git repo (not its top level) is refused, registry unchanged."""
    from issueforge.registry import Registry

    repo = make_git_repo(name="proj")
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)

    result = _runner().invoke(app, ["repo", "add", f"Sub:{sub}"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Traceback (most recent call last)" not in result.stderr
    assert Registry.load().entries() == []


def test_register_refuses_cleanly_when_probe_raises(make_git_repo, monkeypatch):
    """A probe that raises becomes a clean refusal (exit 1, no traceback), not a crash."""
    from issueforge.adapters.pytest_adapter import PytestAdapter

    def boom(self, toolchain=None):
        raise RuntimeError("reporter not installed")

    monkeypatch.setattr(PytestAdapter, "probe", boom)

    repo = make_git_repo()
    result = _runner().invoke(app, ["repo", "add", f"DandD:{repo}"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Traceback (most recent call last)" not in result.stderr


def test_corrupt_registry_file_is_a_clean_error_not_a_traceback(isolated_state_home):
    """A corrupt/empty registry.json surfaces a clean stderr error, never a JSONDecodeError trace."""
    from issueforge.registry import Registry

    Registry.registry_path().write_text("{ this is not json", encoding="utf-8")

    result = _runner().invoke(app, ["repo", "list"])
    assert result.exit_code == 1
    assert "Traceback (most recent call last)" not in result.stderr
    with pytest.raises(RegistryError):
        Registry.load()
