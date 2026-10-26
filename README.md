# DK AIS [`dkais`]

<!-- pyml disable-next-line line-length -->
![PyPI - Version](https://img.shields.io/pypi/v/dkais?style=plastic) ![PyPI - Downloads](https://img.shields.io/pypi/dm/dkais?style=plastic) ![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/andyrids/dkais/ci.yml?branch=main&style=plastic&logo=pytest&logoColor=%230A9EDC&label=pytest) [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/andyrids/dkais/blob/main/docs/examples/my_notebook.ipynb)

This Python package streamlines the download and processing of historical Automatic
Identification System (AIS) data provided by the Danish Maritime Authority (DMA).

AIS is a maritime navigation safety communications system standardised by the International
Telecommunication Union (ITU) and adopted by the International Maritime Organization (IMO). AIS is
a VHF-based navigation and anti-collision tool making it possible to exchange information between
ships.

For more information about AIS, including its history, functionality and applications - see [Wikipedia](https://en.wikipedia.org/wiki/Automatic_identification_system).

<!-- pyml disable MD034 -->
https://github.com/user-attachments/assets/b809ad7f-fca4-404a-9a1e-20e29a72d7e8
<!-- pyml enable MD034 -->

## Usage

`dkais` mirrors the public bucket to a data directory and converts each daily CSV to Parquet.
Set the directory once with `DKAIS_DATA_DIR` (or pass `--data-dir` to any command).

```sh
dkais list --start 2026-10                       # archives & local state
dkais download --start 2026-10 --end 2026-10-07  # zips to <data-dir>/raw
dkais process --start 2026-10 --end 2026-10-07   # to <data-dir>/parquet
```

`list --json` emits machine-readable output, `download -j N` sets concurrent downloads (default 4)
and `process --force` re-converts days that already have Parquet output.

Dates are `YYYY`, `YYYY-MM` or `YYYY-MM-DD` and inclusive. `download` and `process` need `--start`
or `--all` - the full bucket is ~4.2 TB compressed. `download` fetches whole archives (monthly up to
2024-02, daily since), while `process` converts only the days in range. Both skip completed work,
so an interrupted run resumes where it stopped. `process` works offline from `<data-dir>/raw`.

```text
<data-dir>/
  raw/YYYY/aisdk-YYYY-MM[-DD].zip
  parquet/YEAR=2026/MONTH=10/aisdk-2026-10-18.parquet
  tmp/
```

> [!NOTE]
> The `tmp/` directory is used for extracted CSVs when RAM is short.

`process` unzips the next day while converting the current one. A day's CSV is held in memory when
five times its size is available (a 3 GB daily CSV needs ~15 GB), otherwise it is extracted to
`<data-dir>/tmp`. Unzipping uses zlib-ng, either from the `zlib-ng` package or the standard
library where it is built in (CPython 3.14+ on Windows).

Each Parquet file holds one day: all 26 DMA columns renamed to `UPPER_SNAKE` and typed, sorted by
`MMSI` and `DATETIME`. AIS "not available" values (latitude 91, longitude 181, heading 511,
`Unknown` IMO/callsign/destination) become null, and no rows are dropped. The three historical CSV
dialects (comma with 22 columns, header-less semicolon with decimal commas, comma with 26 columns)
share one output schema.

> [!TIP]
> See [Features](#features) for a the full schema table and data types.

```python
import polars as pl

ais = pl.scan_parquet("<data-dir>/parquet", hive_partitioning=True)
october = ais.filter(pl.col("YEAR") == 2026, pl.col("MONTH") == 10)
```

## Dataset

- [DMA website](https://www.dma.dk/safety-at-sea/navigational-information/ais-data)
- [AIS S3](http://aisdata.ais.dk/)

### Metadata

- **Identifiers** - ship name, call sign or IMO number & MMSI number
- **Navigation** - position, course & speed
- **Voyage** - destination, ETA & actual draught

### Veracity

Information received by AIS originates from the ships' own instruments. There can be errors in the
information inserted or technical defects that may give an erroneous impression of the ship's speed
or position.

AIS contributes to safety of navigation and has, in many ways, made it easier to navigate safely.
However, AIS also has some limitations that it is important to take account of. If you trust your
AIS data uncritically, it may be risky.

### Features

See the table below, for the original DMA dataset features and the new feature names in the
typed Parquet files after `dkais` processing.

<!-- pyml disable MD013 -->
| Original                        | Processed        | Type          | Comment |
| ------------------------------- | ---------------- | ------------- | ------- |
| Timestamp | DATETIME | datetime[μs]  | dd/mm/yyyy HH:MM:SS |
| Type of mobile | MOBILE_TYPE | cat | Vessel class |
| MMSI | MMSI | i32 | Maritime Mobile Service Identity (unique 9-digit number) |
| Latitude | LATITUDE | f64 | Decimal degrees, null if 91 (not available) |
| Longitude | LONGITUDE | f64 | Decimal degrees, null if 181 (not available) |
| Navigational Status | NAV_STATUS | cat | Navigational status (AIS metadata) |
| ROT | ROT | f32 | Rate of Turn (AIS metadata) |
| SOG | SOG | f32 | Speed Over Ground (AIS metadata) |
| COG | COG | f32 | Course Over Ground (AIS metadata) |
| Heading | HEADING | i16 | Heading (AIS metadata), null if 511 (not available) |
| IMO | IMO | i32 | International Maritime Organization number, null if `Unknown`  |
| Callsign | CALLSIGN | str | Callsign of the vessel, null if `Unknown` |
| Name | NAME | str | Name of the vessel |
| Ship type | SHIP_TYPE | cat | Type of ship (AIS metadata) |
| Cargo type | CARGO_TYPE | cat | Type of cargo (AIS metadata) |
| Width | WIDTH | i16 | Width of the vessel |
| Length | LENGTH | i16 | Length of the vessel |
| Type of position fixing device | POSITION_DEVICE | cat | e.g. GPS, GLONASS, Galileo, Surveyed |
| Draught | DRAUGHT | f32 | Draught of the vessel (AIS metadata) |
| Destination | DESTINATION | str | Destination of the vessel (AIS metadata), null if `Unknown` |
| ETA | ETA | datetime[μs] | Estimated Time of Arrival, dd/mm/yyyy HH:MM:SS |
| Data source type | DATA_SOURCE | cat | e.g. AIS |
| A | SIZE_A | i16 | Length from GPS to the bow, null in 22-column files |
| B | SIZE_B | i16 | Length from GPS to the stern, null in 22-column files |
| C | SIZE_C | i16 | Length from GPS to starboard side, null in 22-column files |
| D | SIZE_D | i16 | Length from GPS to port side, null in 22-column files |
<!-- pyml enable MD013 -->
