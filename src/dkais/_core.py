"""Core CLI plumbing shared by the `dkais` commands."""

import argparse
import dataclasses
import logging
import os
from collections.abc import Iterable
from enum import IntEnum
from pathlib import Path

from rich.console import Console

from dkais import exceptions

logger = logging.getLogger(__package__)

DATA_DIR_ENV = "DKAIS_DATA_DIR"


class ExitCode(IntEnum):
    """Exit codes for the CLI commands.

    NOTE: Members can be passed to `sys.exit` as `int` type checks pass
    due to `IntEnum` inheritance.
    """

    EX_OK = 0
    EX_FAILURE = 1
    EX_SYNTAX = 2
    EX_USAGE = 64
    EX_INTERRUPT = 130


@dataclasses.dataclass(frozen=True, slots=True)
class CLIContext:
    """Centralized state for the CLI commands.

    Attributes:
        args: The parsed command-line arguments from argparse.
        console: The Rich console instance used for terminal output.
        is_debug: Flag indicating if the CLI is running in debug mode.
    """

    args: argparse.Namespace
    console: Console
    is_debug: bool = False


class CLIGFormatter(argparse.RawDescriptionHelpFormatter):
    """Custom formatter for the CLI help output.

    NOTE: Based on CLI guidance from https://clig.dev/.
    """

    def add_usage(
        self,
        usage: str | None,
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._MutuallyExclusiveGroup],
        prefix: str | None = None,
    ) -> None:
        """Add a custom usage message to the CLI help output.

        Args:
            usage: The usage string to display.
            actions: The list of actions for the parser.
            groups: The list of mutually exclusive groups for the parser.
            prefix: The prefix for the usage message.
        """

        super().add_usage(usage, actions, groups, prefix=prefix or "")


def resolve_data_dir(path: Path | None) -> Path:
    """Resolve the data directory from a CLI flag or the environment.

    Args:
        path: The `--data-dir` flag value, if given.

    Returns:
        The resolved data directory path.

    Raises:
        exceptions.DataDirNotSetError: If neither the flag nor
            `DKAIS_DATA_DIR` is set.
    """
    if path is not None:
        return path.resolve()
    if env := os.environ.get(DATA_DIR_ENV):
        return Path(env).resolve()
    msg = f"Set `--data-dir` or the `{DATA_DIR_ENV}` environment variable"
    raise exceptions.DataDirNotSetError(msg)
