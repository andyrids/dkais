"""Main entry point for the dkais CLI."""

import argparse
import io
import logging
import sys
import textwrap
from typing import NoReturn

from dkais import __version__, _core, exceptions
from dkais._cli import add_subparsers
from dkais.logging import CLI_CONSOLE, configure_cli_logging

logger = logging.getLogger(__package__)


class CLIArgumentParser(argparse.ArgumentParser):
    """Custom CLI ArgumentParser."""

    def error(self, message: str) -> NoReturn:
        """Handle CLI errors by printing a formatted message and exiting.

        Args:
            message: The error message to display.
        """

        CLI_CONSOLE.print(f"[red]ERROR:[/red] [b]{message}[/b]", end="\n\n")
        self.print_usage(sys.stderr)
        CLI_CONSOLE.print(
            f"\n[dim]Try [cyan]{self.prog} --help[/cyan].[/dim]",
        )

        sys.exit(_core.ExitCode.EX_USAGE)


def main() -> NoReturn:
    """Provide a unified `dkais` CLI entrypoint - list, download, process."""

    # Redirected streams default to the locale encoding (cp1252 on Windows),
    # which cannot encode the output's glyphs (e.g. `✓`, `→`)
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")

    # Provides a base parser for shared arguments across subcommands
    base_parser = CLIArgumentParser(add_help=False)
    base_parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="Show this help message and exit",
    )

    description = (
        "dkais downloads Danish Maritime Authority historical AIS archives "
        "from http://aisdata.ais.dk/ and converts them to Parquet, "
        "partitioned by YEAR and MONTH."
    )

    epilog = (
        "EXAMPLES:\n"
        " dkais list --start 2026-10\n"
        " dkais download --start 2026-10 --data-dir D:/ais\n"
        " dkais process --start 2026-10 --data-dir D:/ais\n\n"
        "For more information see https://github.com/andyrids/dkais."
    )

    # Main parser for the CLI
    parser = CLIArgumentParser(
        prog=__package__,
        description=textwrap.fill(description, width=79),
        usage="USAGE:\n %(prog)s [options] <command>",
        epilog=epilog,
        add_help=False,
        parents=[base_parser],
        formatter_class=_core.CLIGFormatter,
    )

    parser._optionals.title = "OPTIONS"

    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Show debug logging output",
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"v{__version__}",
        help="Show program's version number and exit",
    )

    subparsers = parser.add_subparsers(
        title="COMMANDS",
        # Mitigate subparser usage display issue by explicitly setting prog
        # https://github.com/python/cpython/issues/86463
        prog=parser.prog,
        dest="command",
        required=False,
        metavar="<command>",
    )

    add_subparsers(subparsers, base_parser)

    args = parser.parse_args()

    # Empty command (`dkais`) shows header and help
    if args.command is None:
        sys.stdout.write(f"DMA AIS data manager [v{__version__}]\n\n")

        # Prints; USAGE, description, OPTIONS, COMMANDS & epilog
        parser.print_help()

        sys.exit(_core.ExitCode.EX_OK)

    # Configure logging based on debug flag
    is_debug = args.debug
    configure_cli_logging(logging.DEBUG if is_debug else logging.WARNING)

    ctx = _core.CLIContext(args=args, console=CLI_CONSOLE, is_debug=is_debug)

    try:
        exit_code = int(args.func(ctx))
    except exceptions.DataDirNotSetError as err:
        logger.error(str(err))
        exit_code = _core.ExitCode.EX_USAGE
    except exceptions.Error as err:
        logger.error(str(err))
        exit_code = _core.ExitCode.EX_FAILURE
    except Exception:
        logger.exception("Unexpected error")
        exit_code = _core.ExitCode.EX_SYNTAX
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
