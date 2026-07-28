.PHONY: test-fast test-quick test test-parked gate fmt dashboard

# Local read/act surface over the run store, on loopback. See dashboard/server.py.
dashboard:
	uv run python dashboard/server.py

# Development loop: one file (or -k expression), parallel, stop on first failure.
# TEST is required — an empty TEST would silently run the whole suite, which the
# dev loop is specifically meant to avoid.
test-fast:
	@test -n "$(TEST)" || { echo "TEST is required, e.g. make test-fast TEST=tests/test_adapters.py"; exit 1; }
	uv run pytest -n auto --dist worksteal -x $(TEST)

# Inner-loop fast ring: every test EXCEPT those marked `slow`, in parallel. A
# drift guard (tests/conftest.py) fails any unmarked test that runs past ~5s under
# this ring, so slow tests can't silently creep back in. `make test` stays the
# full gate; this is the quick signal while iterating.
test-quick:
	uv run pytest -m "not slow" -n auto --dist worksteal

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
