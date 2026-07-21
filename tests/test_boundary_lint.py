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


# --- issue #40: receiver-aware WriteSeam.write_text exemption (Option A) ------
# The write-surface heuristic flags ANY `.write_text` outside io.py, including a call
# to the sanctioned WriteSeam.write_text — so engine modules that legitimately write
# through the seam are forced into `getattr(seam, "write_text")(...)` dispatch to evade
# the lint (process.py:97). Option A adds a RECEIVER-AWARE exemption: allow `.write_text`
# when the receiver is provably a LOCAL bound to WriteSeam()/io.WriteSeam() in the CURRENT
# scope. The exemption must be receiver-PRECISE and never over-broaden; the guard tests
# below pin every shape it must still flag. Assertions match the FULL emitted rule identity
# (`write-surface: .write_text() outside the IO seam`), not a bare "write_text" substring, so
# an unrelated violation cannot satisfy a guard and hide a regression.

_WRITE_TEXT_RULE = "write-surface: .write_text() outside the IO seam"


def test_class6_process_style_plain_seam_write_is_clean(tmp_path: Path) -> None:
    """(#40) The plain seam.write_text(target, payload) form process.run wants (no getattr) passes.

    Provable-binding form 1: a bare local `seam = WriteSeam()` (Assign) in realistic surroundings.
    """
    code = (
        "from pathlib import Path\n"
        "from issueforge.io import WriteSeam\n"
        "from issueforge.paths import state_root\n"
        "def emit_invocation(payload):\n"
        "    seam = WriteSeam()\n"
        "    directory = Path(state_root()) / 'invocations'\n"
        "    target = directory / 'invocation.json'\n"
        "    seam.write_text(target, payload)\n"
    )
    assert lint(tmp_path, code) == []


def test_class6_write_text_on_qualified_writeseam_is_exempt(tmp_path: Path) -> None:
    """(#40) Provable-binding form 2: a receiver bound to the qualified io.WriteSeam() constructor."""
    code = (
        "from issueforge import io\n"
        "def emit(target, payload):\n"
        "    seam = io.WriteSeam()\n"
        "    seam.write_text(target, payload)\n"
    )
    assert lint(tmp_path, code) == []


def test_class6_write_text_on_annotated_writeseam_is_exempt(tmp_path: Path) -> None:
    """(#40) Provable-binding form 3: an annotated construction `seam: WriteSeam = WriteSeam()` (AnnAssign).

    Distinct AST from the bare Assign in form 1; the binding-type tracking is analogous to the
    command-scope machinery, which already tracks annotated bindings (visit_AnnAssign), so the
    exemption must cover this shape too — an implementation that exempts the bare local but not
    the annotated one would be inconsistent.
    """
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target, payload):\n"
        "    seam: WriteSeam = WriteSeam()\n"
        "    seam.write_text(target, payload)\n"
    )
    assert lint(tmp_path, code) == []


def test_class6_raw_path_write_text_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A raw path.write_text stays flagged — the exemption is receiver-precise."""
    v = lint(tmp_path, "def f(path, payload):\n    path.write_text(payload)\n")
    assert any(_WRITE_TEXT_RULE in x for x in v)


def test_class6_seam_named_non_writeseam_receiver_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A receiver merely NAMED 'seam' but not bound to WriteSeam() is NOT exempt.

    A by-name exemption would let these real raw writes slip through and defeat the boundary.
    Both a parameter named `seam` and a `seam = path` alias rebind must stay flagged.
    """
    param = lint(tmp_path, "def f(seam, target):\n    seam.write_text(target, 'x')\n")
    assert any(_WRITE_TEXT_RULE in x for x in param)
    rebind = lint(
        tmp_path, "def f(path, target):\n    seam = path\n    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in rebind)


def test_class6_seam_from_call_result_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A receiver assigned from an arbitrary CALL is not a provable WriteSeam() — stays flagged.

    Only a literal WriteSeam()/io.WriteSeam() construction is provable; `get_seam()` could return
    anything, so the boundary must not trust it.
    """
    code = (
        "def make():\n    return object()\n"
        "def emit(target):\n"
        "    seam = make()\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_seam_stale_trust_reassignment_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) Rebinding a trusted seam to a non-WriteSeam value CLEARS trust — the later write stays flagged."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(path, target):\n"
        "    seam = WriteSeam()\n"
        "    seam = path\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_seam_augassign_clobber_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) An aug-assign to a trusted seam clobbers the WriteSeam binding — the later write stays flagged."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(path, target):\n"
        "    seam = WriteSeam()\n"
        "    seam += path\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_module_global_seam_not_local_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A MODULE-GLOBAL `seam = WriteSeam()` referenced inside a function is not a local binding.

    The exemption is scoped to a provable LOCAL in the current scope, matching the actual motivating
    case (process.py binds `seam` as a local inside emit_invocation). Keeping module-global out of
    the exemption keeps the write boundary as tight as possible: the global write stays flagged.
    """
    code = (
        "from issueforge.io import WriteSeam\n"
        "seam = WriteSeam()\n"
        "def emit(target):\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_shadowed_seam_param_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) An outer trusted `seam = WriteSeam()` must not exempt an inner function whose PARAMETER is `seam`.

    The inner parameter shadows the outer trusted binding, so the inner write is on an untrusted
    receiver and stays flagged (cf. test_class2_lexically_shadowed_command_annotation_is_flagged).
    """
    code = (
        "from issueforge.io import WriteSeam\n"
        "def outer(target):\n"
        "    seam = WriteSeam()\n"
        "    def inner(seam):\n"
        "        seam.write_text(target, 'x')\n"
        "    return inner\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_rebound_write_text_attr_still_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) `write = path.write_text; write(...)` stays flagged (cf. test_class6_rebound_write_apis)."""
    code = "def f(path):\n    write = path.write_text\n    write('x')\n"
    v = lint(tmp_path, code)
    assert any("write-surface: write() outside the IO seam" in x for x in v)


# --- issue #40 (round 2): binder-precision guards from the Codex cross-review -----------------
# Seam trust is granted ONLY by a provable WriteSeam()/io.WriteSeam() construction bound to a
# FUNCTION-LOCAL name, and is CLEARED by every other binder of that name. These pin the shapes
# the first eight guards did not cover: module scope, exception handlers, destructuring, loop and
# with targets, and lambda/comprehension shadowing. All stay flagged.


def test_class6_module_level_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A MODULE-LEVEL `seam = WriteSeam()` then a direct module-level write is NOT local.

    Option A exempts a provable LOCAL in a function body; the module scope is not a function scope,
    so a module-level seam.write_text stays flagged (cf. the module-global-used-in-function guard).
    """
    code = (
        "from issueforge.io import WriteSeam\nseam = WriteSeam()\nseam.write_text('target', 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_except_as_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) `except ... as seam` binds an exception, not a WriteSeam — trust is cleared."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target):\n"
        "    seam = WriteSeam()\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as seam:\n"
        "        seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_destructured_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A tuple/list unpack is a non-simple binder — it clears trust even if a leaf is WriteSeam()."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target, other):\n"
        "    seam, extra = WriteSeam(), other\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_for_target_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A `for seam in ...` loop target rebinds seam to a loop item — trust is cleared."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target, seams):\n"
        "    seam = WriteSeam()\n"
        "    for seam in seams:\n"
        "        seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_with_target_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A `with ctx as seam` context binding rebinds seam — trust is cleared."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target, ctx):\n"
        "    seam = WriteSeam()\n"
        "    with ctx as seam:\n"
        "        seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_walrus_bound_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A walrus `seam := <non-WriteSeam>` binding is non-simple — trust is cleared."""
    code = (
        "def emit(target, factory):\n"
        "    if (seam := factory()):\n"
        "        seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_lambda_shadowed_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A lambda PARAMETER named seam shadows an outer trusted seam — the body is not exempt."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def outer(target):\n"
        "    seam = WriteSeam()\n"
        "    return lambda seam: seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_comprehension_shadowed_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A comprehension TARGET named seam shadows an outer trusted seam — the body is not exempt."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target, seams):\n"
        "    seam = WriteSeam()\n"
        "    return [seam.write_text(target, 'x') for seam in seams]\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


# --- issue #40 (round 3): unconditional-binding guards from the Codex confirmation ------------
# Trust is granted ONLY for an UNCONDITIONAL binding — one not nested under an if/try/for/while/
# with body, where the WriteSeam value is not guaranteed at the write site — and never for a name
# declared global/nonlocal. These pin the three shapes round 2 still exempted.


def test_class6_conditional_if_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A `seam = WriteSeam()` under an `if` is conditional — not provably a WriteSeam later."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target, flag):\n"
        "    if flag:\n"
        "        seam = WriteSeam()\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_try_body_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A `seam = WriteSeam()` inside a `try` body is conditional — trust is not granted."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target):\n"
        "    try:\n"
        "        seam = WriteSeam()\n"
        "    finally:\n"
        "        pass\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))


def test_class6_global_declared_seam_write_text_flagged_guard(tmp_path: Path) -> None:
    """(#40 guard) A `global seam; seam = WriteSeam()` names the module global, not a local — flagged."""
    code = (
        "from issueforge.io import WriteSeam\n"
        "def emit(target):\n"
        "    global seam\n"
        "    seam = WriteSeam()\n"
        "    seam.write_text(target, 'x')\n"
    )
    assert any(_WRITE_TEXT_RULE in x for x in lint(tmp_path, code))
