"""Conservative local resource leases for instrument sessions.

The MVP targets Linux/WSL on a local filesystem.  It uses advisory
``flock`` locks and keeps holder metadata in the lock file itself.  A stale
metadata record may be cleared only after a non-blocking lock proves that no
process currently holds the file; the lock file is never unlinked.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
from typing import Any, Iterable, Iterator, Literal

from wavebench.errors import ConfigError, ResourceBusyError

try:  # pragma: no cover - the unsupported branch is exercised by a targeted test
    import fcntl
except ImportError:  # pragma: no cover - Windows import path
    fcntl = None


LEASE_SCHEMA = "wavebench.resource_lease.v1"
LeaseMode = Literal["shared", "exclusive"]
_LEASE_MODES = frozenset({"shared", "exclusive"})


def normalize_resource(resource: object) -> str:
    """Normalize a VISA or local serial resource for lease identity."""

    if not isinstance(resource, str) or not resource.strip():
        raise ConfigError("resource lease requires a non-empty resource")
    stripped = " ".join(resource.strip().split())
    if stripped.startswith(("/", "~/")):
        return os.path.normpath(os.path.expanduser(stripped))
    return stripped.casefold()


def normalize_lock_id(lock_id: object | None) -> str:
    if lock_id is None:
        return ""
    if not isinstance(lock_id, str):
        raise ConfigError("resource lease lock_id must be a string")
    return " ".join(lock_id.strip().split())


def resource_lease_key(resource: object, lock_id: object | None = None) -> str:
    normalized_resource = normalize_resource(resource)
    normalized_lock_id = normalize_lock_id(lock_id)
    return f"{normalized_resource}\x00{normalized_lock_id}"


def resource_fingerprint(resource: object, lock_id: object | None = None) -> str:
    return hashlib.sha256(resource_lease_key(resource, lock_id).encode("utf-8")).hexdigest()


def default_lease_directory() -> Path:
    configured = os.environ.get("WAVEBENCH_LEASE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "wavebench" / "locks"


@dataclass
class ResourceLease:
    """One acquired or acquire-able local resource lease."""

    resource: str
    directory: Path = field(default_factory=default_lease_directory)
    lock_id: str = ""
    mode: LeaseMode = "exclusive"
    operation: str | None = None
    _fd: int | None = field(default=None, init=False, repr=False)
    _acquired: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.resource = normalize_resource(self.resource)
        self.directory = Path(self.directory).expanduser()
        self.lock_id = normalize_lock_id(self.lock_id)
        if self.mode not in _LEASE_MODES:
            choices = ", ".join(sorted(_LEASE_MODES))
            raise ConfigError(f"resource lease mode must be one of: {choices}")

    @property
    def fingerprint(self) -> str:
        return resource_fingerprint(self.resource, self.lock_id)

    @property
    def lock_path(self) -> Path:
        return self.directory / f"{self.fingerprint}.lock"

    @property
    def acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> "ResourceLease":
        if self._acquired:
            return self
        if fcntl is None:
            raise ConfigError("resource leases require POSIX flock on Linux/WSL")
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise ConfigError(f"unable to create resource lease file: {exc}") from exc

        flags = fcntl.LOCK_SH if self.mode == "shared" else fcntl.LOCK_EX
        try:
            fcntl.flock(fd, flags | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ResourceBusyError(
                    f"resource lease is busy (fingerprint {self.fingerprint[:16]})"
                ) from exc
            raise ConfigError(f"unable to acquire resource lease: {exc}") from exc

        self._fd = fd
        self._acquired = True
        try:
            self._write_metadata(status="held")
        except Exception:
            self.release()
            raise
        return self

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        try:
            try:
                self._write_metadata(status="released")
            except Exception:
                pass
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._fd = None
            self._acquired = False

    def __enter__(self) -> "ResourceLease":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def holder_metadata(self) -> dict[str, Any] | None:
        """Read lock metadata without changing the lock state."""

        try:
            payload = self.lock_path.read_text(encoding="utf-8")
            value = json.loads(payload)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return dict(value) if isinstance(value, dict) else None

    def status(self) -> dict[str, Any]:
        """Return whether the lock is held, without stealing or unlinking it."""

        if fcntl is None:
            return {
                "schema": LEASE_SCHEMA,
                "available": False,
                "reason": "posix_flock_unavailable",
                "fingerprint": self.fingerprint,
                "lock_path": str(self.lock_path),
            }
        if not self.lock_path.exists():
            return {
                "schema": LEASE_SCHEMA,
                "available": True,
                "held": False,
                "fingerprint": self.fingerprint,
                "lock_path": str(self.lock_path),
                "metadata": None,
            }
        try:
            fd = os.open(self.lock_path, os.O_RDWR)
        except OSError as exc:
            raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
        held = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    held = True
                else:
                    raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
            metadata = _read_metadata_fd(fd)
            if not held:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return {
            "schema": LEASE_SCHEMA,
            "available": True,
            "held": held,
            "fingerprint": self.fingerprint,
            "lock_path": str(self.lock_path),
            "metadata": metadata,
        }

    def clear_stale_metadata(self) -> bool:
        """Clear metadata only after proving the lock is currently free."""

        if fcntl is None or not self.lock_path.exists():
            return False
        try:
            fd = os.open(self.lock_path, os.O_RDWR)
        except OSError as exc:
            raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
        try:
            locked = False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    return False
                raise ConfigError(f"unable to clear resource lease metadata: {exc}") from exc
            locked = True
            _write_json_fd(
                fd,
                {
                    "schema": LEASE_SCHEMA,
                    "status": "stale_metadata_cleared",
                    "resource_fingerprint": self.fingerprint,
                    "cleared_at": _utc_now(),
                },
            )
            return True
        finally:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

    def _metadata(self, *, status: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": LEASE_SCHEMA,
            "status": status,
            "resource_fingerprint": self.fingerprint,
            "lock_id": self.lock_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "mode": self.mode,
        }
        if self.operation:
            payload["operation"] = self.operation
        timestamp_key = "acquired_at" if status == "held" else "released_at"
        payload[timestamp_key] = _utc_now()
        return payload

    def _write_metadata(self, *, status: str) -> None:
        if self._fd is None:
            return
        _write_json_fd(self._fd, self._metadata(status=status))


@dataclass(frozen=True)
class ResourceLeaseManager:
    """Acquire one or more leases in deterministic order."""

    directory: Path = field(default_factory=default_lease_directory)

    def acquire(
        self,
        resource: str,
        *,
        lock_id: str = "",
        mode: LeaseMode = "exclusive",
        operation: str | None = None,
    ) -> ResourceLease:
        return ResourceLease(
            resource=resource,
            directory=self.directory,
            lock_id=lock_id,
            mode=mode,
            operation=operation,
        ).acquire()

    def acquire_many(
        self,
        resources: Iterable[str],
        *,
        mode: LeaseMode = "exclusive",
        operation: str | None = None,
    ) -> list[ResourceLease]:
        leases = [
            ResourceLease(
                resource=resource,
                directory=self.directory,
                mode=mode,
                operation=operation,
            )
            for resource in resources
        ]
        leases.sort(key=lambda lease: (lease.fingerprint, lease.resource))
        acquired: list[ResourceLease] = []
        try:
            for lease in leases:
                if acquired and lease.fingerprint == acquired[-1].fingerprint:
                    continue
                acquired.append(lease.acquire())
            return acquired
        except Exception:
            for lease in reversed(acquired):
                lease.release()
            raise

    @contextmanager
    def hold_many(
        self,
        resources: Iterable[str],
        *,
        mode: LeaseMode = "exclusive",
        operation: str | None = None,
    ) -> Iterator[list[ResourceLease]]:
        leases = self.acquire_many(resources, mode=mode, operation=operation)
        try:
            yield leases
        finally:
            for lease in reversed(leases):
                lease.release()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_metadata_fd(fd: int) -> dict[str, Any] | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 64 * 1024)
        value = json.loads(raw.decode("utf-8")) if raw else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, dict) else None


def _write_json_fd(fd: int, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(encoded)
    while view:
        written = os.write(fd, view)
        view = view[written:]
    os.fsync(fd)


__all__ = [
    "LEASE_SCHEMA",
    "LeaseMode",
    "ResourceLease",
    "ResourceLeaseManager",
    "default_lease_directory",
    "normalize_lock_id",
    "normalize_resource",
    "resource_fingerprint",
    "resource_lease_key",
]
