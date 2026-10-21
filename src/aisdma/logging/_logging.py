"""Logging configuration for `aisdma`.

The Rich global `Console` (`GLOBAL_CONSOLE`) is configured to use STDERR
(`reconfigure(stderr=True)`) for logging and progress display. A separate
`Console` instance (`CLI_CONSOLE`) is used for CLI output on STDOUT.

When a `RichHandler` (logging handler) and `Status`/`Progress` share the same
`Console` instance (`GLOBAL_CONSOLE`), the output can be automatically
coordinated by Rich.

NOTE: When `RichHandler` is initialised without a `Console` instance, it will
use the global `Console` returned by `get_console()` (`GLOBAL_CONSOLE`).
"""

import logging.config
import pathlib
import sys
import tomllib

from rich import get_console, reconfigure
from rich.console import Console

CONFIG_PATH = pathlib.Path(__file__).parent / "config.toml"


GLOBAL_CONSOLE = get_console()
CLI_CONSOLE = Console(force_terminal=sys.stdout.isatty())


def configure_cli_logging(level: int = logging.WARNING) -> None:
    """Configure CLI logging using the `config.toml` settings.

    NOTE: CLI logs are prevented from propagating to the root logger to avoid
    duplication.

    Args:
        level: The desired logging level. Defaults to `logging.WARNING`.
    """

    # Overwrites `__dict__` of existing global console instance
    reconfigure(stderr=True)

    CONFIG_STR = CONFIG_PATH.read_text(encoding="utf-8")
    logging.config.dictConfig(tomllib.loads(CONFIG_STR))

    logger = logging.getLogger("aisdma")
    logger.setLevel(level)
    logger.propagate = False
