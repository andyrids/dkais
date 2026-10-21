"""__init__ for aisdma."""

import contextlib
from importlib import metadata

__all__: list[str] = []

with contextlib.suppress(metadata.PackageNotFoundError):
    __version__: str = metadata.version("aisdma")
