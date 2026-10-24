"""Profile `dkais process` stages on real archives.

Reads zips from `<data-dir>/raw` (read-only) and writes Parquet, CSVs and
reports only under `--out`, so the real `<data-dir>/parquet` is untouched.

USAGE:
    uv run python scripts/profile_process.py \
        --data-dir D:/Data/AISDK --out <scratch> [--days 3] \
        [--mode stages|cprofile|polars] [--repeat 1] [--in-memory]

MODES:
    stages    Wall time per stage (extract, convert) with peak RSS, written
              to `<out>/stages.json`.
    cprofile  cProfile over `convert_member`, dumped to `<out>/*.prof` with
              the top 25 cumulative entries printed.
    polars    Optimised plan & streaming collect time for the first day, in
              `<out>/polars.json`, plus the physical streaming graph in
              `<out>/physical.dot` (shows whether nodes run natively).
"""

import argparse
import cProfile
import json
import pstats
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import polars as pl
import psutil
from rich.console import Console
from rich.table import Table

from dkais import _process, _s3

console = Console()


@contextmanager
def peak_rss(interval: float = 0.05) -> Generator[list[int], None, None]:
    """Sample the process RSS on a background thread.

    Yields:
        A one-item list holding the peak RSS in bytes, final on exit.
    """
    process = psutil.Process()
    peak = [process.memory_info().rss]
    stop = threading.Event()

    def sample() -> None:
        while not stop.wait(interval):
            peak[0] = max(peak[0], process.memory_info().rss)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        yield peak
    finally:
        stop.set()
        thread.join()


@contextmanager
def timed(timings: dict[str, float], key: str) -> Generator[None, None, None]:
    """Record the wall time of a block in `timings[key]`."""
    start = time.perf_counter()
    yield
    timings[key] = time.perf_counter() - start


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


def run_stages(
    member: _process.Member, out: Path, *, in_memory: bool
) -> dict[str, Any]:
    """Time the extract & convert stages of `convert_member` for one day."""
    parquet_dir = out / "parquet"
    timings: dict[str, float] = {}

    with peak_rss() as rss, timed(timings, "total_s"):
        with timed(timings, "extract_s"):
            source = _process.extract_member(
                member, out / "tmp", in_memory=in_memory
            )
        with timed(timings, "convert_s"):
            target = _process.convert(member, source, parquet_dir)
        del source

    rows = pl.scan_parquet(target).select(pl.len()).collect().item()
    return {
        "day": str(member.day),
        "zip_bytes": member.zip_path.stat().st_size,
        "csv_bytes": member.size,
        "parquet_bytes": target.stat().st_size,
        "rows": rows,
        **timings,
        "extract_mb_s": member.size / 1e6 / timings["extract_s"],
        "peak_rss_gb": rss[0] / 1e9,
    }


def mode_stages(
    members: list[_process.Member], out: Path, repeat: int, *, in_memory: bool
) -> None:
    """Run `run_stages` for each member, `repeat` times."""
    results = [
        {"run": run, **run_stages(member, out, in_memory=in_memory)}
        for run in range(1, repeat + 1)
        for member in members
    ]
    (out / "stages.json").write_text(json.dumps(results, indent=2))

    table = Table(title="convert_member stages (s)")
    columns = [
        *("run", "day", "extract_s", "convert_s", "total_s"),
        *("extract_mb_s", "peak_rss_gb", "rows"),
    ]
    for column in columns:
        table.add_column(column, justify="right")
    for result in results:
        table.add_row(
            *(
                f"{v:.2f}" if isinstance(v, float) else str(v)
                for v in (result[c] for c in columns)
            )
        )
    console.print(table)


def mode_cprofile(
    members: list[_process.Member], out: Path, *, in_memory: bool
) -> None:
    """Run cProfile over `convert_member` for each member."""
    for member in members:
        profiler = cProfile.Profile()
        profiler.runcall(
            _process.convert_member,
            member,
            out / "parquet",
            out / "tmp",
            in_memory=in_memory,
        )
        prof_path = out / f"convert-{member.day}.prof"
        profiler.dump_stats(prof_path)
        console.rule(str(prof_path))
        pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)


def mode_polars(
    member: _process.Member, out: Path, *, in_memory: bool
) -> None:
    """Profile the Polars query for one day."""
    source = _process.extract_member(member, out / "tmp", in_memory=in_memory)
    try:
        dialect = _process.detect_dialect(_process._first_line(source))
        raw = pl.scan_csv(
            source,
            has_header=False,
            skip_rows=int(dialect.has_header),
            separator=dialect.separator,
            schema=dict.fromkeys(dialect.columns, pl.String),
            encoding="utf8-lossy",
        )
        lf = _process.transform(raw, dialect)
        plan = lf.explain(engine="streaming")
        graph = lf.show_graph(
            engine="streaming",
            plan_stage="physical",
            raw_output=True,
            show=False,
        )
        timings: dict[str, float] = {}
        with timed(timings, "collect_s"):
            lf.collect(engine="streaming")
    finally:
        if isinstance(source, Path):
            source.unlink(missing_ok=True)

    console.print(plan)
    console.print(timings)
    (out / "physical.dot").write_text(str(graph), encoding="utf-8")
    (out / "polars.json").write_text(
        json.dumps({"plan": plan, **timings}, indent=2)
    )


def main() -> None:
    """Parse arguments & run the selected profiling mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument(
        "--mode", choices=["stages", "cprofile", "polars"], default="stages"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help="Extract CSVs into memory, rather than to `<out>/tmp`",
    )
    args = parser.parse_args()

    out = args.out.resolve()
    if out.is_relative_to(args.data_dir.resolve()):
        parser.error("--out must be outside --data-dir")
    out.mkdir(parents=True, exist_ok=True)

    members = select_members(args.data_dir, args.days)
    console.print(f"Profiling {len(members)} days: {members[0].day}..")
    console.print(f"Inflating with {_process.INFLATE.__name__}")
    if args.mode == "stages":
        mode_stages(members, out, args.repeat, in_memory=args.in_memory)
    elif args.mode == "cprofile":
        mode_cprofile(members, out, in_memory=args.in_memory)
    else:
        mode_polars(members[0], out, in_memory=args.in_memory)


if __name__ == "__main__":
    main()
