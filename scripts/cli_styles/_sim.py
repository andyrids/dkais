"""Simulation harness shared by the CLI style demos.

Fakes `dkais download` & `dkais process` runs - no S3, data dir or network -
so every style renders the same lifecycle: a header, live progress, a mid-run
warning & failure, then a summary.

NOTE: Mirrors the real output contract. Progress & logs go to `ctx.err`
(STDERR, like `GLOBAL_CONSOLE`), the final summary to `ctx.out` (STDOUT, like
`CLI_CONSOLE`).

Each style module exposes `download(ctx) -> int` & `process(ctx) -> int`,
driving `simulate_download` / `simulate_process` with its own callbacks. Both
simulations call back from the same threads as the real commands - download
callbacks arrive on worker threads, process callbacks on the main thread.
"""

import dataclasses
import io
import logging
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from datetime import date, timedelta
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_MB = 1000 * 1000
_CHUNK = 8 * 1024 * 1024
_POLL_SECONDS = 0.1

_WORKER_RATE = 120 * _MB
"""Simulated bytes per second for one download worker at `speed=1`."""

_DAY_SECONDS = 0.9
"""Simulated seconds to process one day at `speed=1`."""


@dataclasses.dataclass(frozen=True, slots=True)
class SimArchive:
    """A fake daily bucket archive, shaped like `dkais._s3.Archive`.

    Attributes:
        key: The S3 object key.
        size: The object size in bytes.
        day: The day the archive covers.
    """

    key: str
    size: int
    day: date

    @property
    def name(self) -> str:
        """The archive file name."""
        return self.key


@dataclasses.dataclass(frozen=True, slots=True)
class SimMember:
    """A fake daily CSV, shaped like `dkais._process.Member`.

    Attributes:
        zip_name: The archive file name.
        name: The member name within the archive.
        day: The day the CSV covers.
        size: The uncompressed CSV size in bytes.
        output_size: The size of the Parquet file written, in bytes.
    """

    zip_name: str
    name: str
    day: date
    size: int
    output_size: int

    def target(self, parquet_dir: Path) -> Path:
        """Return the hive-partitioned Parquet path for this day."""
        partition = f"YEAR={self.day.year}/MONTH={self.day.month}"
        return parquet_dir / partition / f"aisdk-{self.day}.parquet"


class SimTransferError(Exception):
    """A simulated download failure."""


class SimConvertError(Exception):
    """A simulated conversion failure."""


@dataclasses.dataclass(slots=True)
class DemoContext:
    """Everything a style needs to render one simulated run.

    Attributes:
        err: STDERR console for progress & logs (`GLOBAL_CONSOLE`).
        out: STDOUT console for the final summary (`CLI_CONSOLE`).
        logger: Logger rendered like the production `rich-verbose-handler`.
        archives: Every archive in the selected range.
        downloaded: Names of archives already present (skipped).
        members: The days to process.
        data_dir: The fake data directory, for headers & summaries.
        start: The selected range start.
        end: The selected range end.
        jobs: Concurrent downloads (`--jobs`).
        speed: Simulation speed multiplier.
        fail: Whether to inject the one download & one process failure.
    """

    err: Console
    out: Console
    logger: logging.Logger
    archives: list[SimArchive]
    downloaded: frozenset[str]
    members: list[SimMember]
    data_dir: Path = Path("D:/Data/AISDK")
    start: date = date(2026, 10, 1)
    end: date = date(2026, 10, 10)
    jobs: int = 4
    speed: float = 1.0
    fail: bool = True

    @property
    def pending(self) -> list[SimArchive]:
        """The archives still to download."""
        return [a for a in self.archives if a.name not in self.downloaded]

    @property
    def raw_dir(self) -> Path:
        """The raw archive directory."""
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        """The Parquet output directory."""
        return self.data_dir / "parquet"


def make_fixtures() -> tuple[
    list[SimArchive], frozenset[str], list[SimMember]
]:
    """Build deterministic archives & members for 2026-10-01..10.

    Returns:
        The archives, the names already downloaded, and the members.
    """
    rng = random.Random(7)
    archives, members = [], []
    for offset in range(10):
        day = date(2026, 10, 1) + timedelta(days=offset)
        size = rng.randint(350, 500) * _MB + rng.randint(0, _MB)
        archive = SimArchive(key=f"aisdk-{day}.zip", size=size, day=day)
        archives.append(archive)
        members.append(
            SimMember(
                zip_name=archive.name,
                name=f"aisdk-{day}.csv",
                day=day,
                size=int(size * rng.uniform(6.5, 7.5)),
                output_size=int(size * rng.uniform(0.55, 0.7)),
            )
        )
    downloaded = frozenset(a.name for a in archives[:2])
    return archives, downloaded, members[:9]


def make_consoles(
    width: int | None = None,
    *,
    no_color: bool = False,
    record: bool = False,
) -> tuple[Console, Console]:
    """Create the STDERR & STDOUT consoles.

    NOTE: Both force terminal mode so demos animate under `uv run`. With
    `record`, both write to memory as non-terminals instead - `Live` then
    renders only its final frame, which `save_record` exports as text.

    Args:
        width: Fixed console width, else the terminal width.
        no_color: Disable colour, as with `NO_COLOR`.
        record: Record to memory for `save_record`.

    Returns:
        The `(err, out)` consoles.
    """
    if record:
        return tuple(  # type: ignore[return-value]
            Console(
                file=io.StringIO(),
                record=True,
                force_terminal=False,
                width=width or 100,
                no_color=no_color,
            )
            for _ in range(2)
        )
    err = Console(
        stderr=True, force_terminal=True, width=width, no_color=no_color
    )
    out = Console(force_terminal=True, width=width, no_color=no_color)
    return err, out


def save_record(ctx: DemoContext, path: Path) -> None:
    """Write the recorded STDERR & STDOUT text to `path`.

    Args:
        ctx: A context whose consoles were created with `record=True`.
        path: The output text file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "--- STDERR ---\n"
        + ctx.err.export_text()
        + "--- STDOUT ---\n"
        + ctx.out.export_text(),
        encoding="utf-8",
    )


def make_logger(err: Console) -> logging.Logger:
    """Create a logger rendered like the production verbose handler.

    NOTE: Matches `rich-verbose-handler` in `dkais/logging/config.toml`. A
    style may swap the handler to demo a different log format.

    Args:
        err: The STDERR console.

    Returns:
        The demo logger.
    """
    logger = logging.getLogger("dkais.demo")
    logger.handlers.clear()
    handler = RichHandler(
        console=err,
        show_path=True,
        rich_tracebacks=True,
        markup=False,
        omit_repeated_times=False,
    )
    handler.setFormatter(logging.Formatter("[%(funcName)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return logger


def make_context(
    err: Console,
    out: Console,
    *,
    speed: float = 1.0,
    fail: bool = True,
) -> DemoContext:
    """Create a fresh demo context.

    Args:
        err: The STDERR console.
        out: The STDOUT console.
        speed: Simulation speed multiplier.
        fail: Whether to inject failures.

    Returns:
        The demo context.
    """
    archives, downloaded, members = make_fixtures()
    return DemoContext(
        err=err,
        out=out,
        logger=make_logger(err),
        archives=archives,
        downloaded=downloaded,
        members=members,
        speed=speed,
        fail=fail,
    )


def _transfer(
    archive: SimArchive,
    rate: float,
    advance: Callable[[int], None],
    stop: threading.Event,
    *,
    fail: bool,
) -> None:
    """Send an archive's bytes in chunks at `rate` bytes per second.

    Args:
        archive: The archive to transfer.
        rate: The transfer rate, in bytes per second.
        advance: Called with each chunk's size.
        stop: Set to abort the transfer.
        fail: Fail the transfer just past halfway.

    Raises:
        SimTransferError: When stopped, or when `fail`.
    """
    sent = 0
    while sent < archive.size:
        if stop.is_set():
            msg = f"Interrupted `{archive.key}`"
            raise SimTransferError(msg)
        if fail and sent > archive.size * 0.55:
            msg = (
                f"Connection reset by peer while reading "
                f"`{archive.key}` (attempt 5 of 5)"
            )
            raise SimTransferError(msg)
        n = min(_CHUNK, archive.size - sent)
        time.sleep(n / rate)
        sent += n
        advance(n)


def _sleep(seconds: float) -> None:
    """Sleep in short slices so Ctrl-C is handled promptly on Windows."""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(_POLL_SECONDS, remaining))


def simulate_download(
    ctx: DemoContext,
    *,
    start: Callable[[SimArchive], None],
    advance: Callable[[SimArchive, int], None],
    done: Callable[[SimArchive, BaseException | None], None],
    warn: Callable[[str], None] | None = None,
) -> list[SimArchive]:
    """Download `ctx.pending`, `ctx.jobs` at a time, like `download_archives`.

    NOTE: `start`, `advance` & `done` are called on worker threads. `done`
    follows `start`, with the error for a failed archive (one fails when
    `ctx.fail`), except for transfers cut short by Ctrl-C. `warn` defaults
    to `ctx.logger.warning`.

    Args:
        ctx: The demo context.
        start: Called when an archive starts transferring.
        advance: Called with bytes transferred, like the boto3 `Callback`.
        done: Called when an archive finishes, with its error or `None`.
        warn: Called once mid-run with a warning message.

    Returns:
        The archives that failed.

    Raises:
        KeyboardInterrupt: On Ctrl-C, after in-flight workers stop.
    """
    pending = ctx.pending
    failing = pending[2].name if ctx.fail and len(pending) > 2 else None
    warning = pending[5].name if len(pending) > 5 else None
    warn = warn or ctx.logger.warning
    stop = threading.Event()
    rng = random.Random(11)
    rates = {a.name: _WORKER_RATE * rng.uniform(0.7, 1.3) for a in pending}

    def fetch(archive: SimArchive) -> None:
        start(archive)
        error: BaseException | None = None
        try:
            if archive.name == warning:
                warn(
                    f"Slow response for `{archive.key}` - "
                    "retrying (attempt 2 of 5)"
                )
            _transfer(
                archive,
                rates[archive.name] * ctx.speed,
                lambda n: advance(archive, n),
                stop,
                fail=archive.name == failing,
            )
        except SimTransferError as e:
            error = e
            raise
        finally:
            # An interrupted transfer is not a failure - the run is ending
            if not stop.is_set():
                done(archive, error)

    failed: list[SimArchive] = []
    executor = ThreadPoolExecutor(max_workers=ctx.jobs)
    futures: dict[Future[None], SimArchive] = {
        executor.submit(fetch, archive): archive for archive in pending
    }
    not_done = set(futures)
    try:
        while not_done:
            finished, not_done = wait_futures(
                not_done, timeout=_POLL_SECONDS, return_when=FIRST_EXCEPTION
            )
            for future in finished:
                # Re-raise bugs in style callbacks, keep simulated failures
                if isinstance(exc := future.exception(), SimTransferError):
                    failed.append(futures[future])
                elif exc is not None:
                    raise exc
    except KeyboardInterrupt:
        stop.set()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return failed


def simulate_process(
    ctx: DemoContext,
    *,
    start: Callable[[SimMember], None],
    done: Callable[[SimMember, BaseException | None], None],
    warn: Callable[[str], None] | None = None,
) -> list[SimMember]:
    """Convert `ctx.members` one day at a time, like `command_process`.

    NOTE: Callbacks run on the main thread. The real conversion reports no
    per-day progress, so a day is only started then done. One day fails
    when `ctx.fail`; `warn` (default `ctx.logger.warning`) fires once.

    Args:
        ctx: The demo context.
        start: Called when a day starts converting.
        done: Called when a day finishes, with its error or `None`.
        warn: Called once mid-run with a warning message.

    Returns:
        The members that failed.
    """
    members = ctx.members
    failing = members[4].name if ctx.fail and len(members) > 4 else None
    warning = members[6].name if len(members) > 6 else None
    warn = warn or ctx.logger.warning
    rng = random.Random(13)

    failed: list[SimMember] = []
    for member in members:
        start(member)
        error: BaseException | None = None
        if member.name == warning:
            warn(
                f"`{member.name}` exceeds available memory - "
                "extracting to disk"
            )
        duration = _DAY_SECONDS * rng.uniform(0.7, 1.3) / ctx.speed
        if member.name == failing:
            _sleep(duration / 2)
            error = SimConvertError(
                f"Could not parse `91.0000x` as Float64 in column "
                f"`Latitude` of `{member.name}`"
            )
            failed.append(member)
        else:
            _sleep(duration)
        done(member, error)
    return failed
