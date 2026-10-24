"""Unit tests for archive downloads (`dkais._download`)."""

import threading
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from dkais import _download, _s3, exceptions
from dkais._s3 import Archive


class FakeReporter:
    """Record reporter calls, like `dkais._report.PlainReporter` sees them."""

    def __init__(self) -> None:
        """Start with nothing reported."""
        self.bytes = 0
        self.done: list[str] = []
        self.failed: list[str] = []

    def advance(self, n: int) -> None:
        """Count transferred bytes."""
        self.bytes += n

    def item_done(self, name: str, detail: str) -> None:
        """Record a downloaded archive."""
        self.done.append(name)

    def item_failed(self, name: str, error: BaseException) -> None:
        """Record a failed archive."""
        self.failed.append(name)


def _archive(key: str, size: int) -> Archive:
    archive = _s3.parse_key(key, size)
    assert archive is not None
    return archive


def _fake_download(
    bucket: str, key: str, filename: str, Config: Any, Callback: Any
) -> None:
    """Write `size` bytes in one callback, like a completed transfer."""
    size = int(key.split("-")[-1].split(".")[0])
    Callback(size)
    Path(filename).write_bytes(b"x" * size)


def test_pending_skips_complete_files(tmp_path: Path) -> None:
    """Archives with a size-matched local file are not pending."""
    done = _archive("aisdk-2026-10-17.zip", 17)
    partial = _archive("aisdk-2026-10-18.zip", 18)
    todo = _archive("aisdk-2026-10-19.zip", 19)
    for archive, size in ((done, 17), (partial, 5)):
        archive.local_path(tmp_path).parent.mkdir(exist_ok=True, parents=True)
        archive.local_path(tmp_path).write_bytes(b"x" * size)

    pending = _download.pending_archives([done, partial, todo], tmp_path)

    assert pending == [partial, todo]


def test_download_reports_each_archive(tmp_path: Path) -> None:
    """Every archive is downloaded & reported, with its bytes."""
    archives = [_archive(f"aisdk-2026-10-1{d}.zip", 10 + d) for d in (7, 8)]

    client = mock.MagicMock()
    client.download_file.side_effect = _fake_download
    reporter = FakeReporter()

    failed = _download.download_archives(client, archives, tmp_path, reporter)

    assert failed == []
    assert sorted(reporter.done) == [a.name for a in archives]
    assert reporter.bytes == 17 + 18
    assert all(_download.is_downloaded(a, tmp_path) for a in archives)


def test_download_reports_failures_and_continues(tmp_path: Path) -> None:
    """A failed archive is returned while the others still download."""
    bad = _archive("aisdk-2026-10-17.zip", 17)
    good = _archive("aisdk-2026-10-18.zip", 18)

    def download(bucket: str, key: str, filename: str, **kwargs: Any) -> None:
        if key == bad.key:
            raise OSError("disk full")
        _fake_download(bucket, key, filename, **kwargs)

    client = mock.MagicMock()
    client.download_file.side_effect = download

    reporter = FakeReporter()

    failed = _download.download_archives(
        client, [bad, good], tmp_path, reporter
    )

    assert failed == [bad]
    assert reporter.failed == [bad.name]
    assert reporter.done == [good.name]
    assert _download.is_downloaded(good, tmp_path)


def test_download_interrupt_aborts_in_flight_transfer(tmp_path: Path) -> None:
    """Ctrl-C makes the transfer callback raise, then re-raises."""
    archive = _archive("aisdk-2026-10-18.zip", 18)
    started, aborted = threading.Event(), threading.Event()

    def download(bucket: str, key: str, filename: str, **kwargs: Any) -> None:
        started.set()
        try:
            for _ in range(10_000):
                kwargs["Callback"](0)
                threading.Event().wait(0.001)
        except exceptions.InterruptedTransferError:
            aborted.set()
            raise

    def interrupt(*args: Any, **kwargs: Any) -> None:
        started.wait(5)
        raise KeyboardInterrupt

    client = mock.MagicMock()
    client.download_file.side_effect = download

    with (
        mock.patch.object(_download, "wait", side_effect=interrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        _download.download_archives(
            client, [archive], tmp_path, FakeReporter()
        )

    assert aborted.is_set()
