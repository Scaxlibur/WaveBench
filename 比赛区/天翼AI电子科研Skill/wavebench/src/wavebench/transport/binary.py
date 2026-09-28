"""Bounded binary framing and operation-budget primitives for scope RFC R1.3."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from threading import RLock
import time
from typing import Any
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
        "required_framing",
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
        required_framing: BinaryResponseFraming | None = None,
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
        self.required_framing = (
            BinaryResponseFraming(required_framing)
            if required_framing is not None
            else None
        )
        if self.required_framing is BinaryResponseFraming.MESSAGE and transport_trailing:
            raise ValueError("message-framed ledgers cannot declare transport trailing bytes")
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
                "required_framing": (
                    self.required_framing.value if self.required_framing is not None else None
                ),
            }

    @property
    def remaining_resynchronization_bytes(self) -> int:
        with self._lock:
            return max(self.resynchronization_max_bytes - self._discarded_bytes, 0)

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
            discarded_bytes=(
                declared + len(transport_trailing) if boundary_proven else 0
            ),
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


def visa_message_boundary_supported(session: object) -> bool:
    """Return whether a concrete VISA resource can report a message boundary.

    A backend or conformance fake can provide an explicit boolean
    ``wavebench_message_boundary`` attribute.  Real VISA resources are accepted
    only for message-based ``INSTR`` resources; raw TCP sockets and serial
    resources do not have a portable EOM contract.
    """

    explicit = getattr(session, "wavebench_message_boundary", None)
    if isinstance(explicit, bool):
        return explicit
    resource_name = str(
        getattr(session, "resource_name", None)
        or getattr(session, "_resource_name", "")
    ).upper()
    if "::SOCKET" in resource_name or resource_name.startswith("ASRL"):
        return False
    resource_class = getattr(session, "resource_class", None)
    if resource_class is None:
        try:
            resource_class = session.resource_info.resource_class  # type: ignore[attr-defined]
        except Exception:
            resource_class = None
    if str(resource_class or "").upper() != "INSTR":
        return False
    return resource_name.startswith(("GPIB", "TCPIP", "USB", "VXI", "PXI"))


def visa_binary_contract_supported(session: object) -> bool:
    """Return whether a VISA handle can execute the core bounded-binary contract."""

    return (
        visa_message_boundary_supported(session)
        and callable(getattr(session, "write", None))
        and _has_low_level_visa_read(session)
    )


def query_visa_binary_response(
    *,
    session: object,
    write_query: Callable[[str], object],
    command: str,
    framing: BinaryResponseFraming,
    max_bytes: int,
    timeout_ms: int | None,
    replay: ReplayPolicy,
    transport_trailing: bytes = b"",
    resynchronization_max_bytes: int = 0,
) -> BinaryQueryResult:
    """Execute one bounded binary query through a PyVISA-compatible resource.

    The function deliberately uses the low-level VISA read status rather than
    ``read_raw()`` so a backend cannot allocate beyond the response, the
    authorized resynchronization allowance, and one boundary-probe byte. It
    never retries a sent query.
    """

    framing = BinaryResponseFraming(framing)
    replay = ReplayPolicy(replay)
    limit = _positive_int(max_bytes, label="max_bytes")
    resync_limit = _non_negative_int(
        resynchronization_max_bytes,
        label="resynchronization_max_bytes",
    )
    if not isinstance(command, str) or not command:
        raise ValueError("binary query command must be a non-empty string")
    if timeout_ms is not None:
        _positive_int(timeout_ms, label="timeout_ms")
    if not isinstance(transport_trailing, bytes):
        raise TypeError("transport_trailing must be bytes")
    if len(transport_trailing) > 16:
        raise ValueError("transport_trailing cannot exceed 16 bytes")
    if framing is BinaryResponseFraming.MESSAGE and transport_trailing:
        raise ValueError("message framing cannot use transport trailing bytes")
    if replay is ReplayPolicy.READ_CONTINUATION_ONLY:
        raise _binary_transport_error(
            "binary_continuation_unsupported",
            phase=TransportPhase.BEFORE_SEND,
            replay=replay,
            transmission=CommandTransmission.NOT_SENT,
            progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            consumed_bytes=0,
        )
    if not visa_message_boundary_supported(session):
        raise _binary_transport_error(
            "binary_framing_unsupported",
            phase=TransportPhase.BEFORE_SEND,
            replay=replay,
            transmission=CommandTransmission.NOT_SENT,
            progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            consumed_bytes=0,
        )

    if not callable(write_query):
        raise _binary_transport_error(
            "binary_framing_unsupported",
            phase=TransportPhase.BEFORE_SEND,
            replay=replay,
            transmission=CommandTransmission.NOT_SENT,
            progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            consumed_bytes=0,
        )
    if not _has_low_level_visa_read(session):
        raise _binary_transport_error(
            "binary_framing_unsupported",
            phase=TransportPhase.BEFORE_SEND,
            replay=replay,
            transmission=CommandTransmission.NOT_SENT,
            progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            consumed_bytes=0,
        )

    original_timeout: object = _MISSING
    original_termination: object = _MISSING
    settings_changed = False
    transmitted = False
    progress_state = {"consumed": 0}
    primary: BaseException | None = None
    result: BinaryQueryResult | None = None
    try:
        original_timeout = getattr(session, "timeout")
        original_termination = getattr(session, "read_termination")
        if timeout_ms is not None:
            setattr(session, "timeout", timeout_ms)
        setattr(session, "read_termination", None)
        settings_changed = True
    except Exception as exc:
        _restore_visa_read_settings(
            session,
            original_timeout=original_timeout,
            original_termination=original_termination,
        )
        raise _binary_transport_error(
            "binary_framing_unsupported",
            phase=TransportPhase.BEFORE_SEND,
            replay=replay,
            transmission=CommandTransmission.NOT_SENT,
            progress=ResponseProgress.NONE,
            synchronization=Synchronization.PROVEN,
            attempts=0,
            consumed_bytes=0,
        ) from exc

    try:
        try:
            written = write_query(command)
            if written == 0:
                raise _binary_transport_error(
                    None,
                    phase=TransportPhase.BEFORE_SEND,
                    replay=replay,
                    transmission=CommandTransmission.NOT_SENT,
                    progress=ResponseProgress.NONE,
                    synchronization=Synchronization.PROVEN,
                    attempts=0,
                    consumed_bytes=0,
                )
            transmitted = True
        except TransportIOError:
            raise
        except Exception as exc:
            raise _binary_transport_error(
                "binary_timeout" if _is_timeout_error(exc) else None,
                phase=TransportPhase.SENDING,
                replay=replay,
                transmission=CommandTransmission.UNKNOWN,
                progress=ResponseProgress.NONE,
                synchronization=Synchronization.UNPROVEN,
                attempts=1,
                consumed_bytes=0,
            ) from exc

        if framing is BinaryResponseFraming.DEFINITE_BLOCK:
            result = _read_visa_definite_block(
                session,
                max_bytes=limit,
                transport_trailing=transport_trailing,
                resynchronization_max_bytes=resync_limit,
                replay=replay,
                progress_state=progress_state,
            )
        else:
            result = _read_visa_message(
                session,
                max_bytes=limit,
                resynchronization_max_bytes=resync_limit,
                replay=replay,
                progress_state=progress_state,
            )
    except BaseException as exc:
        if isinstance(exc, TransportIOError):
            primary = exc
        elif transmitted:
            primary = _binary_transport_error(
                "binary_timeout" if _is_timeout_error(exc) else "binary_truncated",
                phase=TransportPhase.READING,
                replay=replay,
                transmission=CommandTransmission.SENT,
                progress=(
                    ResponseProgress.PARTIAL
                    if progress_state["consumed"]
                    else ResponseProgress.NONE
                ),
                synchronization=Synchronization.UNPROVEN,
                attempts=1,
                consumed_bytes=progress_state["consumed"],
            )
            primary.__cause__ = exc
        else:
            primary = exc

    restore_error: BaseException | None = None
    if settings_changed:
        try:
            _restore_visa_read_settings(
                session,
                original_timeout=original_timeout,
                original_termination=original_termination,
            )
        except BaseException as exc:
            restore_error = exc
    if restore_error is not None:
        raise _binary_transport_error(
            "binary_transport_trailing_error",
            phase=TransportPhase.READING,
            replay=replay,
            transmission=(
                CommandTransmission.SENT if transmitted else CommandTransmission.UNKNOWN
            ),
            progress=(
                ResponseProgress.COMPLETE
                if result is not None
                else ResponseProgress.PARTIAL
                if progress_state["consumed"]
                else ResponseProgress.NONE
            ),
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
        ) from (primary or restore_error)
    if primary is not None:
        raise primary
    assert result is not None
    return result


_MISSING = object()


def _restore_visa_read_settings(
    session: object,
    *,
    original_timeout: object,
    original_termination: object,
) -> None:
    failures: list[BaseException] = []
    if original_termination is not _MISSING:
        try:
            setattr(session, "read_termination", original_termination)
        except BaseException as exc:
            failures.append(exc)
    if original_timeout is not _MISSING:
        try:
            setattr(session, "timeout", original_timeout)
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise RuntimeError("failed to restore VISA read settings") from failures[0]


def _has_low_level_visa_read(session: object) -> bool:
    visalib = getattr(session, "visalib", None)
    return (
        visalib is not None
        and callable(getattr(visalib, "read", None))
        and getattr(session, "session", None) is not None
        and hasattr(session, "timeout")
        and hasattr(session, "read_termination")
    )


def _read_visa_definite_block(
    session: object,
    *,
    max_bytes: int,
    transport_trailing: bytes,
    resynchronization_max_bytes: int,
    replay: ReplayPolicy,
    progress_state: dict[str, int],
) -> BinaryQueryResult:
    prefix, prefix_eom = _visa_read_exact(
        session,
        2,
        replay=replay,
        progress_state=progress_state,
    )
    if prefix_eom:
        raise _binary_truncated_error(replay, progress_state["consumed"], proven=True)
    if prefix[0:1] != b"#" or prefix[1:2] not in b"123456789":
        raise _binary_transport_error(
            "binary_framing_error",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
        )
    digits = prefix[1] - ord("0")
    length_field, length_eom = _visa_read_exact(
        session,
        digits,
        replay=replay,
        progress_state=progress_state,
    )
    if any(byte < ord("0") or byte > ord("9") for byte in length_field):
        raise _binary_transport_error(
            "binary_framing_error",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
        )
    declared = int(length_field.decode("ascii"))
    if declared == 0:
        raise _binary_transport_error(
            "binary_framing_error",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
        )
    if length_eom:
        raise _binary_truncated_error(replay, progress_state["consumed"], proven=True)
    header_bytes = 2 + digits
    remaining_response = declared + len(transport_trailing)
    if declared > max_bytes:
        if remaining_response > resynchronization_max_bytes:
            raise _binary_transport_error(
                "binary_limit_exceeded",
                phase=TransportPhase.PARSING,
                replay=replay,
                transmission=CommandTransmission.SENT,
                progress=ResponseProgress.PARTIAL,
                synchronization=Synchronization.LOST,
                attempts=1,
                consumed_bytes=progress_state["consumed"],
                discarded_bytes=0,
            )
        discarded = _visa_read_expected_message(
            session,
            remaining_response,
            replay=replay,
            progress_state=progress_state,
            discarded_base=remaining_response,
        )
        trailing = discarded[declared:]
        if trailing != transport_trailing:
            raise _binary_transport_error(
                "binary_transport_trailing_error",
                phase=TransportPhase.PARSING,
                replay=replay,
                transmission=CommandTransmission.SENT,
                progress=ResponseProgress.COMPLETE,
                synchronization=Synchronization.LOST,
                attempts=1,
                consumed_bytes=progress_state["consumed"],
                discarded_bytes=remaining_response,
            )
        raise _binary_transport_error(
            "binary_limit_exceeded",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.COMPLETE,
            synchronization=Synchronization.PROVEN,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
            discarded_bytes=remaining_response,
        )

    response = _visa_read_expected_message(
        session,
        remaining_response,
        replay=replay,
        progress_state=progress_state,
    )
    payload = response[:declared]
    trailing = response[declared:]
    if trailing != transport_trailing:
        raise _binary_transport_error(
            "binary_transport_trailing_error",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.COMPLETE,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
        )
    return BinaryQueryResult(
        data=payload,
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
        declared_length=declared,
        framing_header_bytes=header_bytes,
        consumed_bytes=progress_state["consumed"],
        transport_trailing_bytes=trailing,
    )


def _read_visa_message(
    session: object,
    *,
    max_bytes: int,
    resynchronization_max_bytes: int,
    replay: ReplayPolicy,
    progress_state: dict[str, int],
) -> BinaryQueryResult:
    # Request one byte beyond the authorized payload and resynchronization
    # ceiling. VISA implementations commonly report MAX_CNT when EOM lands on
    # the final requested byte; the bounded probe makes a valid response
    # strictly shorter than the request and therefore proves its boundary.
    capacity = max_bytes + resynchronization_max_bytes + 1
    response, eom = _visa_read_chunk(session, capacity)
    if len(response) > capacity:
        raise _binary_transport_error(
            "binary_framing_error",
            phase=TransportPhase.READING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.PARTIAL,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
        )
    progress_state["consumed"] += len(response)
    if eom:
        if len(response) <= max_bytes:
            return BinaryQueryResult(
                data=response,
                framing=BinaryResponseFraming.MESSAGE,
                declared_length=None,
                framing_header_bytes=0,
                consumed_bytes=len(response),
            )
        raise _binary_transport_error(
            "binary_limit_exceeded",
            phase=TransportPhase.READING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.COMPLETE,
            synchronization=Synchronization.PROVEN,
            attempts=1,
            consumed_bytes=len(response),
            discarded_bytes=len(response) - max_bytes,
        )
    raise _binary_transport_error(
        "binary_limit_exceeded" if len(response) > max_bytes else "binary_truncated",
        phase=TransportPhase.READING,
        replay=replay,
        transmission=CommandTransmission.SENT,
        progress=ResponseProgress.PARTIAL,
        synchronization=Synchronization.LOST,
        attempts=1,
        consumed_bytes=progress_state["consumed"],
        discarded_bytes=max(progress_state["consumed"] - max_bytes, 0),
    )


def _visa_read_expected_message(
    session: object,
    count: int,
    *,
    replay: ReplayPolicy,
    progress_state: dict[str, int],
    discarded_base: int = 0,
) -> bytes:
    """Read an exact expected tail while proving EOM with one bounded probe."""

    response, eom = _visa_read_chunk(session, count + 1)
    progress_state["consumed"] += len(response)
    if len(response) > count:
        raise _binary_transport_error(
            "binary_transport_trailing_error",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.COMPLETE,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
            discarded_bytes=discarded_base + len(response) - count,
        )
    if len(response) < count:
        raise _binary_truncated_error(
            replay,
            progress_state["consumed"],
            proven=eom,
        )
    if not eom:
        raise _binary_transport_error(
            "binary_transport_trailing_error",
            phase=TransportPhase.PARSING,
            replay=replay,
            transmission=CommandTransmission.SENT,
            progress=ResponseProgress.COMPLETE,
            synchronization=Synchronization.LOST,
            attempts=1,
            consumed_bytes=progress_state["consumed"],
            discarded_bytes=discarded_base,
        )
    return response


def _visa_read_exact(
    session: object,
    count: int,
    *,
    replay: ReplayPolicy,
    progress_state: dict[str, int],
) -> tuple[bytes, bool]:
    if count == 0:
        return b"", False
    response = bytearray()
    while len(response) < count:
        chunk, eom = _visa_read_chunk(session, count - len(response))
        if len(chunk) > count - len(response):
            raise _binary_transport_error(
                "binary_framing_error",
                phase=TransportPhase.READING,
                replay=replay,
                transmission=CommandTransmission.SENT,
                progress=ResponseProgress.PARTIAL,
                synchronization=Synchronization.LOST,
                attempts=1,
                consumed_bytes=progress_state["consumed"],
            )
        response.extend(chunk)
        progress_state["consumed"] += len(chunk)
        if eom:
            if len(response) < count:
                raise _binary_truncated_error(
                    replay,
                    progress_state["consumed"],
                    proven=True,
                )
            return bytes(response), True
        if not chunk:
            raise _binary_truncated_error(
                replay,
                progress_state["consumed"],
                proven=False,
            )
    return bytes(response), False


def _visa_read_chunk(session: object, count: int) -> tuple[bytes, bool]:
    visalib = session.visalib  # type: ignore[attr-defined]
    warning_context: Any = nullcontext()
    try:
        from pyvisa.constants import StatusCode

        ignore_warning = getattr(session, "ignore_warning", None)
        if callable(ignore_warning):
            warning_context = ignore_warning(
                StatusCode.success_device_not_present,
                StatusCode.success_max_count_read,
            )
    except Exception:
        pass
    with warning_context:
        chunk, status = visalib.read(session.session, count)  # type: ignore[attr-defined]
    data = bytes(chunk)
    status_name = str(getattr(status, "name", status)).lower()
    max_count = "success_max_count_read" in status_name
    return data, not max_count


def _binary_truncated_error(
    replay: ReplayPolicy,
    consumed_bytes: int,
    *,
    proven: bool,
) -> TransportIOError:
    return _binary_transport_error(
        "binary_truncated",
        phase=TransportPhase.READING,
        replay=replay,
        transmission=CommandTransmission.SENT,
        progress=ResponseProgress.PARTIAL if consumed_bytes else ResponseProgress.NONE,
        synchronization=(Synchronization.PROVEN if proven else Synchronization.UNPROVEN),
        attempts=1,
        consumed_bytes=consumed_bytes,
    )


def _binary_transport_error(
    reason_code: str | None,
    *,
    phase: TransportPhase,
    replay: ReplayPolicy,
    transmission: CommandTransmission,
    progress: ResponseProgress,
    synchronization: Synchronization,
    attempts: int,
    consumed_bytes: int,
    discarded_bytes: int = 0,
) -> TransportIOError:
    return TransportIOError(
        "bounded binary transport exchange failed",
        operation="query_binary",
        phase=phase,
        replay_policy=replay,
        command_transmission=transmission,
        response_progress=progress,
        synchronization=synchronization,
        attempts=attempts,
        reason_code=reason_code,
        consumed_bytes=consumed_bytes,
        discarded_bytes=discarded_bytes,
    )


def _is_timeout_error(exc: BaseException) -> bool:
    error_code = getattr(exc, "error_code", None)
    error_name = getattr(error_code, "name", "")
    token = f"{type(exc).__name__} {error_code!s} {error_name!s}".lower()
    return "timeout" in token or "tmo" in token


__all__ = [
    "BinaryQueryBudget",
    "BinaryQueryLedger",
    "BinaryQueryReservation",
    "parse_definite_block_response",
    "query_visa_binary_response",
    "visa_message_boundary_supported",
]
