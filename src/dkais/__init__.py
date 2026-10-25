"""__init__ for dkais."""

from importlib import metadata

__all__: list[str] = []

# Mypy-recommended method to mitigate `[no-redef]` issues
__version__: str
try:
    __version__ = metadata.version("dkais")
except metadata.PackageNotFoundError:
    __version__ = "0.0.0+unknown"
