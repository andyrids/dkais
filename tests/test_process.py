"""Unit tests for CSV to Parquet conversion (`aisdma._process`)."""

import sys
import types
import zipfile
import zlib
from collections.abc import Callable
from datetime import date
from pathlib import Path
from unittest import mock

import polars as pl
import pytest

from aisdma import _process, exceptions

Samples = dict[str, tuple[str, str]]
ZipWriter = Callable[[Path, dict[str, str]], Path]
Converter = Callable[[str], pl.DataFrame]


@pytest.fixture(params=[False, True], ids=["file", "memory"])
def in_memory(request: pytest.FixtureRequest) -> bool:
    """Extract to a temp CSV file, or into memory."""
    return bool(request.param)


@pytest.fixture
def convert(
    tmp_path: Path, samples: Samples, write_zip: ZipWriter, in_memory: bool
) -> Converter:
    """Convert a named sample day & return its Parquet output."""

    def converter(sample: str) -> pl.DataFrame:
        name, text = samples[sample]
        zip_path = write_zip(tmp_path / "raw.zip", {name: text})
        (member,) = _process.list_members(zip_path)
        parquet_dir, tmp_dir = tmp_path / "parquet", tmp_path / "tmp"
        return pl.read_parquet(
            _process.convert_member(
                member, parquet_dir, tmp_dir, in_memory=in_memory
            )
        )

    return converter


def _zip_member(
    path: Path, name: str, text: str, compression: int
) -> _process.Member:
    """Write a one-member zip & return the member."""
    with zipfile.ZipFile(path, "w", compression) as zf:
        zf.writestr(name, text)
    (member,) = _process.list_members(path)
    return member


def test_inflate_module_prefers_zlib_ng_builds() -> None:
    """A zlib-ng stdlib build wins, then the package, then plain zlib."""
    plain = types.ModuleType("zlib")
    with mock.patch.object(zlib, "ZLIBNG_VERSION", "2.2.4", create=True):
        assert _process._inflate_module() is zlib

    with (
        mock.patch.object(_process, "zlib", plain),
        mock.patch.dict(sys.modules, {"zlib_ng": None}),
    ):
        assert _process._inflate_module() is plain

    zlib_ng = pytest.importorskip("zlib_ng.zlib_ng")
    with mock.patch.object(_process, "zlib", plain):
        assert _process._inflate_module() is zlib_ng


def test_fits_in_memory() -> None:
    """A member fits if `IN_MEMORY_FACTOR` times its size is available."""
    member = _process.Member(Path("a.zip"), "a.csv", date(2026, 1, 1), 100)
    assert _process.fits_in_memory(member, 100 * _process.IN_MEMORY_FACTOR)
    assert not _process.fits_in_memory(member, 100)


@pytest.mark.parametrize(
    "compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED]
)
def test_extract_member_round_trips(
    tmp_path: Path, samples: Samples, in_memory: bool, compression: int
) -> None:
    """Deflated & stored members extract to the original CSV."""
    name, text = samples["comma-26"]
    member = _zip_member(tmp_path / "raw.zip", name, text, compression)

    source = _process.extract_member(
        member, tmp_path / "tmp", in_memory=in_memory
    )

    data = source if isinstance(source, bytes) else source.read_bytes()
    assert isinstance(source, bytes) is in_memory
    assert data == text.encode()


def test_extract_member_rejects_corrupt_data(
    tmp_path: Path, samples: Samples, in_memory: bool
) -> None:
    """Corrupt deflate data raises `BadZipFile` & leaves no CSV behind."""
    name, text = samples["comma-26"]
    zip_path = tmp_path / "raw.zip"
    member = _zip_member(zip_path, name, text * 50, zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo(name)
    raw = bytearray(zip_path.read_bytes())
    data_start = info.header_offset + 30 + len(name)
    raw[data_start + info.compress_size // 2] ^= 0xFF
    zip_path.write_bytes(raw)

    with pytest.raises(zipfile.BadZipFile):
        _process.extract_member(member, tmp_path / "tmp", in_memory=in_memory)
    assert not list(tmp_path.glob("tmp/*"))


def test_transform_sort_is_stable(
    tmp_path: Path, samples: Samples, write_zip: ZipWriter
) -> None:
    """Rows sharing an MMSI & timestamp keep their file order."""
    name, text = samples["comma-26"]
    header, row = text.splitlines()
    rows = [row.replace(",Unknown,Unknown,", f",{n},{n},") for n in "BCA"]
    zip_path = write_zip(
        tmp_path / "raw.zip", {name: "\n".join([header, *rows])}
    )
    (member,) = _process.list_members(zip_path)

    target = _process.convert_member(
        member, tmp_path / "parquet", tmp_path / "tmp"
    )

    assert pl.read_parquet(target)["CALLSIGN"].to_list() == ["B", "C", "A"]


@pytest.mark.parametrize(
    ("sample", "separator", "has_header", "n_columns"),
    [
        ("comma-22", ",", True, 22),
        ("comma-26", ",", True, 26),
        ("semicolon", ";", False, 22),
    ],
)
def test_detect_dialect(
    samples: Samples,
    sample: str,
    separator: str,
    has_header: bool,
    n_columns: int,
) -> None:
    """Each era's first line maps to its separator, header & columns."""
    first_line = samples[sample][1].splitlines(keepends=True)[0]
    dialect = _process.detect_dialect(first_line)
    assert dialect.separator == separator
    assert dialect.has_header is has_header
    assert dialect.decimal_comma is (separator == ";")
    assert len(dialect.columns) == n_columns


@pytest.mark.parametrize("line", ["# Timestamp,Bogus", "a;b;c"])
def test_detect_dialect_rejects_unknown_layouts(line: str) -> None:
    """Unknown header names & column counts raise `CSVDialectError`."""
    with pytest.raises(exceptions.CSVDialectError):
        _process.detect_dialect(line)


@pytest.mark.parametrize("sample", ["comma-22", "comma-26", "semicolon"])
def test_convert_member_output_schema(convert: Converter, sample: str) -> None:
    """Every dialect converts to the same 26-column schema."""
    assert convert(sample).schema == _process.SCHEMA


def test_convert_member_normalises_values(convert: Converter) -> None:
    """Sentinels become null, rows sort by MMSI & time, none are dropped."""
    df = convert("comma-22")

    assert df["MMSI"].to_list() == [257413900, 258608000]
    sentinel = df.row(1, named=True)
    assert sentinel["LATITUDE"] is None
    assert sentinel["LONGITUDE"] is None
    assert sentinel["HEADING"] is None
    assert sentinel["IMO"] == 7508271
    assert sentinel["SIZE_A"] is None
    first = df.row(0, named=True)
    assert first["IMO"] is None
    assert first["CALLSIGN"] is None
    assert first["DESTINATION"] is None


def test_convert_member_decimal_comma(convert: Converter) -> None:
    """Semicolon-era decimal commas parse as floats."""
    row = convert("semicolon").row(1, named=True)
    assert row["MMSI"] == 265610940
    assert row["LATITUDE"] == pytest.approx(56.044792)
    assert row["SOG"] == pytest.approx(10.5)
    assert row["DRAUGHT"] == pytest.approx(4.2)


def test_convert_member_writes_hive_path(
    tmp_path: Path, convert: Converter
) -> None:
    """Output lands in `YEAR=/MONTH=` with no temporary files left."""
    convert("comma-26")
    files = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.*"))
    assert files == [
        Path("parquet/YEAR=2023/MONTH=12/aisdk-2023-12-01.parquet"),
        Path("raw.zip"),
    ]


def test_list_members_filters_by_day(
    tmp_path: Path, write_zip: ZipWriter
) -> None:
    """Only members within the date range are listed, sorted by day."""
    zip_path = write_zip(
        tmp_path / "month.zip",
        {f"aisdk_200603{d:02d}.csv": "" for d in (3, 1, 2)},
    )
    members = _process.list_members(zip_path, start=date(2006, 3, 2))
    assert [m.day for m in members] == [date(2006, 3, 2), date(2006, 3, 3)]


def test_clean_stale_removes_leftovers(tmp_path: Path) -> None:
    """Partial Parquet & extracted CSVs from an interrupted run are removed."""
    keep = tmp_path / "parquet/YEAR=2026/MONTH=10/aisdk-2026-10-18.parquet"
    stale = keep.with_name(f"{keep.name}.tmp")
    csv = tmp_path / "tmp/aisdk-2026-10-18.csv"
    for path in (keep, stale, csv):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    _process.clean_stale(tmp_path / "parquet", tmp_path / "tmp")

    assert keep.exists()
    assert not stale.exists()
    assert not csv.exists()
