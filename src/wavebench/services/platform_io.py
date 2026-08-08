"""Platform-aware private-directory and atomic JSON helpers."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any

from wavebench.errors import ConfigError


def ensure_private_directory(path: str | Path) -> Path:
    """Create and validate a directory used for WaveBench state."""

    directory = Path(path).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.is_dir():
            raise OSError("path is not a directory")
        if os.name != "nt":
            os.chmod(directory, 0o700)
        else:
            _validate_windows_state_path(directory)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"unable to prepare private state directory: {directory}: {exc}") from exc
    return directory


def ensure_private_file(path: str | Path) -> None:
    """Apply the POSIX mode contract; Windows uses inherited ACLs."""

    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise ConfigError(f"unable to restrict state file permissions: {path}: {exc}") from exc


def atomic_write_json(path: str | Path, value: dict[str, Any]) -> None:
    """Write one JSON object atomically and durably enough for its platform."""

    target = Path(path)
    ensure_private_directory(target.parent)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    )
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(temporary, flags, 0o600)
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            _flush_file(handle.fileno())
        _replace_file(temporary, target)
        ensure_private_file(target)
        if os.name != "nt":
            _fsync_directory(target.parent)
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigError(f"unable to atomically write state file: {target}: {exc}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _flush_file(descriptor: int) -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        from ctypes import wintypes

        kernel32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
    except (AttributeError, OSError):
        # os.fsync above is the available durability primitive in constrained
        # Python environments; the replace still remains atomic.
        return
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    if not kernel32.FlushFileBuffers(handle):
        error = ctypes.get_last_error()
        raise OSError(error, "FlushFileBuffers failed")


def _replace_file(source: Path, target: Path) -> None:
    if os.name != "nt":
        os.replace(source, target)
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        kernel32.MoveFileExW.restype = ctypes.c_int
        flags = 0x00000001 | 0x00000008  # REPLACE_EXISTING | WRITE_THROUGH
        last_error = 0
        for delay in (0.0, 0.05, 0.15):
            if delay:
                time.sleep(delay)
            if kernel32.MoveFileExW(str(source), str(target), flags):
                return
            last_error = ctypes.get_last_error()
        raise OSError(last_error, "MoveFileExW failed")
    except AttributeError:
        os.replace(source, target)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_windows_state_path(path: Path) -> None:
    """Reject lock state locations whose cross-process semantics are unknown."""

    if os.name != "nt":
        return
    raw = str(path)
    if raw.startswith("\\\\"):
        raise ConfigError("Windows lock state must be on a local filesystem")


__all__ = [
    "atomic_write_json",
    "ensure_private_directory",
    "ensure_private_file",
]
