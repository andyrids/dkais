"""Unit tests for the `dkais` CLI (`dkais.__main__`, `dkais._cli`)."""

import argparse
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from dkais import __main__, _cli, _s3, exceptions
from dkais._core import ExitCode, resolve_data_dir

CLI = "dkais._cli"

Samples = dict[str, tuple[str, str]]
ZipWriter = Callable[[Path, dict[str, str]], Path]


def _run_main(argv: list[str]) -> int:
    """Run `dkais.__main__.main` with `argv` & return the exit code."""
    with (
        mock.patch("sys.argv", ["dkais", *argv]),
        mock.patch("dkais.__main__.configure_cli_logging"),
        pytest.raises(SystemExit) as exc_info,
    ):
        __main__.main()

    code = exc_info.value.code
    assert isinstance(code, int)
    return code


@pytest.mark.parametrize(
    ("value", "start", "end"),
    [
        ("2006", date(2006, 1, 1), date(2006, 12, 31)),
        ("2024-02", date(2024, 2, 1), date(2024, 2, 29)),
        ("2026-10-18", date(2026, 10, 18), date(2026, 10, 18)),
    ],
)
def test_parse_dates(value: str, start: date, end: date) -> None:
    """Partial dates widen to the first or last day they cover."""
    assert _cli._parse_start(value) == start
    assert _cli._parse_end(value) == end


@pytest.mark.parametrize("value", ["26", "2026-13", "2026-02-30", "2026/10"])
def test_parse_dates_rejects_invalid(value: str) -> None:
    """Malformed or impossible dates are argparse type errors."""
    with pytest.raises(argparse.ArgumentTypeError):
        _cli._parse_start(value)


def test_resolve_data_dir_env_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag wins, then `DKAIS_DATA_DIR`, else an error."""
    monkeypatch.setenv("DKAIS_DATA_DIR", str(tmp_path / "env"))
    assert resolve_data_dir(tmp_path / "flag") == tmp_path / "flag"
    assert resolve_data_dir(None) == tmp_path / "env"

    monkeypatch.delenv("DKAIS_DATA_DIR")
    with pytest.raises(exceptions.DataDirNotSetError):
        resolve_data_dir(None)


def test_main_without_command_shows_help(
    capsys: pytest.CaptureFixture,
) -> None:
    """`dkais` prints a header & help, exiting 0."""
    assert _run_main([]) == ExitCode.EX_OK
    assert "COMMANDS" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["download"],
        ["process"],
        ["download", "--all", "--start", "2026"],
    ],
)
def test_main_requires_selection(data_dir: Path, argv: list[str]) -> None:
    """`download`/`process` need `--start` or `--all`, not both."""
    assert _run_main([*argv, "--data-dir", str(data_dir)]) == ExitCode.EX_USAGE


def test_main_maps_missing_data_dir_to_usage(data_dir: Path) -> None:
    """No `--data-dir` & no `DKAIS_DATA_DIR` is a usage error."""
    assert _run_main(["process", "--all"]) == ExitCode.EX_USAGE


def test_main_maps_error_to_exit_1(data_dir: Path) -> None:
    """An `Error` maps to exit code 1."""
    error = exceptions.CSVDialectError("bad")
    with mock.patch(f"{CLI}.command_process", side_effect=error):
        assert _run_main(["process", "--all"]) == ExitCode.EX_FAILURE


def test_download_interrupt_exits_130(data_dir: Path) -> None:
    """Ctrl-C during downloads exits with `EX_INTERRUPT`."""
    archive = _s3.parse_key("aisdk-2026-10-18.zip", 1)
    with (
        mock.patch(f"{CLI}._s3.make_client"),
        mock.patch(f"{CLI}._s3.list_archives", return_value=[archive]),
        mock.patch(
            f"{CLI}._download.download_archives", side_effect=KeyboardInterrupt
        ),
    ):
        exit_code = _run_main(
            ["download", "--all", "--data-dir", str(data_dir)]
        )
    assert exit_code == ExitCode.EX_INTERRUPT


def test_process_converts_selected_days(
    data_dir: Path, samples: Samples, write_zip: ZipWriter
) -> None:
    """`process` converts in-range days once, and again with `--force`."""
    name, text = samples["comma-22"]
    write_zip(
        data_dir / "raw/2006/aisdk-2006-03.zip",
        {name: text, "aisdk_20060303.csv": text},
    )
    argv = ["process", "--start", "2006-03-02", "--end", "2006-03-02"]
    argv += ["--data-dir", str(data_dir)]
    target = data_dir / "parquet/YEAR=2006/MONTH=3/aisdk-2006-03-02.parquet"

    assert _run_main(argv) == ExitCode.EX_OK
    assert [p.name for p in (data_dir / "parquet").rglob("*.parquet")] == [
        target.name
    ]

    mtime = target.stat().st_mtime_ns
    assert _run_main(argv) == ExitCode.EX_OK
    assert target.stat().st_mtime_ns == mtime

    assert _run_main([*argv, "--force"]) == ExitCode.EX_OK
    assert target.stat().st_mtime_ns != mtime


@pytest.mark.parametrize("available", [0, 1 << 40], ids=["file", "memory"])
def test_process_extracts_ahead(
    data_dir: Path, samples: Samples, write_zip: ZipWriter, available: int
) -> None:
    """Every day converts, in or out of memory, leaving no temp files."""
    name, text = samples["comma-22"]
    days = [f"aisdk_200603{d:02d}.csv" for d in (1, 2, 3)]
    write_zip(
        data_dir / "raw/2006/aisdk-2006-03.zip", dict.fromkeys(days, text)
    )
    argv = ["process", "--all", "--data-dir", str(data_dir)]

    memory = mock.Mock(available=available)
    with mock.patch(f"{CLI}.psutil.virtual_memory", return_value=memory):
        assert _run_main(argv) == ExitCode.EX_OK

    written = sorted(p.name for p in (data_dir / "parquet").rglob("*.*"))
    assert written == [f"aisdk-2006-03-0{d}.parquet" for d in (1, 2, 3)]
    assert not list(data_dir.glob("tmp/*"))


def test_process_reports_failed_days(
    data_dir: Path, write_zip: ZipWriter, capsys: pytest.CaptureFixture
) -> None:
    """A day with an unknown dialect fails the run with exit code 1."""
    write_zip(
        data_dir / "raw/2026/aisdk-2026-10-18.zip",
        {"aisdk-2026-10-18.csv": "a;b"},
    )
    argv = ["process", "--all", "--data-dir", str(data_dir)]
    assert _run_main(argv) == ExitCode.EX_FAILURE

    # Not a terminal, so plain lines - no traceback for an expected failure
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "[1/1] aisdk-2026-10-18.csv  FAILED: " in output
    assert "error: 1 of 1 day failed, re-run to retry" in output
    assert "Traceback" not in output
    assert "processed=0 already_processed=0 failed=1 " in captured.out


def test_download_summary_is_key_value(
    data_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """Off a terminal, the download summary is one `key=value` line."""
    archive = _s3.parse_key("aisdk-2026-10-18.zip", 5)
    with (
        mock.patch(f"{CLI}._s3.make_client"),
        mock.patch(f"{CLI}._s3.list_archives", return_value=[archive]),
        mock.patch(f"{CLI}._download.download_archives", return_value=[]),
    ):
        exit_code = _run_main(
            ["download", "--all", "--data-dir", str(data_dir)]
        )
    assert exit_code == ExitCode.EX_OK
    summary = capsys.readouterr().out.splitlines()[-1]
    assert summary.startswith(
        "downloaded=1 bytes=5 already_present=0 failed=0 elapsed="
    )


def _list_one_processed_day(data_dir: Path, *flags: str) -> int:
    """Run `list` over a downloaded March 2006 archive with 1 day processed."""
    archive = _s3.parse_key("2006/aisdk-2006-03.zip", 3)
    assert archive is not None
    path = archive.local_path(data_dir / "raw")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"xyz")
    day = data_dir / "parquet/YEAR=2006/MONTH=3/aisdk-2006-03-02.parquet"
    day.parent.mkdir(parents=True)
    day.touch()

    with (
        mock.patch(f"{CLI}._s3.make_client"),
        mock.patch(f"{CLI}._s3.list_archives", return_value=[archive]),
    ):
        return _run_main(["list", *flags, "--data-dir", str(data_dir)])


def test_list_table_shows_processed_of_period(
    data_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """`list` shows processed days out of the days the archive covers."""
    assert _list_one_processed_day(data_dir) == ExitCode.EX_OK
    header, *rows = capsys.readouterr().out.strip().splitlines()
    assert header.split()[-1] == "Processed"
    assert rows[1].split()[-1] == "1/31"


def test_list_warns_without_data_dir(
    data_dir: Path,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a data dir, `list` omits local state & says why."""
    archive = _s3.parse_key("2006/aisdk-2006-03.zip", 3)
    with (
        mock.patch(f"{CLI}._s3.make_client"),
        mock.patch(f"{CLI}._s3.list_archives", return_value=[archive]),
    ):
        assert _run_main(["list"]) == ExitCode.EX_OK
    assert "Processed" not in capsys.readouterr().out
    assert "DKAIS_DATA_DIR" in caplog.text


def test_list_json_includes_local_state(
    data_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """`list --json` reports download & processed state per archive."""
    assert _list_one_processed_day(data_dir, "--json") == ExitCode.EX_OK
    assert json.loads(capsys.readouterr().out) == [
        {
            "key": "2006/aisdk-2006-03.zip",
            "start": "2006-03-01",
            "end": "2006-03-31",
            "size": 3,
            "downloaded": True,
            "processed_days": 1,
        }
    ]
