"""Committed PENDING acceptance suite for #7 (S3) — register a repo; resolve or refuse its adapter.

The registry module and the ``repo`` CLI subcommand do not exist yet, so each test is
``xfail(strict=True)``: while pending it fails (ImportError, or the ``repo`` command is unknown),
and once S3 lands it XPASSes, which strict mode turns into a failure signalling the marker's
removal. ``issueforge.registry`` is imported inside each test body (as ``test_config.py`` does)
so the ImportError is the expected xfail, not a collection error.

Presentation is dual-layer: a plain-English behavior line, then the exact golden values under
``technical (contract):``.

The registry interface this suite authors (ATDD — the tests define the seam S3 must expose):
- ``Registry.load()`` reads the registry at the current ``ISSUEFORGE_STATE_HOME``.
- ``Registry.registry_path()`` is the canonical registry file, under ``paths.state_root()``.
- ``reg.get(alias)`` resolves case-insensitively; ``reg.entries()`` lists all entries.
- an entry exposes ``alias`` (entered spelling), ``path`` (resolved absolute), ``slug``,
  ``default_branch``, ``baseline`` (argv list), ``framework``, ``reporter``, ``adapter``.

Design decisions resolved during /spec-up (recorded on the issue):
- Registry home = ``paths.state_root()`` (``ISSUEFORGE_STATE_HOME``), persisted via the S25
  WriteSeam. The issue's ``~/.issueforge``/``ISSUEFORGE_HOME`` text is superseded by that seam.
- ``(framework, reporter)`` is declared by an explicit ``framework`` field in the committed
  ``.issueforge.toml`` (``reporter`` defaults to ``framework``); registration resolves the
  adapter from it and refuses an unknown framework/reporter, naming it.
- Default branch is read from the clone's ``refs/remotes/origin/HEAD``; unresolvable -> refused
  (no ``or "main"`` fallback).
- "Mismatched remote" = same alias re-added against a repo whose normalized origin slug DIFFERS;
  "duplicate alias" = same alias re-added, slug MATCHES. Both refuse, registry byte-identical.

Deliberately deferred (resolved with Matt during /spec-up, follow-up issues filed):
- Mandatory probe-capability enforcement (US-1.5 "capability is mandatory") — the shipped pytest
  adapter cannot prove dependency-protection until S12; S3 tests resolution + probe-invoked +
  unknown-(framework,reporter) refusal only.
- Runtime logging/observability — no runtime logging seam exists yet.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from issueforge.cli import app

PYTEST_CONFIG = 'baseline = ["pytest"]\nframework = "pytest"\n'


def _runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # click >= 8.2 always separates stderr
        return CliRunner()


def _add(alias: str, repo: Path):
    return _runner().invoke(app, ["repo", "add", f"{alias}:{repo}"])


def _snapshot(root: Path) -> dict[str, bytes]:
    """Byte-for-byte contents of every file under ``root`` (keyed by relative path)."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _aliases() -> list[str]:
    from issueforge.registry import Registry

    return sorted(e.alias for e in Registry.load().entries())


def _no_traceback(text: str) -> bool:
    return "Traceback (most recent call last)" not in text


# --------------------------------------------------------------------------- records / display


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_repo_add_records_all_fields_with_exact_values_and_list_prints_them(make_git_repo):
    """repo add records the alias, resolved path, origin slug, default branch, baseline, framework, reporter, and adapter as exact values; repo list prints the alias and slug.

    technical (contract): a repo with origin git@github.com:Owner/DandD.git,
    refs/remotes/origin/HEAD -> origin/main, and committed baseline = ["pytest"] /
    framework = "pytest" -> after `repo add DandD:<repo>` (exit 0),
    Registry.load().get("DandD") has alias=="DandD", path==<repo>.resolve(),
    slug=="Owner/DandD", default_branch=="main", baseline==["pytest"], framework=="pytest",
    reporter=="pytest", adapter=="pytest"; and `repo list` stdout contains "DandD" and
    "Owner/DandD".
    """
    from issueforge.registry import Registry

    repo = make_git_repo(origin="git@github.com:Owner/DandD.git", branch="main")
    assert _add("DandD", repo).exit_code == 0

    entry = Registry.load().get("DandD")
    assert entry.alias == "DandD"
    assert entry.path == repo.resolve()
    assert entry.slug == "Owner/DandD"
    assert entry.default_branch == "main"
    assert entry.baseline == ["pytest"]
    assert entry.framework == "pytest"
    assert entry.reporter == "pytest"
    assert entry.adapter == "pytest"

    listed = _runner().invoke(app, ["repo", "list"])
    assert listed.exit_code == 0
    assert "DandD" in listed.stdout
    assert "Owner/DandD" in listed.stdout


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_tilde_expanded_to_absolute_path_in_persisted_entry(make_git_repo, monkeypatch, tmp_path):
    """A ~ in the added path is expanded to an absolute path in the stored entry, not kept literally.

    technical (contract): with HOME set to a temp dir and a repo at $HOME/proj/DandD,
    `repo add DandD:~/proj/DandD` (exit 0) -> Registry.load().get("DandD").path ==
    (<HOME>/proj/DandD).resolve(), and "~" not in str(that path).
    """
    from issueforge.registry import Registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    make_git_repo(name="DandD", parent=home / "proj")

    assert _runner().invoke(app, ["repo", "add", "DandD:~/proj/DandD"]).exit_code == 0

    entry = Registry.load().get("DandD")
    assert entry.path == (home / "proj" / "DandD").resolve()
    assert "~" not in str(entry.path)


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_alias_lookup_case_insensitive_preserves_display_spelling(
    make_git_repo, isolated_state_home
):
    """Alias lookup is case-insensitive across spellings, the entered spelling is preserved, and a differently-cased re-add is refused.

    technical (contract): after `repo add DandD:<repo>`, Registry.load().get returns the same
    entry for "dandd", "DANDD", and "DandD", each with alias=="DandD"; a second
    `repo add DANDD:<repo>` -> non-zero exit (duplicate) and the state home is byte-identical.
    """
    from issueforge.registry import Registry

    repo = make_git_repo()
    assert _add("DandD", repo).exit_code == 0

    reg = Registry.load()
    for spelling in ("dandd", "DANDD", "DandD"):
        assert reg.get(spelling).alias == "DandD"

    before = _snapshot(isolated_state_home)
    again = _add("DANDD", repo)
    assert again.exit_code != 0
    assert _snapshot(isolated_state_home) == before


# --------------------------------------------------------------------------- origin / slug


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
@pytest.mark.parametrize(
    "origin",
    [
        "git@github.com:Owner/DandD.git",
        "https://github.com/Owner/DandD.git",
        "https://github.com/Owner/DandD/",
        "ssh://git@github.com/Owner/DandD",
    ],
)
def test_origin_slug_normalized_across_url_spellings(make_git_repo, origin):
    """The recorded origin slug is normalized to owner/repo regardless of URL spelling (ssh, https, .git, trailing slash).

    technical (contract): a repo whose origin is any of git@github.com:Owner/DandD.git,
    https://github.com/Owner/DandD.git, https://github.com/Owner/DandD/, or
    ssh://git@github.com/Owner/DandD -> after `repo add R:<repo>`,
    Registry.load().get("R").slug == "Owner/DandD".
    """
    from issueforge.registry import Registry

    repo = make_git_repo(origin=origin)
    assert _add("R", repo).exit_code == 0
    assert Registry.load().get("R").slug == "Owner/DandD"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_repo_without_origin_refused_registry_unchanged(make_git_repo, isolated_state_home):
    """A Git repo with no origin remote is refused (no slug can be recorded), leaving the registry unchanged.

    technical (contract): a repo created with no origin remote -> `repo add N:<repo>` non-zero
    exit whose stderr names the origin/remote problem, empty stdout, no traceback; the state
    home is byte-identical and Registry has no "N" entry. A valid-origin control registers.
    """
    no_origin = make_git_repo(name="n", origin=None)
    control = make_git_repo(name="c")
    assert _add("C", control).exit_code == 0

    before = _snapshot(isolated_state_home)
    refused = _add("N", no_origin)
    assert refused.exit_code != 0
    assert "origin" in refused.stderr.lower() or "remote" in refused.stderr.lower()
    assert refused.stdout == ""
    assert _no_traceback(refused.stderr)
    assert _snapshot(isolated_state_home) == before
    assert "N" not in _aliases()


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_malformed_origin_refused_registry_unchanged(make_git_repo, isolated_state_home):
    """An origin URL that cannot be normalized to owner/repo is refused, registry unchanged.

    technical (contract): a repo whose origin is "not-a-valid-remote" -> `repo add B:<repo>`
    non-zero exit, empty stdout, no traceback; the state home is byte-identical. A valid-origin
    control registers (exit 0).
    """
    bad = make_git_repo(name="b", origin="not-a-valid-remote")
    control = make_git_repo(name="c")
    assert _add("C", control).exit_code == 0

    before = _snapshot(isolated_state_home)
    refused = _add("B", bad)
    assert refused.exit_code != 0
    assert refused.stdout == ""
    assert _no_traceback(refused.stderr)
    assert _snapshot(isolated_state_home) == before


# --------------------------------------------------------------------------- duplicate / mismatch


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_duplicate_alias_refused_registry_byte_identical(make_git_repo, isolated_state_home):
    """Re-adding an already-registered alias whose slug matches is refused as a duplicate, registry byte-identical.

    technical (contract): after a successful `repo add DandD:<repo>`, a second
    `repo add DandD:<repo>` -> non-zero exit whose stderr says duplicate/already, empty stdout;
    the state-home byte-snapshot is identical before and after.
    """
    repo = make_git_repo()
    assert _add("DandD", repo).exit_code == 0

    before = _snapshot(isolated_state_home)
    again = _add("DandD", repo)
    assert again.exit_code != 0
    assert "duplicate" in again.stderr.lower() or "already" in again.stderr.lower()
    assert again.stdout == ""
    assert _snapshot(isolated_state_home) == before


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_mismatched_remote_refused_but_equivalent_remote_is_a_plain_duplicate(
    make_git_repo, isolated_state_home
):
    """Re-adding an alias against a different origin slug is refused as a remote mismatch; an equivalent-slug remote in different URL syntax is treated as a plain duplicate — proving the comparison is on the normalized slug.

    technical (contract): with DandD registered from origin git@github.com:Owner/DandD.git:
    (a) `repo add DandD:<repoB>` where repoB origin is git@github.com:Other/DandD.git ->
    non-zero exit whose stderr signals a remote/mismatch (not a bare duplicate);
    (b) `repo add DandD:<repoC>` where repoC origin is https://github.com/Owner/DandD.git
    (same normalized slug) -> non-zero exit whose stderr says duplicate/already (NOT mismatch).
    The state home is byte-identical after each.
    """
    a = make_git_repo(name="a", origin="git@github.com:Owner/DandD.git")
    b = make_git_repo(name="b", origin="git@github.com:Other/DandD.git")
    c = make_git_repo(name="c", origin="https://github.com/Owner/DandD.git")
    assert _add("DandD", a).exit_code == 0

    before = _snapshot(isolated_state_home)
    mism = _add("DandD", b)
    assert mism.exit_code != 0
    assert "mismatch" in mism.stderr.lower() or "remote" in mism.stderr.lower()
    assert _snapshot(isolated_state_home) == before

    dup = _add("DandD", c)
    assert dup.exit_code != 0
    assert "duplicate" in dup.stderr.lower() or "already" in dup.stderr.lower()
    assert "mismatch" not in dup.stderr.lower()
    assert _snapshot(isolated_state_home) == before


# --------------------------------------------------------------------------- path validity


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_missing_path_refused_naming_path_clean_channel(
    make_git_repo, isolated_state_home, tmp_path
):
    """A repo add against a nonexistent path is refused, naming the path on stderr with a clean error channel, registry unchanged.

    technical (contract): after a valid `repo add Good:<repo>`, `repo add Bad:<tmp>/nope` ->
    non-zero exit; stderr contains the missing path, stdout is empty, no traceback; the
    state-home byte-snapshot is identical before and after.
    """
    good = make_git_repo(name="good")
    assert _add("Good", good).exit_code == 0
    missing_path = tmp_path / "nope"

    before = _snapshot(isolated_state_home)
    refused = _runner().invoke(app, ["repo", "add", f"Bad:{missing_path}"])
    assert refused.exit_code != 0
    assert str(missing_path) in refused.stderr
    assert refused.stdout == ""
    assert _no_traceback(refused.stderr)
    assert _snapshot(isolated_state_home) == before


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_non_git_path_refused_clean_channel(isolated_state_home, make_git_repo, tmp_path):
    """A repo add against a real directory that is not a Git repository is refused with a clean error channel, registry unchanged.

    technical (contract): after a valid `repo add Good:<repo>`, a plain directory <tmp>/plain
    (exists, no .git) -> `repo add Bad:<tmp>/plain` non-zero exit, empty stdout, no traceback;
    the state-home byte-snapshot is identical before and after.
    """
    good = make_git_repo(name="good")
    assert _add("Good", good).exit_code == 0
    plain = tmp_path / "plain"
    plain.mkdir()

    before = _snapshot(isolated_state_home)
    refused = _runner().invoke(app, ["repo", "add", f"Bad:{plain}"])
    assert refused.exit_code != 0
    assert refused.stdout == ""
    assert _no_traceback(refused.stderr)
    assert _snapshot(isolated_state_home) == before


# --------------------------------------------------------------------------- default branch


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_default_branch_read_from_origin_head_not_local_branch(make_git_repo):
    """The recorded default branch comes from the clone's remote HEAD, not the checked-out local branch, and is never assumed to be main.

    technical (contract): a repo checked out on local branch "feature" whose
    refs/remotes/origin/HEAD points to origin/trunk -> after `repo add T:<repo>`,
    Registry.load().get("T").default_branch == "trunk" (not "feature", not "main").
    """
    from issueforge.registry import Registry

    repo = make_git_repo(branch="feature", default_branch="trunk")
    assert _add("T", repo).exit_code == 0

    entry = Registry.load().get("T")
    assert entry.default_branch == "trunk"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_unresolvable_default_branch_refused_registry_unchanged(make_git_repo, isolated_state_home):
    """A repo whose remote default branch cannot be established is refused rather than defaulted, registry unchanged.

    technical (contract): a repo with an origin but no refs/remotes/origin/HEAD ->
    `repo add U:<repo>` non-zero exit, empty stdout, no traceback; the state-home byte-snapshot
    is identical before and after; no "main" fallback is recorded (no "U" entry exists).
    """
    repo = make_git_repo(name="u", set_origin_head=False)

    before = _snapshot(isolated_state_home)
    refused = _add("U", repo)
    assert refused.exit_code != 0
    assert refused.stdout == ""
    assert _no_traceback(refused.stderr)
    assert _snapshot(isolated_state_home) == before
    assert "U" not in _aliases()


# --------------------------------------------------------------------------- adapter / framework / reporter


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_unsupported_framework_refused_naming_it_pytest_control_resolves(
    make_git_repo, isolated_state_home
):
    """A repo whose declared framework has no installed adapter is refused at registration, naming that framework; a pytest repo resolves the pytest adapter.

    technical (contract): a committed config framework = "zigtest" / baseline = ["zig", "test"]
    -> `repo add Z:<repo>` non-zero exit whose stderr contains "zigtest", empty stdout, and the
    state home is byte-identical; a committed framework = "pytest" repo -> `repo add P:<repo>`
    exit 0 and Registry.load().get("P").adapter == "pytest".
    """
    from issueforge.registry import Registry

    zig = make_git_repo(name="z", config='baseline = ["zig", "test"]\nframework = "zigtest"\n')

    before = _snapshot(isolated_state_home)
    refused = _add("Z", zig)
    assert refused.exit_code != 0
    assert "zigtest" in refused.stderr.lower()
    assert refused.stdout == ""
    assert _snapshot(isolated_state_home) == before

    py = make_git_repo(name="py", config=PYTEST_CONFIG)
    assert _add("P", py).exit_code == 0
    assert Registry.load().get("P").adapter == "pytest"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_missing_framework_field_refused(make_git_repo, isolated_state_home):
    """A committed config with no framework field is refused: registration cannot resolve an adapter without one.

    technical (contract): a repo whose committed .issueforge.toml has baseline = ["pytest"] but
    no framework -> `repo add M:<repo>` non-zero exit whose stderr names framework, empty
    stdout; the state home is byte-identical.
    """
    no_fw = make_git_repo(name="m", config='baseline = ["pytest"]\n')

    before = _snapshot(isolated_state_home)
    refused = _add("M", no_fw)
    assert refused.exit_code != 0
    assert "framework" in refused.stderr.lower()
    assert refused.stdout == ""
    assert _snapshot(isolated_state_home) == before


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_reporter_defaults_to_framework_when_omitted(make_git_repo):
    """When the config omits reporter, it defaults to the framework for adapter resolution.

    technical (contract): a committed config framework = "pytest" with no reporter ->
    `repo add P:<repo>` exit 0 and Registry.load().get("P").reporter == "pytest".
    """
    from issueforge.registry import Registry

    repo = make_git_repo(config=PYTEST_CONFIG)
    assert _add("P", repo).exit_code == 0
    assert Registry.load().get("P").reporter == "pytest"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_unknown_reporter_refused_naming_pair(make_git_repo, isolated_state_home):
    """A known framework with a reporter that has no adapter is refused, naming the (framework, reporter) pair.

    technical (contract): a committed config framework = "pytest", reporter = "nosuch" ->
    `repo add R:<repo>` non-zero exit whose stderr contains "pytest" and "nosuch", empty
    stdout; the state home is byte-identical.
    """
    repo = make_git_repo(
        config='baseline = ["pytest"]\nframework = "pytest"\nreporter = "nosuch"\n'
    )

    before = _snapshot(isolated_state_home)
    refused = _add("R", repo)
    assert refused.exit_code != 0
    assert "pytest" in refused.stderr.lower() and "nosuch" in refused.stderr.lower()
    assert refused.stdout == ""
    assert _snapshot(isolated_state_home) == before


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_config_loader_parses_framework_and_reporter_fields(make_git_repo):
    """The committed-config loader (config.py) exposes framework and reporter; both are absent (None) when omitted.

    technical (contract): load_config on a committed config with framework = "pytest",
    reporter = "custom" -> config.framework == "pytest" and config.reporter == "custom";
    load_config on a committed baseline = ["pytest"] only -> config.framework is None and
    config.reporter is None.
    """
    from issueforge.config import load_config

    declared = make_git_repo(
        name="d", config='baseline = ["pytest"]\nframework = "pytest"\nreporter = "custom"\n'
    )
    cfg = load_config(declared)
    assert cfg.framework == "pytest"
    assert cfg.reporter == "custom"

    bare = make_git_repo(name="bare", config='baseline = ["pytest"]\n')
    bare_cfg = load_config(bare)
    assert bare_cfg.framework is None
    assert bare_cfg.reporter is None


# --------------------------------------------------------------------------- committed-object fidelity


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_registration_reads_committed_config_not_dirty_worktree(make_git_repo):
    """Registration records the committed config, ignoring divergent dirty working-tree content.

    technical (contract): a repo whose committed .issueforge.toml is
    baseline = ["from-commit"] / framework = "pytest", with the working tree then edited to
    baseline = ["from-worktree"] / framework = "zigtest" -> after `repo add D:<repo>` (exit 0),
    Registry.load().get("D") has baseline == ["from-commit"] and framework == "pytest".
    """
    from issueforge.registry import Registry

    repo = make_git_repo(
        name="d",
        config='baseline = ["from-commit"]\nframework = "pytest"\n',
        dirty='baseline = ["from-worktree"]\nframework = "zigtest"\n',
    )
    assert _add("D", repo).exit_code == 0

    entry = Registry.load().get("D")
    assert entry.baseline == ["from-commit"]
    assert entry.framework == "pytest"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_untracked_only_config_refused_registry_unchanged(make_git_repo, isolated_state_home):
    """A .issueforge.toml present only as untracked working-tree content is rejected at registration.

    technical (contract): a repo whose valid .issueforge.toml is written but never committed ->
    `repo add U:<repo>` non-zero exit whose stderr says no committed config, empty stdout; the
    state home is byte-identical. A committed-config control registers (exit 0).
    """
    untracked = make_git_repo(name="u", commit_config=False)

    before = _snapshot(isolated_state_home)
    refused = _add("U", untracked)
    assert refused.exit_code != 0
    assert "commit" in refused.stderr.lower()
    assert refused.stdout == ""
    assert _snapshot(isolated_state_home) == before

    control = make_git_repo(name="c")
    assert _add("C", control).exit_code == 0


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_missing_baseline_refused_at_registration_clean_channel(make_git_repo, isolated_state_home):
    """A committed config with no baseline command is refused at registration (US-4.1), not left as a runtime surprise.

    technical (contract): a repo whose committed .issueforge.toml declares framework = "pytest"
    but no baseline -> `repo add M:<repo>` non-zero exit whose stderr names baseline as
    missing/required, empty stdout, no traceback; the state home is byte-identical. A control
    with a baseline registers (exit 0).
    """
    no_baseline = make_git_repo(name="m", config='framework = "pytest"\n')

    before = _snapshot(isolated_state_home)
    refused = _add("M", no_baseline)
    assert refused.exit_code != 0
    assert "baseline" in refused.stderr.lower()
    assert refused.stdout == ""
    assert _no_traceback(refused.stderr)
    assert _snapshot(isolated_state_home) == before

    control = make_git_repo(name="c")
    assert _add("C", control).exit_code == 0


# --------------------------------------------------------------------------- behavioral guards


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_registration_invokes_the_resolved_adapter_probe(make_git_repo, monkeypatch):
    """Registration actually runs the resolved adapter's probe (US-1.5), it does not merely look the adapter up.

    technical (contract): with PytestAdapter.probe wrapped by a spy, a successful
    `repo add P:<repo>` on a pytest repo -> the spy recorded at least one call.
    """
    from issueforge.adapters.pytest_adapter import PytestAdapter

    calls: list[int] = []
    original = PytestAdapter.probe

    def spy(self, toolchain=None):
        calls.append(1)
        return original(self, toolchain)

    monkeypatch.setattr(PytestAdapter, "probe", spy)

    repo = make_git_repo(config=PYTEST_CONFIG)
    assert _add("P", repo).exit_code == 0
    assert calls, "registration must invoke the resolved adapter's probe"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_registration_persists_through_the_write_seam(make_git_repo, monkeypatch):
    """The registry is persisted through the S25 WriteSeam, not a raw file write.

    technical (contract): with WriteSeam.write_text wrapped by a spy, a successful
    `repo add DandD:<repo>` -> the spy recorded a write whose resolved target equals
    Registry.registry_path().resolve().
    """
    from issueforge.io import WriteSeam
    from issueforge.registry import Registry

    targets: list[Path] = []
    original = WriteSeam.write_text

    def spy(self, path, data, encoding="utf-8"):
        targets.append(Path(path).resolve())
        return original(self, path, data, encoding)

    monkeypatch.setattr(WriteSeam, "write_text", spy)

    repo = make_git_repo()
    assert _add("DandD", repo).exit_code == 0
    assert Registry.registry_path().resolve() in targets


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_registration_never_invokes_git_clone(make_git_repo, monkeypatch):
    """No registration path shells out to `git clone`: IssueForge resolves existing clones, never creating them.

    technical (contract): with subprocess.run wrapped by a spy during a successful
    `repo add DandD:<repo>`, no captured git argv has "clone" as its subcommand.
    """
    repo = make_git_repo()  # built before the spy is installed

    seen: list[list[str]] = []
    original = subprocess.run

    def spy(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)):
            seen.append(list(cmd))
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    assert _add("DandD", repo).exit_code == 0

    git_calls = [c for c in seen if c and c[0] == "git"]
    assert git_calls, "registration is expected to inspect the repo via git"
    assert all("clone" not in c for c in git_calls)


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_never_auto_registers_repositories_on_disk(make_git_repo, tmp_path):
    """IssueForge never auto-discovers or auto-registers repositories: only an explicit repo add adds an entry.

    technical (contract): with two valid unregistered repos on disk and nothing added,
    `repo list` (exit 0) records no entries and Registry.load().entries() == []; an unknown
    alias lookup raises rather than discovering a nearby repo.
    """
    from issueforge.registry import Registry

    make_git_repo(name="one", parent=tmp_path / "repos")
    make_git_repo(name="two", parent=tmp_path / "repos")

    listed = _runner().invoke(app, ["repo", "list"])
    assert listed.exit_code == 0
    assert Registry.load().entries() == []
    with pytest.raises(Exception):
        Registry.load().get("one")


# --------------------------------------------------------------------------- registry location / persistence


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_registry_file_lives_under_state_root_and_is_not_markdown(
    make_git_repo, isolated_state_home
):
    """The canonical registry file lives under IssueForge's env-overridable state root and is not a Markdown file.

    technical (contract): after `repo add DandD:<repo>`, Registry.registry_path() exists, is
    located under paths.state_root() (== ISSUEFORGE_STATE_HOME here), and its suffix is not
    ".md".
    """
    from issueforge.paths import state_root
    from issueforge.registry import Registry

    repo = make_git_repo()
    assert _add("DandD", repo).exit_code == 0

    path = Registry.registry_path().resolve()
    assert path.exists()
    root = Path(state_root()).resolve()
    assert root == isolated_state_home.resolve() or isolated_state_home.resolve() in path.parents
    assert root in path.parents or root == path.parent
    assert path.suffix != ".md"


@pytest.mark.xfail(strict=True, reason="PENDING (#7)")
def test_registration_survives_a_fresh_process_reload(make_git_repo, isolated_state_home):
    """A registered entry is persisted to disk and reloads intact in a fresh process, not just in memory.

    technical (contract): after `repo add DandD:<repo>` in this process, a separate Python
    process with the same ISSUEFORGE_STATE_HOME that does Registry.load().get("DandD") reads
    slug exactly "Owner/DandD", default_branch exactly "main", and baseline exactly ["pytest"]
    (exact, not substring — "Owner/DandD-old"/"mainline"/["pytest2"] must NOT satisfy it).
    """
    repo = make_git_repo(origin="git@github.com:Owner/DandD.git", branch="main")
    assert _add("DandD", repo).exit_code == 0

    code = (
        "from issueforge.registry import Registry\n"
        "e = Registry.load().get('DandD')\n"
        "print('slug=' + e.slug)\n"
        "print('branch=' + e.default_branch)\n"
        "print('baseline=' + repr(e.baseline))\n"
    )
    env = {**os.environ, "ISSUEFORGE_STATE_HOME": str(isolated_state_home)}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "slug=Owner/DandD" in lines
    assert "branch=main" in lines
    assert "baseline=['pytest']" in lines
