# IssueForge

Human-gated TDD orchestration for turning existing GitHub issues into verified pull requests.

IssueForge is being designed as a deterministic Python workflow engine with both CLI and Textual TUI interfaces. AI CLIs provide judgment-heavy issue shaping, test authoring, implementation, and review; Python owns state, safety, verification, and recovery.

See `docs/architecture.md` for the approved version-one flow.

## Baseline environment requirement

IssueForge runs your repo's baseline command with `--report-log` injected, so the **environment that
runs the baseline must have `pytest-reportlog` installed**. This matters when your baseline
self-provisions its own interpreter or virtualenv (for example `uv run --extra dev --directory
backend python -m pytest ...`): that command bypasses the venv IssueForge provisions, so the reporter
has to be present in *that* environment. If it is missing, pytest exits with a usage error and the run
pauses as `USAGE_ERROR`.

`issueforge repo add` preflights this: it probes the baseline environment for the report-log reporter
and refuses onboarding with an actionable message if the interpreter runs but cannot load it. Add
`pytest-reportlog` to the baseline's environment (its dev extras or requirements) before registering.
