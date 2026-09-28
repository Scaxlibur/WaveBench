"""Small, fail-closed cross-platform file-lock adapter.

WaveBench uses a kernel-backed file lock as the ownership fact for both
instrument resource leases and plugin lifecycle transactions.  ``portalocker``
provides the POSIX ``flock`` implementation and, with its ``win32`` extra, the
Win32 ``LockFileEx`` implementation needed for shared locks on Windows.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
from importlib import import_module
import os
from pathlib import Path
from typing import BinaryIO, Literal

from .platform_io import ensure_private_file


LockMode = Literal["shared", "exclusive"]


class FileLockError(Exception):
    """Base class for expected adapter failures."""


class FileLockBusy(FileLockError):
    """The requested non-blocking lock is held by another process."""


class FileLockUnavailable(FileLockError):
    """The platform locking backend is not usable."""


@dataclass(frozen=True)
class LockBackendInfo:
    name: str
    available: bool
    reason: str | None = None


def backend_info() -> LockBackendInfo:
    """Describe the backend without creating or touching a lock file."""

    try:
        import_module("portalocker")
    except ImportError as exc:
        return LockBackendInfo(
            name="portalocker",
            available=False,
            reason=f"portalocker_unavailable: {exc}",
        )

    if os.name == "posix":
        return LockBackendInfo(name="portalocker.posix", available=True)
    if os.name != "nt":
        return LockBackendInfo(
            name=f"portalocker.{os.name}",
            available=False,
            reason="unsupported operating system",
        )

    try:
        from portalocker.portalocker import Win32Locker

        # Constructing the locker verifies that the win32 extra (pywin32) is
        # present.  The instance used by a FileLock is kept for its lifetime.
        Win32Locker()
    except (ImportError, OSError, AttributeError) as exc:
        return LockBackendInfo(
            name="portalocker.win32",
            available=False,
            reason=f"win32_backend_unavailable: {exc}",
        )
    return LockBackendInfo(name="portalocker.win32", available=True)


def _busy_exception(exc: BaseException) -> bool:
    """Recognize the platform's non-blocking contention errors."""

    try:
        import portalocker

        if isinstance(exc, portalocker.exceptions.AlreadyLocked):
            return True
    except (ImportError, AttributeError):
        pass

    if getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN}:
        return True
    # ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION.
    if getattr(exc, "winerror", None) in {32, 33}:
        return True
    text = str(exc).casefold()
    return "sharing violation" in text or "lock violation" in text


class FileLock:
    """One non-blocking shared or exclusive lock on a stable file path."""

    def __init__(self, path: str | Path, *, mode: LockMode = "exclusive") -> None:
        if mode not in {"shared", "exclusive"}:
            raise ValueError("file lock mode must be shared or exclusive")
        self.path = Path(path)
        self.mode = mode
        self._handle: BinaryIO | None = None
        self._locker: object | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> "FileLock":
        if self._handle is not None:
            return self
        info = backend_info()
        if not info.available:
            raise FileLockUnavailable(info.reason or f"{info.name} unavailable")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            try:
                ensure_private_file(self.path)
                handle = os.fdopen(descriptor, "r+b", buffering=0)
            except Exception:
                os.close(descriptor)
                raise
        except (OSError, ValueError) as exc:
            raise FileLockError(f"unable to open lock file {self.path}: {exc}") from exc

        try:
            import portalocker

            lock_flags = portalocker.LockFlags.NON_BLOCKING
            lock_flags |= (
                portalocker.LockFlags.SHARED
                if self.mode == "shared"
                else portalocker.LockFlags.EXCLUSIVE
            )
            if os.name == "nt":
                from portalocker.portalocker import Win32Locker

                locker = Win32Locker()
                locker.lock(handle, lock_flags)
                self._locker = locker
            else:
                portalocker.lock(handle, lock_flags)
                self._locker = portalocker
        except Exception as exc:
            try:
                handle.close()
            except OSError:
                pass
            if _busy_exception(exc):
                raise FileLockBusy(f"lock is busy: {self.path}") from exc
            if isinstance(exc, ImportError):
                raise FileLockUnavailable(str(exc)) from exc
            raise FileLockError(f"unable to acquire lock {self.path}: {exc}") from exc

        self._handle = handle
        return self

    def release(self) -> None:
        handle = self._handle
        locker = self._locker
        if handle is None:
            return
        self._handle = None
        self._locker = None
        try:
            if locker is not None:
                if os.name == "nt":
                    locker.unlock(handle)  # type: ignore[attr-defined]
                else:
                    locker.unlock(handle)  # type: ignore[attr-defined]
        except Exception:
            # The descriptor close below still releases a kernel lock.  Release
            # is best-effort so cleanup paths cannot mask the original error.
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def probe_lock(path: str | Path) -> bool:
    """Return whether an existing lock file can be exclusively probed."""

    candidate = Path(path)
    if not candidate.exists():
        return True
    lock = FileLock(candidate, mode="exclusive")
    try:
        lock.acquire()
    except FileLockBusy:
        return False
    finally:
        lock.release()
    return True


__all__ = [
    "FileLock",
    "FileLockBusy",
    "FileLockError",
    "FileLockUnavailable",
    "LockBackendInfo",
    "LockMode",
    "backend_info",
    "probe_lock",
]
