"""Custom exceptions for `dkais`."""


class Error(Exception):
    """Base-class for all exceptions raised by `dkais`."""


class DataDirNotSetError(Error):
    """Raised when no data directory is given by flag or environment."""


class CSVDialectError(Error):
    """Raised when a CSV file's dialect cannot be identified."""


class InterruptedTransferError(Error):
    """Raised from a transfer callback to abort an in-flight download."""
