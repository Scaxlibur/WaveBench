"""Conservative local resource leases for instrument sessions.

The MVP targets Linux/WSL on a local filesystem.  Advisory ``flock`` is the
only ownership fact.  A sidecar JSON file is diagnostic metadata only; stale
metadata may be removed after a non-blocking lock proves that the lock is
free, while the ``.lock`` inode is never unlinked.
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
import re
import secrets
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
_TCPIP_PREFIX = re.compile(r"^tcpip(?P<board>\d*)$", re.IGNORECASE)


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
    if stripped.startswith(("/", "~/")):
        return os.path.realpath(os.path.expanduser(stripped))
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
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.access(runtime, os.W_OK):
        return Path(runtime) / "wavebench" / "resource-leases-v1"
    return Path.home() / ".cache" / "wavebench" / "resource-leases-v1"


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
        if fcntl is None:
            raise ConfigError("resource leases require POSIX flock on Linux/WSL")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.lock_path, flags, 0o600)
            os.fchmod(fd, 0o600)
        except OSError as exc:
            raise ConfigError(f"unable to create resource lease file: {exc}") from exc

        lock_flags = fcntl.LOCK_SH if self.mode == "shared" else fcntl.LOCK_EX
        try:
            fcntl.flock(fd, lock_flags | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ResourceBusyError(
                    f"resource lease is busy (fingerprint {self.fingerprint[:16]})"
                ) from exc
            raise ConfigError(f"unable to acquire resource lease: {exc}") from exc

        self._fd = fd
        self._acquired = True
        self._lease_id = secrets.token_hex(16)
        try:
            self._write_metadata()
        except Exception:
            self.release()
            raise
        return self

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        try:
            self._remove_own_metadata()
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

        if fcntl is None:
            return {
                "schema": LEASE_SCHEMA,
                "available": False,
                "reason": "posix_flock_unavailable",
                "fingerprint": self.fingerprint,
                "lock_path": str(self.lock_path),
                "metadata_path": str(self.metadata_path),
            }
        if not self.lock_path.exists():
            return self._status_payload(held=False, stale_metadata=False, metadata=None)
        try:
            fd = os.open(self.lock_path, os.O_RDWR)
        except OSError as exc:
            raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
        held = False
        locked_here = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_here = True
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    held = True
                else:
                    raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
            metadata = self.holder_metadata()
            stale = not held and metadata is not None
        finally:
            if locked_here:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
        return self._status_payload(held=held, stale_metadata=stale, metadata=metadata)

    def clear_stale_metadata(self) -> bool:
        """Remove only sidecar metadata after proving the lock is free."""

        if fcntl is None or not self.lock_path.exists() or not self.metadata_path.exists():
            return False
        try:
            fd = os.open(self.lock_path, os.O_RDWR)
        except OSError as exc:
            raise ConfigError(f"unable to inspect resource lease: {exc}") from exc
        locked_here = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_here = True
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    return False
                raise ConfigError(f"unable to clear resource lease metadata: {exc}") from exc
            try:
                self.metadata_path.unlink()
            except FileNotFoundError:
                return False
            return True
        finally:
            if locked_here:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

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
        if self._fd is None:
            return
        _atomic_write_json(self.metadata_path, self._metadata())

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


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(temporary, flags, 0o600)
    try:
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    temporary.replace(path)
    os.chmod(path, 0o600)


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
