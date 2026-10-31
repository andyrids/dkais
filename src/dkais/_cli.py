"""CLI commands to list, download & process DMA AIS archives.

NOTE: `dkais.__main__` adds these commands to the `dkais` group. polars,
boto3, psutil & Rich progress are imported inside the commands, so `--help`,
`-v` & usage errors never pay for them.
"""

import calendar
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import click
import pyclig
from rich.filesize import decimal
from rich.text import Text

if TYPE_CHECKING:
    from dkais._process import Member
    from dkais._s3 import Archive

logger = logging.getLogger(__package__)

FC = TypeVar("FC", bound=Callable[..., Any])

_DATE_PATTERN = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
_POLL_SECONDS = 0.5


class DateBound(click.ParamType[date]):
    """A range boundary - `YYYY`, `YYYY-MM` or `YYYY-MM-DD`.

    Attributes:
        end: Resolve a partial date to its last day, else its first.
    """

    name = "date"

    def __init__(self, *, end: bool) -> None:
        """Create the type.

        Args:
            end: Resolve a partial date to its last day, else its first.
        """
        self.end = end

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> date:
        """Parse `YYYY`, `YYYY-MM` or `YYYY-MM-DD` into a range boundary.

        Args:
            value: The CLI argument value.
            param: The option being parsed.
            ctx: The command's context.

        Returns:
            The boundary date.
        """
        if isinstance(value, date):
            return value
        if not (match := _DATE_PATTERN.fullmatch(str(value))):
            msg = f"invalid date `{value}` - use YYYY, YYYY-MM or YYYY-MM-DD"
            self.fail(msg, param, ctx)
        year, month, day = match.groups()
        try:
            y = int(year)
            m = int(month) if month else (12 if self.end else 1)
            last = calendar.monthrange(y, m)[1]
            d = int(day) if day else (last if self.end else 1)
            return date(y, m, d)
        except ValueError as e:
            self.fail(f"invalid date `{value}` - {e}", param, ctx)


def _data_dir(*, required: bool) -> Callable[[FC], FC]:
    """Add `--data-dir`, defaulting to `$DKAIS_DATA_DIR`.

    Args:
        required: Fail without a data directory, else pass None.

    Returns:
        The option decorator.
    """
    return click.option(
        "--data-dir",
        type=click.Path(file_okay=False, resolve_path=True, path_type=Path),
        envvar="DKAIS_DATA_DIR",
        show_envvar=True,
        required=required,
        metavar="PATH",
        help="Data directory root",
    )


def _date_range(f: FC) -> FC:
    """Add `--start` & `--end`, shared by every command."""
    f = click.option(
        "--end",
        type=DateBound(end=True),
        metavar="DATE",
        help="Range end, inclusive (default: latest)",
    )(f)
    return click.option(
        "--start",
        type=DateBound(end=False),
        metavar="DATE",
        help="Range start, inclusive - YYYY, YYYY-MM or YYYY-MM-DD",
    )(f)


def _check_range(start: date | None, end: date | None) -> None:
    """Check that `--start` is not after `--end`.

    NOTE: An inverted range inside one month still overlaps that month's
    archive, so it would otherwise select the whole month.

    Args:
        start: The `--start` value.
        end: The `--end` value.

    Raises:
        click.UsageError: If both are given and `start` is after `end`.
    """
    if start and end and start > end:
        msg = f"`--start` {start} is after `--end` {end}"
        raise click.UsageError(msg)


def _require_selection(
    select_all: bool, start: date | None, end: date | None
) -> None:
    """Check that `--start` or `--all` was given, but not both.

    NOTE: Guards `download` & `process` against an accidental run over the
    whole ~4.2 TB bucket.

    Args:
        select_all: Whether `--all` was given.
        start: The `--start` value.
        end: The `--end` value.

    Raises:
        click.UsageError: If the selection is missing, contradictory or
            inverted.
    """
    if select_all and (start or end):
        msg = "`--all` cannot be combined with `--start`/`--end`"
        raise click.UsageError(msg)
    if not (select_all or start):
        raise click.UsageError("Select archives with `--start` or `--all`")
    _check_range(start, end)


def _tsv(value: object) -> str:
    """Format a `list` row value for tab-separated output."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@click.command("list", cls=pyclig.Command)
@_data_dir(required=False)
@_date_range
@pyclig.options.json_output(help="Output as a JSON array")
@pyclig.pass_context
def command_list(
    ctx: pyclig.Context,
    data_dir: Path | None,
    start: date | None,
    end: date | None,
    json_output: bool,
) -> int:
    """List bucket archives & local state.

    \f
    NOTE: Off a terminal, rows are tab-separated - the `--json` keys, in
    order, without a header - and the caption goes to STDERR.

    Args:
        ctx: The CLI context.
        data_dir: The data directory, for local state.
        start: The range start.
        end: The range end.
        json_output: Print a JSON array instead of a table.

    Returns:
        The integer process exit code.
    """
    _check_range(start, end)
    from dkais import _download, _s3

    archives = _s3.list_archives(_s3.make_client(), start, end)
    if data_dir is None:
        logger.warning(
            "No data dir - set `--data-dir` or `DKAIS_DATA_DIR` "
            "to show local state"
        )

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

    if json_output:
        click.echo(json.dumps(rows, indent=2))
        return pyclig.ExitCode.OK

    total = decimal(sum(a.size for a in archives))
    caption = f"{len(archives)} archives, {total}"
    if not ctx.out.is_terminal:
        for row in rows:
            click.echo("\t".join(_tsv(value) for value in row.values()))
        pyclig.info(ctx.err, message=caption)
        return pyclig.ExitCode.OK

    from rich import box
    from rich.table import Table

    table = Table(caption=caption, box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("Key", no_wrap=True)
    table.add_column("Start", no_wrap=True)
    table.add_column("End", no_wrap=True)
    table.add_column("Size", justify="right", no_wrap=True)
    if data_dir:
        table.add_column("Raw", justify="center")
        table.add_column("Processed", justify="right", no_wrap=True)

    for archive, row in zip(archives, rows, strict=True):
        cells: list[str | Text] = [
            row["key"],
            row["start"],
            row["end"],
            decimal(row["size"]),
        ]
        if data_dir:
            days = (archive.end - archive.start).days + 1
            cells += [
                Text("✓", style="green") if row["downloaded"] else "",
                f"{row['processed_days']}/{days}",
            ]
        table.add_row(*cells)

    ctx.out.print(table)
    return pyclig.ExitCode.OK


def _processed_days(archive: "Archive", parquet_dir: Path) -> int:
    """Count the days of an archive's period with a Parquet file.

    Args:
        archive: The bucket archive.
        parquet_dir: The root Parquet directory.

    Returns:
        The number of processed days.
    """
    from dkais import _process

    days = range(archive.start.toordinal(), archive.end.toordinal() + 1)
    return sum(
        _process.day_path(parquet_dir, date.fromordinal(day)).exists()
        for day in days
    )


@click.command("download", cls=pyclig.Command, no_args_is_help=True)
@_data_dir(required=True)
@_date_range
@pyclig.options.select_all(help="Select every archive")
@click.option(
    "-j",
    "--jobs",
    type=click.IntRange(min=1),
    default=4,
    metavar="N",
    help="Concurrent file downloads (default: 4)",
)
@pyclig.pass_context
def command_download(
    ctx: pyclig.Context,
    data_dir: Path,
    start: date | None,
    end: date | None,
    select_all: bool,
    jobs: int,
) -> int:
    """Download archives to <data-dir>/raw.

    \f
    Args:
        ctx: The CLI context.
        data_dir: The data directory.
        start: The range start.
        end: The range end.
        select_all: Select every archive.
        jobs: The number of concurrent file downloads.

    Returns:
        The integer process exit code.
    """
    _require_selection(select_all, start, end)
    from dkais import _download, _s3
    from dkais._report import duration, plural, reporter, summarise

    raw_dir = data_dir / "raw"
    client = _s3.make_client(jobs)
    archives = _s3.list_archives(client, start, end)
    if not archives:
        logger.warning("No archives in the selected range")
        return pyclig.ExitCode.OK

    pending = _download.pending_archives(archives, raw_dir)
    present = len(archives) - len(pending)
    total = sum(a.size for a in pending)
    pyclig.info(
        ctx.err,
        message=f"{plural(len(pending), 'archive')} to download · "
        f"{decimal(total)} · {present} already present",
    )
    began = time.monotonic()
    try:
        with reporter(ctx.err, "download", len(pending), total) as report:
            failed = _download.download_archives(
                client, pending, raw_dir, report, jobs
            )
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted - unfinished downloads discarded, "
            "re-run to download them again"
        )
        return pyclig.ExitCode.INTERRUPT

    elapsed = time.monotonic() - began
    size = sum(a.size for a in pending if a not in failed)
    done = len(pending) - len(failed)
    summarise(
        "Downloaded",
        "archive",
        done,
        len(pending),
        f" ({decimal(size)}) in {duration(elapsed)}"
        + (f", {present} already present" if present else ""),
        raw_dir,
        {
            "downloaded": done,
            "bytes": size,
            "already_present": present,
            "failed": len(failed),
            "elapsed": f"{elapsed:.1f}s",
            "dest": raw_dir,
        },
        out=ctx.out,
        err=ctx.err,
    )
    return pyclig.ExitCode.FAILURE if failed else pyclig.ExitCode.OK


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
    from dkais import _process

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


@click.command("process", cls=pyclig.Command, no_args_is_help=True)
@_data_dir(required=True)
@_date_range
@pyclig.options.select_all(help="Select every archive")
@pyclig.options.force(help="Re-process days that already have Parquet output")
@pyclig.pass_context
def command_process(
    ctx: pyclig.Context,
    data_dir: Path,
    start: date | None,
    end: date | None,
    select_all: bool,
    force: bool,
) -> int:
    """Convert archives to <data-dir>/parquet.

    \f
    NOTE: Works offline - archives are discovered in `<data-dir>/raw`.

    Args:
        ctx: The CLI context.
        data_dir: The data directory.
        start: The range start.
        end: The range end.
        select_all: Select every downloaded archive.
        force: Re-process days that already have Parquet output.

    Returns:
        The integer process exit code.
    """
    _require_selection(select_all, start, end)
    import zipfile

    import polars as pl
    import psutil

    from dkais import _process, _s3, exceptions
    from dkais._report import duration, plural, reporter, summarise

    parquet_dir, tmp_dir = data_dir / "parquet", data_dir / "tmp"

    zip_paths = [
        path
        for path in sorted((data_dir / "raw").glob("*/*.zip"))
        if (archive := _s3.parse_key(path.name, path.stat().st_size))
        and archive.overlaps(start, end)
    ]
    in_range = [
        member
        for path in zip_paths
        for member in _process.list_members(path, start, end)
    ]
    members = [
        member
        for member in in_range
        if force or not member.target(parquet_dir).exists()
    ]
    if not members:
        logger.warning("Nothing to process - download first or use `--force`")
        return pyclig.ExitCode.OK
    skipped = len(in_range) - len(members)

    csv_size = decimal(sum(member.size for member in members))
    pyclig.info(
        ctx.err,
        message=f"{plural(len(members), 'day')} to process · {csv_size} CSV "
        f"· {skipped} already processed",
    )
    _process.clean_stale(parquet_dir, tmp_dir)
    available = psutil.virtual_memory().available
    logger.debug(
        "Inflating with %s, %s available",
        _process.INFLATE.__name__,
        decimal(available),
    )
    failed = 0
    began = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with reporter(ctx.err, "process", len(members)) as report:
            for member, extracted in _extract_ahead(
                pool, members, tmp_dir, available
            ):
                report.item_started(member.name)
                started = time.monotonic()
                try:
                    target = _process.convert(
                        member, _result(extracted), parquet_dir
                    )
                except (
                    exceptions.Error,
                    pl.exceptions.PolarsError,
                    zipfile.BadZipFile,
                    OSError,
                ) as e:
                    logger.debug(
                        "Failed to process `%s`", member.name, exc_info=True
                    )
                    report.item_failed(member.name, e)
                    failed += 1
                else:
                    logger.debug("Wrote `%s`", target)
                    size = decimal(target.stat().st_size)
                    took = duration(time.monotonic() - started)
                    path = target.relative_to(parquet_dir).as_posix()
                    report.item_done(member.name, f"{path}  {size} in {took}")
                finally:
                    # Free in-memory CSV bytes before the next extraction
                    del extracted
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted - partial output discarded, re-run to resume"
        )
        return pyclig.ExitCode.INTERRUPT
    finally:
        # Discard a prefetched CSV file left by an interrupted run
        pool.shutdown(wait=True, cancel_futures=True)
        _process.clean_stale(parquet_dir, tmp_dir)

    elapsed = time.monotonic() - began
    done = len(members) - failed
    summarise(
        "Processed",
        "day",
        done,
        len(members),
        f" in {duration(elapsed)}"
        + (f", {skipped} already processed" if skipped else ""),
        parquet_dir,
        {
            "processed": done,
            "already_processed": skipped,
            "failed": failed,
            "elapsed": f"{elapsed:.1f}s",
            "dest": parquet_dir,
        },
        out=ctx.out,
        err=ctx.err,
    )
    return pyclig.ExitCode.FAILURE if failed else pyclig.ExitCode.OK
