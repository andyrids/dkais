"""Shared fixtures accross the `tests/` directory."""

import argparse
import logging
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from rich.console import Console

from aisdma._core import CLIContext
from aisdma.logging import GLOBAL_CONSOLE, configure_cli_logging

HEADER_22 = (
    "# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,"
    "ROT,SOG,COG,Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,"
    "Type of position fixing device,Draught,Destination,ETA,Data source type"
)

# One sample day per CSV dialect era - (member name, CSV text)
SAMPLES: dict[str, tuple[str, str]] = {
    "comma-22": (
        "aisdk_20060302.csv",
        (
            f"{HEADER_22}\r\n"
            "02/03/2006 00:00:01,Class A,258608000,91.000000,181.000000,"
            "Unknown value,,,,511,7508271,LAVE5,LINE,Cargo,,14,93,GPS,6.3,"
            "WISMAR,01/03/2006 15:00:00,AIS\r\n"
            "02/03/2006 00:00:00,Class A,257413900,63.105432,7.745262,"
            "Under way using engine,0.0,0.5,333.2,347,Unknown,Unknown,,"
            "Undefined,,,,Undefined,,Unknown,,AIS\r\n"
        ),
    ),
    "semicolon": (
        "FtpRoot/ais_data/aisdk_20150601.csv",
        (
            "01/06/2015 00:00:00;Class A;265610940;56,044792;12,687882;"
            "Under way using engine;0,0;10,5;355,0;66;Unknown;;;Undefined;;;;"
            "Undefined;4,2;;;AIS\r\n"
            "01/06/2015 00:00:00;Base Station;2194006;55,538917;5,033400;"
            "Unknown value;;;;;Unknown;;;Undefined;;;;Surveyed;;;;AIS\r\n"
        ),
    ),
    "comma-26": (
        "aisdk-2023-12-01.csv",
        (
            f"{HEADER_22},A,B,C,D\n"
            "01/12/2023 00:00:00,Class A,219005826,56.803653,9.019943,"
            "Under way using engine,,0.0,,,Unknown,Unknown,,Undefined,,,,"
            "Undefined,,Unknown,,AIS,10,20,3,4\n"
        ),
    ),
}


ZipWriter = Callable[[Path, dict[str, str]], Path]


@pytest.fixture
def samples() -> dict[str, tuple[str, str]]:
    """Provide one sample day per CSV dialect era."""
    return SAMPLES


@pytest.fixture
def write_zip() -> ZipWriter:
    """Provide a writer for zip archives of CSV members."""

    def writer(path: Path, members: dict[str, str]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in members.items():
                zf.writestr(name, text)
        return path

    return writer


@pytest.fixture
def configured_logging() -> Iterator[None]:
    """Configure CLI logging for tests & clean up handlers afterwards."""
    configure_cli_logging()
    try:
        yield
    finally:
        logger = logging.getLogger("aisdma")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)


@pytest.fixture
def tty_stderr_enable() -> Iterator[None]:
    """Treat GLOBAL_CONSOLE (STDERR) as a TTY for progress/log tests."""
    with mock.patch.object(GLOBAL_CONSOLE, "_force_terminal", True):
        yield


@pytest.fixture
def tty_stderr_disable() -> Iterator[None]:
    """Treat GLOBAL_CONSOLE (STDERR) as non-TTY for progress/log tests."""
    with mock.patch.object(GLOBAL_CONSOLE, "_force_terminal", False):
        yield


@pytest.fixture
def make_cli_context() -> Callable[..., CLIContext]:
    """Factory-build a `CLIContext` with defaults for every field."""

    def factory(**overrides: Any) -> CLIContext:
        args = argparse.Namespace()
        vars(args).update(vars(overrides.pop("args", argparse.Namespace())))
        defaults: dict[str, Any] = {
            "args": args,
            "console": Console(stderr=True),
            "is_debug": False,
        }
        return CLIContext(**{**defaults, **overrides})

    return factory


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an empty data directory, isolated from `AISDMA_DATA_DIR`."""
    monkeypatch.delenv("AISDMA_DATA_DIR", raising=False)
    root = tmp_path / "data"
    root.mkdir()
    return root
