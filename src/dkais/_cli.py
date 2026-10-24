"""CLI commands to list, download & process DMA AIS archives.

NOTE: The `__main__` `argparse.ArgumentParser` dkais CLI entry point uses this
module to register subcommands (`add_subparsers`).
"""

import argparse
import calendar
import json
import logging
import re
import sys
import zipfile
from collections import deque
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import psutil
from rich import box
from rich.filesize import decimal
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from dkais import _download, _process, _s3, exceptions
from dkais._core import CLIContext, CLIGFormatter, ExitCode, resolve_data_dir
from dkais.logging import GLOBAL_CONSOLE

if TYPE_CHECKING:
    from dkais._process import Member
    from dkais._s3 import Archive

logger = logging.getLogger(__package__)

_DATE_PATTERN = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
_POLL_SECONDS = 0.5


def _parse_date(value: str, *, end: bool) -> date:
    """Parse `YYYY`, `YYYY-MM` or `YYYY-MM-DD` into a range boundary.

    Args:
        value: The CLI argument value.
        end: Resolve a partial date to its last day, else its first.

    Returns:
        The boundary date.

    Raises:
        argparse.ArgumentTypeError: On an invalid date.
    """
    if not (match := _DATE_PATTERN.fullmatch(value)):
        msg = f"invalid date `{value}` - use YYYY, YYYY-MM or YYYY-MM-DD"
        raise argparse.ArgumentTypeError(msg)
    year, month, day = match.groups()
    try:
        y = int(year)
        m = int(month) if month else (12 if end else 1)
        d = int(day) if day else (calendar.monthrange(y, m)[1] if end else 1)
        return date(y, m, d)
    except ValueError as e:
        msg = f"invalid date `{value}` - {e}"
        raise argparse.ArgumentTypeError(msg) from e


def _parse_start(value: str) -> date:
    """Parse a `--start` value to the first day it covers."""
    return _parse_date(value, end=False)


def _parse_end(value: str) -> date:
    """Parse an `--end` value to the last day it covers."""
    return _parse_date(value, end=True)


def _requires_selection(ctx: CLIContext) -> bool:
    """Check that `--start` or `--all` was given, else print concise help.

    NOTE: Guards `download` & `process` against an accidental run over the
    whole ~4.2 TB bucket.

    Args:
        ctx: The CLI context.

    Returns:
        True if a selection was given.
    """
    args = ctx.args
    if args.all and (args.start or args.end):
        logger.error("`--all` cannot be combined with `--start`/`--end`")
        return False
    if not (args.all or args.start):
        args.parser.print_help(sys.stderr)
        return False
    return True


@contextmanager
def _setup_progress(
    *columns: ProgressColumn,
) -> Generator[Progress, None, None]:
    """Create a Rich progress display on STDERR.

    NOTE: Like logs, progress bars and spinners are diagnostic metadata, which
    are kept on STDERR and share the same console instance (`GLOBAL_CONSOLE`).

    Args:
        columns: The progress columns to render.

    Yields:
        A Rich `Progress` instance for use within a context manager.
    """
    progress = Progress(
        *columns,
        console=GLOBAL_CONSOLE,
        disable=not GLOBAL_CONSOLE.is_terminal,
        transient=False,
    )
    with progress:
        yield progress


def command_list(ctx: CLIContext) -> int:
    """List bucket archives, with local state when a data dir is set.

    Args:
        ctx: The CLI context.

    Returns:
        The integer process exit code. Uses `_core.ExitCode`.
    """
    args = ctx.args
    archives = _s3.list_archives(_s3.make_client(), args.start, args.end)

    try:
        data_dir: Path | None = resolve_data_dir(args.data_dir)
    except exceptions.DataDirNotSetError:
        data_dir = None

    rows: list[dict[str, Any]] = []
    for archive in archives:
        row: dict[str, Any] = {
            "key": archive.key,
            "start": archive.start.isoformat(),
            "end": archive.end.isoformat(),
            "size": archive.size,
        }
        if data_dir:
            row["downloaded"] = _download.is_downloaded(
                archive, data_dir / "raw"
            )
            row["processed_days"] = _processed_days(
                archive, data_dir / "parquet"
            )
        rows.append(row)

    if args.json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return ExitCode.EX_OK

    total = decimal(sum(a.size for a in archives))
    table = Table(
        caption=f"{len(archives)} archives, {total}",
        box=box.SIMPLE_HEAD,
        pad_edge=False,
    )
    table.add_column("Key", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("End", no_wrap=True)
    table.add_column("Size", justify="right", no_wrap=True)
    if data_dir:
        table.add_column("Raw", justify="center")
        table.add_column("Days", justify="right")

    for row in rows:
        cells = [row["key"], row["start"], row["end"], decimal(row["size"])]
        if data_dir:
            cells += [
                "[green]✓" if row["downloaded"] else "",
                str(row["processed_days"]),
            ]
        table.add_row(*cells)

    ctx.console.print(table)
    return ExitCode.EX_OK


def _processed_days(archive: "Archive", parquet_dir: Path) -> int:
    """Count the days of an archive's period with a Parquet file.

    Args:
        archive: The bucket archive.
        parquet_dir: The root Parquet directory.

    Returns:
        The number of processed days.
    """
    days = range(archive.start.toordinal(), archive.end.toordinal() + 1)
    return sum(
        _process.day_path(parquet_dir, date.fromordinal(day)).exists()
        for day in days
    )


def command_download(ctx: CLIContext) -> int:
    """Download archives in range to `<data-dir>/raw`.

    Args:
        ctx: The CLI context.

    Returns:
        The integer process exit code. Uses `_core.ExitCode`.
    """
    args = ctx.args
    if not _requires_selection(ctx):
        return ExitCode.EX_USAGE
    raw_dir = resolve_data_dir(args.data_dir) / "raw"

    client = _s3.make_client(args.jobs)
    archives = _s3.list_archives(client, args.start, args.end)
    if not archives:
        logger.warning("No archives in the selected range")
        return ExitCode.EX_OK

    GLOBAL_CONSOLE.rule("[cyan]dkais download")
    try:
        with _setup_progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(elapsed_when_finished=True),
        ) as progress:
            failed = _download.download_archives(
                client, archives, raw_dir, progress, args.jobs
            )
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted - partial downloads discarded, re-run to resume"
        )
        return ExitCode.EX_INTERRUPT

    if failed:
        logger.error(
            "%d of %d archives failed to download", len(failed), len(archives)
        )
        return ExitCode.EX_FAILURE

    size = decimal(sum(a.size for a in archives))
    ctx.console.print(f"{len(archives)} archives ({size}) in {raw_dir}")
    return ExitCode.EX_OK


def _extract_ahead(
    pool: ThreadPoolExecutor,
    members: list["Member"],
    tmp_dir: Path,
    available: int,
) -> Generator[tuple["Member", "Future[bytes | Path]"], None, None]:
    """Yield members with their extraction, one member ahead of the caller.

    NOTE: Inflating releases the GIL, so the next member is extracted on a
    single `pool` thread while the caller converts the current one. Members
    that fit in `available` memory are extracted to bytes, else to a file.

    Args:
        pool: A single-worker executor for extraction.
        members: The members to extract, in conversion order.
        tmp_dir: The directory for extracted CSV files.
        available: The memory available to the run, in bytes.

    Yields:
        Each member & the future of its `extract_member` result.
    """
    pending: deque[tuple[Member, Future[bytes | Path]]] = deque()
    for member in members:
        in_memory = _process.fits_in_memory(member, available)
        future = pool.submit(
            _process.extract_member, member, tmp_dir, in_memory=in_memory
        )
        pending.append((member, future))
        if len(pending) > 1:
            yield pending.popleft()
    yield from pending


def _result(future: "Future[bytes | Path]") -> bytes | Path:
    """Wait for a future, polling so Ctrl-C is handled on Windows."""
    while True:
        try:
            return future.result(timeout=_POLL_SECONDS)
        except TimeoutError:
            continue


def command_process(ctx: CLIContext) -> int:
    """Convert downloaded archives in range to `<data-dir>/parquet`.

    NOTE: Works offline - archives are discovered in `<data-dir>/raw`.

    Args:
        ctx: The CLI context.

    Returns:
        The integer process exit code. Uses `_core.ExitCode`.
    """
    args = ctx.args
    if not _requires_selection(ctx):
        return ExitCode.EX_USAGE
    data_dir = resolve_data_dir(args.data_dir)
    parquet_dir, tmp_dir = data_dir / "parquet", data_dir / "tmp"

    zip_paths = [
        path
        for path in sorted((data_dir / "raw").glob("*/*.zip"))
        if (archive := _s3.parse_key(path.name, path.stat().st_size))
        and archive.overlaps(args.start, args.end)
    ]
    members = [
        member
        for path in zip_paths
        for member in _process.list_members(path, args.start, args.end)
        if args.force or not member.target(parquet_dir).exists()
    ]
    if not members:
        logger.warning("Nothing to process - download first or use `--force`")
        return ExitCode.EX_OK

    _process.clean_stale(parquet_dir, tmp_dir)
    available = psutil.virtual_memory().available
    logger.debug(
        "Inflating with %s, %s available",
        _process.INFLATE.__name__,
        decimal(available),
    )
    GLOBAL_CONSOLE.rule("[cyan]dkais process")
    failed = 0
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with _setup_progress(
            SpinnerColumn(finished_text="[green]✓"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            overall = progress.add_task("[cyan]Process", total=len(members))
            for member, extracted in _extract_ahead(
                pool, members, tmp_dir, available
            ):
                step = progress.add_task(
                    f"{member.zip_path.name} - {member.day}"
                )
                try:
                    target = _process.convert(
                        member, _result(extracted), parquet_dir
                    )
                    logger.debug("Wrote `%s`", target)
                except (
                    exceptions.Error,
                    pl.exceptions.PolarsError,
                    zipfile.BadZipFile,
                    OSError,
                ):
                    logger.exception("Failed to process `%s`", member.name)
                    failed += 1
                finally:
                    progress.remove_task(step)
                    # Free in-memory CSV bytes before the next extraction
                    del extracted
                progress.advance(overall)
            progress.update(overall, description="[green]Process")
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted - partial output discarded, re-run to resume"
        )
        return ExitCode.EX_INTERRUPT
    finally:
        # Discard a prefetched CSV file left by an interrupted run
        pool.shutdown(wait=True, cancel_futures=True)
        _process.clean_stale(parquet_dir, tmp_dir)

    if failed:
        logger.error("%d of %d days failed to process", failed, len(members))
        return ExitCode.EX_FAILURE

    ctx.console.print(f"{len(members)} days processed in {parquet_dir}")
    return ExitCode.EX_OK


def add_subparsers(
    subparsers: "argparse._SubParsersAction[Any]",
    parent: argparse.ArgumentParser | None = None,
) -> None:
    """Add the `list`, `download` & `process` subcommands.

    Args:
        subparsers: The main CLI parser's subparsers action.
        parent: A parent parser to include for shared arguments.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        type=Path,
        metavar="PATH",
        help="Data directory root (default: $DKAIS_DATA_DIR)",
    )
    common.add_argument(
        "--start",
        type=_parse_start,
        metavar="DATE",
        help="Range start, inclusive - YYYY, YYYY-MM or YYYY-MM-DD",
    )
    common.add_argument(
        "--end",
        type=_parse_end,
        metavar="DATE",
        help="Range end, inclusive (default: latest)",
    )
    parents = [parent, common] if parent else [common]

    commands = {
        "list": ("List bucket archives & local state", command_list),
        "download": ("Download archives to <data-dir>/raw", command_download),
        "process": ("Convert archives to <data-dir>/parquet", command_process),
    }
    for name, (text, func) in commands.items():
        parser = subparsers.add_parser(
            name,
            help=text,
            description=text,
            usage="USAGE:\n %(prog)s [options]",
            add_help=False,
            parents=parents,
            formatter_class=CLIGFormatter,
        )
        parser._optionals.title = "OPTIONS"
        parser.set_defaults(func=func, parser=parser)

        if name == "list":
            parser.add_argument(
                "--json", action="store_true", help="Output as a JSON array"
            )
            continue

        parser.add_argument(
            "-a", "--all", action="store_true", help="Select every archive"
        )
        if name == "download":
            parser.add_argument(
                "-j",
                "--jobs",
                type=int,
                default=4,
                metavar="N",
                help="Concurrent file downloads (default: 4)",
            )
        else:
            parser.add_argument(
                "-f",
                "--force",
                action="store_true",
                help="Re-process days that already have Parquet output",
            )
