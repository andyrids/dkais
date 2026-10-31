"""Unit tests for the `dkais` CLI (`dkais.__main__`, `dkais._cli`)."""

import importlib.util
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

import click
import pytest
from botocore.stub import Stubber
from click.testing import Result
from pyclig import ExitCode
from pyclig.testing import invoke

from dkais import _cli, _s3, exceptions
from dkais.__main__ import cli

Samples = dict[str, tuple[str, str]]
ZipWriter = Callable[[Path, dict[str, str]], Path]


def _run(argv: list[str], **kwargs: Any) -> Result:
    """Run the CLI in-process, isolated from the shell's `DKAIS_DATA_DIR`."""
    env = {"DKAIS_DATA_DIR": None, **kwargs.pop("env", {})}
    return invoke(cli, argv, env=env, **kwargs)


def _subprocess_env(**env: str) -> dict[str, str]:
    """Return a clean environment for a `python -m dkais` subprocess."""
    cleared = ("DKAIS_DATA_DIR", "FORCE_COLOR", "NO_COLOR", "TTY_COMPATIBLE")
    base = {k: v for k, v in os.environ.items() if k not in cleared}
    return base | env


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
    assert _cli.DateBound(end=False).convert(value, None, None) == start
    assert _cli.DateBound(end=True).convert(value, None, None) == end


@pytest.mark.parametrize("value", ["26", "2026-13", "2026-02-30", "2026/10"])
def test_parse_dates_rejects_invalid(value: str) -> None:
    """Malformed or impossible dates are click parameter errors."""
    with pytest.raises(click.BadParameter, match="invalid date"):
        _cli.DateBound(end=False).convert(value, None, None)


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_invalid_jobs_is_a_usage_error(data_dir: Path, value: str) -> None:
    """An invalid `--jobs` fails at parse time with the usage exit code."""
    argv = ["download", "--all", "--jobs", value, "--data-dir", str(data_dir)]
    result = _run(argv)
    assert result.exit_code == ExitCode.USAGE
    assert "'-j' / '--jobs'" in result.stderr


def test_main_without_command_shows_concise_help() -> None:
    """`dkais` prints a header & concise help to STDOUT, exiting 0."""
    result = _run([])
    assert result.exit_code == ExitCode.OK
    assert result.stdout.startswith("DMA AIS data manager [dkais ")
    assert "COMMANDS:" in result.stdout


def test_commands_listed_in_workflow_order() -> None:
    """`COMMANDS:` lists `list`, `download` & `process` in that order."""
    lines = _run(["--help"]).stdout.splitlines()
    start = lines.index("COMMANDS:") + 1
    assert [line.split()[0] for line in lines[start : start + 3]] == [
        "list",
        "download",
        "process",
    ]


@pytest.mark.parametrize("command", ["download", "process"])
def test_bare_command_prints_help_to_stderr(command: str) -> None:
    """`download`/`process` without arguments print their help to STDERR."""
    result = _run([command])
    assert result.exit_code == ExitCode.USAGE
    assert result.stdout == ""
    assert result.stderr.startswith(f"USAGE:\n  dkais {command} [options]")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["download"], "Select archives with `--start` or `--all`"),
        (["process", "--end", "2026"], "Select archives with `--start`"),
        (
            ["download", "--all", "--start", "2026"],
            "`--all` cannot be combined with `--start`/`--end`",
        ),
    ],
)
def test_main_requires_selection(
    data_dir: Path, argv: list[str], message: str
) -> None:
    """`download`/`process` need `--start` or `--all`, not both."""
    result = _run([*argv, "--data-dir", str(data_dir)])
    assert result.exit_code == ExitCode.USAGE
    assert result.stderr.startswith(f"error: {message}")


def test_missing_data_dir_names_env_var(data_dir: Path) -> None:
    """No `--data-dir` & no `DKAIS_DATA_DIR` is a usage error naming both."""
    result = _run(["process", "--all"])
    assert result.exit_code == ExitCode.USAGE
    assert (
        "error: Missing option '--data-dir' (env var: 'DKAIS_DATA_DIR')."
        in result.stderr
    )


def test_data_dir_from_env(data_dir: Path) -> None:
    """`DKAIS_DATA_DIR` supplies the data directory."""
    result = _run(["process", "--all"], env={"DKAIS_DATA_DIR": str(data_dir)})
    assert result.exit_code == ExitCode.OK
    assert "Nothing to process" in result.stderr


def _mock_bucket(**kwargs: Any) -> Any:
    """Patch the S3 client & listing; `kwargs` configure `list_archives`."""
    return mock.patch.multiple(
        _s3, make_client=mock.DEFAULT, list_archives=mock.Mock(**kwargs)
    )


def test_usage_error_only_on_stderr() -> None:
    """A usage error writes nothing to STDOUT & its message to STDERR (#7)."""
    result = _run(["list", "--start", "nope"])
    assert result.exit_code == ExitCode.USAGE
    assert result.stdout == ""
    message = (
        "error: Invalid value for '--start': invalid date `nope` - use YYYY, "
        "YYYY-MM or YYYY-MM-DD"
    )
    assert result.stderr.splitlines() == [
        message,
        "",
        "USAGE:",
        "  dkais list [options]",
        "",
        "Try 'dkais list --help' for more information.",
    ]


@pytest.mark.parametrize("value", ["[/b]", "[red]x", ":warning:"])
def test_usage_error_prints_input_literally(value: str) -> None:
    """Markup in a bad value prints as typed, with no traceback (#7)."""
    result = _run(["list", "--start", value])
    assert result.exit_code == ExitCode.USAGE
    assert f"invalid date `{value}`" in result.stderr
    assert "Traceback" not in result.stderr


_INVERTED = ["--start", "2026-10-20", "--end", "2026-10-05"]


@pytest.mark.parametrize("command", ["list", "download", "process"])
def test_inverted_range_is_a_usage_error(data_dir: Path, command: str) -> None:
    """`--start` after `--end` exits 2 before the bucket is listed."""
    with _mock_bucket(return_value=[]):
        result = _run([command, *_INVERTED, "--data-dir", str(data_dir)])
        _s3.list_archives.assert_not_called()
    assert result.exit_code == ExitCode.USAGE
    assert result.stdout == ""
    assert result.stderr.startswith(
        "error: `--start` 2026-10-20 is after `--end` 2026-10-05\n"
    )


def _logging_listing(*args: object) -> list[_s3.Archive]:
    """Stand in for `list_archives`, logging a debug record."""
    logging.getLogger("dkais._s3").debug("bucket-listed")
    return []


@pytest.mark.parametrize(
    ("argv", "debug"),
    [(["-d", "list"], True), (["list", "-d"], True), (["list"], False)],
)
def test_debug_before_or_after_command(argv: list[str], debug: bool) -> None:
    """`-d` enables debug logging before or after the command name (#8)."""
    with _mock_bucket(side_effect=_logging_listing):
        result = _run(argv)
    assert result.exit_code == ExitCode.OK
    assert ("bucket-listed" in result.stderr) is debug


def test_list_interrupt_exits_130() -> None:
    """Ctrl-C during `list` prints one line to STDERR & exits 130 (#9)."""
    with _mock_bucket(side_effect=KeyboardInterrupt):
        result = _run(["list"])
    assert result.exit_code == ExitCode.INTERRUPT
    assert result.stderr == "warning: interrupted\n"


def test_download_interrupt_message_unchanged(data_dir: Path) -> None:
    """Ctrl-C during downloads keeps its tailored message & exits 130."""
    archive = _s3.parse_key("aisdk-2026-10-18.zip", 1)
    with (
        _mock_bucket(return_value=[archive]),
        mock.patch(
            "dkais._download.download_archives", side_effect=KeyboardInterrupt
        ),
    ):
        result = _run(["download", "--all", "--data-dir", str(data_dir)])
    assert result.exit_code == ExitCode.INTERRUPT
    assert result.stderr.endswith(
        "warning: Interrupted - unfinished downloads discarded, "
        "re-run to download them again\n"
    )


def test_bucket_error_is_reported_without_traceback() -> None:
    """An expected `Error` prints `error: message` & exits 1."""
    error = exceptions.BucketError("Cannot list bucket `x`: offline")
    with _mock_bucket(side_effect=error):
        result = _run(["list"])
    assert result.exit_code == ExitCode.FAILURE
    assert result.stderr == "error: Cannot list bucket `x`: offline\n"


def test_crash_exits_70_with_bug_hint() -> None:
    """An unexpected exception exits 70 with a bug report hint (#12)."""
    with _mock_bucket(side_effect=RuntimeError("boom")):
        result = _run(["list"])
    assert result.exit_code == ExitCode.INTERNAL
    assert "error: unexpected RuntimeError: boom" in result.stderr
    assert "https://github.com/andyrids/dkais/issues" in result.stderr


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

    assert _run(argv).exit_code == ExitCode.OK
    assert [p.name for p in (data_dir / "parquet").rglob("*.parquet")] == [
        target.name
    ]

    mtime = target.stat().st_mtime_ns
    assert _run(argv).exit_code == ExitCode.OK
    assert target.stat().st_mtime_ns == mtime

    assert _run([*argv, "--force"]).exit_code == ExitCode.OK
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
    with mock.patch("psutil.virtual_memory", return_value=memory):
        assert _run(argv).exit_code == ExitCode.OK

    written = sorted(p.name for p in (data_dir / "parquet").rglob("*.*"))
    assert written == [f"aisdk-2006-03-0{d}.parquet" for d in (1, 2, 3)]
    assert not list(data_dir.glob("tmp/*"))


def test_process_reports_failed_days(
    data_dir: Path, write_zip: ZipWriter
) -> None:
    """A failed day is reported on STDERR & summarised on STDOUT (#15)."""
    write_zip(
        data_dir / "raw/2026/aisdk-2026-10-18.zip",
        {"aisdk-2026-10-18.csv": "a;b"},
    )
    result = _run(["process", "--all", "--data-dir", str(data_dir)])
    assert result.exit_code == ExitCode.FAILURE

    # Not a terminal, so plain lines - no traceback for an expected failure
    assert "[1/1] aisdk-2026-10-18.csv  FAILED: " in result.stderr
    assert "error: 1 of 1 day failed, re-run to retry" in result.stderr
    assert "Traceback" not in result.output
    assert result.stdout.startswith(
        "processed=0 already_processed=0 failed=1 "
    )


def test_download_summary_is_key_value(data_dir: Path) -> None:
    """Off a terminal, the download summary is one `key=value` line (#15)."""
    archive = _s3.parse_key("aisdk-2026-10-18.zip", 5)
    with (
        _mock_bucket(return_value=[archive]),
        mock.patch("dkais._download.download_archives", return_value=[]),
    ):
        result = _run(["download", "--all", "--data-dir", str(data_dir)])
    assert result.exit_code == ExitCode.OK
    assert result.stdout.startswith(
        "downloaded=1 bytes=5 already_present=0 failed=0 elapsed="
    )
    assert result.stderr.startswith("1 archive to download · 5 bytes")


def test_download_inverted_range_skips_monthly_archive(data_dir: Path) -> None:
    """A same-month inverted range no longer downloads the whole month."""
    client = _s3.make_client()
    contents = [{"Key": "2026/aisdk-2026-10.zip", "Size": 5}]
    with (
        Stubber(client) as stubber,
        mock.patch.object(_s3, "make_client", return_value=client),
        mock.patch(
            "dkais._download.download_archives", return_value=[]
        ) as download,
    ):
        stubber.add_response(
            "list_objects_v2",
            {"Contents": contents, "IsTruncated": False},
            {"Bucket": _s3.BUCKET},
        )
        result = _run(["download", *_INVERTED, "--data-dir", str(data_dir)])
    download.assert_not_called()
    assert result.exit_code == ExitCode.USAGE


def _list_one_processed_day(
    data_dir: Path, *flags: str, **kwargs: Any
) -> Result:
    """Run `list` over a downloaded March 2006 archive with 1 day processed."""
    archive = _s3.parse_key("2006/aisdk-2006-03.zip", 3)
    assert archive is not None
    path = archive.local_path(data_dir / "raw")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"xyz")
    day = data_dir / "parquet/YEAR=2006/MONTH=3/aisdk-2006-03-02.parquet"
    day.parent.mkdir(parents=True)
    day.touch()

    with _mock_bucket(return_value=[archive]):
        return _run(["list", *flags, "--data-dir", str(data_dir)], **kwargs)


def test_list_table_on_a_terminal(data_dir: Path) -> None:
    """On a terminal, `list` shows a table of processed days per period."""
    result = _list_one_processed_day(data_dir, out_tty=True, width=120)
    assert result.exit_code == ExitCode.OK
    header, _, row, *_ = result.stdout.strip().splitlines()
    assert header.split()[-1] == "Processed"
    assert row.split()[-1] == "1/31"


def test_list_rows_when_piped(data_dir: Path) -> None:
    """Off a terminal, `list` prints tab-separated rows, caption on STDERR."""
    result = _list_one_processed_day(data_dir)
    assert result.exit_code == ExitCode.OK
    assert result.stdout == (
        "2006/aisdk-2006-03.zip\t2006-03-01\t2006-03-31\t3\ttrue\t1\n"
    )
    assert result.stderr == "1 archives, 3 bytes\n"


def test_list_rows_empty_when_nothing_listed() -> None:
    """An empty listing prints nothing to STDOUT."""
    with _mock_bucket(return_value=[]):
        result = _run(["list"])
    assert result.stdout == ""


def test_list_warns_without_data_dir() -> None:
    """Without a data dir, `list` omits local state & says why on STDERR."""
    archive = _s3.parse_key("2006/aisdk-2006-03.zip", 3)
    with _mock_bucket(return_value=[archive]):
        result = _run(["list"])
    assert result.exit_code == ExitCode.OK
    assert result.stdout.count("\t") == 3
    assert result.stderr.startswith("warning: No data dir - set `--data-dir`")


def test_list_json_includes_local_state(data_dir: Path) -> None:
    """`list --json` reports download & processed state per archive."""
    result = _list_one_processed_day(data_dir, "--json")
    assert result.exit_code == ExitCode.OK
    assert json.loads(result.stdout) == [
        {
            "key": "2006/aisdk-2006-03.zip",
            "start": "2006-03-01",
            "end": "2006-03-31",
            "size": 3,
            "downloaded": True,
            "processed_days": 1,
        }
    ]


def _process_one_day(
    data_dir: Path, samples: Samples, write_zip: ZipWriter, **env: str
) -> str:
    """Run `python -m dkais process` over one sample day; return STDOUT."""
    name, text = samples["comma-22"]
    write_zip(data_dir / "raw/2006/aisdk-2006-03.zip", {name: text})
    result = subprocess.run(
        [sys.executable, "-m", "dkais", "process", "--all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_subprocess_env(DKAIS_DATA_DIR=str(data_dir), **env),
        check=True,
    )
    return result.stdout


def test_forced_colour_styles_piped_summary(
    data_dir: Path, samples: Samples, write_zip: ZipWriter
) -> None:
    """`FORCE_COLOR=1` renders the piped summary as on a terminal (#10)."""
    stdout = _process_one_day(data_dir, samples, write_zip, FORCE_COLOR="1")
    assert "\x1b[" in stdout
    assert "Processed 1 day" in stdout


def test_piped_summary_plain_by_default(
    data_dir: Path, samples: Samples, write_zip: ZipWriter
) -> None:
    """Without `FORCE_COLOR`, the piped summary stays a `key=value` line."""
    stdout = _process_one_day(data_dir, samples, write_zip)
    assert stdout.startswith("processed=1 already_processed=0 failed=0 ")


_HEAVY = """
import sys
from dkais.__main__ import cli
try:
    cli(sys.argv[1:])
except SystemExit:
    pass
heavy = ("polars", "boto3", "botocore", "s3transfer", "psutil", "zlib_ng",
         "pygments", "rich.progress", "rich.traceback")
loaded = sorted(m for m in sys.modules if m.startswith(heavy))
print(loaded, file=sys.__stderr__)
"""


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--help"], {}),
        (["-v"], {}),
        ([], {}),
        (["list", "--start", "nope"], {}),
        (["list", *_INVERTED], {}),
        (
            [],
            {
                "_DKAIS_COMPLETE": "bash_complete",
                "COMP_WORDS": "dkais l",
                "COMP_CWORD": "1",
            },
        ),
    ],
    ids=[
        "help",
        "version",
        "bare",
        "usage-error",
        "inverted-range",
        "completion",
    ],
)
def test_fast_paths_skip_heavy_imports(
    argv: list[str], env: dict[str, str]
) -> None:
    """Assert the fast paths import no heavy module (#13)."""
    result = subprocess.run(
        [sys.executable, "-c", _HEAVY, *argv],
        capture_output=True,
        text=True,
        env=_subprocess_env(**env),
        check=True,
    )
    assert result.stderr.splitlines()[-1] == "[]"


def test_import_installs_only_a_null_handler() -> None:
    """`import dkais` adds a `NullHandler` & no logging config (#16)."""
    handlers = logging.getLogger("dkais").handlers
    assert handlers
    assert all(isinstance(h, logging.NullHandler) for h in handlers)
    assert importlib.util.find_spec("dkais.logging") is None
