"""Final access and session-health gate around one concrete transport."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from wavebench.errors import AccessDeniedError, SessionHealthError, TransportIOError
from wavebench.services.access_policy import AccessMode, normalize_access_mode
from wavebench.services.resource_lease import ResourceLease

from .base import InstrumentTransport
from .binary import BinaryQueryBudget
from .contracts import (
    BinaryQueryResult,
    BinaryResponseFraming,
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
    construction_latched: bool = False
    _closed: bool = field(default=False, init=False, repr=False)
    _bounded_binary_backend_verified: bool = field(default=False, init=False, repr=False)

    def __setattr__(self, name: str, value: object) -> None:
        # A guard and its session state form one connection epoch.  Rebinding
        # the state after construction would let callers replace a latched
        # poisoned epoch with a fresh healthy object.
        if name == "session_state" and hasattr(self, "session_state"):
            raise AttributeError("session_state is immutable after transport construction")
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        self.access = normalize_access_mode(self.access, "access")
        if not isinstance(self.construction_latched, bool):
            raise TypeError("construction_latched must be bool")

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
            effective_timeout_ms = timeout_ms
            if authorization is not None:
                remaining_ms = int(
                    max(authorization.deadline - time.monotonic(), 0.0) * 1000.0
                )
                if remaining_ms < 1:
                    raise self._deadline_preflight_error("query_float_list", replay)
                effective_timeout_ms = min(
                    authorization.io_timeout_ms,
                    remaining_ms,
                    timeout_ms if timeout_ms is not None else authorization.io_timeout_ms,
                )
            try:
                result = self.inner.query_float_list(
                    command,
                    timeout_ms=effective_timeout_ms,
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
            self._check_construction_latch("query_bin_block")
            active = self.session_state._active_authorization()
            if active is not None and active.binary_budget is not None:
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_legacy_entry_unsupported", replay)
            authorization = self._gate("query_bin_block")
            try:
                result = self.inner.query_bin_block(command, replay=replay)
            except Exception as exc:
                self._transition_after_failure(exc, authorization)
                raise
            self._record_success(authorization, "query_bin_block")
            return result

    def query_binary(
        self,
        command: str,
        *,
        framing: BinaryResponseFraming,
        max_bytes: int,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> BinaryQueryResult:
        with self.session_state.transaction_lock:
            replay = ReplayPolicy(replay)
            framing = BinaryResponseFraming(framing)
            self._check_access("query_binary")
            self.counters.binary_query_calls += 1
            self._check_construction_latch("query_binary")
            if replay is ReplayPolicy.READ_CONTINUATION_ONLY:
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_continuation_unsupported", replay)
            backend_query = getattr(self.inner, "query_binary", None)
            if not callable(backend_query):
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_framing_unsupported", replay)
            active = self.session_state._active_authorization()
            if active is None or not isinstance(active.binary_budget, BinaryQueryBudget):
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_budget_missing", replay)
            if replay is not ReplayPolicy.NO_REPLAY:
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_replay_unsupported", replay)
            budget = active.binary_budget
            ledger = budget._ledger
            if ledger.required_framing is not None and framing is not ledger.required_framing:
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_framing_profile_unsupported", replay)
            authorization = self._gate("query_binary")
            assert authorization is active
            remaining_ms = int(
                max(authorization.deadline - time.monotonic(), 0.0) * 1000.0
            )
            if remaining_ms < 1:
                self.counters.blocked_binary_query_calls += 1
                raise self._deadline_preflight_error("query_binary", replay)
            effective_timeout_ms = min(authorization.io_timeout_ms, remaining_ms)
            try:
                reservation = ledger.reserve(
                    budget,
                    context_id=active.context_id or "",
                    operation_id=active.operation_id,
                    correlation_id=active.correlation_id or "",
                    session_epoch=active.epoch_id,
                    max_bytes=max_bytes,
                )
            except (TypeError, ValueError) as exc:
                self.counters.blocked_binary_query_calls += 1
                raise self._binary_preflight_error("binary_budget_rejected", replay) from exc
            try:
                backend_kwargs: dict[str, object] = {
                    "framing": framing,
                    "max_bytes": reservation.effective_max_bytes,
                    "timeout_ms": effective_timeout_ms,
                    "replay": replay,
                }
                if bool(
                    getattr(self.inner, "_wavebench_binary_budget_parameters", False)
                ):
                    backend_kwargs.update(
                        _transport_trailing=ledger.transport_trailing,
                        _resynchronization_max_bytes=(
                            ledger.remaining_resynchronization_bytes
                        ),
                    )
                result = backend_query(command, **backend_kwargs)
            except Exception as exc:
                if isinstance(exc, TransportIOError):
                    try:
                        ledger.fail(
                            reservation,
                            # Structured failures expose total consumed bytes,
                            # not a trusted payload/header split.  Debit the
                            # bounded maximum conservatively so failure cannot
                            # increase the remaining operation allowance.
                            consumed_payload_bytes=min(
                                exc.consumed_bytes or 0,
                                reservation.effective_max_bytes,
                            ),
                            discarded_bytes=exc.discarded_bytes or 0,
                            synchronization_proven=(
                                exc.synchronization is Synchronization.PROVEN
                            ),
                        )
                    except (TypeError, ValueError):
                        ledger.invalidate()
                        if self.session_state.health in {
                            SessionHealth.HEALTHY,
                            SessionHealth.UNCERTAIN,
                        }:
                            self.session_state.degrade(
                                SessionHealth.POISONED,
                                reason="binary_budget_violation",
                            )
                else:
                    ledger.invalidate()
                self._transition_after_failure(exc, authorization)
                if self.session_state.health is SessionHealth.POISONED:
                    self._close_poisoned_backend()
                raise
            if not isinstance(result, BinaryQueryResult) or result.framing is not framing:
                try:
                    ledger.fail(
                        reservation,
                        synchronization_proven=False,
                    )
                except (TypeError, ValueError):
                    ledger.invalidate()
                error = TransportIOError(
                    "binary backend violated the result contract",
                    operation="query_binary",
                    phase=TransportPhase.PARSING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.SENT,
                    response_progress=ResponseProgress.COMPLETE,
                    synchronization=Synchronization.LOST,
                    attempts=1,
                    reason_code="binary_contract_violation",
                )
                self._transition_after_failure(error, authorization)
                self._close_poisoned_backend()
                raise error
            try:
                ledger.commit(reservation, result)
            except (TypeError, ValueError) as exc:
                error = TransportIOError(
                    "binary response exceeded the authorized contract",
                    operation="query_binary",
                    phase=TransportPhase.PARSING,
                    replay_policy=replay,
                    command_transmission=CommandTransmission.SENT,
                    response_progress=ResponseProgress.COMPLETE,
                    synchronization=Synchronization.LOST,
                    attempts=1,
                    reason_code="binary_contract_violation",
                    consumed_bytes=result.consumed_bytes,
                )
                self._transition_after_failure(error, authorization)
                self._close_poisoned_backend()
                raise error from exc
            self._record_success(authorization, "query_binary")
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

    def _release_construction_latch(self) -> None:
        """Release the factory-owned opt-in I/O latch after static validation."""

        with self.session_state.transaction_lock:
            if not self.construction_latched:
                return
            if self._closed or self.session_state.health is not SessionHealth.HEALTHY:
                raise RuntimeError("cannot release construction latch on an unavailable transport")
            self.construction_latched = False

    def _mark_bounded_binary_backend_verified(self) -> None:
        """Record the factory's successful bounded-binary backend conformance check."""

        with self.session_state.transaction_lock:
            if self._closed or self.session_state.health is not SessionHealth.HEALTHY:
                raise RuntimeError("cannot verify bounded backend on an unavailable transport")
            self._bounded_binary_backend_verified = True

    def _has_verified_bounded_binary_backend(self) -> bool:
        with self.session_state.transaction_lock:
            return (
                self._bounded_binary_backend_verified
                and not self._closed
                and self.session_state.health is SessionHealth.HEALTHY
            )

    def _mark_bounded_waveform_backend_verified(self) -> None:
        """Compatibility alias for the former waveform-specific internal marker."""

        self._mark_bounded_binary_backend_verified()

    def _has_verified_bounded_waveform_backend(self) -> bool:
        """Compatibility alias for the former waveform-specific internal predicate."""

        return self._has_verified_bounded_binary_backend()

    def _check_access(self, operation: str, *, write: bool = False) -> None:
        if write and self.access != "read_write":
            if operation == "write":
                self.counters.blocked_write_requests += 1
            else:
                self.counters.blocked_binary_write_requests += 1
            raise self._denied(operation)
        if not write and self.access == "disabled":
            if operation in {"query_bin_block", "query_binary"}:
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
        self._check_construction_latch(io_kind)
        try:
            authorization = self.session_state._consume_authorization(io_kind)
        except ValueError as exc:
            self.counters.blocked_session_io += 1
            raise self._session_denied(io_kind, reason=str(exc)) from exc
        if self.session_state.health is SessionHealth.UNCERTAIN and authorization is None:
            self.counters.blocked_session_io += 1
            raise self._session_denied(io_kind)
        return authorization

    def _check_construction_latch(self, io_kind: str) -> None:
        if not self.construction_latched:
            return
        self.counters.blocked_session_io += 1
        raise self._construction_latch_error(io_kind)

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

    @staticmethod
    def _binary_preflight_error(
        reason_code: str,
        replay: ReplayPolicy,
    ) -> TransportIOError:
        return TransportIOError(
            "binary query was rejected before transmission",
            operation="query_binary",
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=replay,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            reason_code=reason_code,
            consumed_bytes=0,
            discarded_bytes=0,
        )

    @staticmethod
    def _deadline_preflight_error(
        operation: str,
        replay: ReplayPolicy,
    ) -> TransportIOError:
        return TransportIOError(
            "operation deadline was exhausted before transmission",
            operation=operation,
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=replay,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            reason_code="deadline_exhausted",
            consumed_bytes=0,
            discarded_bytes=0,
        )

    @staticmethod
    def _construction_latch_error(io_kind: str) -> TransportIOError:
        return TransportIOError(
            "instrument I/O is blocked until factory construction validation completes",
            operation=io_kind,
            phase=TransportPhase.BEFORE_SEND,
            replay_policy=ReplayPolicy.NO_REPLAY,
            command_transmission=CommandTransmission.NOT_SENT,
            response_progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            reason_code="factory_construction_pending",
            consumed_bytes=0,
            discarded_bytes=0,
        )

    def _close_poisoned_backend(self) -> None:
        """Close a framing-lost backend while preserving the poisoned diagnosis."""

        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self.inner, "close", None)
            if callable(close):
                close()
        except Exception:
            # The triggering structured failure remains primary.  Backend close
            # evidence is intentionally not allowed to replace it here.
            pass
        finally:
            if self.release_lease_on_close and self.lease is not None:
                try:
                    self.lease.release()
                except Exception:
                    pass


__all__ = ["AUDIT_SCHEMA", "AuditCounters", "GuardedAuditedTransport"]
