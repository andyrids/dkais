"""Benchmark `aisdma process` variants on real archives.

Reads zips from `<data-dir>/raw` (read-only) and writes CSVs, Parquet and
results only under `--out`, so the real `<data-dir>/parquet` is untouched.

Each variant is a set of knobs over the `convert_member` pipeline:

    extract   zipfile (stdlib `ZipExtFile` copy, as `convert_member`),
              raw (hand-inflated deflate stream to a temp CSV) or
              mem (inflated into one `bytes`, no temp CSV)
    inflate   zlib, isal or zlibng (the latter two need `uv run --with`)
    scan      string (all String, then cast) or typed (typed `scan_csv`)
    sort      1 or 0 (0 is cost attribution only, not shippable)
    codec     sink_parquet compression; level & rows for level/row groups
    schedule  seq, prefetch (extract day N+1 while converting day N) or
              conc (`jobs` whole days in flight on threads)

USAGE:
    uv run --with psutil python scripts/bench_variants.py \
        --data-dir D:/Data/AISDK --out <scratch> [--days 3] [--repeat 2] \
        [--keep-dir <dir>] [--keep-day 2026-01-02] [--tag py314] \
        baseline mem prefetch "best:extract=mem,schedule=prefetch"

Each variant gets one warm-up day (not reported), then `--repeat` timed
passes over all days. Results go to `<out>/results.json` (appended per
variant); `--keep-day` Parquet goes to `<keep-dir>/<variant>/`.
"""

import argparse
import dataclasses
import json
import os
import shutil
import struct
import sys
import threading
import time
import zipfile
from collections import deque
from collections.abc import Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, TypeVar, cast

import polars as pl
import psutil
from rich.console import Console
from rich.table import Table

from aisdma import _process, _s3

console = Console()

_LOCAL_HEADER = b"PK\x03\x04"
_READ_CHUNK = 4 * 1024 * 1024

T = TypeVar("T")
Codec = Literal["lz4", "uncompressed", "snappy", "gzip", "brotli", "zstd"]


@dataclasses.dataclass(frozen=True)
class Config:
    """The knobs of one benchmark variant."""

    extract: str = "zipfile"
    inflate: str = "zlib"
    scan: str = "string"
    sort: int = 1
    codec: str = "zstd"
    level: int | None = None
    rows: int | None = None
    schedule: str = "seq"
    jobs: int = 1


PRESETS: dict[str, Config] = {
    "baseline": Config(),
    "raw_zlib": Config(extract="raw"),
    "isal": Config(extract="raw", inflate="isal"),
    "zlibng": Config(extract="raw", inflate="zlibng"),
    "mem": Config(extract="mem"),
    "mem_isal": Config(extract="mem", inflate="isal"),
    "prefetch": Config(schedule="prefetch"),
    "prefetch2": Config(schedule="prefetch", jobs=2),
    "conc2": Config(schedule="conc", jobs=2),
    "conc3": Config(schedule="conc", jobs=3),
    "nosort": Config(sort=0),
    "typed": Config(scan="typed"),
    "zstd1": Config(level=1),
    "lz4": Config(codec="lz4"),
    "snappy": Config(codec="snappy"),
    "rg1m": Config(rows=1_000_000),
}


def parse_variant(spec: str) -> tuple[str, Config]:
    """Parse `preset` or `name:key=value,...` (overrides on baseline)."""
    if spec in PRESETS:
        return spec, PRESETS[spec]
    name, _, overrides = spec.partition(":")
    base = PRESETS.get(name, Config())
    fields = {f.name: f.type for f in dataclasses.fields(Config)}
    changes: dict[str, Any] = {}
    for item in filter(None, overrides.split(",")):
        key, _, value = item.partition("=")
        if key not in fields:
            msg = f"Unknown knob {key!r} in {spec!r}"
            raise ValueError(msg)
        is_text = key in ("extract", "inflate", "scan", "codec", "schedule")
        changes[key] = value if is_text else int(value)
    return name, dataclasses.replace(base, **changes)


def zlib_module(name: str) -> ModuleType:
    """Return the zlib-compatible module for an `inflate` knob."""
    if name == "isal":
        from isal import isal_zlib  # type: ignore[import-not-found]

        module: ModuleType = isal_zlib
        return module
    if name == "zlibng":
        from zlib_ng import zlib_ng

        return zlib_ng
    import zlib

    return zlib


@contextmanager
def sampled(interval: float = 0.05) -> Generator[dict[str, float]]:
    """Sample peak RSS & mean CPU use of this process during a block.

    Yields:
        A dict holding `peak_rss_gb` and `cpu_cores`, final on exit.
    """
    process = psutil.Process()
    stats = {"peak_rss_gb": process.memory_info().rss / 1e9}
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(interval):
            rss = process.memory_info().rss / 1e9
            stats["peak_rss_gb"] = max(stats["peak_rss_gb"], rss)

    cpu_start, wall_start = process.cpu_times(), time.perf_counter()
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        yield stats
    finally:
        stop.set()
        thread.join()
        cpu_end = process.cpu_times()
        cpu = (cpu_end.user - cpu_start.user) + (
            cpu_end.system - cpu_start.system
        )
        stats["cpu_cores"] = cpu / (time.perf_counter() - wall_start)


def select_members(data_dir: Path, days: int) -> list[_process.Member]:
    """Return the first `days` members found in `<data-dir>/raw`."""
    zip_paths = sorted((data_dir / "raw").glob("*/*.zip"))
    members = [
        member
        for path in zip_paths
        if _s3.parse_key(path.name, path.stat().st_size)
        for member in _process.list_members(path)
    ]
    return members[:days]


def raw_chunks(member: _process.Member, zmod: ModuleType) -> Iterator[bytes]:
    """Inflate a deflated member straight from its local file header.

    Bypasses `ZipExtFile`, so `zmod` can be any zlib-compatible module.

    Raises:
        zipfile.BadZipFile: On a bad header, truncated stream or bad CRC.
    """
    with zipfile.ZipFile(member.zip_path) as zf:
        info = zf.getinfo(member.name)
    if info.compress_type != zipfile.ZIP_DEFLATED or info.flag_bits & 1:
        msg = f"{member.name} is not plain deflate"
        raise zipfile.BadZipFile(msg)

    inflater, crc, left = zmod.decompressobj(-15), 0, info.compress_size
    with member.zip_path.open("rb", buffering=0) as f:
        f.seek(info.header_offset)
        header = f.read(30)
        if header[:4] != _LOCAL_HEADER:
            msg = f"Bad local header for {member.name}"
            raise zipfile.BadZipFile(msg)
        name_len, extra_len = struct.unpack_from("<HH", header, 26)
        f.seek(name_len + extra_len, os.SEEK_CUR)
        while left:
            chunk = f.read(min(_READ_CHUNK, left))
            left -= len(chunk)
            out = inflater.decompress(chunk)
            crc = zmod.crc32(out, crc)
            yield out
    out = inflater.flush()
    crc = zmod.crc32(out, crc)
    yield out
    if not inflater.eof or crc != info.CRC:
        msg = f"Bad CRC-32 for {member.name}"
        raise zipfile.BadZipFile(msg)


def extract_file(member: _process.Member, csv_path: Path, cfg: Config) -> Path:
    """Extract a member to `csv_path` with the configured method."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("wb") as dst:
        if cfg.extract == "zipfile":
            with (
                zipfile.ZipFile(member.zip_path) as zf,
                zf.open(member.name) as src,
            ):
                shutil.copyfileobj(src, dst, _process._COPY_BUFFER)
        else:
            for chunk in raw_chunks(member, zlib_module(cfg.inflate)):
                dst.write(chunk)
    return csv_path


def extract_bytes(member: _process.Member, cfg: Config) -> bytes:
    """Inflate a member into one `bytes` with a single allocation.

    Raises:
        zipfile.BadZipFile: On a bad header or bad CRC.
    """
    zmod = zlib_module(cfg.inflate)
    with zipfile.ZipFile(member.zip_path) as zf:
        info = zf.getinfo(member.name)
    with member.zip_path.open("rb", buffering=0) as f:
        f.seek(info.header_offset)
        header = f.read(30)
        if header[:4] != _LOCAL_HEADER:
            msg = f"Bad local header for {member.name}"
            raise zipfile.BadZipFile(msg)
        name_len, extra_len = struct.unpack_from("<HH", header, 26)
        f.seek(name_len + extra_len, os.SEEK_CUR)
        compressed = f.read(info.compress_size)
    data: bytes = zmod.decompress(compressed, -15, info.file_size)
    del compressed
    if zmod.crc32(data) != info.CRC:
        msg = f"Bad CRC-32 for {member.name}"
        raise zipfile.BadZipFile(msg)
    return data


def extract(member: _process.Member, tmp_dir: Path, cfg: Config) -> Any:
    """Extract one member: a CSV path, or `bytes` for `extract=mem`."""
    if cfg.extract == "mem":
        return extract_bytes(member, cfg)
    return extract_file(member, tmp_dir / f"aisdk-{member.day}.csv", cfg)


def first_line(source: Path | bytes) -> str:
    """Return the first line of an extracted CSV."""
    if isinstance(source, bytes):
        end = source.find(b"\n", 0, 1 << 16)
        return source[: end + 1].decode("utf-8", errors="replace")
    with source.open(encoding="utf-8", errors="replace") as f:
        return f.readline()


def typed_frame(
    source: Path | bytes, dialect: _process.Dialect
) -> pl.LazyFrame:
    """Scan with numeric & categorical dtypes parsed by the CSV reader."""
    read = {
        c: pl.String
        if isinstance(_process.SCHEMA[c], pl.Datetime)
        else _process.SCHEMA[c]
        for c in dialect.columns
    }
    lf = pl.scan_csv(
        source,
        has_header=False,
        skip_rows=int(dialect.has_header),
        separator=dialect.separator,
        schema=read,
        ignore_errors=True,
        decimal_comma=dialect.decimal_comma,
        encoding="utf8-lossy",
    )
    exprs = []
    for name, dtype in _process.SCHEMA.items():
        if name not in dialect.columns:
            exprs.append(pl.lit(None, dtype).alias(name))
            continue
        expr = pl.col(name)
        if isinstance(dtype, pl.Datetime):
            expr = expr.str.to_datetime(
                _process.DATETIME_FORMAT, time_unit="us", strict=False
            )
        if name in _process.SENTINELS:
            expr = pl.when(expr != _process.SENTINELS[name]).then(expr)
        exprs.append(expr.alias(name))
    return lf.select(exprs)


def frame(source: Path | bytes, cfg: Config) -> pl.LazyFrame:
    """Build the conversion query for one extracted CSV."""
    dialect = _process.detect_dialect(first_line(source))
    if cfg.scan == "typed":
        lf = typed_frame(source, dialect)
    else:
        raw = pl.scan_csv(
            source,
            has_header=False,
            skip_rows=int(dialect.has_header),
            separator=dialect.separator,
            schema=dict.fromkeys(dialect.columns, pl.String),
            encoding="utf8-lossy",
        )
        lf = raw.select(
            _process._column(name, dialect) for name in _process.SCHEMA
        )
    return lf.sort("MMSI", "DATETIME") if cfg.sort else lf


def convert(
    member: _process.Member, source: Any, parquet_dir: Path, cfg: Config
) -> Path:
    """Convert an extracted CSV to Parquet, then drop the CSV."""
    target = member.target(parquet_dir)
    partial = target.with_name(f"{target.name}.tmp")
    try:
        frame(source, cfg).sink_parquet(
            partial,
            compression=cast("Codec", cfg.codec),
            compression_level=cfg.level,
            row_group_size=cfg.rows,
            mkdir=True,
        )
        partial.replace(target)
    finally:
        if isinstance(source, Path):
            source.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return target


def timed_call(timings: dict[str, float], key: str, fn: Callable[[], T]) -> T:
    """Call `fn`, adding its wall time to `timings[key]`."""
    start = time.perf_counter()
    result = fn()
    timings[key] = timings.get(key, 0.0) + time.perf_counter() - start
    return result


def one_day(
    member: _process.Member,
    work: Path,
    cfg: Config,
    timings: dict[str, float],
) -> Path:
    """Extract & convert one day, timing both stages."""
    if cfg == PRESETS["baseline"]:
        return timed_call(
            timings,
            "convert_member_s",
            lambda: _process.convert_member(
                member, work / "parquet", work / "tmp"
            ),
        )
    source = timed_call(
        timings, "extract_s", lambda: extract(member, work / "tmp", cfg)
    )
    return timed_call(
        timings,
        "convert_s",
        lambda: convert(member, source, work / "parquet", cfg),
    )


def run_prefetch(
    members: list[_process.Member],
    work: Path,
    cfg: Config,
    timings: dict[str, float],
) -> list[Path]:
    """Extract up to `jobs` days ahead on threads; convert in order."""
    targets: list[Path] = []
    pending: deque[tuple[_process.Member, Any]] = deque()
    queue = iter(members)
    with ThreadPoolExecutor(max_workers=cfg.jobs) as pool:

        def submit() -> None:
            if (member := next(queue, None)) is not None:
                future = pool.submit(extract, member, work / "tmp", cfg)
                pending.append((member, future))

        for _ in range(cfg.jobs + 1):
            submit()
        while pending:
            member, future = pending.popleft()
            source = timed_call(timings, "wait_extract_s", future.result)
            submit()
            targets.append(
                timed_call(
                    timings,
                    "convert_s",
                    partial(convert, member, source, work / "parquet", cfg),
                )
            )
    return targets


def run_pass(
    members: list[_process.Member], work: Path, cfg: Config
) -> dict[str, Any]:
    """Process all members once, returning wall time & resource use."""
    timings: dict[str, float] = {}
    serial = dataclasses.replace(cfg, schedule="seq", jobs=1)
    with sampled() as stats:
        start = time.perf_counter()
        if cfg.schedule == "prefetch":
            targets = run_prefetch(members, work, cfg, timings)
        elif cfg.schedule == "conc":
            with ThreadPoolExecutor(max_workers=cfg.jobs) as pool:
                targets = list(
                    pool.map(lambda m: one_day(m, work, serial, {}), members)
                )
        else:
            targets = [one_day(m, work, cfg, timings) for m in members]
        wall = time.perf_counter() - start
    return {
        "wall_s": wall,
        "s_per_day": wall / len(members),
        **stats,
        "stage_s": timings,
        "parquet_bytes": {
            str(m.day): t.stat().st_size
            for m, t in zip(members, targets, strict=True)
        },
        "targets": targets,
    }


def bench_variant(
    name: str, cfg: Config, members: list[_process.Member], args: Any
) -> dict[str, Any]:
    """Warm up, then time `args.repeat` passes of one variant."""
    work = args.out / name
    shutil.rmtree(work, ignore_errors=True)
    console.rule(f"{name}: {cfg}")
    warm = run_pass(members[:1], work, dataclasses.replace(cfg, jobs=1))
    console.print(f"warm-up {members[0].day}: {warm['wall_s']:.2f} s")

    passes = []
    for run in range(1, args.repeat + 1):
        result = run_pass(members, work, cfg)
        targets = result.pop("targets")
        rows = [
            pl.scan_parquet(t).select(pl.len()).collect().item()
            for t in targets
        ]
        passes.append({"run": run, **result, "rows": rows})
        console.print(
            f"run {run}: {result['wall_s']:.2f} s "
            f"({result['s_per_day']:.2f} s/day, "
            f"{result['peak_rss_gb']:.1f} GB, "
            f"{result['cpu_cores']:.1f} cores)"
        )
        keep(targets, args, name)
    shutil.rmtree(work, ignore_errors=True)

    walls = [p["wall_s"] for p in passes]
    return {
        "variant": name,
        "config": dataclasses.asdict(cfg),
        "python": sys.version.split()[0],
        "polars": pl.__version__,
        "out_drive": args.out.drive,
        "days": [str(m.day) for m in members],
        "mean_wall_s": sum(walls) / len(walls),
        "mean_s_per_day": sum(walls) / len(walls) / len(members),
        "peak_rss_gb": max(p["peak_rss_gb"] for p in passes),
        "parquet_bytes": sum(passes[-1]["parquet_bytes"].values()),
        "passes": passes,
    }


def keep(targets: list[Path], args: Any, name: str) -> None:
    """Copy the `--keep-day` Parquet to `<keep-dir>/<name>/`."""
    if args.keep_dir is None:
        return
    for target in targets:
        if target.name == f"aisdk-{args.keep_day}.parquet":
            dest = args.keep_dir / name
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dest / target.name)


def report(results: list[dict[str, Any]]) -> None:
    """Print a summary table, with speed-up vs `baseline` if present."""
    base = next(
        (r for r in results if r["variant"].endswith("baseline")), None
    )
    table = Table(title="variants (mean over timed passes)")
    for column in ("variant", "s/day", "x base", "total s", "RSS GB", "MB"):
        table.add_column(column, justify="right")
    for r in results:
        speedup = base["mean_wall_s"] / r["mean_wall_s"] if base else 0.0
        table.add_row(
            r["variant"],
            f"{r['mean_s_per_day']:.2f}",
            f"{speedup:.2f}",
            f"{r['mean_wall_s']:.2f}",
            f"{r['peak_rss_gb']:.1f}",
            f"{r['parquet_bytes'] / 1e6:.0f}",
        )
    console.print(table)


def main() -> None:
    """Parse arguments & benchmark the selected variants."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("variants", nargs="+")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--keep-dir", type=Path)
    parser.add_argument("--keep-day", type=date.fromisoformat)
    parser.add_argument("--tag", default="", help="prefix for variant names")
    args = parser.parse_args()

    args.out = args.out.resolve()
    if args.out.is_relative_to(args.data_dir.resolve()):
        parser.error("--out must be outside --data-dir")
    args.out.mkdir(parents=True, exist_ok=True)
    variants = [parse_variant(spec) for spec in args.variants]
    members = select_members(args.data_dir, args.days)

    results_path = args.out / "results.json"
    try:
        existing = json.loads(results_path.read_text())
    except FileNotFoundError:
        existing = []
    results = []
    for name, cfg in variants:
        label = f"{args.tag}-{name}" if args.tag else name
        results.append(bench_variant(label, cfg, members, args))
        labels = {r["variant"] for r in results}
        merged = [r for r in existing if r["variant"] not in labels]
        merged += results
        results_path.write_text(json.dumps(merged, indent=2, default=str))
    report(results)


if __name__ == "__main__":
    main()
