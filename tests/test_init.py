"""Smoke tests for the `dkais` package."""

import importlib
from importlib import metadata

import pytest

import dkais


def test_version() -> None:
    """Assert the installed package exposes a version string."""
    assert isinstance(dkais.__version__, str)


def test_version_fallback_without_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert a fallback version when package metadata is unavailable."""

    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    try:
        assert importlib.reload(dkais).__version__ == "0.0.0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(dkais)
