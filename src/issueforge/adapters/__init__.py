"""Verification adapters: the portable seam between the engine and a test framework.

Importing this package eagerly registers the built-in adapters, so a caller resolving through the
``adapters.base.registry.resolve(framework=..., reporter=...)`` seam (the source the S13 contract
gate and the acceptance suites use) always finds them without importing the concrete adapter module
first.
"""

from issueforge.adapters import pytest_adapter  # noqa: F401  (registers the pytest adapter)
