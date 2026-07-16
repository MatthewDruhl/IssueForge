"""Acceptance tests for the six-class AST boundary lint (issue #5, slice S25)."""

from pathlib import Path

from typer.testing import CliRunner

from issueforge.boundary import check_source
from issueforge.cli import app

# Declared third-party roots for import-rule tests (mirrors pyproject dependencies).
DEPS = frozenset({"typer", "textual"})


def lint(tmp_path: Path, code: str, name: str = "mod.py", deps=DEPS) -> list[str]:
    """Write `code` to a fixture module and return the boundary violations for it."""
    path = tmp_path / name
    path.write_text(code)
    return check_source(path, deps=deps)


def run(args: list[str]):
    return CliRunner().invoke(app, args)


def test_clean_module_has_no_violations(tmp_path: Path) -> None:
    """Tracer: a module using only stdlib + declared deps + issueforge is clean."""
    code = (
        "import json\n"
        "import typer\n"
        "from issueforge.paths import state_root\n"
        "\n"
        "def go() -> str:\n"
        "    return json.dumps({'root': str(state_root())})\n"
    )
    assert lint(tmp_path, code) == []


def test_class1_foreign_import_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import marvin_sibling\n")
    assert len(v) == 1
    assert "import" in v[0] and "marvin_sibling" in v[0]


def test_class1_sys_path_insert_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import sys\nsys.path.insert(0, '/x')\n")
    assert any("sys.path" in x for x in v)


def test_class1_spec_from_file_location_flagged(tmp_path: Path) -> None:
    code = "import importlib.util\nimportlib.util.spec_from_file_location('m', '/p')\n"
    v = lint(tmp_path, code)
    assert any("spec_from_file_location" in x for x in v)


def test_class2_git_literal_argv_is_clean(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run(['git', 'fetch'])\n")
    assert v == []


def test_class2_config_read_argv_is_clean(tmp_path: Path) -> None:
    # argv[0] read from config (non-literal) is allowed: provenance, not identity.
    code = "import subprocess\ndef go(cfg):\n    subprocess.run(cfg.baseline_cmd)\n"
    assert lint(tmp_path, code) == []


def test_class2_hardcoded_executable_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run(['pytest', '-q'])\n")
    assert any("argv" in x and "pytest" in x for x in v)


def test_class2_shell_true_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run(['git', 'x'], shell=True)\n")
    assert any("shell=True" in x for x in v)


def test_class2_marvin_and_home_argv_elements_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run(['git', 'clone', '~/marvin'])\n")
    assert any("argv" in x for x in v)


def test_class2_string_argv_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run('git fetch')\n")
    assert any("argv" in x and "list" in x for x in v)


def test_class3_marvin_env_subscript_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import os\nx = os.environ['MARVIN_ROOT']\n")
    assert any("env" in x and "MARVIN_ROOT" in x for x in v)


def test_class3_agent_logs_dir_get_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import os\nx = os.environ.get('AGENT_LOGS_DIR')\n")
    assert any("AGENT_LOGS_DIR" in x for x in v)


def test_class3_getenv_marvin_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import os\nx = os.getenv('MARVIN_PIPELINE_ROOT')\n")
    assert any("MARVIN_PIPELINE_ROOT" in x for x in v)


def test_class3_issueforge_env_is_clean(tmp_path: Path) -> None:
    v = lint(tmp_path, "import os\nx = os.environ.get('ISSUEFORGE_STATE_HOME')\n")
    assert v == []


def test_class5_marvin_state_path_literals_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "A = 'state/projects.md'\nB = 'model-rates.json'\nC = '/Users/x/y'\n")
    joined = "\n".join(v)
    assert "state/projects.md" in joined
    assert "model-rates.json" in joined
    assert "/Users/x/y" in joined


def test_class5_artifact_type_patterns_are_clean(tmp_path: Path) -> None:
    # Narrowed denylist: skills//context//SKILL.md are artifact patterns, not coupling.
    assert lint(tmp_path, "GLOBS = {'skills/': '**/SKILL.md', 'context/': '*.md'}\n") == []


def test_class5_boundary_module_is_exempt(tmp_path: Path) -> None:
    # boundary.py holds the denylist tokens as its own data.
    v = lint(tmp_path, "DENY = ('state/', 'projects.md', '/Users/')\n", name="boundary.py")
    assert v == []


def test_class4_marvin_project_default_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "def run(project='marvin'):\n    return project\n")
    assert any("default" in x and "marvin" in x for x in v)


def test_class4_argparse_default_flagged(tmp_path: Path) -> None:
    code = "def build(p):\n    p.add_argument('--project', default='marvin')\n"
    v = lint(tmp_path, code)
    assert any("default" in x for x in v)


def test_class4_absolute_and_home_constants_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "RATES = '/opt/rates.json'\nHOME = '~/sibling'\n")
    assert any("/opt/rates.json" in x for x in v)
    assert any("~/sibling" in x for x in v)


def test_class4_ordinary_defaults_clean(tmp_path: Path) -> None:
    code = "NAME = 'issueforge'\nTIMEOUT = 30\nREL = 'docs/x'\ndef f(mode='q'):\n    return mode\n"
    assert lint(tmp_path, code) == []


def test_class6_write_surface_flagged(tmp_path: Path) -> None:
    code = (
        "import os, shutil, tempfile\n"
        "def go(p, d):\n"
        "    open('x', 'w')\n"
        "    p.write_text('a')\n"
        "    d.mkdir()\n"
        "    os.makedirs('y')\n"
        "    shutil.rmtree('z')\n"
        "    tempfile.mkstemp()\n"
    )
    v = lint(tmp_path, code)
    joined = "\n".join(v)
    assert all(
        t in joined for t in ["open", "write_text", "mkdir", "makedirs", "rmtree", "mkstemp"]
    )


def test_class6_reads_and_str_methods_clean(tmp_path: Path) -> None:
    code = "def go(p):\n    open('x')\n    return p.read_text() + 'ab'.replace('a', 'b')\n"
    assert lint(tmp_path, code) == []


def test_class6_seam_module_is_exempt(tmp_path: Path) -> None:
    v = lint(tmp_path, "def w(p):\n    p.write_text('a')\n    open('x', 'w')\n", name="io.py")
    assert v == []


def test_class0_dunder_file_outside_paths_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "from pathlib import Path\nROOT = Path(__file__).parent\n")
    assert any("__file__" in x for x in v)


def test_class0_dunder_file_in_paths_module_clean(tmp_path: Path) -> None:
    v = lint(tmp_path, "from pathlib import Path\nROOT = Path(__file__).parent\n", name="paths.py")
    assert v == []


def test_reports_every_violation_no_fail_fast(tmp_path: Path) -> None:
    code = (
        "import marvin_sibling\n"
        "import subprocess\n"
        "def go(p):\n"
        "    subprocess.run(['pytest'], shell=True)\n"
        "    p.write_text('x')\n"
    )
    v = lint(tmp_path, code)
    joined = "\n".join(v)
    assert "marvin_sibling" in joined  # import
    assert "shell=True" in joined  # argv
    assert "pytest" in joined  # argv hardcoded exe
    assert "write_text" in joined  # write surface
    assert len(v) >= 4


def test_cli_lint_boundary_passes_on_real_package() -> None:
    """Dogfood: the real issueforge package passes its own boundary lint."""
    result = run(["lint", "boundary"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "OK"


def test_cli_lint_boundary_fails_on_violation(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import some_marvin_sibling\n")

    result = run(["lint", "boundary", "--root", str(pkg)])

    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "ERROR:" in result.stderr
    assert "bad.py" in result.stderr


def test_ci_runs_boundary_lint_on_pull_requests() -> None:
    """CI-order guarantee: the boundary lint is a required check on every PR."""
    repo_root = Path(__file__).resolve().parents[1]
    workflows = list((repo_root / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no CI workflow found"
    texts = [wf.read_text() for wf in workflows]
    runs_lint = [t for t in texts if "issueforge lint boundary" in t]
    assert runs_lint, "no workflow runs `issueforge lint boundary`"
    assert any("pull_request" in t for t in runs_lint), "boundary lint not wired to pull_request"


def test_class6_path_rename_and_replace_flagged(tmp_path: Path) -> None:
    """Contract lists Path.rename/replace as write surface (1-arg form, not str.replace)."""
    v = lint(tmp_path, "def f(p, q):\n    p.rename(q)\n    p.replace(q)\n")
    assert any("rename" in x for x in v)
    assert any(".replace" in x for x in v)


def test_class6_str_replace_two_args_not_flagged(tmp_path: Path) -> None:
    assert lint(tmp_path, "def f(s):\n    return s.replace('a', 'b')\n") == []


def test_class6_aliased_tempfile_and_shutil_writes_flagged(tmp_path: Path) -> None:
    code = (
        "from tempfile import NamedTemporaryFile\n"
        "from shutil import rmtree\n"
        "def f(d):\n    NamedTemporaryFile()\n    rmtree(d)\n"
    )
    v = lint(tmp_path, code)
    assert any("NamedTemporaryFile" in x for x in v)
    assert any("rmtree" in x for x in v)


def test_class3_aliased_environ_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "from os import environ\nx = environ['MARVIN_ROOT']\n")
    assert any("MARVIN_ROOT" in x for x in v)


def test_class1_dunder_import_nonliteral_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "def f(n):\n    return __import__(n)\n")
    assert any("__import__" in x for x in v)


def test_class2_nonliteral_argv0_in_list_is_allowed(tmp_path: Path) -> None:
    # argv[0] read from config (a Name) inside a list literal is provenance, not identity.
    assert lint(tmp_path, "import subprocess\ndef f(exe):\n    subprocess.run([exe, '-x'])\n") == []


def test_class6_rename_keyword_form_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "def f(p, q):\n    p.rename(target=q)\n")
    assert any("rename" in x for x in v)
