"""Concurrent download of bucket archives with Rich progress."""

import logging
import threading
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
from rich.progress import Progress

from aisdma import exceptions
from aisdma._s3 import BUCKET, TRANSFER_CONCURRENCY, Archive

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__package__)

_MIB = 1024 * 1024

TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=8 * _MIB,
    multipart_chunksize=8 * _MIB,
    max_concurrency=TRANSFER_CONCURRENCY,
    use_threads=True,
)

# Interval for the main thread to wake while waiting, so Ctrl-C is handled
_POLL_SECONDS = 0.5


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


def download_archives(
    client: "S3Client",
    archives: Sequence[Archive],
    raw_dir: Path,
    progress: Progress,
    jobs: int = 4,
) -> list[Archive]:
    """Download archives not already present, `jobs` files at a time.

    A failed archive is logged and skipped so the rest still download; a
    re-run retries it. On `KeyboardInterrupt`, in-flight transfers are aborted
    (their temporary files are removed) before the interrupt is re-raised.

    Args:
        client: The S3 client.
        archives: The archives to download.
        raw_dir: The root directory for raw archives.
        progress: The active progress display.
        jobs: The number of concurrent file downloads.

    Returns:
        The archives that failed to download.
    """
    pending = [a for a in archives if not is_downloaded(a, raw_dir)]
    logger.debug(
        "Skipping %d downloaded archives", len(archives) - len(pending)
    )

    overall = progress.add_task(
        "[cyan]Download", total=sum(a.size for a in pending)
    )
    stop = threading.Event()

    def fetch(archive: Archive) -> None:
        path = archive.local_path(raw_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        task = progress.add_task(archive.name, total=archive.size)

        def callback(transferred: int) -> None:
            if stop.is_set():
                raise exceptions.InterruptedTransferError(archive.key)
            progress.advance(task, transferred)
            progress.advance(overall, transferred)

        try:
            client.download_file(
                BUCKET,
                archive.key,
                str(path),
                Config=TRANSFER_CONFIG,
                Callback=callback,
            )
        finally:
            progress.remove_task(task)

    failed: list[Archive] = []
    executor = ThreadPoolExecutor(max_workers=jobs)
    futures: dict[Future[None], Archive] = {
        executor.submit(fetch, archive): archive for archive in pending
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
                except (BotoCoreError, ClientError, OSError):
                    logger.exception(
                        "Failed to download `%s`", futures[future].key
                    )
                    failed.append(futures[future])
    except KeyboardInterrupt:
        stop.set()
        raise
    finally:
        # Waits for aborted transfers to clean up their temporary files
        executor.shutdown(wait=True, cancel_futures=True)

    progress.update(overall, description="[green]Download")
    return failed
