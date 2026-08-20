"""Private binary framing and operation-budget primitives for scope RFC R1.3.

These types are intentionally not exported from :mod:`wavebench.transport` yet.
The RFC remains Draft and no existing backend or public capability uses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
import time
from uuid import uuid4

from wavebench.errors import TransportIOError

from .contracts import (
    BinaryQueryResult,
    BinaryResponseFraming,
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _non_negative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _binding(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


@dataclass(frozen=True, slots=True, init=False, eq=False)
class BinaryQueryBudget:
    """Opaque handle issued for exactly one core-owned binary ledger."""

    _ledger: "BinaryQueryLedger"
    _nonce: object

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("binary query budgets are issued by BinaryQueryLedger")

    @classmethod
    def _issue(cls, ledger: "BinaryQueryLedger", nonce: object) -> "BinaryQueryBudget":
        instance = object.__new__(cls)
        object.__setattr__(instance, "_ledger", ledger)
        object.__setattr__(instance, "_nonce", nonce)
        return instance

    @property
    def ledger_id(self) -> str:
        return self._ledger.ledger_id


@dataclass(frozen=True, slots=True, init=False, eq=False)
class BinaryQueryReservation:
    """One pre-reserved binary query; a failed exchange never refunds it."""

    _ledger: "BinaryQueryLedger"
    _nonce: object
    _reservation_id: str
    effective_max_bytes: int

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("binary query reservations are issued by BinaryQueryLedger")

    @classmethod
    def _issue(
        cls,
        ledger: "BinaryQueryLedger",
        nonce: object,
        reservation_id: str,
        effective_max_bytes: int,
    ) -> "BinaryQueryReservation":
        instance = object.__new__(cls)
        object.__setattr__(instance, "_ledger", ledger)
        object.__setattr__(instance, "_nonce", nonce)
        object.__setattr__(instance, "_reservation_id", reservation_id)
        object.__setattr__(instance, "effective_max_bytes", effective_max_bytes)
        return instance


@dataclass(slots=True)
class _ReservationRecord:
    effective_max_bytes: int
    active: bool = True


class BinaryQueryLedger:
    """Thread-safe response/operation/query/resynchronization budget ledger."""

    __slots__ = (
        "ledger_id",
        "context_id",
        "operation_id",
        "correlation_id",
        "session_epoch",
        "deadline",
        "per_response_max_bytes",
        "operation_max_bytes",
        "query_max_count",
        "resynchronization_max_bytes",
        "transport_trailing",
        "_remaining_operation_bytes",
        "_remaining_query_count",
        "_discarded_bytes",
        "_active",
        "_lock",
        "_nonce",
        "_reservations",
    )

    def __init__(
        self,
        *,
        context_id: str,
        operation_id: str,
        correlation_id: str,
        session_epoch: str,
        deadline: float,
        per_response_max_bytes: int,
        operation_max_bytes: int,
        query_max_count: int,
        resynchronization_max_bytes: int,
        transport_trailing: bytes = b"",
        ledger_id: str | None = None,
    ) -> None:
        self.context_id = _binding(context_id, label="context_id")
        self.operation_id = _binding(operation_id, label="operation_id")
        self.correlation_id = _binding(correlation_id, label="correlation_id")
        self.session_epoch = _binding(session_epoch, label="session_epoch")
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise ValueError("deadline must be a monotonic timestamp")
        self.deadline = float(deadline)
        if self.deadline <= time.monotonic():
            raise ValueError("binary query ledger deadline must be in the future")
        self.per_response_max_bytes = _positive_int(
            per_response_max_bytes, label="per_response_max_bytes"
        )
        self.operation_max_bytes = _positive_int(
            operation_max_bytes, label="operation_max_bytes"
        )
        if self.operation_max_bytes < self.per_response_max_bytes:
            raise ValueError("operation_max_bytes must cover at least one full response")
        self.query_max_count = _positive_int(query_max_count, label="query_max_count")
        self.resynchronization_max_bytes = _non_negative_int(
            resynchronization_max_bytes,
            label="resynchronization_max_bytes",
        )
        if not isinstance(transport_trailing, bytes):
            raise TypeError("transport_trailing must be bytes")
        if len(transport_trailing) > 16:
            raise ValueError("transport_trailing cannot exceed 16 bytes")
        self.transport_trailing = transport_trailing
        self.ledger_id = _binding(ledger_id or uuid4().hex, label="ledger_id")
        self._remaining_operation_bytes = self.operation_max_bytes
        self._remaining_query_count = self.query_max_count
        self._discarded_bytes = 0
        self._active = True
        self._lock = RLock()
        self._nonce = object()
        self._reservations: dict[str, _ReservationRecord] = {}

    def issue_budget(self) -> BinaryQueryBudget:
        with self._lock:
            if not self._active:
                raise ValueError("binary query ledger is inactive")
            return BinaryQueryBudget._issue(self, self._nonce)

    def reserve(
        self,
        budget: BinaryQueryBudget,
        *,
        context_id: str,
        operation_id: str,
        correlation_id: str,
        session_epoch: str,
        max_bytes: int,
    ) -> BinaryQueryReservation:
        requested = _positive_int(max_bytes, label="max_bytes")
        with self._lock:
            self._validate_budget(budget)
            self._validate_binding(
                context_id=context_id,
                operation_id=operation_id,
                correlation_id=correlation_id,
                session_epoch=session_epoch,
            )
            if time.monotonic() >= self.deadline:
                raise ValueError("binary query ledger deadline is exhausted")
            if self._remaining_query_count < 1:
                raise ValueError("binary query count budget is exhausted")
            if requested > self.per_response_max_bytes:
                raise ValueError("requested max_bytes exceeds the per-response budget")
            effective = min(
                requested,
                self.per_response_max_bytes,
                self._remaining_operation_bytes,
            )
            if effective < 1:
                raise ValueError("binary operation byte budget is exhausted")
            self._remaining_query_count -= 1
            reservation_id = uuid4().hex
            self._reservations[reservation_id] = _ReservationRecord(effective)
            return BinaryQueryReservation._issue(
                self,
                self._nonce,
                reservation_id,
                effective,
            )

    def commit(
        self,
        reservation: BinaryQueryReservation,
        result: BinaryQueryResult,
    ) -> None:
        if not isinstance(result, BinaryQueryResult):
            raise TypeError("binary query result has an invalid type")
        with self._lock:
            record = self._consume_reservation(reservation)
            if len(result.data) > record.effective_max_bytes:
                self._active = False
                raise ValueError("binary backend returned more bytes than reserved")
            if result.transport_trailing_bytes != self.transport_trailing:
                self._active = False
                raise ValueError("binary transport trailing bytes violate the ledger profile")
            self._remaining_operation_bytes -= len(result.data)

    def fail(
        self,
        reservation: BinaryQueryReservation,
        *,
        consumed_payload_bytes: int = 0,
        discarded_bytes: int = 0,
        synchronization_proven: bool,
    ) -> None:
        consumed = _non_negative_int(
            consumed_payload_bytes,
            label="consumed_payload_bytes",
        )
        discarded = _non_negative_int(discarded_bytes, label="discarded_bytes")
        if not isinstance(synchronization_proven, bool):
            raise TypeError("synchronization_proven must be bool")
        with self._lock:
            record = self._consume_reservation(reservation)
            if consumed > record.effective_max_bytes:
                self._active = False
                raise ValueError("failed binary query consumed more payload than reserved")
            self._remaining_operation_bytes = max(
                self._remaining_operation_bytes - consumed,
                0,
            )
            self._discarded_bytes += discarded
            if self._discarded_bytes > self.resynchronization_max_bytes:
                self._active = False
                raise ValueError("binary resynchronization budget is exceeded")
            if not synchronization_proven:
                self._active = False

    def invalidate(self) -> None:
        with self._lock:
            self._active = False
            for record in self._reservations.values():
                record.active = False

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "ledger_id": self.ledger_id,
                "active": self._active,
                "per_response_max_bytes": self.per_response_max_bytes,
                "operation_max_bytes": self.operation_max_bytes,
                "remaining_operation_bytes": self._remaining_operation_bytes,
                "query_max_count": self.query_max_count,
                "remaining_query_count": self._remaining_query_count,
                "resynchronization_max_bytes": self.resynchronization_max_bytes,
                "discarded_bytes": self._discarded_bytes,
                "transport_trailing_bytes": len(self.transport_trailing),
            }

    def _validate_budget(self, budget: BinaryQueryBudget) -> None:
        if not isinstance(budget, BinaryQueryBudget):
            raise TypeError("binary query budget has an invalid type")
        if budget._ledger is not self or budget._nonce is not self._nonce:
            raise ValueError("binary query budget is not owned by this ledger")
        if not self._active:
            raise ValueError("binary query ledger is inactive")

    def _validate_binding(
        self,
        *,
        context_id: str,
        operation_id: str,
        correlation_id: str,
        session_epoch: str,
    ) -> None:
        if (
            context_id != self.context_id
            or operation_id != self.operation_id
            or correlation_id != self.correlation_id
            or session_epoch != self.session_epoch
        ):
            raise ValueError("binary query budget binding does not match the active operation")

    def _consume_reservation(
        self,
        reservation: BinaryQueryReservation,
    ) -> _ReservationRecord:
        if not isinstance(reservation, BinaryQueryReservation):
            raise TypeError("binary query reservation has an invalid type")
        if reservation._ledger is not self or reservation._nonce is not self._nonce:
            raise ValueError("binary query reservation is not owned by this ledger")
        if not self._active:
            raise ValueError("binary query ledger is inactive")
        record = self._reservations.get(reservation._reservation_id)
        if record is None or not record.active:
            raise ValueError("binary query reservation is no longer active")
        record.active = False
        return record


def parse_definite_block_response(
    raw: bytes,
    *,
    max_bytes: int,
    transport_trailing: bytes = b"",
    allow_zero_payload: bool = False,
) -> BinaryQueryResult:
    """Parse one already bounded raw IEEE 488.2 definite-block response."""

    limit = _positive_int(max_bytes, label="max_bytes")
    if not isinstance(raw, bytes):
        raise TypeError("raw definite-block response must be bytes")
    if not isinstance(transport_trailing, bytes):
        raise TypeError("transport_trailing must be bytes")
    if len(transport_trailing) > 16:
        raise ValueError("transport_trailing cannot exceed 16 bytes")
    if not isinstance(allow_zero_payload, bool):
        raise TypeError("allow_zero_payload must be bool")

    def fail(
        reason_code: str,
        *,
        progress: ResponseProgress,
        synchronization: Synchronization,
        consumed_bytes: int,
        discarded_bytes: int = 0,
    ) -> TransportIOError:
        return TransportIOError(
            "binary definite-block response validation failed",
            operation="query_binary",
            phase=TransportPhase.PARSING,
            replay_policy=ReplayPolicy.NO_REPLAY,
            command_transmission=CommandTransmission.SENT,
            response_progress=progress,
            synchronization=synchronization,
            attempts=1,
            reason_code=reason_code,
            consumed_bytes=consumed_bytes,
            discarded_bytes=discarded_bytes,
        )

    if len(raw) < 2 or raw[0:1] != b"#" or raw[1:2] not in b"123456789":
        raise fail(
            "binary_framing_error",
            progress=ResponseProgress.PARTIAL if raw else ResponseProgress.NONE,
            synchronization=Synchronization.LOST,
            consumed_bytes=len(raw),
        )
    digits = raw[1] - ord("0")
    header_bytes = 2 + digits
    if len(raw) < header_bytes:
        raise fail(
            "binary_truncated",
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            consumed_bytes=len(raw),
        )
    length_field = raw[2:header_bytes]
    if any(byte < ord("0") or byte > ord("9") for byte in length_field):
        raise fail(
            "binary_framing_error",
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            consumed_bytes=header_bytes,
        )
    declared = int(length_field.decode("ascii"))
    expected_total = header_bytes + declared + len(transport_trailing)
    if declared == 0 and not allow_zero_payload:
        raise fail(
            "binary_framing_error",
            progress=ResponseProgress.COMPLETE if len(raw) == expected_total else ResponseProgress.PARTIAL,
            synchronization=(
                Synchronization.PROVEN
                if len(raw) == expected_total
                else Synchronization.LOST
            ),
            consumed_bytes=min(len(raw), expected_total),
        )
    if declared > limit:
        boundary_proven = len(raw) == expected_total
        if boundary_proven and transport_trailing:
            boundary_proven = raw[-len(transport_trailing) :] == transport_trailing
        raise fail(
            "binary_limit_exceeded",
            progress=ResponseProgress.COMPLETE if boundary_proven else ResponseProgress.PARTIAL,
            synchronization=(
                Synchronization.PROVEN if boundary_proven else Synchronization.LOST
            ),
            consumed_bytes=len(raw),
            discarded_bytes=declared if boundary_proven else 0,
        )
    if len(raw) < expected_total:
        raise fail(
            "binary_truncated",
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            consumed_bytes=len(raw),
        )
    if len(raw) > expected_total or (
        transport_trailing and raw[-len(transport_trailing) :] != transport_trailing
    ):
        raise fail(
            "binary_transport_trailing_error",
            progress=ResponseProgress.COMPLETE,
            synchronization=Synchronization.LOST,
            consumed_bytes=len(raw),
        )
    payload = raw[header_bytes : header_bytes + declared]
    return BinaryQueryResult(
        data=payload,
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
        declared_length=declared,
        framing_header_bytes=header_bytes,
        consumed_bytes=expected_total,
        transport_trailing_bytes=transport_trailing,
    )


__all__ = [
    "BinaryQueryBudget",
    "BinaryQueryLedger",
    "BinaryQueryReservation",
    "parse_definite_block_response",
]
