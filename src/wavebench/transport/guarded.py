"""Defensive transport boundary and native I/O counters.

The wrapper deliberately does not inspect SCPI text.  Service-level
``OperationSpec`` checks decide whether an operation is allowed; this layer
only provides a final write boundary and records what reached the concrete
transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from wavebench.errors import AccessDeniedError
from wavebench.services.access_policy import AccessMode, normalize_access_mode

from .base import InstrumentTransport


AUDIT_SCHEMA = "wavebench.instrument_io.v1"


@dataclass
class AuditCounters:
    """Thread-safe mutable counters are kept outside the transport protocol."""

    query_calls: int = 0
    binary_query_calls: int = 0
    blocked_query_calls: int = 0
    blocked_binary_query_calls: int = 0
    write_requests: int = 0
    write_transmitted: int = 0
    write_completed: int = 0
    binary_write_requests: int = 0
    binary_write_transmitted: int = 0
    binary_write_completed: int = 0
    blocked_write_requests: int = 0
    blocked_binary_write_requests: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "query_calls": self.query_calls,
            "binary_query_calls": self.binary_query_calls,
            "blocked_query_calls": self.blocked_query_calls,
            "blocked_binary_query_calls": self.blocked_binary_query_calls,
            "write_requests": self.write_requests,
            "write_transmitted": self.write_transmitted,
            "write_completed": self.write_completed,
            "binary_write_requests": self.binary_write_requests,
            "binary_write_transmitted": self.binary_write_transmitted,
            "binary_write_completed": self.binary_write_completed,
            "blocked_write_requests": self.blocked_write_requests,
            "blocked_binary_write_requests": self.blocked_binary_write_requests,
        }


@dataclass
class GuardedAuditedTransport:
    """Wrap one concrete transport without changing its driver-facing API."""

    inner: InstrumentTransport
    access: AccessMode = "read_write"
    counters: AuditCounters = field(default_factory=AuditCounters)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.access = normalize_access_mode(self.access, "access")

    @property
    def resource(self) -> str:
        return str(getattr(self.inner, "resource", ""))

    def record_event(self, direction: str, text: str) -> None:
        self.inner.record_event(direction, text)

    def write(self, command: str) -> None:
        with self._lock:
            self.counters.write_requests += 1
            if self.access != "read_write":
                self.counters.blocked_write_requests += 1
                raise self._denied("write")
            self.counters.write_transmitted += 1
        try:
            self.inner.write(command)
        except Exception:
            raise
        else:
            with self._lock:
                self.counters.write_completed += 1

    def write_bytes(self, command: bytes) -> None:
        with self._lock:
            self.counters.binary_write_requests += 1
            if self.access != "read_write":
                self.counters.blocked_binary_write_requests += 1
                raise self._denied("write_bytes")
            self.counters.binary_write_transmitted += 1
        try:
            self.inner.write_bytes(command)
        except Exception:
            raise
        else:
            with self._lock:
                self.counters.binary_write_completed += 1

    def query(self, command: str) -> str:
        self._count_query(binary=False)
        return self.inner.query(command)

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
    ) -> list[float]:
        self._count_query(binary=False)
        return self.inner.query_float_list(command, timeout_ms=timeout_ms)

    def query_bin_block(self, command: str) -> bytes:
        self._count_query(binary=True)
        return self.inner.query_bin_block(command)

    def query_opc(self) -> str:
        self._count_query(binary=False)
        return self.inner.query_opc()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()

    def audit_snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = self.counters.as_dict()
        counters["instrument_mutation_writes"] = (
            counters["write_transmitted"] + counters["binary_write_transmitted"]
        )
        counters["instrument_mutation_writes_completed"] = (
            counters["write_completed"] + counters["binary_write_completed"]
        )
        return {
            "schema": AUDIT_SCHEMA,
            "access": self.access,
            "counters": counters,
        }

    def _count_query(self, *, binary: bool) -> None:
        with self._lock:
            if self.access == "disabled":
                key = "blocked_binary_query_calls" if binary else "blocked_query_calls"
                setattr(self.counters, key, getattr(self.counters, key) + 1)
                raise self._denied("query_bin_block" if binary else "query")
            key = "binary_query_calls" if binary else "query_calls"
            setattr(self.counters, key, getattr(self.counters, key) + 1)

    def _denied(self, operation: str) -> AccessDeniedError:
        return AccessDeniedError(
            f"transport {operation} is blocked by access policy {self.access!r}"
        )


__all__ = ["AUDIT_SCHEMA", "AuditCounters", "GuardedAuditedTransport"]
