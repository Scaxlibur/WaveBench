"""WaveBench package."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib

__all__ = ["__version__"]


def _version_from_pyproject() -> str | None:
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.exists():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data["project"]["version"])
        except Exception:
            return None
    return None


try:
    __version__ = _version_from_pyproject() or version("wavebench")
except PackageNotFoundError:  # pragma: no cover - editable-tree fallback
    __version__ = "0.4.1.dev0"
