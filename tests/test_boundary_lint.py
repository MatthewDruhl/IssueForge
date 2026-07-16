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
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "def go(config):\n"
        "    command: Command = Command.from_config(config.baseline_cmd, cwd=config.worktree)\n"
        "    subprocess.run(command, cwd=command.cwd)\n"
    )
    assert lint(tmp_path, code) == []


def test_class2_unproven_command_annotation_is_flagged(tmp_path: Path) -> None:
    code = "import subprocess\ndef go(command: Command):\n    subprocess.run(command)\n"
    assert any("typed Command" in item for item in lint(tmp_path, code))


def test_class2_shadowed_command_annotation_is_flagged(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "Command = list[str]\n"
        "def go(command: Command):\n    subprocess.run(command)\n"
    )
    assert any("typed Command" in item for item in lint(tmp_path, code))


def test_class2_lexically_shadowed_command_annotation_is_flagged(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "def outer(Command):\n"
        "    def go(command: Command):\n        subprocess.run(command)\n"
    )
    assert any("typed Command" in item for item in lint(tmp_path, code))


def test_class2_qualified_command_annotation_is_clean(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "import issueforge.boundary\n"
        "def go(config):\n"
        "    command: issueforge.boundary.Command = "
        "issueforge.boundary.Command.from_config(config.baseline_cmd, cwd=config.worktree)\n"
        "    subprocess.run(command, cwd=command.cwd)\n"
    )
    assert lint(tmp_path, code) == []


def test_class2_typed_command_requires_provenance_cwd_and_survives_no_reassignment(
    tmp_path: Path,
) -> None:
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "def go(config):\n"
        "    command: Command = Command.from_config(config.baseline_cmd, cwd=config.worktree)\n"
        "    command = ['git', 'status']\n"
        "    subprocess.run(command)\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert "list literal or typed Command" in violations


def test_class2_typed_command_rejects_unconstrained_output(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "def go(config, output):\n"
        "    command: Command = Command.from_config(config.baseline_cmd, cwd=config.worktree)\n"
        "    subprocess.run(command, cwd=command.cwd, stdout=output)\n"
    )
    assert any("stdout is not constrained" in item for item in lint(tmp_path, code))


def test_class2_imported_getoutput_and_shadowed_run(tmp_path: Path) -> None:
    code = (
        "from subprocess import getoutput, run\n"
        "getoutput('pytest')\n"
        "def clean(run):\n    run(['pytest'])\n"
    )
    violations = lint(tmp_path, code)
    assert any("shell string" in item for item in violations)
    assert not any(item.startswith("mod.py:4:") for item in violations)


def test_class2_command_loop_reassignment_is_not_trusted(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "def go(config, commands):\n"
        "    command: Command = Command.from_config(config.baseline_cmd, cwd=config.worktree)\n"
        "    for command in commands:\n        pass\n"
        "    subprocess.run(command)\n"
    )
    assert any("typed Command" in item for item in lint(tmp_path, code))


def test_rebound_protected_modules_are_checked(tmp_path: Path) -> None:
    code = (
        "import os, subprocess\n"
        "environment = os\n"
        "processes = subprocess\n"
        "a = environment.environ['MARVIN_ROOT']\n"
        "processes.run(['pytest'])\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert "MARVIN_ROOT" in violations
    assert "hardcoded executable 'pytest'" in violations


def test_conditional_reassignment_cannot_clear_protected_binding(tmp_path: Path) -> None:
    code = (
        "import builtins\n"
        "writer = builtins.open\n"
        "if enabled:\n    writer = safe_writer\n"
        "writer('outside', 'w')\n"
    )
    assert any("open(mode='w')" in item for item in lint(tmp_path, code))


def test_class2_hardcoded_executable_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run(['pytest', '-q'])\n")
    assert any("argv" in x and "pytest" in x for x in v)


def test_class2_shell_true_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "import subprocess\nsubprocess.run(['git', 'x'], shell=True)\n")
    assert any("shell must be literal False" in x for x in v)


def test_class2_dynamic_shell_flagged(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "from issueforge.boundary import Command\n"
        "def f(config, use_shell):\n"
        "    command: Command = Command.from_config(config.baseline_cmd, cwd=config.worktree)\n"
        "    subprocess.run(command, cwd=command.cwd, shell=use_shell)\n"
    )
    assert any("shell must be literal False" in item for item in lint(tmp_path, code))


def test_class2_positional_popen_shell_flagged(tmp_path: Path) -> None:
    code = (
        "import subprocess\n"
        "subprocess.Popen(['git'], -1, None, None, None, None, None, True, True)\n"
    )
    assert any("positional shell" in item for item in lint(tmp_path, code))


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
    assert "shell must be literal False" in joined  # argv
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


def test_class1_aliased_dunder_import_nonliteral_flagged(tmp_path: Path) -> None:
    code = "from builtins import __import__ as load\ndef f(name):\n    return load(name)\n"
    assert any("__import__" in item for item in lint(tmp_path, code))


def test_class1_rebound_dunder_import_nonliteral_flagged(tmp_path: Path) -> None:
    code = "import builtins\nload = builtins.__import__\ndef f(name):\n    return load(name)\n"
    assert any("__import__" in item for item in lint(tmp_path, code))


def test_class2_untyped_argv_is_flagged(tmp_path: Path) -> None:
    code = "import subprocess\ndef f(exe):\n    subprocess.run([exe, '-x'])\n"
    assert any("engine-owned literal" in item for item in lint(tmp_path, code))


def test_class2_subprocess_alias_keyword_args_and_local_list_are_flagged(tmp_path: Path) -> None:
    code = (
        "import subprocess as sp\n"
        "def f():\n"
        "    command = ['pytest', '-q']\n"
        "    sp.run(args=command, shell=True)\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert "list literal or typed Command" in violations
    assert "shell must be literal False" in violations


def test_class2_rebound_subprocess_call_is_checked(tmp_path: Path) -> None:
    code = "import subprocess\nexecute = subprocess.run\nexecute(['pytest'])\n"
    assert any("hardcoded executable 'pytest'" in item for item in lint(tmp_path, code))


def test_class2_imported_popen_alias_is_checked(tmp_path: Path) -> None:
    code = "from subprocess import Popen as Process\nProcess(args=['pytest'])\n"
    assert any("hardcoded executable 'pytest'" in item for item in lint(tmp_path, code))


def test_class1_sys_path_import_alias_flagged(tmp_path: Path) -> None:
    code = "import sys as system\nsystem.path.insert(0, '/sibling')\n"
    assert any("sys.path.insert" in item for item in lint(tmp_path, code))


def test_class3_rebound_environment_apis_flagged(tmp_path: Path) -> None:
    code = (
        "import os\n"
        "environment = os.environ\n"
        "read_environment = os.getenv\n"
        "a = environment['MARVIN_ROOT']\n"
        "b = read_environment('AGENT_LOGS_DIR')\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert "MARVIN_ROOT" in violations
    assert "AGENT_LOGS_DIR" in violations


def test_class3_os_import_alias_flagged(tmp_path: Path) -> None:
    code = "import os as operating_system\na = operating_system.environ['MARVIN_ROOT']\n"
    assert any("MARVIN_ROOT" in item for item in lint(tmp_path, code))


def test_class3_annotated_environment_rebinding_flagged(tmp_path: Path) -> None:
    code = "import os\nenvironment: object = os.environ\na = environment['MARVIN_ROOT']\n"
    assert any("MARVIN_ROOT" in item for item in lint(tmp_path, code))


def test_class6_module_and_open_import_aliases_flagged(tmp_path: Path) -> None:
    code = (
        "import os as operating_system\n"
        "import shutil as sh\n"
        "from builtins import open as writer\n"
        "def f(path):\n"
        "    writer(path / 'new.txt', 'w')\n"
        "    operating_system.makedirs(path / 'dir')\n"
        "    sh.rmtree(path / 'old')\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert all(name in violations for name in ("open", "makedirs", "rmtree"))


def test_class6_unresolved_open_mode_flagged(tmp_path: Path) -> None:
    code = "def f(path):\n    mode = 'w'\n    open(path, mode)\n"
    assert any("mode=unresolved" in item for item in lint(tmp_path, code))


def test_class6_open_kwargs_mode_is_unresolved(tmp_path: Path) -> None:
    code = "def f(path, options):\n    open(path, **options)\n"
    assert any("open(**kwargs)" in item for item in lint(tmp_path, code))


def test_class6_aliased_read_only_open_is_clean(tmp_path: Path) -> None:
    code = (
        "from builtins import open as reader\ndef f(path):\n    return reader(path, 'rb').read()\n"
    )
    assert lint(tmp_path, code) == []


def test_class6_io_open_alias_write_is_flagged(tmp_path: Path) -> None:
    code = "from io import open as writer\ndef f(path):\n    writer(path, 'w')\n"
    assert any("open(mode='w')" in item for item in lint(tmp_path, code))


def test_class6_rebound_write_apis_are_flagged(tmp_path: Path) -> None:
    code = (
        "import builtins, io, os, shutil\n"
        "writer = io.open\n"
        "other_writer = builtins.open\n"
        "delete = os.remove\n"
        "remove_tree = shutil.rmtree\n"
        "def f(path):\n"
        "    write = path.write_text\n"
        "    writer(path, 'w')\n"
        "    other_writer(path, 'a')\n"
        "    delete(path)\n"
        "    remove_tree(path)\n"
        "    write('x')\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert violations.count("open(mode=") == 2
    assert all(name in violations for name in ("delete", "rmtree", "write"))


def test_class6_chained_rebinding_path_open_and_rename_are_flagged(tmp_path: Path) -> None:
    code = (
        "import os\n"
        "first = second = os.remove\n"
        "def f(path, target):\n"
        "    move = path.rename\n"
        "    path.open('w')\n"
        "    first(path)\n"
        "    move(target)\n"
    )
    violations = "\n".join(lint(tmp_path, code))
    assert all(name in violations for name in ("open", "first", "move"))


def test_class6_rebound_path_open_is_flagged(tmp_path: Path) -> None:
    code = "def f(path):\n    writer = path.open\n    writer('w')\n"
    assert any("open(mode='w')" in item for item in lint(tmp_path, code))


def test_class4_nested_literal_containers_are_scanned(tmp_path: Path) -> None:
    code = "import pathlib as pl\nROOTS = [pl.Path('/opt/sibling'), {'../outside': ({'safe'},)}]\n"
    violations = "\n".join(lint(tmp_path, code))
    assert "/opt/sibling" in violations
    assert "../outside" in violations


def test_class6_rename_keyword_form_flagged(tmp_path: Path) -> None:
    v = lint(tmp_path, "def f(p, q):\n    p.rename(target=q)\n")
    assert any("rename" in x for x in v)
