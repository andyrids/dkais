"""Minimal style - quiet, uv-like output.

One dim intro line, one transient overall progress line (no per-file rows),
single-line `warning:` / `error:` messages printed above the bar, and a one
line success summary on STDOUT.
"""

import threading
import time

from _sim import (
    DemoContext,
    SimArchive,
    SimMember,
    simulate_download,
    simulate_process,
)
from rich.console import Console
from rich.filesize import decimal
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TransferSpeedColumn,
)
from rich.table import Column, Table
from rich.text import Text

from dkais._core import ExitCode

_GLYPHS = {"dot": "·", "arrow": "→", "tick": "✓", "bang": "!"}
_ASCII = {"dot": "|", "arrow": "->", "tick": "OK", "bang": "!"}
_INTERRUPTED = "interrupted - partial {} discarded, re-run to resume"


def _glyphs(console: Console) -> dict[str, str]:
    """Return unicode glyphs, or ASCII ones when the stream cannot encode.

    Args:
        console: The console that will print them.

    Returns:
        The glyph table.
    """
    utf = console.encoding.lower().replace("-", "").startswith("utf")
    return _GLYPHS if utf else _ASCII


def _duration(seconds: float) -> str:
    """Format a duration like `7.2s` or `1m04s`.

    Args:
        seconds: The duration in seconds.

    Returns:
        The formatted duration.
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m{rest:02d}s"


def _plural(n: int, word: str) -> str:
    """Return `n word`, pluralised.

    Args:
        n: The count.
        word: The singular noun.

    Returns:
        The count and noun.
    """
    return f"{n} {word}{'' if n == 1 else 's'}"


def _count(done: int, total: int, word: str) -> str:
    """Return `7 archives`, or `7 of 8 archives` when some failed.

    Args:
        done: The completed count.
        total: The attempted count.
        word: The singular noun.

    Returns:
        The count and noun.
    """
    return (
        _plural(done, word)
        if done == total
        else (f"{done} of {_plural(total, word)}")
    )


def _message(prefix: str, style: str, text: str) -> Table:
    """Build a `prefix: text` line with a bold coloured prefix.

    NOTE: A grid gives wrapped text a hanging indent under the message.

    Args:
        prefix: The lowercase prefix, e.g. `warning:`.
        style: The prefix colour.
        text: The message.

    Returns:
        The renderable line.
    """
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(Text(prefix, style=f"bold {style}"), Text(text))
    return grid


class _ETAColumn(ProgressColumn):
    """Time remaining, formatted like the summary durations."""

    def render(self, task: Task) -> Text:
        """Render `ETA 3.0s`, or nothing until an estimate exists.

        Args:
            task: The progress task.

        Returns:
            The rendered column.
        """
        remaining = task.time_remaining
        if remaining is None or task.finished:
            return Text("")
        return Text(f"ETA {_duration(remaining)}", style="cyan")


def _summary(
    ctx: DemoContext, verb: str, done: str, failed: int, detail: str
) -> Text:
    """Build the STDOUT summary, marked `!` when anything failed.

    Args:
        ctx: The demo context.
        verb: The past-tense verb, e.g. `Downloaded`.
        done: The completed count, e.g. `7 of 8 archives (2.9 GB)`.
        failed: The failed count.
        detail: The trailing `in 7.2s → dir` detail.

    Returns:
        The summary line.
    """
    glyphs = _glyphs(ctx.out)
    mark = (glyphs["bang"], "yellow") if failed else (glyphs["tick"], "green")
    return Text.assemble((f"{mark[0]} ", mark[1]), f"{verb} {done} {detail}")


def _progress(ctx: DemoContext, *columns: ProgressColumn) -> Progress:
    """Create the transient single-line progress display.

    Args:
        ctx: The demo context.
        *columns: Columns placed between the bar and the active names.

    Returns:
        The progress display.
    """
    names = TextColumn(
        "{task.fields[active]}",
        style="dim",
        table_column=Column(no_wrap=True, overflow="ellipsis", ratio=1),
    )
    spinner = "dots" if _glyphs(ctx.err) is _GLYPHS else "line"
    return Progress(
        SpinnerColumn(spinner, style="cyan"),
        BarColumn(bar_width=20, complete_style="cyan", finished_style="green"),
        *columns,
        names,
        console=ctx.err,
        transient=True,
        expand=True,
    )


def download(ctx: DemoContext) -> int:
    """Render a simulated download in the minimal style.

    Args:
        ctx: The demo context.

    Returns:
        The exit code.
    """
    err = ctx.err
    dot = _glyphs(err)["dot"]
    pending = ctx.pending
    total = sum(a.size for a in pending)
    err.print(
        f"{_plural(len(pending), 'archive')} to download {dot} "
        f"{decimal(total)} {dot} {len(ctx.downloaded)} already present",
        style="dim",
        highlight=False,
    )

    lock = threading.Lock()
    active: list[str] = []
    started = time.monotonic()

    try:
        with _progress(
            ctx,
            DownloadColumn(),
            TransferSpeedColumn(),
            _ETAColumn(),
        ) as progress:
            task: TaskID = progress.add_task("", total=total, active="")

            def start(archive: SimArchive) -> None:
                with lock:
                    active.append(archive.name)
                    progress.update(task, active=" ".join(active))

            def advance(archive: SimArchive, n: int) -> None:
                progress.advance(task, n)

            def done(archive: SimArchive, error: BaseException | None) -> None:
                with lock:
                    active.remove(archive.name)
                    progress.update(task, active=" ".join(active))
                if error:
                    progress.console.print(
                        _message("error:", "red", str(error)), highlight=False
                    )

            def warn(message: str) -> None:
                progress.console.print(
                    _message("warning:", "yellow", message), highlight=False
                )

            failed = simulate_download(
                ctx, start=start, advance=advance, done=done, warn=warn
            )
    except KeyboardInterrupt:
        err.print(
            _message("warning:", "yellow", _INTERRUPTED.format("downloads"))
        )
        return ExitCode.EX_INTERRUPT

    elapsed = _duration(time.monotonic() - started)
    ok = [a for a in pending if a not in failed]
    size = decimal(sum(a.size for a in ok))
    if failed:
        err.print(
            _message(
                "error:",
                "red",
                f"{len(failed)} of {_plural(len(pending), 'archive')} "
                "failed to download",
            ),
            highlight=False,
        )
    arrow = _glyphs(ctx.out)["arrow"]
    ctx.out.print(
        _summary(
            ctx,
            "Downloaded",
            _count(len(ok), len(pending), "archive") + f" ({size})",
            len(failed),
            f"in {elapsed} {arrow} {ctx.raw_dir}",
        ),
        highlight=False,
    )
    return ExitCode.EX_FAILURE if failed else ExitCode.EX_OK


def process(ctx: DemoContext) -> int:
    """Render a simulated process run in the minimal style.

    Args:
        ctx: The demo context.

    Returns:
        The exit code.
    """
    err = ctx.err
    dot = _glyphs(err)["dot"]
    members = ctx.members
    err.print(
        f"{_plural(len(members), 'day')} to process {dot} "
        f"{decimal(sum(m.size for m in members))} CSV {dot} "
        f"{members[0].day} to {members[-1].day}",
        style="dim",
        highlight=False,
    )

    started = time.monotonic()
    try:
        with _progress(ctx, MofNCompleteColumn(), _ETAColumn()) as progress:
            task = progress.add_task("", total=len(members), active="")

            def start(member: SimMember) -> None:
                progress.update(task, active=member.name)

            def done(member: SimMember, error: BaseException | None) -> None:
                if error:
                    progress.console.print(
                        _message("error:", "red", str(error)), highlight=False
                    )
                progress.advance(task)

            def warn(message: str) -> None:
                progress.console.print(
                    _message("warning:", "yellow", message), highlight=False
                )

            failed = simulate_process(ctx, start=start, done=done, warn=warn)
    except KeyboardInterrupt:
        err.print(
            _message("warning:", "yellow", _INTERRUPTED.format("output"))
        )
        return ExitCode.EX_INTERRUPT

    elapsed = _duration(time.monotonic() - started)
    if failed:
        err.print(
            _message(
                "error:",
                "red",
                f"{len(failed)} of {_plural(len(members), 'day')} "
                "failed to process",
            ),
            highlight=False,
        )
    arrow = _glyphs(ctx.out)["arrow"]
    ctx.out.print(
        _summary(
            ctx,
            "Processed",
            _count(len(members) - len(failed), len(members), "day"),
            len(failed),
            f"in {elapsed} {arrow} {ctx.parquet_dir}",
        ),
        highlight=False,
    )
    return ExitCode.EX_FAILURE if failed else ExitCode.EX_OK
