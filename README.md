# Danish Maritime Authority AIS dataset

This Python package streamlines the download and processing of historical Automatic
Identification System (AIS) data provided by the Danish Maritime Authority (DMA).

**For more information about AIS, see:**

- `AIS Message Types <https://arundaleais.github.io/docs/ais/ais_message_types.html>`_: Types of
  AIS messages used for communication.
- `Navigation Center <https://www.navcen.uscg.gov/ais-messages>`_: This is an overview of
  Navigation Systems and Services.
- `AIS transponders <https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx>`_: Regulations regarding
  AIS use, maritime security, and additional information.
- `Wikipedia Article <https://en.wikipedia.org/wiki/Automatic_identification_system>`_: Here is an
  overview of AIS, including its history, functionality and applications.

## Usage

`aisdma` mirrors the public bucket to a data directory and converts each daily CSV to Parquet.
Set the directory once with `AISDMA_DATA_DIR` (or pass `--data-dir` to any command).

```sh
aisdma list --start 2026-10                       # archives & local state
aisdma download --start 2026-10 --end 2026-10-07  # zips to <data-dir>/raw
aisdma process --start 2026-10 --end 2026-10-07   # to <data-dir>/parquet
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
  tmp/                                  # extracted CSVs, when RAM is short
```

`process` unzips the next day while converting the current one. A day's CSV is held in memory when
five times its size is available (a 3 GB daily CSV needs ~15 GB), otherwise it is extracted to
`<data-dir>/tmp`. Unzipping uses zlib-ng, either from the `zlib-ng` package or the standard
library where it is built in (CPython 3.14+ on Windows).

Each Parquet file holds one day: all 26 DMA columns renamed to `UPPER_SNAKE` and typed, sorted by
`MMSI` and `DATETIME`. AIS "not available" values (latitude 91, longitude 181, heading 511,
`Unknown` IMO/callsign/destination) become null, and no rows are dropped. The three historical CSV
dialects (comma with 22 columns, header-less semicolon with decimal commas, comma with 26 columns)
share one output schema.

```python
import polars as pl

ais = pl.scan_parquet("<data-dir>/parquet", hive_partitioning=True)
october = ais.filter(pl.col("YEAR") == 2026, pl.col("MONTH") == 10)
```

## Dataset

- [DMA](https://www.dma.dk/safety-at-sea/navigational-information/ais-data)
- [S3](http://aisdata.ais.dk/)

AIS is a maritime navigation safety communications system standardised by the International
Telecommunication Union (ITU) and adopted by the International Maritime Organization (IMO). AIS is
a VHF-based navigation and anti-collision tool making it possible to exchange information between
ships.

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

Below are the features in the dataset provided by the DMA

1. Timestamp - dd/mm/yyyy HH:MM:SS
2. Type of mobile - Vessel class
3. MMSI - Maritime Mobile Service Identity (unique 9-digit number)
4. Latitude - Decimal degrees
5. Longitude - Decimal degrees
6. Navigational Status - status (AIS metadata)
7. ROT - Rate of Turn (AIS metadata)
8. SOG - Speed Over Ground (AIS metadata)
9. COG - Course Over Ground (AIS metadata)
10. Heading - Heading (AIS metadata)
11. IMO - International Maritime Organization number
12. Callsign - Callsign of the vessel
13. Name - Name of the vessel
14. Ship type - Type of ship (AIS metadata)
15. Cargo type - Type of cargo (AIS metadata)
16. Width - Width of the vessel
17. Length - Length of the vessel
18. Type of position fixing device
19. Draught - Draught of the vessel (AIS metadata)
20. Destination - Destination of the vessel (AIS metadata)
21. ETA - Estimated Time of Arrival
22. Data source type
23. Size A - Length from GPS to the bow
24. Size B - Length from GPS to the stern
25. Size C - Length from GPS to starboard side
26. Size D - Length from GPS to port side
