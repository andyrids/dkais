"""Concurrent download of bucket archives, reporting each archive."""

import logging
import threading
import time
from collections.abc import Sequence
from concurrent.futures import (
    FIRST_EXCEPTION,
    Future,
    ThreadPoolExecutor,
    wait,
)
from pathlib import Path
from typing import TYPE_CHECKING

from boto3.s3.transfer import TransferConfig
from botocore.exceptions import BotoCoreError, ClientError
from rich.filesize import decimal

from dkais import exceptions
from dkais._report import duration
from dkais._s3 import BUCKET, TRANSFER_CONCURRENCY, Archive

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from dkais._report import Reporter

logger = logging.getLogger(__package__)

_MIB = 1024 * 1024

TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=8 * _MIB,
    multipart_chunksize=8 * _MIB,
    max_concurrency=TRANSFER_CONCURRENCY,
    use_threads=True,
)

_POLL_SECONDS = 0.5
"""Interval for the main thread to wake while waiting, so Ctrl-C is handled."""


def is_downloaded(archive: Archive, raw_dir: Path) -> bool:
    """Check whether an archive is fully downloaded.

    NOTE: s3transfer writes to a temporary name and renames on success, so a
    size match identifies a complete file.

    Args:
        archive: The bucket archive.
        raw_dir: The root directory for raw archives.

    Returns:
        True if the local file exists with the remote size.
    """
    path = archive.local_path(raw_dir)
    return path.is_file() and path.stat().st_size == archive.size


def pending_archives(
    archives: Sequence[Archive], raw_dir: Path
) -> list[Archive]:
    """Return the archives not yet fully downloaded.

    Args:
        archives: The archives in the selected range.
        raw_dir: The root directory for raw archives.

    Returns:
        The archives to download, in order.
    """
    return [a for a in archives if not is_downloaded(a, raw_dir)]


def download_archives(
    client: "S3Client",
    archives: Sequence[Archive],
    raw_dir: Path,
    reporter: "Reporter",
    jobs: int = 4,
) -> list[Archive]:
    """Download archives, `jobs` files at a time.

    A failed archive is reported and skipped so the rest still download; a
    re-run retries it. On `KeyboardInterrupt`, in-flight transfers are aborted
    (their temporary files are removed) before the interrupt is re-raised.

    Args:
        client: The S3 client.
        archives: The archives to download, see `pending_archives`.
        raw_dir: The root directory for raw archives.
        reporter: Receives transferred bytes & each archive's outcome.
        jobs: The number of concurrent file downloads.

    Returns:
        The archives that failed to download.
    """
    stop = threading.Event()

    def fetch(archive: Archive) -> None:
        path = archive.local_path(raw_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        began = time.monotonic()

        def callback(transferred: int) -> None:
            if stop.is_set():
                raise exceptions.InterruptedTransferError(archive.key)
            reporter.advance(transferred)

        client.download_file(
            BUCKET,
            archive.key,
            str(path),
            Config=TRANSFER_CONFIG,
            Callback=callback,
        )
        elapsed = duration(time.monotonic() - began)
        reporter.item_done(
            archive.name, f"{decimal(archive.size)} in {elapsed}"
        )

    failed: list[Archive] = []
    executor = ThreadPoolExecutor(max_workers=jobs)
    futures: dict[Future[None], Archive] = {
        executor.submit(fetch, archive): archive for archive in archives
    }
    not_done = set(futures)
    try:
        while not_done:
            done, not_done = wait(
                not_done, timeout=_POLL_SECONDS, return_when=FIRST_EXCEPTION
            )
            for future in done:
                try:
                    future.result()
                except (BotoCoreError, ClientError, OSError) as e:
                    archive = futures[future]
                    logger.debug(
                        "Failed to download `%s`", archive.key, exc_info=True
                    )
                    reporter.item_failed(archive.name, e)
                    failed.append(archive)
    except KeyboardInterrupt:
        stop.set()
        raise
    finally:
        # Waits for aborted transfers to clean up their temporary files
        executor.shutdown(wait=True, cancel_futures=True)
    return failed
