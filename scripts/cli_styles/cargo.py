"""Cargo style - permanent event lines above one live status bar.

Every finished item scrolls up as a line led by a right-aligned bold verb
column, while a single `Download` / `Process` bar stays pinned at the bottom.
The bar is removed when the run ends, leaving only the event log and a
`Finished` line.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from _sim import (
    DemoContext,
    SimArchive,
    SimMember,
    simulate_download,
    simulate_process,
)
from rich.console import Console, ConsoleOptions, RenderResult
from rich.filesize import decimal, pick_unit_and_suffix
from rich.live import Live
from rich.text import Text

from dkais._core import ExitCode

_VERB_WIDTH = 12
_MAX_BAR = 60
_RATE_WINDOW = 3.0
_SUFFIXES = ["B", "kB", "MB", "GB", "TB"]


def _duration(seconds: float) -> str:
    """Format a duration as `7.2s` or `1m04s`.

    Args:
        seconds: The duration in seconds.

    Returns:
        The formatted duration.
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m{rest:02d}s"


def _size_pair(done: int, total: int) -> str:
    """Format `done/total` sharing the unit of `total`, e.g. `2.1/3.3 GB`.

    Args:
        done: The bytes transferred.
        total: The bytes expected.

    Returns:
        The formatted pair.
    """
    unit, suffix = pick_unit_and_suffix(total, _SUFFIXES, 1000)
    return f"{done / unit:.1f}/{total / unit:.1f} {suffix}"


def _event(
    console: Console, verb: str, style: str, message: str | Text
) -> None:
    """Print one event line with a right-aligned bold verb column.

    NOTE: Wraps the message by hand, so continuation lines hang under it and
    carry no trailing padding into logs. The lines print in one call, so
    events from download worker threads never interleave.

    Args:
        console: The console to print to.
        verb: The verb, at most 12 characters.
        style: The colour of the verb.
        message: The event text; a `Text` keeps its own wrapping.
    """
    text = Text(message) if isinstance(message, str) else message
    room = max(console.width - _VERB_WIDTH - 1, 20)
    rows = []
    for i, line in enumerate(text.wrap(console, room)):
        line.rstrip()
        head = Text(verb.rjust(_VERB_WIDTH), style=f"bold {style}")
        rows.append(
            Text.assemble(head if i == 0 else " " * _VERB_WIDTH, " ", line)
        )
    console.print(Text("\n").join(rows))


def _line(main: str, detail: str) -> Text:
    """Build a single-line event message with a dim detail.

    Args:
        main: The primary text.
        detail: The dim trailing text.

    Returns:
        The message, ellipsized rather than wrapped.
    """
    return Text.assemble(
        main, (f" {detail}", "dim"), no_wrap=True, overflow="ellipsis"
    )


def _bar(fraction: float, width: int) -> Text:
    """Draw a cargo-like `=====>` bar.

    Args:
        fraction: Completed share between 0 and 1.
        width: The bar width in characters.

    Returns:
        The bar.
    """
    filled = int(width * min(max(fraction, 0.0), 1.0))
    if filled >= width:
        return Text("=" * width, style="cyan")
    return Text.assemble(
        ("=" * filled + ">", "cyan"), " " * (width - filled - 1)
    )


class _Status:
    """The live bottom line: `Verb [=====>   ] tail`."""

    def __init__(
        self,
        verb: str,
        fraction: Callable[[], float],
        tail: Callable[[], str],
    ) -> None:
        """Create the status line.

        Args:
            verb: The cyan verb.
            fraction: Returns the completed share.
            tail: Returns the text after the bar.
        """
        self._verb = verb
        self._fraction = fraction
        self._tail = tail

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        """Render the line, sizing the bar to the remaining width."""
        tail = self._tail()
        room = options.max_width - _VERB_WIDTH - 3 - len(tail)
        bar = _bar(self._fraction(), max(8, min(room, _MAX_BAR)))
        yield Text.assemble(
            (f"{self._verb:>{_VERB_WIDTH}}", "bold cyan"),
            " [",
            bar,
            "]",
            tail,
            no_wrap=True,
            overflow="ellipsis",
        )


class _Tracker:
    """Thread-safe byte & item counters behind the download bar."""

    def __init__(self, items: int, total: int) -> None:
        """Create the tracker.

        Args:
            items: The archives expected.
            total: The bytes expected.
        """
        self._items = items
        self._total = total
        self._lock = threading.Lock()
        self._began: dict[str, float] = {}
        self._samples: deque[tuple[float, int]] = deque()
        self._sent = 0
        self._finished = 0
        self._t0 = time.monotonic()

    def start(self, name: str) -> None:
        """Record when an archive starts."""
        with self._lock:
            self._began[name] = time.monotonic()

    def advance(self, n: int) -> None:
        """Count transferred bytes."""
        with self._lock:
            self._sent += n
            self._samples.append((time.monotonic(), n))

    def finish(self, name: str) -> float:
        """Count a finished archive, returning its elapsed seconds."""
        with self._lock:
            self._finished += 1
            return time.monotonic() - self._began.pop(name)

    def fraction(self) -> float:
        """Return the completed byte share."""
        with self._lock:
            return self._sent / self._total if self._total else 1.0

    def tail(self) -> str:
        """Return the count, size, rate & ETA text."""
        now = time.monotonic()
        with self._lock:
            while self._samples and now - self._samples[0][0] > _RATE_WINDOW:
                self._samples.popleft()
            span = min(_RATE_WINDOW, now - self._t0)
            moved = sum(n for _, n in self._samples)
            rate = moved / span if span > 0.2 else 0.0
            eta = "--"
            if rate > 0:
                eta = _duration((self._total - self._sent) / rate)
            return (
                f"  {self._finished}/{self._items}"
                f"  {_size_pair(self._sent, self._total)}"
                f"  {decimal(int(rate)) if rate else '--'}/s"
                f"  ETA {eta}"
            )


def download(ctx: DemoContext) -> int:
    """Render a simulated download in cargo style.

    Args:
        ctx: The demo context.

    Returns:
        The exit code.
    """
    err = ctx.err
    pending = ctx.pending
    tracker = _Tracker(len(pending), sum(a.size for a in pending))
    t0 = time.monotonic()

    def start(archive: SimArchive) -> None:
        """Record when an archive starts."""
        tracker.start(archive.name)

    def advance(archive: SimArchive, n: int) -> None:
        """Count transferred bytes."""
        tracker.advance(n)

    def done(archive: SimArchive, error: BaseException | None) -> None:
        """Print the finished or failed archive."""
        elapsed = tracker.finish(archive.name)
        if error is None:
            detail = f"({decimal(archive.size)} in {_duration(elapsed)})"
            _event(err, "Downloaded", "green", _line(archive.name, detail))
        else:
            _event(err, "Failed", "red", str(error))

    def warn(message: str) -> None:
        """Print a warning above the bar."""
        _event(err, "Warning", "yellow", message)

    _event(
        err,
        "Resolving",
        "cyan",
        f"{len(ctx.archives)} archives in range "
        f"({len(ctx.downloaded)} already present)",
    )
    try:
        with Live(
            _Status("Download", tracker.fraction, tracker.tail),
            console=err,
            refresh_per_second=10,
            transient=True,
        ):
            failed = simulate_download(
                ctx, start=start, advance=advance, done=done, warn=warn
            )
    except KeyboardInterrupt:
        _event(
            err,
            "Interrupted",
            "yellow",
            "partial downloads discarded, re-run to resume",
        )
        return ExitCode.EX_INTERRUPT

    elapsed = _duration(time.monotonic() - t0)
    ok = len(pending) - len(failed)
    size = decimal(sum(a.size for a in pending if a not in failed))
    count = f"{ok} archives"
    if failed:
        count = f"{ok} of {len(pending)} archives"
        _event(
            err,
            "Failed",
            "red",
            f"{len(failed)} of {len(pending)} archives, re-run to retry",
        )
    _event(
        ctx.out,
        "Finished",
        "yellow" if failed else "green",
        f"{count} ({size}) in {elapsed} → {ctx.raw_dir}",
    )
    return ExitCode.EX_FAILURE if failed else ExitCode.EX_OK


def process(ctx: DemoContext) -> int:
    """Render a simulated process run in cargo style.

    Args:
        ctx: The demo context.

    Returns:
        The exit code.
    """
    err = ctx.err
    members = ctx.members
    lock = threading.Lock()
    began: dict[str, float] = {}
    count = 0
    current = ""
    t0 = time.monotonic()

    def fraction() -> float:
        """Return the completed day share."""
        with lock:
            return count / len(members) if members else 1.0

    def tail() -> str:
        """Return the count, current day & ETA text."""
        with lock:
            eta = "--"
            if count:
                spent = time.monotonic() - t0
                eta = _duration(spent / count * (len(members) - count))
            return f"  {count}/{len(members)}  {current}  ETA {eta}"

    def start(member: SimMember) -> None:
        """Record the day being converted."""
        nonlocal current
        with lock:
            current = str(member.day)
            began[member.name] = time.monotonic()

    def done(member: SimMember, error: BaseException | None) -> None:
        """Print the processed or failed day."""
        nonlocal count
        with lock:
            count += 1
            elapsed = time.monotonic() - began.pop(member.name)
        if error is not None:
            _event(err, "Failed", "red", str(error))
            return
        target = member.target(ctx.parquet_dir).relative_to(ctx.parquet_dir)
        # The partition path already names the day, so drop the day first
        # when the line would not fit
        detail = f"({decimal(member.output_size)}, {_duration(elapsed)})"
        path = target.as_posix()
        full = f"{member.day} → {path}"
        room = err.width - _VERB_WIDTH - 1
        main = full if len(full) + len(detail) + 2 <= room else path
        _event(err, "Processed", "green", _line(main, detail))

    def warn(message: str) -> None:
        """Print a warning above the bar."""
        _event(err, "Warning", "yellow", message)

    _event(
        err,
        "Resolving",
        "cyan",
        f"{len(members)} days in range {ctx.start}..{ctx.end}",
    )
    try:
        with Live(
            _Status("Process", fraction, tail),
            console=err,
            refresh_per_second=10,
            transient=True,
        ):
            failed = simulate_process(ctx, start=start, done=done, warn=warn)
    except KeyboardInterrupt:
        _event(
            err,
            "Interrupted",
            "yellow",
            "partial output discarded, re-run to resume",
        )
        return ExitCode.EX_INTERRUPT

    elapsed = _duration(time.monotonic() - t0)
    ok = len(members) - len(failed)
    days = f"{ok} days"
    if failed:
        days = f"{ok} of {len(members)} days"
        _event(
            err,
            "Failed",
            "red",
            f"{len(failed)} of {len(members)} days, re-run to retry",
        )
    _event(
        ctx.out,
        "Finished",
        "yellow" if failed else "green",
        f"{days} processed in {elapsed} → {ctx.parquet_dir}",
    )
    return ExitCode.EX_FAILURE if failed else ExitCode.EX_OK
