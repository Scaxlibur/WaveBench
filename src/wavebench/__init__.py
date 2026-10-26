"""WaveBench package."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]


try:
    __version__ = version("wavebench")
except PackageNotFoundError:
    __version__ = "0+unknown"
