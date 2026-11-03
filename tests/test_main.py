"""Tests for the `dkais` CLI."""

from pyclig.testing import invoke

from dkais.__main__ import cli


def test_version() -> None:
    """Assert `--version` prints the program name & exits cleanly."""
    result = invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith(f"{cli.name} ")


def test_no_command_exits_cleanly() -> None:
    """Assert a bare invocation exits cleanly."""
    assert invoke(cli).exit_code == 0
