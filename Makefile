.PHONY: test-fast test

# Development loop: one file (or -k expression), parallel, stop on first failure.
# TEST is required — an empty TEST would silently run the whole suite, which the
# dev loop is specifically meant to avoid.
test-fast:
	@test -n "$(TEST)" || { echo "TEST is required, e.g. make test-fast TEST=tests/test_adapters.py"; exit 1; }
	uv run pytest -n auto -x $(TEST)

# Final local gate, and the command CI runs. Full suite, parallel, all failures reported.
test:
	uv run pytest -n auto
