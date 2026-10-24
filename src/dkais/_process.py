"""Conversion of raw archive CSVs to hive-partitioned Parquet.

Each daily CSV inside an archive becomes one Parquet file at
`YEAR=y/MONTH=m/aisdk-YYYY-MM-DD.parquet`, so conversion is idempotent per day
regardless of whether the source archive is daily or monthly.

The CSV dialect varies by era and is detected per file (`detect_dialect`):

- 2006-ish: `,` separated, header with 22 columns (no `A`-`D`)
- 2015-ish: `;` separated, no header, decimal comma
- 2023+: `,` separated, header with 26 columns

Extraction (`extract_member`) inflates a member into memory when it fits
(`fits_in_memory`), else to a CSV in a temp dir, using the fastest available
deflate implementation (`INFLATE`).
"""

import dataclasses
import logging
import os
import re
import shutil
import struct
import zipfile
import zlib
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import BinaryIO

import polars as pl

from dkais import exceptions

logger = logging.getLogger(__package__)


def _inflate_module() -> ModuleType:
    """Return the fastest available zlib-compatible module.

    NOTE: zlib-ng inflates ~1.5x faster than zlib. CPython 3.14+ Windows
    builds ship it as `zlib`; elsewhere the `zlib-ng` package provides it.

    Returns:
        The stdlib `zlib` if built on zlib-ng, else `zlib_ng.zlib_ng` if
        installed, else the stdlib `zlib`.
    """
    if hasattr(zlib, "ZLIBNG_VERSION"):
        return zlib
    try:
        from zlib_ng import zlib_ng
    except ImportError:
        return zlib
    return zlib_ng


INFLATE = _inflate_module()

# http://aisdata.ais.dk/!_README_information_CSV_files.txt
HEADER_NAMES: dict[str, str] = {
    "# Timestamp": "DATETIME",
    "Type of mobile": "MOBILE_TYPE",
    "MMSI": "MMSI",
    "Latitude": "LATITUDE",
    "Longitude": "LONGITUDE",
    "Navigational status": "NAV_STATUS",
    "ROT": "ROT",
    "SOG": "SOG",
    "COG": "COG",
    "Heading": "HEADING",
    "IMO": "IMO",
    "Callsign": "CALLSIGN",
    "Name": "NAME",
    "Ship type": "SHIP_TYPE",
    "Cargo type": "CARGO_TYPE",
    "Width": "WIDTH",
    "Length": "LENGTH",
    "Type of position fixing device": "POSITION_DEVICE",
    "Draught": "DRAUGHT",
    "Destination": "DESTINATION",
    "ETA": "ETA",
    "Data source type": "DATA_SOURCE",
    "A": "SIZE_A",
    "B": "SIZE_B",
    "C": "SIZE_C",
    "D": "SIZE_D",
}

SCHEMA = pl.Schema(
    {
        "DATETIME": pl.Datetime("us"),
        "MOBILE_TYPE": pl.Categorical(),
        "MMSI": pl.Int32(),
        "LATITUDE": pl.Float64(),
        "LONGITUDE": pl.Float64(),
        "NAV_STATUS": pl.Categorical(),
        "ROT": pl.Float32(),
        "SOG": pl.Float32(),
        "COG": pl.Float32(),
        "HEADING": pl.Int16(),
        "IMO": pl.Int32(),
        "CALLSIGN": pl.String(),
        "NAME": pl.String(),
        "SHIP_TYPE": pl.Categorical(),
        "CARGO_TYPE": pl.Categorical(),
        "WIDTH": pl.Int16(),
        "LENGTH": pl.Int16(),
        "POSITION_DEVICE": pl.Categorical(),
        "DRAUGHT": pl.Float32(),
        "DESTINATION": pl.String(),
        "ETA": pl.Datetime("us"),
        "DATA_SOURCE": pl.Categorical(),
        "SIZE_A": pl.Int16(),
        "SIZE_B": pl.Int16(),
        "SIZE_C": pl.Int16(),
        "SIZE_D": pl.Int16(),
    }
)

DATETIME_FORMAT = "%d/%m/%Y %H:%M:%S"

# AIS "not available" values, mapped to null
SENTINELS: dict[str, int | str] = {
    "LATITUDE": 91,
    "LONGITUDE": 181,
    "HEADING": 511,
    "CALLSIGN": "Unknown",
    "DESTINATION": "Unknown",
}

_MEMBER_DAY = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})\.csv")
_COPY_BUFFER = 16 * 1024 * 1024

# Peak memory is ~4.6x the CSV size when converting one day in memory while
# the next is extracted (measured on 2026 daily files)
IN_MEMORY_FACTOR = 5

# Zip local file header: signature, 22 fixed bytes, name & extra lengths
_LOCAL_HEADER = struct.Struct("<4s22xHH")
_LOCAL_SIGNATURE = b"PK\x03\x04"
_ENCRYPTED = 0x1


@dataclasses.dataclass(frozen=True, slots=True)
class Dialect:
    """The CSV layout of one raw file.

    Attributes:
        separator: The field separator.
        has_header: Whether the first line is a header row.
        columns: The output column names present, in file order.
        decimal_comma: Whether floats use a decimal comma.
    """

    separator: str
    has_header: bool
    columns: tuple[str, ...]
    decimal_comma: bool


@dataclasses.dataclass(frozen=True, slots=True)
class Member:
    """A daily CSV inside a raw archive.

    Attributes:
        zip_path: The local archive path.
        name: The member name within the archive.
        day: The day the CSV covers.
        size: The uncompressed CSV size in bytes.
    """

    zip_path: Path
    name: str
    day: date
    size: int

    def target(self, parquet_dir: Path) -> Path:
        """Return the hive-partitioned Parquet path for this day.

        Args:
            parquet_dir: The root Parquet directory.

        Returns:
            The path `YEAR=y/MONTH=m/aisdk-YYYY-MM-DD.parquet`.
        """
        return day_path(parquet_dir, self.day)


def day_path(parquet_dir: Path, day: date) -> Path:
    """Return the hive-partitioned Parquet path for a day.

    Args:
        parquet_dir: The root Parquet directory.
        day: The day the file covers.

    Returns:
        The path `YEAR=y/MONTH=m/aisdk-YYYY-MM-DD.parquet`.
    """
    partition = f"YEAR={day.year}/MONTH={day.month}"
    return parquet_dir / partition / f"aisdk-{day}.parquet"


def detect_dialect(first_line: str) -> Dialect:
    """Detect the CSV dialect from a file's first line.

    Args:
        first_line: The first line of the CSV file.

    Returns:
        The detected dialect.

    Raises:
        exceptions.CSVDialectError: On unknown header names or an
            unexpected column count.
    """
    line = first_line.rstrip("\r\n")
    separator = ";" if line.count(";") > line.count(",") else ","
    fields = line.split(separator)
    has_header = line.startswith("# Timestamp")

    if has_header:
        try:
            columns = tuple(HEADER_NAMES[field.strip()] for field in fields)
        except KeyError as e:
            msg = f"Unknown CSV header column {e}"
            raise exceptions.CSVDialectError(msg) from e
    elif len(fields) in (22, len(SCHEMA)):
        columns = tuple(SCHEMA.names()[: len(fields)])
    else:
        msg = f"Unexpected header-less CSV with {len(fields)} columns"
        raise exceptions.CSVDialectError(msg)

    return Dialect(
        separator=separator,
        has_header=has_header,
        columns=columns,
        decimal_comma=separator == ";",
    )


def _column(name: str, dialect: Dialect) -> pl.Expr:
    """Build the expression casting one raw String column to `SCHEMA`.

    Args:
        name: The output column name.
        dialect: The source file dialect.

    Returns:
        The typed, sentinel-normalised column expression.
    """
    dtype = SCHEMA[name]
    if name not in dialect.columns:
        return pl.lit(None, dtype).alias(name)

    expr = pl.col(name)
    if dtype.is_float() and dialect.decimal_comma:
        expr = expr.str.replace(",", ".", literal=True)
    if isinstance(dtype, pl.Datetime):
        expr = expr.str.to_datetime(
            DATETIME_FORMAT, time_unit="us", strict=False
        )
    else:
        expr = expr.cast(dtype, strict=False)
    if name in SENTINELS:
        expr = pl.when(expr != SENTINELS[name]).then(expr)
    return expr.alias(name)


def transform(lf: pl.LazyFrame, dialect: Dialect) -> pl.LazyFrame:
    """Cast a raw all-String frame to `SCHEMA`, sorted by MMSI & time.

    NOTE: Casts are non-strict, so malformed values become null rather than
    failing a multi-GB file. No rows are dropped. The sort is stable, so rows
    sharing an MMSI & timestamp keep their file order between runs.

    Args:
        lf: The raw frame, with `dialect.columns` as String columns.
        dialect: The source file dialect.

    Returns:
        The typed frame with all `SCHEMA` columns.
    """
    return lf.select(_column(name, dialect) for name in SCHEMA).sort(
        "MMSI", "DATETIME", maintain_order=True
    )


def list_members(
    zip_path: Path,
    start: date | None = None,
    end: date | None = None,
) -> list[Member]:
    """List the daily CSVs in an archive within a date range.

    Args:
        zip_path: The local archive path.
        start: The inclusive range start, or `None` for unbounded.
        end: The inclusive range end, or `None` for unbounded.

    Returns:
        The members in range, sorted by day.
    """
    members = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not (match := _MEMBER_DAY.search(info.filename)):
                continue
            day = date(*map(int, match.groups()))
            if (start is None or day >= start) and (end is None or day <= end):
                members.append(
                    Member(
                        zip_path=zip_path,
                        name=info.filename,
                        day=day,
                        size=info.file_size,
                    )
                )
    return sorted(members, key=lambda m: m.day)


def fits_in_memory(member: Member, available: int) -> bool:
    """Check whether a member can be converted in memory.

    Args:
        member: The archive member.
        available: The memory available to the run, in bytes.

    Returns:
        True if `IN_MEMORY_FACTOR` times the CSV size fits in `available`.
    """
    return member.size * IN_MEMORY_FACTOR <= available


def _is_plain_deflate(info: zipfile.ZipInfo) -> bool:
    """Check whether a member is unencrypted deflate (raw-inflatable)."""
    return (
        info.compress_type == zipfile.ZIP_DEFLATED
        and not info.flag_bits & _ENCRYPTED
    )


def _open_deflate(member: Member, info: zipfile.ZipInfo) -> BinaryIO:
    """Open an archive positioned at the start of a member's deflate data.

    NOTE: Reading the raw stream bypasses `zipfile`, so it can be inflated
    with `INFLATE` rather than the stdlib `zlib`.

    Args:
        member: The archive member.
        info: The member's zip info.

    Returns:
        The open binary archive file.

    Raises:
        zipfile.BadZipFile: On a bad local file header.
    """
    f = member.zip_path.open("rb")
    f.seek(info.header_offset)
    header = f.read(_LOCAL_HEADER.size)
    if len(header) == _LOCAL_HEADER.size:
        signature, name_len, extra_len = _LOCAL_HEADER.unpack(header)
        if signature == _LOCAL_SIGNATURE:
            f.seek(name_len + extra_len, os.SEEK_CUR)
            return f
    f.close()
    msg = f"Bad local file header for `{member.name}`"
    raise zipfile.BadZipFile(msg)


def _check_crc(member: Member, info: zipfile.ZipInfo, crc: int) -> None:
    """Raise `zipfile.BadZipFile` if `crc` does not match the member's."""
    if crc != info.CRC:
        msg = f"Bad CRC-32 for `{member.name}`"
        raise zipfile.BadZipFile(msg)


def _read_member(member: Member, info: zipfile.ZipInfo) -> bytes:
    """Inflate a member into memory with a single output allocation."""
    if not _is_plain_deflate(info):
        with zipfile.ZipFile(member.zip_path) as zf:
            return zf.read(info)

    with _open_deflate(member, info) as f:
        compressed = f.read(info.compress_size)
    try:
        data: bytes = INFLATE.decompress(compressed, -15, info.file_size)
    except INFLATE.error as e:
        msg = f"Bad deflate stream for `{member.name}`: {e}"
        raise zipfile.BadZipFile(msg) from e
    _check_crc(member, info, INFLATE.crc32(data))
    return data


def _write_member(
    member: Member, info: zipfile.ZipInfo, csv_path: Path
) -> None:
    """Inflate a member to `csv_path` in chunks."""
    with csv_path.open("wb") as dst:
        if not _is_plain_deflate(info):
            with (
                zipfile.ZipFile(member.zip_path) as zf,
                zf.open(info) as src,
            ):
                shutil.copyfileobj(src, dst, _COPY_BUFFER)
            return

        inflater = INFLATE.decompressobj(-15)
        crc, left = 0, info.compress_size
        try:
            with _open_deflate(member, info) as src:
                while left and (chunk := src.read(min(_COPY_BUFFER, left))):
                    left -= len(chunk)
                    out = inflater.decompress(chunk)
                    crc = INFLATE.crc32(out, crc)
                    dst.write(out)
            out = inflater.flush()
        except INFLATE.error as e:
            msg = f"Bad deflate stream for `{member.name}`: {e}"
            raise zipfile.BadZipFile(msg) from e
        dst.write(out)

    if left or not inflater.eof:
        msg = f"Truncated deflate stream for `{member.name}`"
        raise zipfile.BadZipFile(msg)
    _check_crc(member, info, INFLATE.crc32(out, crc))


def extract_member(
    member: Member, tmp_dir: Path, *, in_memory: bool
) -> bytes | Path:
    """Extract one daily CSV, into memory or to `tmp_dir`.

    NOTE: Polars cannot scan inside a zip. In memory avoids writing & reading
    back a multi-GB temp file, at the cost of holding the CSV in RAM.

    Args:
        member: The archive member to extract.
        tmp_dir: The directory for an extracted CSV file.
        in_memory: Return the CSV bytes, rather than a file path.

    Returns:
        The CSV bytes, or the extracted CSV path.

    Raises:
        zipfile.BadZipFile: On a bad header, deflate stream or CRC.
    """
    with zipfile.ZipFile(member.zip_path) as zf:
        info = zf.getinfo(member.name)
    if in_memory:
        return _read_member(member, info)

    tmp_dir.mkdir(parents=True, exist_ok=True)
    csv_path = tmp_dir / f"aisdk-{member.day}.csv"
    try:
        _write_member(member, info, csv_path)
    except BaseException:
        csv_path.unlink(missing_ok=True)
        raise
    return csv_path


def _first_line(source: bytes | Path) -> str:
    """Return the first line of an extracted CSV."""
    if isinstance(source, Path):
        with source.open(encoding="utf-8", errors="replace") as f:
            return f.readline()
    end = source.find(b"\n")
    return source[: end if end >= 0 else None].decode("utf-8", "replace")


def convert(member: Member, source: bytes | Path, parquet_dir: Path) -> Path:
    """Convert one extracted daily CSV to Parquet.

    The Parquet is written under a `.tmp` name, then renamed into place. A
    CSV file `source` is deleted afterwards.

    Args:
        member: The archive member the CSV came from.
        source: The CSV bytes or file path, from `extract_member`.
        parquet_dir: The root Parquet directory.

    Returns:
        The written Parquet path.
    """
    target = member.target(parquet_dir)
    partial = target.with_name(f"{target.name}.tmp")

    try:
        dialect = detect_dialect(_first_line(source))
        logger.debug("Detected %s for `%s`", dialect, member.name)

        # Positional read, so `schema` names apply whatever the header says
        lf = pl.scan_csv(
            source,
            has_header=False,
            skip_rows=int(dialect.has_header),
            separator=dialect.separator,
            schema=dict.fromkeys(dialect.columns, pl.String),
            encoding="utf8-lossy",
        )
        transform(lf, dialect).sink_parquet(partial, mkdir=True)
        partial.replace(target)
    finally:
        if isinstance(source, Path):
            source.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return target


def convert_member(
    member: Member,
    parquet_dir: Path,
    tmp_dir: Path,
    *,
    in_memory: bool = False,
) -> Path:
    """Extract & convert one daily CSV to Parquet.

    Args:
        member: The archive member to convert.
        parquet_dir: The root Parquet directory.
        tmp_dir: The directory for an extracted CSV file.
        in_memory: Extract the CSV into memory, rather than to `tmp_dir`.

    Returns:
        The written Parquet path.
    """
    source = extract_member(member, tmp_dir, in_memory=in_memory)
    return convert(member, source, parquet_dir)


def clean_stale(parquet_dir: Path, tmp_dir: Path) -> None:
    """Remove leftovers from an interrupted run.

    Args:
        parquet_dir: The root Parquet directory.
        tmp_dir: The directory for extracted CSVs.
    """
    stale = [*parquet_dir.rglob("*.parquet.tmp"), *tmp_dir.glob("*.csv")]
    for path in stale:
        path.unlink()
    if stale:
        logger.debug("Removed %d stale files", len(stale))
