"""Final access and session-health gate around one concrete transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wavebench.errors import AccessDeniedError, SessionHealthError, TransportIOError
from wavebench.services.access_policy import AccessMode, normalize_access_mode
from wavebench.services.resource_lease import ResourceLease

from .base import InstrumentTransport
from .contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)
from .session import InstrumentSessionState, SessionAuthorization, SessionHealth


AUDIT_SCHEMA = "wavebench.instrument_io.v1"
@dataclass
class AuditCounters:
    """Counters are mutated only while the session transaction lock is held."""

    query_calls: int = 0
    binary_query_calls: int = 0
    blocked_query_calls: int = 0
    blocked_binary_query_calls: int = 0
    write_requests: int = 0
    write_attempts: int = 0
    write_transmitted: int = 0
    write_completed: int = 0
    write_outcome_unknown: int = 0
    binary_write_requests: int = 0
    binary_write_attempts: int = 0
    binary_write_transmitted: int = 0
    binary_write_completed: int = 0
    binary_write_outcome_unknown: int = 0
    blocked_write_requests: int = 0
    blocked_binary_write_requests: int = 0
    blocked_session_io: int = 0
    session_health_transitions: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "query_calls": self.query_calls,
            "binary_query_calls": self.binary_query_calls,
            "blocked_query_calls": self.blocked_query_calls,
            "blocked_binary_query_calls": self.blocked_binary_query_calls,
            "write_requests": self.write_requests,
            "write_attempts": self.write_attempts,
            "write_transmitted": self.write_transmitted,
            "write_completed": self.write_completed,
            "write_outcome_unknown": self.write_outcome_unknown,
            "binary_write_requests": self.binary_write_requests,
            "binary_write_attempts": self.binary_write_attempts,
            "binary_write_transmitted": self.binary_write_transmitted,
            "binary_write_completed": self.binary_write_completed,
            "binary_write_outcome_unknown": self.binary_write_outcome_unknown,
            "blocked_write_requests": self.blocked_write_requests,
            "blocked_binary_write_requests": self.blocked_binary_write_requests,
            "blocked_session_io": self.blocked_session_io,
            "session_health_transitions": self.session_health_transitions,
        }


@dataclass
class GuardedAuditedTransport:
    """Wrap one concrete transport and serialize its complete I/O transaction."""

    inner: InstrumentTransport
    access: AccessMode = "read_write"
    counters: AuditCounters = field(default_factory=AuditCounters)
    lease: ResourceLease | None = None
    release_lease_on_close: bool = True
    session_state: InstrumentSessionState = field(default_factory=InstrumentSessionState)
    _closed: bool = field(default=False, init=False, repr=False)

    def __setattr__(self, name: str, value: object) -> None:
        # A guard and its session state form one connection epoch.  Rebinding
        # the state after construction would let callers replace a latched
        # poisoned epoch with a fresh healthy object.
        if name == "session_state" and hasattr(self, "session_state"):
            raise AttributeError("session_state is immutable after transport construction")
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        self.access = normalize_access_mode(self.access, "access")

    @property
    def resource(self) -> str:
        return str(getattr(self.inner, "resource", ""))

    def record_event(self, direction: str, text: str) -> None:
        # This is local audit logging, not an instrument exchange.
        self.inner.record_event(direction, text)

    def write(self, command: str) -> None:
        with self.session_state.transaction_lock:
            self.counters.write_requests += 1
            self._check_access("write", write=True)
            authorization = self._gate("write")
            self.counters.write_attempts += 1
            try:
                self.inner.write(command)
            except Exception as exc:
                self.counters.write_outcome_unknown += 1
                self._transition_after_failure(exc, authorization)
                raise
            self.counters.write_transmitted += 1
            self.counters.write_completed += 1
            self._record_success(authorization, "write")

    def write_bytes(self, command: bytes) -> None:
        with self.session_state.transaction_lock:
            self.counters.binary_write_requests += 1
            self._check_access("write_bytes", write=True)
            authorization = self._gate("write_bytes")
            self.counters.binary_write_attempts += 1
            try:
                self.inner.write_bytes(command)
            except Exception as exc:
                self.counters.binary_write_outcome_unknown += 1
                self._transition_after_failure(exc, authorization)
                raise
            self.counters.binary_write_transmitted += 1
            self.counters.binary_write_completed += 1
            self._record_success(authorization, "write_bytes")

    def query(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str:
        with self.session_state.transaction_lock:
            self._check_access("query")
            self.counters.query_calls += 1
            authorization = self._gate("query")
            try:
                result = self.inner.query(command, replay=replay)
            except Exception as exc:
                self._transition_after_failure(exc, authorization)
                raise
            self._record_success(authorization, "query")
            return result

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]:
        with self.session_state.transaction_lock:
            self._check_access("query_float_list")
            self.counters.query_calls += 1
            authorization = self._gate("query_float_list")
            try:
                result = self.inner.query_float_list(
                    command,
                    timeout_ms=timeout_ms,
                    replay=replay,
                )
            except Exception as exc:
                self._transition_after_failure(exc, authorization)
                raise
            self._record_success(authorization, "query_float_list")
            return result

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        with self.session_state.transaction_lock:
            self._check_access("query_bin_block")
            self.counters.binary_query_calls += 1
            authorization = self._gate("query_bin_block")
            try:
                result = self.inner.query_bin_block(command, replay=replay)
            except Exception as exc:
                self._transition_after_failure(exc, authorization)
                raise
            self._record_success(authorization, "query_bin_block")
            return result

    def query_opc(
        self,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> str:
        with self.session_state.transaction_lock:
            self._check_access("query_opc")
            self.counters.query_calls += 1
            authorization = self._gate("query_opc")
            try:
                result = self.inner.query_opc(replay=replay)
            except Exception as exc:
                self._transition_after_failure(exc, authorization)
                raise
            self._record_success(authorization, "query_opc")
            return result

    def close(self) -> None:
        release_lease: ResourceLease | None = None
        try:
            with self.session_state.transaction_lock:
                if self._closed:
                    return
                self._closed = True
                release_lease = self.lease if self.release_lease_on_close else None
                # Invalidate the epoch before calling backend close.  A close
                # failure must not leave the old transport usable.
                self.session_state.close()
                close = getattr(self.inner, "close", None)
                if callable(close):
                    close()
        finally:
            # This is deliberately outside the session-lock block: a backend
            # close exception must not skip lease release.
            if release_lease is not None:
                release_lease.release()

    def audit_snapshot(self) -> dict[str, Any]:
        with self.session_state.transaction_lock:
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
                "session": self.session_state.snapshot(),
            }

    def _check_access(self, operation: str, *, write: bool = False) -> None:
        if write and self.access != "read_write":
            if operation == "write":
                self.counters.blocked_write_requests += 1
            else:
                self.counters.blocked_binary_write_requests += 1
            raise self._denied(operation)
        if not write and self.access == "disabled":
            if operation == "query_bin_block":
                self.counters.blocked_binary_query_calls += 1
            else:
                self.counters.blocked_query_calls += 1
            raise self._denied(operation)

    def _gate(self, io_kind: str) -> SessionAuthorization | None:
        if self._closed or self.session_state.health in {
            SessionHealth.POISONED,
            SessionHealth.CLOSED,
        }:
            self.counters.blocked_session_io += 1
            raise self._session_denied(io_kind)
        try:
            authorization = self.session_state._consume_authorization(io_kind)
        except ValueError as exc:
            self.counters.blocked_session_io += 1
            raise self._session_denied(io_kind, reason=str(exc)) from exc
        if self.session_state.health is SessionHealth.UNCERTAIN and authorization is None:
            self.counters.blocked_session_io += 1
            raise self._session_denied(io_kind)
        return authorization

    def _record_success(
        self,
        authorization: SessionAuthorization | None,
        io_kind: str,
    ) -> None:
        if authorization is not None:
            self.session_state._record_authorized_success(authorization, io_kind)

    def _transition_after_failure(
        self,
        exc: BaseException,
        authorization: SessionAuthorization | None,
    ) -> None:
        current = self.session_state.health
        if isinstance(exc, TransportIOError):
            if (
                exc.attempts == 0
                and exc.phase is TransportPhase.BEFORE_SEND
                and exc.command_transmission is CommandTransmission.NOT_SENT
                and exc.response_progress is ResponseProgress.NONE
                and exc.synchronization is Synchronization.PROVEN
            ):
                return
            if exc.synchronization in {Synchronization.UNPROVEN, Synchronization.LOST}:
                target = SessionHealth.POISONED
                reason = "transport_synchronization_unproven"
            else:
                target = SessionHealth.UNCERTAIN
                reason = "transport_result_unknown"
        else:
            target = SessionHealth.POISONED
            reason = "unstructured_transport_failure"
        if authorization is not None and authorization.purpose.value == "recovery":
            target = SessionHealth.POISONED
            reason = "authorized_recovery_failed"
        self.session_state.degrade(target, reason=reason)
        if self.session_state.health is not current:
            self.counters.session_health_transitions += 1

    def _denied(self, operation: str) -> AccessDeniedError:
        return AccessDeniedError(
            f"transport {operation} is blocked by access policy {self.access!r}"
        )

    def _session_denied(self, io_kind: str, *, reason: str | None = None) -> SessionHealthError:
        message = (
            f"transport {io_kind} is blocked because session health is "
            f"{self.session_state.health.value!r}"
        )
        # Do not copy authorization/backend exception text into a stable error
        # message; it may contain commands, responses, or private paths.
        return SessionHealthError(
            message,
            health=self.session_state.health.value,
            io_kind=io_kind,
            epoch_id=self.session_state.epoch_id,
        )


__all__ = ["AUDIT_SCHEMA", "AuditCounters", "GuardedAuditedTransport"]
