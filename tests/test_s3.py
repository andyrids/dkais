"""Unit tests for the bucket catalogue (`dkais._s3`)."""

from datetime import date
from pathlib import Path

import pytest
from botocore.stub import Stubber

from dkais import _s3, exceptions


@pytest.mark.parametrize(
    ("key", "start", "end"),
    [
        ("2006/aisdk-2006-03.zip", date(2006, 3, 1), date(2006, 3, 31)),
        ("2024/aisdk-2024-02.zip", date(2024, 2, 1), date(2024, 2, 29)),
        ("2017/all_sources_2017-02.zip", date(2017, 2, 1), date(2017, 2, 28)),
        ("2024/aisdk-2024-03-01.zip", date(2024, 3, 1), date(2024, 3, 1)),
        ("aisdk-2026-10-18.zip", date(2026, 10, 18), date(2026, 10, 18)),
    ],
)
def test_parse_key_archive_patterns(key: str, start: date, end: date) -> None:
    """Each bucket archive layout parses to its covered period."""
    archive = _s3.parse_key(key, 1)
    assert archive is not None
    assert (archive.start, archive.end) == (start, end)


@pytest.mark.parametrize(
    "key",
    [
        "!_README_information_CSV_files.txt",
        "index.html",
        "list.js",
        "jquery.min.js",
    ],
)
def test_parse_key_ignores_non_archives(key: str) -> None:
    """README & web assets are not archives."""
    assert _s3.parse_key(key, 1) is None


def test_local_path_groups_by_year() -> None:
    """Root-level daily archives are stored under their year, like the rest."""
    archive = _s3.parse_key("aisdk-2026-10-18.zip", 1)
    assert archive is not None
    assert archive.local_path(Path("raw")) == Path(
        "raw/2026/aisdk-2026-10-18.zip"
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, True),
        (date(2006, 3, 31), None, True),
        (date(2006, 4, 1), None, False),
        (None, date(2006, 3, 1), True),
        (None, date(2006, 2, 28), False),
        (date(2006, 3, 10), date(2006, 3, 12), True),
    ],
)
def test_overlaps(
    start: date | None, end: date | None, expected: bool
) -> None:
    """A monthly archive overlaps any range touching one of its days."""
    archive = _s3.parse_key("2006/aisdk-2006-03.zip", 1)
    assert archive is not None
    assert archive.overlaps(start, end) is expected


def test_list_archives_filters_and_sorts() -> None:
    """Listing drops non-archives & out-of-range keys, oldest first."""
    client = _s3.make_client()
    contents = [
        {"Key": "aisdk-2026-10-18.zip", "Size": 3},
        {"Key": "index.html", "Size": 1},
        {"Key": "2006/aisdk-2006-03.zip", "Size": 2},
        {"Key": "2024/aisdk-2024-03-01.zip", "Size": 4},
    ]
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {"Contents": contents, "IsTruncated": False},
            {"Bucket": _s3.BUCKET},
        )
        archives = _s3.list_archives(client, end=date(2025, 1, 1))

    assert [a.key for a in archives] == [
        "2006/aisdk-2006-03.zip",
        "2024/aisdk-2024-03-01.zip",
    ]


def test_list_archives_wraps_bucket_errors() -> None:
    """An S3 failure becomes a `BucketError`, reported without a traceback."""
    client = _s3.make_client()
    with Stubber(client) as stubber:
        stubber.add_client_error("list_objects_v2", "AccessDenied")
        with pytest.raises(exceptions.BucketError, match="Cannot list bucket"):
            _s3.list_archives(client)
