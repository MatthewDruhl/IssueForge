.PHONY: test-fast test test-parked gate fmt

# Development loop: one file (or -k expression), parallel, stop on first failure.
# TEST is required — an empty TEST would silently run the whole suite, which the
# dev loop is specifically meant to avoid.
test-fast:
	@test -n "$(TEST)" || { echo "TEST is required, e.g. make test-fast TEST=tests/test_adapters.py"; exit 1; }
	uv run pytest -n auto --dist worksteal -x $(TEST)

# Full suite, parallel, all failures reported. The pytest portion of the gate.
test:
	uv run pytest -n auto --dist worksteal

# The parked observability suites, on demand. They are excluded from the default
# run by `addopts` in pyproject.toml until observability.py is wired into the
# runtime; `--override-ini=addopts=` clears that, which naming the files alone
# cannot do (pytest's --ignore beats an explicitly-named positional path).
test-parked:
	uv run pytest -n auto --dist worksteal --override-ini=addopts= tests/test_observability.py tests/test_observability_unit.py

# The FULL CI gate, reproduced locally: green here == green in CI. Mirrors every
# step in .github/workflows/ci.yml, fast checks first so a formatting slip fails
# in seconds instead of after the full suite. Run this before pushing.
gate:
	uv run issueforge lint boundary
	uv run ruff format --check .
	uv run ruff check .
	uv run pytest -n auto --dist worksteal
	uv run issueforge audit check S2

# Auto-fix formatting to satisfy `gate`'s read-only `ruff format --check`.
fmt:
	uv run ruff format .
