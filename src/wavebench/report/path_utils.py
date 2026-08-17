"""Safe artifact URL construction for POSIX and Windows paths."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path
from urllib.parse import quote


def artifact_url(path: Path, output_dir: Path) -> str:
    """Return a browser-safe relative URL or an explicit file URI."""

    path_text = str(path)
    output_text = str(output_dir)
    if _looks_windows_path(path_text) or _looks_windows_path(output_text):
        try:
            relative = ntpath.relpath(path_text, start=output_text)
        except ValueError:
            return _windows_file_uri(path_text)
        return _quote_url(relative.replace("\\", "/"))
    try:
        relative = os.path.relpath(path, start=output_dir)
    except ValueError:
        return _posix_file_uri(path)
    return _quote_url(relative.replace(os.sep, "/").replace("\\", "/"))


def _looks_windows_path(value: str) -> bool:
    drive, _ = ntpath.splitdrive(value)
    return bool(drive) or value.startswith("\\\\")


def _quote_url(value: str) -> str:
    return quote(value, safe="/.:@?&=+$,;~!()'_-")


def _windows_file_uri(value: str) -> str:
    normalized = ntpath.normpath(value).replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return "file://" + _quote_url(normalized)


def _posix_file_uri(path: Path) -> str:
    candidate = path if path.is_absolute() else Path.cwd() / path
    return _quote_url(candidate.resolve().as_uri())


__all__ = ["artifact_url"]
