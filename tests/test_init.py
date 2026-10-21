"""Smoke tests for the `aisdma` package."""

import aisdma


def test_version() -> None:
    """Assert the installed package exposes a version string."""
    assert isinstance(aisdma.__version__, str)
