"""Unit tests for archive downloads (`dkais._download`)."""

import threading
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from rich.progress import Progress

from dkais import _download, _s3, exceptions
from dkais._s3 import Archive


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


def test_download_skips_complete_files(tmp_path: Path) -> None:
    """Archives with a size-matched local file are not re-downloaded."""
    done = _archive("aisdk-2026-10-17.zip", 17)
    todo = _archive("aisdk-2026-10-18.zip", 18)
    done.local_path(tmp_path).parent.mkdir(parents=True)
    done.local_path(tmp_path).write_bytes(b"x" * 17)

    client = mock.MagicMock()
    client.download_file.side_effect = _fake_download
    progress = Progress(disable=True)

    failed = _download.download_archives(
        client, [done, todo], tmp_path, progress
    )

    assert failed == []
    assert client.download_file.call_count == 1
    assert client.download_file.call_args.args[1] == todo.key
    assert _download.is_downloaded(todo, tmp_path)
    overall = progress.tasks[0]
    assert overall.total == overall.completed == 18


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

    failed = _download.download_archives(
        client, [bad, good], tmp_path, Progress(disable=True)
    )

    assert failed == [bad]
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
            client, [archive], tmp_path, Progress(disable=True)
        )

    assert aborted.is_set()
