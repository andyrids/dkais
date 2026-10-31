"""Custom exceptions for `dkais`."""


class Error(Exception):
    """Base-class for all exceptions raised by `dkais`."""


class BucketError(Error):
    """Raised when the S3 bucket cannot be listed, e.g. when offline."""


class CSVDialectError(Error):
    """Raised when a CSV file's dialect cannot be identified."""


class InterruptedTransferError(Error):
    """Raised from a transfer callback to abort an in-flight download."""
