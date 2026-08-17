"""Conservative local resource leases for instrument sessions.

The kernel-backed lock is the only ownership fact.  A sidecar JSON file is
diagnostic metadata only; stale metadata may be removed after a non-blocking
lock proves that the lock is free, while the ``.lock`` inode is never unlinked.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
import posixpath
from pathlib import Path
import re
import socket
from typing import Any, Iterable, Iterator, Literal

from wavebench.errors import ConfigError, ResourceBusyError

from .file_lock import (
    FileLock,
    FileLockBusy,
    FileLockError,
    FileLockUnavailable,
    backend_info,
)
from .platform_io import atomic_write_json, ensure_private_directory


LEASE_SCHEMA = "wavebench.resource_lease.v1"
LeaseMode = Literal["shared", "exclusive"]
_LEASE_MODES = frozenset({"shared", "exclusive"})
_TCPIP_PREFIX = re.compile(r"^tcpip(?P<board>\d*)$", re.IGNORECASE)
_WINDOWS_COM = re.compile(r"^(?:\\\\\.\\)?com(?P<number>\d+)$", re.IGNORECASE)
_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:[\\/]")


def normalize_resource(resource: object) -> str:
    """Normalize a VISA or local serial resource for lease identity.

    VISA resource spelling is case-insensitive for lease purposes.  Serial
    paths retain case and are normalized with ``realpath`` so symlink aliases
    such as ``/dev/serial/by-id`` share one lock when they resolve to the same
    device.
    """

    if not isinstance(resource, str) or not resource.strip():
        raise ConfigError("resource lease requires a non-empty resource")
    stripped = " ".join(resource.strip().split())
    com_match = _WINDOWS_COM.fullmatch(stripped)
    if com_match is not None:
        number = int(com_match.group("number"))
        if number > 0:
            return f"com{number}"
    if stripped.startswith("/"):
        # Keep POSIX device paths stable even when a Windows process sees a
        # leading slash as a drive-relative path (for example ``/dev/ttyUSB0``).
        return posixpath.realpath(stripped)
    if stripped.startswith("~/"):
        return os.path.realpath(os.path.expanduser(stripped))
    if stripped.startswith("\\\\") or _WINDOWS_DRIVE.match(stripped):
        return ntpath.normcase(ntpath.normpath(stripped))
    parts = stripped.split("::")
    if len(parts) >= 2:
        prefix = _TCPIP_PREFIX.fullmatch(parts[0])
        if prefix is not None:
            # TCPIP and TCPIP0 are the same default VISA board.  Other board
            # numbers remain distinct; SOCKET and INSTR are intentionally not
            # collapsed because they can be different server endpoints.
            board = prefix.group("board")
            normalized_prefix = "tcpip" if board in {"", "0"} else f"tcpip{int(board)}"
            return "::".join([normalized_prefix, *(part.casefold() for part in parts[1:])])
    return stripped.casefold()


def normalize_lock_id(lock_id: object | None) -> str:
    if lock_id is None:
        return ""
    if not isinstance(lock_id, str):
        raise ConfigError("resource lease lock_id must be a string")
    normalized = " ".join(lock_id.strip().split())
    if any(ord(character) < 32 for character in normalized):
        raise ConfigError("resource lease lock_id must not contain control characters")
    return normalized


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
    if _is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "WaveBench" / "resource-leases-v1"
        try:
            return Path.home() / "AppData" / "Local" / "WaveBench" / "resource-leases-v1"
        except RuntimeError as exc:
            raise ConfigError("unable to determine the Windows user data directory") from exc
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.access(runtime, os.W_OK):
        return Path(runtime) / "wavebench" / "resource-leases-v1"
    return Path.home() / ".cache" / "wavebench" / "resource-leases-v1"


def _is_windows() -> bool:
    return os.name == "nt"


@dataclass
class ResourceLease:
    """One acquired or acquire-able local resource lease."""

    resource: str
    directory: Path = field(default_factory=default_lease_directory)
    lock_id: str = ""
    mode: LeaseMode = "exclusive"
    operation: str | None = None
    _lock: FileLock | None = field(default=None, init=False, repr=False)
    _acquired: bool = field(default=False, init=False, repr=False)
    _lease_id: str | None = field(default=None, init=False, repr=False)

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
    def metadata_path(self) -> Path:
        return self.directory / f"{self.fingerprint}.json"

    @property
    def acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> "ResourceLease":
        if self._acquired:
            return self
        ensure_private_directory(self.directory)
        lock = FileLock(self.lock_path, mode=self.mode)
        try:
            lock.acquire()
        except FileLockBusy as exc:
            raise ResourceBusyError(
                f"resource lease is busy (fingerprint {self.fingerprint[:16]})"
            ) from exc
        except (FileLockUnavailable, FileLockError) as exc:
            raise ConfigError(f"unable to acquire resource lease: {exc}") from exc

        self._lock = lock
        self._acquired = True
        self._lease_id = os.urandom(16).hex()
        try:
            self._write_metadata()
        except Exception:
            self.release()
            raise
        return self

    def release(self) -> None:
        lock = self._lock
        if lock is None:
            return
        try:
            self._remove_own_metadata()
        finally:
            lock.release()
            self._lock = None
            self._acquired = False
            self._lease_id = None

    def __enter__(self) -> "ResourceLease":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def holder_metadata(self) -> dict[str, Any] | None:
        """Read sidecar metadata without changing the lock state."""

        try:
            payload = self.metadata_path.read_text(encoding="utf-8")
            value = json.loads(payload)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return dict(value) if isinstance(value, dict) else None

    def status(self) -> dict[str, Any]:
        """Return lock ownership and stale-metadata state."""

        info = backend_info()
        if not info.available:
            return {
                "schema": LEASE_SCHEMA,
                "available": False,
                "reason": "file_lock_backend_unavailable",
                "backend": info.name,
                "backend_error": info.reason,
                "fingerprint": self.fingerprint,
                "lock_path": str(self.lock_path),
                "metadata_path": str(self.metadata_path),
            }
        if not self.lock_path.exists():
            return self._status_payload(held=False, stale_metadata=False, metadata=None)
        lock = FileLock(self.lock_path, mode="exclusive")
        held = False
        try:
            try:
                lock.acquire()
            except FileLockBusy:
                held = True
            except (FileLockUnavailable, FileLockError) as exc:
                raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
            metadata = self.holder_metadata()
            stale = not held and metadata is not None
        finally:
            lock.release()
        return self._status_payload(held=held, stale_metadata=stale, metadata=metadata)

    def clear_stale_metadata(self) -> bool:
        """Remove only sidecar metadata after proving the lock is free."""

        info = backend_info()
        if (
            not info.available
            or not self.lock_path.exists()
            or not self.metadata_path.exists()
        ):
            return False
        lock = FileLock(self.lock_path, mode="exclusive")
        try:
            try:
                lock.acquire()
            except FileLockBusy:
                return False
            except (FileLockUnavailable, FileLockError) as exc:
                raise ConfigError(f"unable to clear resource lease metadata: {exc}") from exc
            try:
                self.metadata_path.unlink()
            except FileNotFoundError:
                return False
            return True
        finally:
            lock.release()

    def _status_payload(
        self,
        *,
        held: bool,
        stale_metadata: bool,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "schema": LEASE_SCHEMA,
            "available": True,
            "backend": backend_info().name,
            "platform": os.name,
            "held": held,
            "stale_metadata": stale_metadata,
            "fingerprint": self.fingerprint,
            "lock_path": str(self.lock_path),
            "metadata_path": str(self.metadata_path),
            "metadata": metadata,
        }

    def _metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": LEASE_SCHEMA,
            "backend": backend_info().name,
            "platform": os.name,
            "lease_id": self._lease_id,
            "resource_fingerprint": self.fingerprint,
            "lock_id": self.lock_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "mode": self.mode,
            "acquired_at": _utc_now(),
        }
        if self.operation:
            payload["operation"] = self.operation
        return payload

    def _write_metadata(self) -> None:
        if self._lock is None:
            return
        atomic_write_json(self.metadata_path, self._metadata())

    def _remove_own_metadata(self) -> None:
        if self._lease_id is None:
            return
        metadata = self.holder_metadata()
        if metadata is None or metadata.get("lease_id") != self._lease_id:
            return
        try:
            self.metadata_path.unlink()
        except FileNotFoundError:
            pass


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
        seen: set[str] = set()
        try:
            for lease in leases:
                if lease.fingerprint in seen:
                    continue
                seen.add(lease.fingerprint)
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
