"""Unit tests for terminal & plain output (`dkais._report`)."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from dkais import _report


def _console(*, terminal: bool, width: int = 80) -> Console:
    """Create an in-memory console, as pyclig creates them for a run."""
    return Console(
        file=io.StringIO(),
        force_terminal=terminal,
        width=width,
        color_system=None,
        markup=False,
    )


def _text(console: Console) -> str:
    """Return everything written to an in-memory console."""
    assert isinstance(console.file, io.StringIO)
    return console.file.getvalue()


@pytest.mark.parametrize(
    ("seconds", "text"), [(0.04, "0.0s"), (7.25, "7.2s"), (64, "1m04s")]
)
def test_duration(seconds: float, text: str) -> None:
    """Durations read as seconds, then minutes & seconds."""
    assert _report.duration(seconds) == text


def test_plain_reporter_lines() -> None:
    """Off a terminal, each item is one numbered line."""
    console = _console(terminal=False)

    with _report.reporter(console, "download", 2, total=10) as report:
        assert isinstance(report, _report.PlainReporter)
        report.item_started("a.zip")
        report.advance(10)
        report.item_done("a.zip", "4 B in 0.1s")
        report.item_failed("b.zip", OSError("disk full\nmore detail"))

    assert _text(console) == (
        "[1/2] a.zip  4 B in 0.1s\n[2/2] b.zip  FAILED: disk full\n"
    )


def test_live_reporter_prints_only_failures() -> None:
    """On a terminal, only failures print a line above the transient bar."""
    console = _console(terminal=True)

    with _report.reporter(console, "process", 2) as report:
        assert isinstance(report, _report.LiveReporter)
        report.item_started("a.csv")
        report.item_done("a.csv", "a.parquet  1 MB in 0.1s")
        report.item_started("b.csv")
        report.item_failed("b.csv", ValueError("bad row"))

    text = _text(console)
    assert "error: failed to process `b.csv`: bad row" in text
    assert "1 MB in 0.1s" not in text


def test_summarise_plain_is_key_value() -> None:
    """Off a terminal, STDOUT gets `key=value` & STDERR the failed count."""
    err, out = _console(terminal=False), _console(terminal=False)

    dest = Path("D:/My Data/raw")
    fields = {"downloaded": 7, "failed": 1, "dest": dest}
    _report.summarise(
        "Downloaded",
        "archive",
        7,
        8,
        " in 1.0s",
        dest,
        fields,
        out=out,
        err=err,
    )

    assert _text(err) == "error: 1 of 8 archives failed, re-run to retry\n"
    assert _text(out) == f'downloaded=7 failed=1 dest="{dest}"\n'


def test_summarise_terminal_is_one_line() -> None:
    """On a terminal, STDOUT gets one `✓ Processed …` line."""
    err, out = _console(terminal=True), _console(terminal=True, width=120)

    dest = Path("D:/raw")
    _report.summarise(
        "Processed", "day", 9, 9, " in 1.0s", dest, {}, out=out, err=err
    )

    assert _text(err) == ""
    assert _text(out) == f"✓ Processed 9 days in 1.0s → {dest}\n"
