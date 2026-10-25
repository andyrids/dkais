"""Check the Parquet output in `<data-dir>/parquet` without loading it.

Prints the schema, row count & first 10 rows for October 2026. Each is
evaluated lazily, so only those rows are read.

USAGE:
    just test-processed
"""

import os
import sys

import polars as pl
from rich.pretty import pprint

try:
    DATA_DIR = os.environ["DKAIS_DATA_DIR"]
except KeyError:
    DATA_DIR = "C:\\Users\\andre\\Data\\AISDK"
PARQUET_DIR = os.path.join(DATA_DIR, "parquet")

ais = pl.scan_parquet(PARQUET_DIR, hive_partitioning=True)
october = ais.filter(pl.col("YEAR") == 2026, pl.col("MONTH") == 10)

sys.stdout.write("SCHEMA:\n")
pprint(october.collect_schema().to_python(), expand_all=True)

count = october.select(pl.len()).collect().item()
sys.stdout.write(f"ROW COUNT: {count:,}\n\n")

sys.stdout.write(f"FIRST 10 ROWS:\n")
pprint(october.head(10).collect(), expand_all=True)
