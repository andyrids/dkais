"""Terminal output for `dkais download` & `dkais process`.

Two renderings share one interface, chosen per stream as the TTY check in
`docs/toolchain-logging.md` requires:

- cargo (a terminal): right-aligned bold verbs above one transient bar
- plain (not a terminal): one ASCII line per event, no colour or animation

NOTE: Events & progress go to STDERR (`GLOBAL_CONSOLE`) and the closing
summary to STDOUT (`CLI_CONSOLE`), each rendered for its own stream.
"""

import logging
import threading
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from rich.console import Console, ConsoleOptions, RenderResult
from rich.filesize import decimal, pick_unit_and_suffix
from rich.measure import Measurement
from rich.progress import Progress, ProgressColumn, Task, TextColumn
from rich.table import Column
from rich.text import Text
from rich.traceback import Traceback

from dkais.logging import CLI_CONSOLE, GLOBAL_CONSOLE

VERB_WIDTH = 12
"""Width of the right-aligned verb column."""

_BAR_WIDTH = (12, 62)
"""Minimum & maximum width of the status bar, brackets included."""

_SUFFIXES = ["B", "kB", "MB", "GB", "TB"]


class Reporter(Protocol):
    """Receives item progress from a running command."""

    def advance(self, n: int) -> None:
        """Count `n` units (bytes) of progress."""

    def item_done(self, name: str, detail: str) -> None:
        """Report a completed item, with a short detail."""

    def item_failed(self, name: str, error: BaseException) -> None:
        """Report a failed item."""


def duration(seconds: float) -> str:
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


def plural(n: int, noun: str) -> str:
    """Return `n noun`, pluralised.

    Args:
        n: The count.
        noun: The singular noun.

    Returns:
        The count & noun.
    """
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _size_pair(done: int, total: int) -> str:
    """Format `done/total` in the unit of `total`, e.g. `2.1/3.3 GB`."""
    unit, suffix = pick_unit_and_suffix(total, _SUFFIXES, 1000)
    return f"{done / unit:.1f}/{total / unit:.1f} {suffix}"


def _reason(error: BaseException) -> str:
    """Return the first line of an error message, or its type name."""
    lines = str(error).strip().splitlines()
    return lines[0] if lines else type(error).__name__


def event(
    console: Console, verb: str, style: str, message: str | Text
) -> None:
    """Print a cargo event line, led by a right-aligned bold verb.

    NOTE: Wraps the message by hand, so continuation lines hang under it
    without trailing padding. The lines print in one call, so events from
    download worker threads never interleave.

    Args:
        console: The console to print to.
        verb: The verb, at most `VERB_WIDTH` characters.
        style: The colour of the verb.
        message: The event text; a `Text` keeps its own wrapping.
    """
    text = Text(message) if isinstance(message, str) else message
    room = max(console.width - VERB_WIDTH - 1, 20)
    rows = []
    for i, line in enumerate(text.wrap(console, room)):
        line.rstrip()
        head = Text(verb.rjust(VERB_WIDTH), style=f"bold {style}")
        rows.append(
            Text.assemble(head if i == 0 else " " * VERB_WIDTH, " ", line)
        )
    console.print(Text("\n").join(rows))


def notice(console: Console, verb: str, style: str, message: str) -> None:
    """Print an event, as a cargo line or a plain `VERB: message` line.

    Args:
        console: The console to print to.
        verb: The verb, e.g. `Warning`.
        style: The colour of the verb on a terminal.
        message: The event text.
    """
    if console.is_terminal:
        event(console, verb, style, message)
    else:
        _plain(console, f"{verb.upper()}: {message}")


def _plain(console: Console, line: str) -> None:
    """Print an unstyled line that never wraps, for logs & `grep`."""
    console.print(line, markup=False, highlight=False, soft_wrap=True)


class EventHandler(logging.Handler):
    """Render log records as events on the global console.

    NOTE: Used below `--debug`, where expected failures are reported by the
    commands and only unexpected errors carry a traceback.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Print the record as a `Warning` or `Error` event.

        Args:
            record: The log record.
        """
        try:
            console = GLOBAL_CONSOLE
            if record.levelno >= logging.ERROR:
                verb, style = "Error", "red"
            elif record.levelno >= logging.WARNING:
                verb, style = "Warning", "yellow"
            else:
                verb, style = "Info", "dim"
            notice(console, verb, style, record.getMessage())
            if record.exc_info and record.exc_info[1]:
                if console.is_terminal:
                    _, error, tb = record.exc_info
                    console.print(
                        Traceback.from_exception(type(error), error, tb)
                    )
                else:
                    text = logging.Formatter().formatException(record.exc_info)
                    _plain(console, text)
        except Exception:
            self.handleError(record)


class _Bar:
    """A cargo-like `[=====>   ]` bar, sized to its column."""

    def __init__(self, fraction: float) -> None:
        """Create the bar.

        Args:
            fraction: The completed share, between 0 and 1.
        """
        self._fraction = min(max(fraction, 0.0), 1.0)

    def __rich_measure__(
        self, console: Console, options: ConsoleOptions
    ) -> Measurement:
        """Measure the bar between its minimum & maximum width."""
        return Measurement(*_BAR_WIDTH)

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        """Render the bar to the allotted width."""
        width = max(options.max_width - 2, 1)
        filled = int(width * self._fraction)
        if filled >= width:
            inner = Text("=" * width, style="cyan")
        else:
            inner = Text.assemble(
                ("=" * filled + ">", "cyan"), " " * (width - filled - 1)
            )
        yield Text.assemble("[", inner, "]")


class _BarColumn(ProgressColumn):
    """The status bar column."""

    def render(self, task: Task) -> _Bar:
        """Render the bar for a task."""
        return _Bar(task.percentage / 100)


class _TailColumn(ProgressColumn):
    """Items done, then bytes & rate for transfers, then the ETA."""

    def __init__(self, *, transfer: bool) -> None:
        """Create the column.

        Args:
            transfer: Show bytes & rate, as for downloads.
        """
        # Reserve the widest typical tail, so the bar doesn't jitter
        width = 44 if transfer else 16
        super().__init__(table_column=Column(no_wrap=True, min_width=width))
        self._transfer = transfer

    def render(self, task: Task) -> Text:
        """Render e.g. `4/8  2.1/3.3 GB  480.0 MB/s  ETA 3.2s`."""
        parts = [f"{task.fields['done']}/{task.fields['items']}"]
        if self._transfer:
            speed = task.speed
            parts += [
                _size_pair(int(task.completed), int(task.total or 0)),
                f"{decimal(int(speed))}/s" if speed else "--/s",
            ]
        remaining = task.time_remaining
        # Rich rounds the estimate up to whole seconds, so show those
        eta = "--"
        if remaining:
            eta = (
                f"{remaining:.0f}s" if remaining < 60 else duration(remaining)
            )
        parts.append(f"ETA {eta}")
        return Text("  ".join(parts), style="dim")


class LiveReporter:
    """Cargo rendering: event lines above one transient status bar."""

    def __init__(
        self, progress: Progress, done_verb: str, *, counts_items: bool
    ) -> None:
        """Create the reporter.

        Args:
            progress: The started status bar display, with one task.
            done_verb: The verb for completed items, e.g. `Downloaded`.
            counts_items: Advance the bar per item, rather than per byte.
        """
        self._progress = progress
        self._done_verb = done_verb
        self._lock = threading.Lock()
        self._done = 0
        self._task = progress.tasks[0].id
        self._step = 1 if counts_items else 0

    def advance(self, n: int) -> None:
        """Advance the bar by `n` units."""
        self._progress.advance(self._task, n)

    def _count(self) -> None:
        """Count one finished item, done or failed."""
        with self._lock:
            self._done += 1
            self._progress.update(
                self._task, done=self._done, advance=self._step
            )

    def item_done(self, name: str, detail: str) -> None:
        """Print a `Downloaded name (detail)` line above the bar."""
        self._count()
        line = Text.assemble(
            name, (f" ({detail})", "dim"), no_wrap=True, overflow="ellipsis"
        )
        event(self._progress.console, self._done_verb, "green", line)

    def item_failed(self, name: str, error: BaseException) -> None:
        """Print a `Failed name: reason` line above the bar."""
        self._count()
        message = f"{name}: {_reason(error)}"
        event(self._progress.console, "Failed", "red", message)


class PlainReporter:
    """Plain rendering: one `[n/N] name  detail` line per item."""

    def __init__(self, console: Console, items: int) -> None:
        """Create the reporter.

        Args:
            console: The console to print to.
            items: The number of items expected.
        """
        self._console = console
        self._items = items
        self._lock = threading.Lock()
        self._done = 0

    def advance(self, n: int) -> None:
        """Ignore byte progress; plain output has no live display."""

    def _line(self, text: str) -> None:
        """Count one finished item & print its line."""
        with self._lock:
            self._done += 1
            _plain(self._console, f"[{self._done}/{self._items}] {text}")

    def item_done(self, name: str, detail: str) -> None:
        """Print `[n/N] name  detail`."""
        self._line(f"{name}  {detail}")

    def item_failed(self, name: str, error: BaseException) -> None:
        """Print `[n/N] name  FAILED: reason`."""
        self._line(f"{name}  FAILED: {_reason(error)}")


@contextmanager
def reporter(
    verb: str, done_verb: str, items: int, total: int | None = None
) -> Generator[Reporter, None, None]:
    """Report item progress for STDERR, cargo on a terminal, else plain.

    Args:
        verb: The status bar verb, e.g. `Download`.
        done_verb: The verb for completed items, e.g. `Downloaded`.
        items: The number of items expected.
        total: The bytes expected for a transfer, else bar counts items.

    Yields:
        The reporter for the running command.
    """
    console = GLOBAL_CONSOLE
    if not console.is_terminal:
        yield PlainReporter(console, items)
        return
    progress = Progress(
        TextColumn(
            "{task.description}",
            style="bold cyan",
            justify="right",
            table_column=Column(width=VERB_WIDTH, no_wrap=True),
        ),
        _BarColumn(),
        _TailColumn(transfer=total is not None),
        console=console,
        transient=True,
    )
    with progress:
        progress.add_task(
            verb,
            total=items if total is None else total,
            done=0,
            items=items,
        )
        yield LiveReporter(progress, done_verb, counts_items=total is None)


def _field(value: object) -> str:
    """Format a `key=value` value, quoting it when it holds a space."""
    text = str(value)
    return f'"{text}"' if " " in text else text


def summarize(
    noun: str,
    done: int,
    attempted: int,
    detail: str,
    dest: Path,
    fields: Mapping[str, object],
) -> None:
    """Print the failure count to STDERR & the run summary to STDOUT.

    NOTE: On a terminal, STDOUT gets a cargo `Finished` line; otherwise a
    single `key=value` line for scripts.

    Args:
        noun: The singular item noun, e.g. `archive`.
        done: The items completed.
        attempted: The items attempted.
        detail: Text after the count, e.g. ` (2.9 GB) in 7.2s`.
        dest: The output directory.
        fields: The `key=value` fields for plain output.
    """
    failed = attempted - done
    if failed:
        notice(
            GLOBAL_CONSOLE,
            "Failed",
            "red",
            f"{failed} of {plural(attempted, noun)}, re-run to retry",
        )
    if CLI_CONSOLE.is_terminal:
        count = plural(done, noun)
        if failed:
            count = f"{done} of {plural(attempted, noun)}"
        event(
            CLI_CONSOLE,
            "Finished",
            "yellow" if failed else "green",
            f"{count}{detail} → {dest}",
        )
    else:
        line = " ".join(f"{k}={_field(v)}" for k, v in fields.items())
        _plain(CLI_CONSOLE, line)
