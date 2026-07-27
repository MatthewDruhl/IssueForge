"""IssueForge command-line entry point."""

import sys
from pathlib import Path

import typer

from issueforge.io import WriteSeam

app = typer.Typer(help="Turn GitHub issues into human-approved, TDD-built pull requests.")


def _isatty() -> bool:
    """Is a human actually watching this process's stdin? (#140)

    A module-level seam rather than an inline ``sys.stdin.isatty()`` call: under Typer's
    ``CliRunner`` — which every CLI test uses — stdin is never a tty, so an inline call would make
    the interactive branch untestable. The DEFAULT reports the process's real stdin.
    """
    return sys.stdin.isatty()


audit_app = typer.Typer(help="Build-time source-audit gates (provenance completeness).")
app.add_typer(audit_app, name="audit")


@audit_app.command("check")
def audit_check(
    stage: str,
    manifest: Path = typer.Option(None, help="Path to the extraction manifest."),
    stages_dir: Path = typer.Option(None, help="Directory holding per-stage records."),
    source_root: Path = typer.Option(
        None, help="MARVIN checkout to discover unmanifested candidates (mode 5)."
    ),
) -> None:
    """Validate a stage's provenance record against the extraction manifest."""
    from issueforge.audit import DEFAULT_MANIFEST, DEFAULT_STAGES_DIR, check_stage

    violations = check_stage(
        stage,
        manifest or DEFAULT_MANIFEST,
        stages_dir or DEFAULT_STAGES_DIR,
        source_root,
    )
    if violations:
        for violation in violations:
            typer.echo(f"ERROR: {violation}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


lint_app = typer.Typer(help="Structural boundary lints (build-time gates).")
app.add_typer(lint_app, name="lint")


@lint_app.command("boundary")
def lint_boundary(
    root: Path = typer.Option(None, help="Package root to scan (defaults to issueforge)."),
) -> None:
    """Fail on any code that could write outside IssueForge or reach a sibling checkout."""
    from issueforge.boundary import check_tree, declared_deps, find_pyproject
    from issueforge.paths import package_root

    scan_root = root or package_root()
    pyproject = find_pyproject(scan_root) or find_pyproject(Path.cwd())
    deps = declared_deps(pyproject) if pyproject else frozenset()

    violations = check_tree(scan_root, deps=deps)
    if violations:
        for violation in violations:
            typer.echo(f"ERROR: {violation}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


config_app = typer.Typer(help="Inspect a repository's committed .issueforge.toml.")
app.add_typer(config_app, name="config")


@config_app.command("check")
def config_check(path: Path) -> None:
    """Resolve and print a repo's build plan, or fail loudly naming the offending field."""
    from issueforge.adapters.pytest_adapter import PytestAdapter
    from issueforge.config import ConfigError, load_config

    try:
        config = load_config(path)
    except ConfigError as error:
        for violation in error.violations:
            typer.echo(str(violation), err=True)
        raise typer.Exit(1)

    adapter = PytestAdapter()
    probe = adapter.probe()

    typer.echo(f"adapter: {adapter.framework} (reporter={adapter.reporter})")
    typer.echo(f"reporter_version: {probe.reporter_version}")
    typer.echo(f"capabilities: {probe.capabilities}")
    typer.echo(f"baseline: {config.baseline}")
    typer.echo(f"acceptance: {config.acceptance}")
    typer.echo(f"lint: {config.lint}")
    typer.echo(f"build: {config.build}")
    typer.echo(f"contract_paths: {config.contract_paths}")
    typer.echo(f"sensitive_fields: {config.sensitive_fields}")


provider_app = typer.Typer(help="Inspect and verify AI provider configuration.")
app.add_typer(provider_app, name="provider")


@provider_app.command("check")
def provider_check(
    config: Path = typer.Option(..., "--config", help="Path to a providers TOML file."),
) -> None:
    """Verify the primary provider's CLI is authenticated; fail closed with no fallback launch."""
    import tomllib

    from issueforge import providers
    from issueforge.config import ConfigError, load_roles

    try:
        data = tomllib.loads(config.read_text())
        roles = load_roles(data)
    except ConfigError as error:
        for violation in error.violations:
            typer.echo(str(violation), err=True)
        raise typer.Exit(1) from None

    typer.echo(f"role primary -> provider {roles.primary.name}")
    if roles.secondary is not None:
        typer.echo(f"role secondary -> provider {roles.secondary.name}")

    if not providers.authenticated(roles.primary):
        typer.echo(f"provider {roles.primary.name!r} is not authenticated", err=True)
        raise typer.Exit(1)

    typer.echo("authenticated: primary")


repo_app = typer.Typer(help="Register and list verified local clones.")
app.add_typer(repo_app, name="repo")

# The baseline-env preflight probes with the report-log reporter the adapter injects; a repo whose
# own baseline environment lacks it is a slow, opaque mid-run USAGE_ERROR, so we bound the probe.
_PREFLIGHT_TIMEOUT = 120.0


def preflight_baseline_env(alias: str, path_token: str) -> str | None:
    """Probe the just-registered repo's OWN baseline environment for the report-log reporter (#141).

    A module-level seam (mirrors ``_isatty``) so callers and tests can stub it; the DEFAULT genuinely
    probes. Three outcomes, keyed on running the repo's committed baseline command with the
    ``--report-log`` the pytest adapter injects:

    * PASS — the command ran and the reporter loaded -> return ``None`` (onboard).
    * REJECT — the command RAN and pytest conclusively could not load the reporter (it exits nonzero
      naming the missing plugin) -> return an actionable refusal message the CLI surfaces verbatim.
    * INCONCLUSIVE — the command could not even start (missing interpreter, or a launcher that failed
      before reaching pytest) -> return ``None``; nothing was learned about the reporter, so onboard.

    The contract is the OUTCOME, driven by actually executing the command: two environments that
    differ only in whether the reporter loads are separable only by running them.
    """
    import re
    import uuid

    from issueforge.paths import state_root
    from issueforge.process import run
    from issueforge.registry import Registry

    baseline = list(Registry.load().get(alias).baseline)
    repo_path = Registry.load().get(alias).path
    if not baseline:
        return None  # no baseline command to probe against

    # A fresh report directory OUTSIDE the repo, created through the write seam so pytest-reportlog
    # has a real parent to write into (the report itself is written by the probed subprocess).
    report_dir = Path(state_root()) / "reports" / uuid.uuid4().hex
    report_file = report_dir / "report.jsonl"
    seam = WriteSeam()
    seam.write_text(report_dir / ".if-keep", "")

    argv = [*baseline, "-p", "no:cacheprovider", f"--report-log={report_file}", "--collect-only"]
    try:
        result = run(argv, cwd=repo_path, timeout=_PREFLIGHT_TIMEOUT)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        # The command could not start -> inconclusive; nothing was learned about the reporter.
        return None

    if result.timed_out:
        return None  # inconclusive
    # The reporter genuinely loaded IFF it produced its report file — the truest probe, and exactly
    # what the real run needs. A green, empty, or even failing suite all still WRITE the report when
    # the reporter is present, so onboard on any run that produced one (PASS).
    if report_file.exists():
        return None
    # No report was written. Either the command never reached pytest (INCONCLUSIVE: a launcher that
    # failed before pytest, e.g. a missing --directory) or pytest RAN and conclusively could not load
    # the reporter (REJECT). Only the latter surfaces the reporter as the failure; a launcher error
    # names its own problem (a missing directory) and says nothing about the reporter.
    combined = re.sub(r"[-_]", "", (result.stdout + result.stderr).lower())
    if "reportlog" in combined:
        return (
            f"cannot register {alias}: its baseline environment cannot load the pytest-reportlog "
            f"reporter that IssueForge injects via --report-log. Install pytest-reportlog in the "
            f"environment that runs the baseline command ({' '.join(baseline)}), then try again."
        )
    return None


def _restore_registry(prior: str | None) -> None:
    """Undo a registration the preflight went on to refuse (#141): rewrite the registry to exactly
    the bytes it held before ``register`` ran, so a refused ``repo add`` leaves nothing registered.

    ``register`` persists BEFORE the preflight runs (the preflight must observe the alias already
    registered), so a REJECT has to roll the write back. Done through the sanctioned ``WriteSeam``;
    ``registry.register`` itself stays pure and untouched.
    """
    from issueforge.registry import Registry

    seam = WriteSeam()
    seam.write_text(Registry.registry_path(), prior if prior is not None else '{"entries": []}\n')


@repo_app.command("add")
def repo_add(spec: str = typer.Argument(..., help="ALIAS:PATH of the clone to register.")) -> None:
    """Register a verified existing clone under ALIAS, or refuse loudly on stderr."""
    from issueforge.registry import Registry, RegistryError, register

    alias, sep, path_token = spec.partition(":")
    if not sep or not alias or not path_token:
        typer.echo(f"invalid ALIAS:PATH argument: {spec!r}", err=True)
        raise typer.Exit(1)

    registry_path = Registry.registry_path()
    prior = registry_path.read_text(encoding="utf-8") if registry_path.exists() else None

    try:
        register(alias, path_token)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None

    # Preflight the baseline environment AFTER register (register stays pure; the seam observes the
    # alias already registered). A non-empty message is an actionable refusal: roll the registration
    # back and surface the seam's own words, so onboarding fails loud here instead of hours later.
    message = preflight_baseline_env(alias, path_token)
    if message:
        _restore_registry(prior)
        typer.echo(message, err=True)
        raise typer.Exit(1)

    typer.echo(f"registered {alias}")


@repo_app.command("list")
def repo_list() -> None:
    """Print each registered clone: alias, absolute path, slug, default branch, baseline, adapter."""
    from issueforge.registry import Registry, RegistryError

    try:
        entries = Registry.load().entries()
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None

    for entry in entries:
        baseline = " ".join(entry.baseline)
        typer.echo(
            f"{entry.alias}\t{entry.path}\t{entry.slug}\t{entry.default_branch}\t"
            f"{baseline}\t{entry.adapter}"
        )


@app.command()
def run(
    spec: str = typer.Argument(..., help="ALIAS#N of the issue to run."),
    scope: list[str] = typer.Option(
        None,
        "--scope",
        help="Approved write-scope path (repeat per path). Answers the pre-authoring scope gate.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Pre-approve the authored acceptance contract instead of being asked.",
    ),
) -> None:
    """Run an issue end-to-end: resolve ALIAS#N, verify it is open, then drive the composed PoC-D
    default stage (candidate -> readiness -> deliver one PR, landing waiting-for-merge; #115).

    Two human gates run over stdin. From a terminal they are asked interactively; without a TTY they
    would hit EOF and auto-reject, so a keyboard-less run must answer BOTH up front with ``--scope``
    and ``--yes`` (#140). Missing either is a LOUD refusal here, before any run is admitted.
    """
    from issueforge import engine

    # Drop blank --scope values (a bare "" or whitespace is not an answer, #164); keep
    # non-blank paths verbatim. If nothing real remains, approved_scope is None and the
    # missing-flag check below refuses the run, matching the interactive empty-answer reject.
    approved_scope = [v for v in (scope or []) if v.strip()] or None
    if not _isatty():
        missing = [
            flag
            for flag, given in (("--scope", approved_scope is not None), ("--yes", yes))
            if not given
        ]
        if missing:
            typer.echo(
                f"run needs answers to its human gates but stdin is not a terminal; missing "
                f"{' and '.join(missing)}. Pass --scope <path> (repeat per path) and --yes to run "
                f"headless, or run it in a terminal to be asked interactively.",
                err=True,
            )
            raise typer.Exit(2)

    engine.run(spec, approved_scope=approved_scope, auto_approve_contract=yes)


@app.command()
def queue() -> None:
    """List the active run, then the queued runs in FIFO order."""
    from issueforge import store

    state = store.RunStore().read_queue()
    if state.get("active"):
        typer.echo(state["active"])
    for run_id in state.get("queue", []):
        typer.echo(run_id)


@app.command()
def pause(run_id: str = typer.Argument(..., help="Run id to pause.")) -> None:
    """Pause the active running run (keeps the worker slot)."""
    from issueforge import engine

    engine.pause(run_id)


@app.command()
def park(run_id: str = typer.Argument(..., help="Run id to park.")) -> None:
    """Park a run (releases the worker and advances the FIFO)."""
    from issueforge import engine

    engine.park(run_id)


@app.command()
def cancel(run_id: str = typer.Argument(..., help="Run id to cancel.")) -> None:
    """Cancel a queued run or the current paused run."""
    from issueforge import engine

    engine.cancel(run_id)


@app.command("continue")
def continue_(run_id: str = typer.Argument(..., help="Run id to resume.")) -> None:
    """Resume a paused, parked, or crash-orphaned run."""
    from issueforge import engine

    engine.continue_run(run_id)


@app.command()
def reorder(
    run_id: str = typer.Argument(..., help="Queued run id to move."),
    index: int = typer.Argument(..., help="Absolute 0-based target position in the FIFO."),
) -> None:
    """Move a queued run to an absolute 0-based index in the FIFO."""
    from issueforge import engine

    engine.reorder(run_id, index)


@app.command()
def version() -> None:
    """Show the installed IssueForge version."""
    from issueforge import __version__

    typer.echo(__version__)


@app.command()
def tui() -> None:
    """Open the IssueForge terminal interface."""
    from issueforge.tui import IssueForgeApp

    IssueForgeApp().run()


if __name__ == "__main__":
    app()
