"""Catalogue of the public DMA AIS archives on S3 (`aisdata.ais.dk`).

The bucket holds three archive layouts, all parsed by `parse_key`:

- `YYYY/aisdk-YYYY-MM.zip` & `YYYY/all_sources_YYYY-MM.zip` - monthly
- `YYYY/aisdk-YYYY-MM-DD.zip` - daily, archived by year
- `aisdk-YYYY-MM-DD.zip` - daily, at the bucket root (most recent)
"""

import calendar
import dataclasses
import logging
import re
from datetime import date
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import boto3
from botocore import UNSIGNED
from botocore.config import Config

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__package__)

BUCKET = "aisdata.ais.dk"
REGION = "eu-central-1"

# Parallel ranged GETs per file, used to size the connection pool
TRANSFER_CONCURRENCY = 4

_KEY_PATTERN = re.compile(
    r"(?:\d{4}/)?(?:aisdk|all_sources)[-_]"
    r"(?P<year>\d{4})-(?P<month>\d{2})(?:-(?P<day>\d{2}))?\.zip"
)


@dataclasses.dataclass(frozen=True, slots=True)
class Archive:
    """A zip archive in the bucket, covering one day or one month.

    Attributes:
        key: The S3 object key.
        size: The object size in bytes.
        start: The first day covered by the archive.
        end: The last day covered by the archive.
    """

    key: str
    size: int
    start: date
    end: date

    @property
    def name(self) -> str:
        """The archive file name, without any key prefix."""
        return PurePosixPath(self.key).name

    def local_path(self, raw_dir: Path) -> Path:
        """Return the local download path, grouped by year.

        Args:
            raw_dir: The root directory for raw archives.

        Returns:
            The path `raw_dir/YYYY/<name>`.
        """
        return raw_dir / str(self.start.year) / self.name

    def overlaps(self, start: date | None, end: date | None) -> bool:
        """Check whether the archive covers any day in a date range.

        Args:
            start: The inclusive range start, or `None` for unbounded.
            end: The inclusive range end, or `None` for unbounded.

        Returns:
            True if the archive period overlaps the range.
        """
        return (start is None or self.end >= start) and (
            end is None or self.start <= end
        )


def parse_key(key: str, size: int) -> Archive | None:
    """Parse an S3 key into an `Archive`.

    Args:
        key: The S3 object key.
        size: The object size in bytes.

    Returns:
        The archive, or `None` for non-archive keys (README, web assets).
    """
    if not (match := _KEY_PATTERN.fullmatch(key)):
        return None
    year, month = int(match["year"]), int(match["month"])
    if match["day"]:
        start = end = date(year, month, int(match["day"]))
    else:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    return Archive(key=key, size=size, start=start, end=end)


def make_client(jobs: int = 1) -> "S3Client":
    """Create an anonymous S3 client for the public bucket.

    Args:
        jobs: The number of concurrent file downloads to size the
            connection pool for.

    Returns:
        An unsigned, thread-safe S3 client.
    """
    config = Config(
        signature_version=UNSIGNED,
        max_pool_connections=max(10, jobs * TRANSFER_CONCURRENCY),
        retries={"mode": "adaptive", "max_attempts": 10},
        connect_timeout=10,
        read_timeout=60,
    )
    return boto3.client("s3", region_name=REGION, config=config)


def list_archives(
    client: "S3Client",
    start: date | None = None,
    end: date | None = None,
) -> list[Archive]:
    """List bucket archives overlapping a date range, oldest first.

    Args:
        client: The S3 client.
        start: The inclusive range start, or `None` for unbounded.
        end: The inclusive range end, or `None` for unbounded.

    Returns:
        The matching archives, sorted by start date.
    """
    paginator = client.get_paginator("list_objects_v2")
    archives = [
        archive
        for page in paginator.paginate(Bucket=BUCKET)
        for obj in page.get("Contents", [])
        if (archive := parse_key(obj["Key"], obj["Size"]))
        and archive.overlaps(start, end)
    ]
    logger.debug("Listed %d archives in `%s`", len(archives), BUCKET)
    return sorted(archives, key=lambda a: a.start)
