"""Smoke tests for the `dkais` package."""

import dkais


def test_version() -> None:
    """Assert the installed package exposes a version string."""
    assert isinstance(dkais.__version__, str)
