"""Unit tests for terminal & plain output (`dkais._report`)."""

import io
import itertools
import logging
import threading
from pathlib import Path

import pytest
from rich.console import Console

from dkais import _report


def _console(*, terminal: bool, width: int = 80) -> Console:
    """Create an in-memory console, recording its text."""
    return Console(
        file=io.StringIO(), force_terminal=terminal, record=True, width=width
    )


@pytest.mark.parametrize(
    ("seconds", "text"), [(0.04, "0.0s"), (7.25, "7.2s"), (64, "1m04s")]
)
def test_duration(seconds: float, text: str) -> None:
    """Durations read as seconds, then minutes & seconds."""
    assert _report.duration(seconds) == text


def test_notice_wraps_with_hanging_indent() -> None:
    """Long messages wrap under the message, with no trailing spaces."""
    console = _console(terminal=True, width=40)
    _report.notice(console, "warning", "yellow", "word " * 12)

    lines = console.export_text().splitlines()
    assert lines[0].startswith("warning: word")
    assert all(line.startswith(" " * 9 + "word") for line in lines[1:])
    assert len(lines) > 1
    assert not any(line.endswith(" ") for line in lines)


def test_notice_is_plain_off_a_terminal() -> None:
    """Off a terminal, notices are `level: message` lines that never wrap."""
    console = _console(terminal=False, width=20)
    _report.notice(console, "warning", "yellow", "a long warning message")
    assert console.export_text() == "warning: a long warning message\n"


@pytest.mark.parametrize("terminal", [True, False])
def test_event_handler_renders_records(
    monkeypatch: pytest.MonkeyPatch, terminal: bool
) -> None:
    """Records render as notices; only `exc_info` adds a traceback."""
    console = _console(terminal=terminal, width=120)
    monkeypatch.setattr(_report, "GLOBAL_CONSOLE", console)
    logger = logging.getLogger("dkais.test_report")
    logger.addHandler(_report.EventHandler())
    logger.propagate = False

    logger.warning("Slow `%s`", "x.zip")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("Unexpected error")

    text = console.export_text()
    assert "warning: Slow `x.zip`\nerror: Unexpected error\n" in text
    assert "ValueError: boom" in text


def test_plain_reporter_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off a terminal, each item is one numbered line."""
    console = _console(terminal=False)
    monkeypatch.setattr(_report, "GLOBAL_CONSOLE", console)

    with _report.reporter("download", 2, total=10) as report:
        assert isinstance(report, _report.PlainReporter)
        report.item_started("a.zip")
        report.advance(10)
        report.item_done("a.zip", "4 B in 0.1s")
        report.item_failed("b.zip", OSError("disk full\nmore detail"))

    assert console.export_text() == (
        "[1/2] a.zip  4 B in 0.1s\n[2/2] b.zip  FAILED: disk full\n"
    )


def test_live_reporter_prints_only_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a terminal, only failures print a line above the transient bar."""
    console = _console(terminal=True)
    monkeypatch.setattr(_report, "GLOBAL_CONSOLE", console)

    with _report.reporter("process", 2) as report:
        assert isinstance(report, _report.LiveReporter)
        report.item_started("a.csv")
        report.item_done("a.csv", "a.parquet  1 MB in 0.1s")
        report.item_started("b.csv")
        report.item_failed("b.csv", ValueError("bad row"))

    text = console.export_text()
    assert "error: failed to process `b.csv`: bad row" in text
    assert "1 MB in 0.1s" not in text


def test_summarize_plain_is_key_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off a terminal, STDOUT gets `key=value` & STDERR the failed count."""
    err, out = _console(terminal=False), _console(terminal=False)
    monkeypatch.setattr(_report, "GLOBAL_CONSOLE", err)
    monkeypatch.setattr(_report, "CLI_CONSOLE", out)

    dest = Path("D:/My Data/raw")
    fields = {"downloaded": 7, "failed": 1, "dest": dest}
    _report.summarize("Downloaded", "archive", 7, 8, " in 1.0s", dest, fields)

    assert err.export_text() == (
        "error: 1 of 8 archives failed, re-run to retry\n"
    )
    assert out.export_text() == f'downloaded=7 failed=1 dest="{dest}"\n'


def test_summarize_terminal_is_one_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a terminal, STDOUT gets one `✓ Processed …` line."""
    err, out = _console(terminal=True), _console(terminal=True, width=120)
    monkeypatch.setattr(_report, "GLOBAL_CONSOLE", err)
    monkeypatch.setattr(_report, "CLI_CONSOLE", out)

    dest = Path("D:/raw")
    _report.summarize("Processed", "day", 9, 9, " in 1.0s", dest, {})

    assert err.export_text() == ""
    assert out.export_text() == f"✓ Processed 9 days in 1.0s → {dest}\n"


def test_events_from_threads_never_interleave() -> None:
    """A wrapped notice's lines stay together under concurrent printing."""
    console = _console(terminal=True, width=40)
    message = "word " * 15

    def emit() -> None:
        for _ in range(20):
            _report.notice(console, "warning", "yellow", message)

    threads = [threading.Thread(target=emit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = console.export_text().splitlines()
    heads = [i for i, line in enumerate(lines) if "warning:" in line]
    assert len(heads) == 8 * 20
    assert {b - a for a, b in itertools.pairwise(heads)} == {3}
