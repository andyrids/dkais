"""__init__ for dkais."""

import contextlib
from importlib import metadata

__all__: list[str] = []

with contextlib.suppress(metadata.PackageNotFoundError):
    __version__: str = metadata.version("dkais")
