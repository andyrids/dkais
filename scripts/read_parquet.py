import os
import sys
import polars as pl

DATA_DIR = os.environ["DKAIS_DATA_DIR"]
PARQUET_DIR = os.path.join(DATA_DIR, "parquet")

ais = pl.scan_parquet(PARQUET_DIR, hive_partitioning=True)
october = ais.filter(pl.col("YEAR") == 2026, pl.col("MONTH") == 10)
sys.stdout.write(f"{october.collect_schema()}\n")
sys.stdout.write(f"{october.select(pl.len()).collect()}\n")
sys.stdout.write(f"{october.head(10).collect()}\n")
