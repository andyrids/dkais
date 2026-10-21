"""Main entry point for aisdma."""

import logging

logger = logging.getLogger(__package__)


def main() -> int:
    """Provide a unified `aisdma` CLI entrypoint.

    Returns:
        The process exit code.
    """
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
