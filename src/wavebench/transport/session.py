from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
import re
import time
from typing import Any, Iterable, Iterator
from uuid import uuid4


class SessionHealth(StrEnum):
    HEALTHY = "healthy"
    UNCERTAIN = "uncertain"
    POISONED = "poisoned"
    CLOSED = "closed"


class SessionPurpose(StrEnum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    VERIFICATION = "verification"
    LIFECYCLE = "lifecycle"


_HEALTH_RANK = {
    SessionHealth.HEALTHY: 0,
    SessionHealth.UNCERTAIN: 1,
    SessionHealth.POISONED: 2,
    SessionHealth.CLOSED: 3,
}
_AUTHORIZED_IO = frozenset(
    {
        "query",
        "query_float_list",
        "query_bin_block",
        "query_opc",
        "write",
        "write_bytes",
    }
)
_VERIFICATION_IO = frozenset(
    {"query", "query_float_list", "query_bin_block", "query_opc"}
)
_SAFE_REASON = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def _normalize_field_set(
    values: Iterable[str], *, label: str, allow_empty: bool = False
) -> frozenset[str]:
    """Normalize a field collection without silently accepting bad input."""

    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be an iterable of field names, not a string")
    normalized = frozenset(values)
    if (not normalized and not allow_empty) or any(
        not isinstance(value, str) or not value or value.strip() != value
        for value in normalized
    ):
        raise ValueError(f"{label} must contain non-empty, trimmed field names")
    return normalized


@dataclass
class _AuthorizationRecord:
    epoch_id: str
    operation_id: str
    purpose: SessionPurpose
    allowed_io: frozenset[str]
    fields: frozenset[str]
    deadline: float
    remaining_steps: int
    evidence_fields: dict[str, frozenset[str]]
    successful_steps: int = 0
    successful_io: dict[str, int] = field(default_factory=dict)
    successful_fields: set[str] = field(default_factory=set)
    active: bool = True
    completed: bool = False


@dataclass(frozen=True, slots=True, init=False, eq=False)
class SessionAuthorization:
    """Opaque, bounded capability installed by the core coordinator."""

    _record: _AuthorizationRecord = field(repr=False, compare=False)
    _nonce: object = field(repr=False, compare=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("session authorizations are issued by SessionTransactionCoordinator")

    @classmethod
    def _issue(
        cls,
        record: _AuthorizationRecord,
        nonce: object,
    ) -> "SessionAuthorization":
        instance = object.__new__(cls)
        object.__setattr__(instance, "_record", record)
        object.__setattr__(instance, "_nonce", nonce)
        return instance

    @property
    def epoch_id(self) -> str:
        return self._record.epoch_id

    @property
    def operation_id(self) -> str:
        return self._record.operation_id

    @property
    def purpose(self) -> SessionPurpose:
        return self._record.purpose

    @property
    def allowed_io(self) -> frozenset[str]:
        return self._record.allowed_io

    @property
    def fields(self) -> frozenset[str]:
        return self._record.fields


class InstrumentSessionState:
    """Health and verified-field state bound to one concrete connection epoch."""

    __slots__ = (
        "_epoch_id",
        "_health",
        "_verified_fields",
        "_last_transition",
        "transaction_lock",
        "_authorization_context",
        "_authorization_nonce",
    )

    def __init__(
        self,
        epoch_id: str | None = None,
        health: SessionHealth = SessionHealth.HEALTHY,
        verified_fields: Iterable[str] | None = None,
    ) -> None:
        self._epoch_id = epoch_id or uuid4().hex
        self._health = SessionHealth(health)
        self._verified_fields = set(
            _normalize_field_set(
                verified_fields,
                label="verified_fields",
                allow_empty=True,
            )
            if verified_fields is not None
            else ()
        )
        if self._health is not SessionHealth.HEALTHY:
            self._verified_fields.clear()
        self._last_transition: dict[str, Any] | None = None
        self.transaction_lock = RLock()
        self._authorization_context: ContextVar[SessionAuthorization | None] = ContextVar(
            f"wavebench_session_authorization_{uuid4().hex}",
            default=None,
        )
        self._authorization_nonce = object()

    @property
    def epoch_id(self) -> str:
        return self._epoch_id

    @property
    def health(self) -> SessionHealth:
        return self._health

    @property
    def verified_fields(self) -> frozenset[str]:
        return frozenset(self._verified_fields)

    @property
    def last_transition(self) -> dict[str, Any] | None:
        with self.transaction_lock:
            return dict(self._last_transition) if self._last_transition is not None else None

    def snapshot(self) -> dict[str, object]:
        with self.transaction_lock:
            return {
                "epoch_id": self.epoch_id,
                "health": self.health.value,
                "verified_fields": sorted(self._verified_fields),
                "last_transition": dict(self._last_transition)
                if self._last_transition is not None
                else None,
            }

    def degrade(self, health: SessionHealth, *, reason: str) -> None:
        """Move to an equally or more conservative health state."""

        with self.transaction_lock:
            if not isinstance(reason, str) or _SAFE_REASON.fullmatch(reason) is None:
                raise ValueError("session transition reason must be a short safe code")
            health = SessionHealth(health)
            if _HEALTH_RANK[health] < _HEALTH_RANK[self._health]:
                raise ValueError(
                    f"session health cannot improve through degrade: {self._health} -> {health}"
                )
            previous = self._health
            self._health = health
            if health is not SessionHealth.HEALTHY:
                self._verified_fields.clear()
            self._last_transition = {
                "from": previous.value,
                "to": health.value,
                "reason": reason,
            }

    def close(self) -> None:
        """Make this connection epoch terminal before its backend is closed."""

        with self.transaction_lock:
            if self._health is SessionHealth.CLOSED:
                return
            self.degrade(SessionHealth.CLOSED, reason="connection_closed")

    def _complete_verification(
        self,
        fields: Iterable[str],
        *,
        reason: str,
        _issuer: object | None = None,
    ) -> None:
        """Internal coordinator hook; normal services must not call this."""

        if _issuer is not self._authorization_nonce:
            raise ValueError("verification completion is coordinator-owned")
        normalized = _normalize_field_set(fields, label="verified fields")
        if _SAFE_REASON.fullmatch(reason) is None:
            raise ValueError("session transition reason must be a short safe code")
        with self.transaction_lock:
            if self._health not in {SessionHealth.HEALTHY, SessionHealth.UNCERTAIN}:
                raise ValueError(f"cannot verify a {self._health.value} session")
            previous = self._health
            self._health = SessionHealth.HEALTHY
            self._verified_fields.update(normalized)
            self._last_transition = {
                "from": previous.value,
                "to": SessionHealth.HEALTHY.value,
                "reason": reason,
            }

    def _active_authorization(self) -> SessionAuthorization | None:
        return self._authorization_context.get()

    def _validate_authorization(self, authorization: SessionAuthorization) -> None:
        if authorization._nonce is not self._authorization_nonce:
            raise ValueError("session authorization is not owned by this session")
        if authorization._record.epoch_id != self.epoch_id:
            raise ValueError("session authorization belongs to a different connection epoch")

    def _consume_authorization(self, io_kind: str) -> SessionAuthorization | None:
        """Consume one bounded I/O step; caller must hold ``transaction_lock``."""

        authorization = self._authorization_context.get()
        if authorization is None:
            return None
        record = authorization._record
        self._validate_authorization(authorization)
        if not record.active:
            raise ValueError("session authorization is no longer active")
        if record.completed:
            raise ValueError("session authorization is already complete")
        if record.epoch_id != self.epoch_id:
            raise ValueError("session authorization belongs to a different connection epoch")
        if time.monotonic() > record.deadline:
            raise ValueError("session authorization expired")
        if io_kind not in record.allowed_io:
            raise ValueError(f"session authorization does not allow {io_kind}")
        if record.remaining_steps < 1:
            raise ValueError("session authorization step bound is exhausted")
        record.remaining_steps -= 1
        return authorization

    def _record_authorized_success(
        self,
        authorization: SessionAuthorization,
        io_kind: str,
    ) -> None:
        """Record a successful exchange for the active core authorization."""

        if self._active_authorization() is not authorization:
            raise ValueError("authorization is not active for this session")
        self._validate_authorization(authorization)
        if not authorization._record.active or authorization._record.completed:
            raise ValueError("session authorization is no longer active")
        authorization._record.successful_steps += 1
        authorization._record.successful_io[io_kind] = (
            authorization._record.successful_io.get(io_kind, 0) + 1
        )

    def _record_authorized_evidence(
        self,
        authorization: SessionAuthorization,
        io_kind: str,
        fields: Iterable[str],
    ) -> None:
        """Record fields after a core verifier checked the returned value."""

        if self._active_authorization() is not authorization:
            raise ValueError("authorization is not active for this session")
        self._validate_authorization(authorization)
        record = authorization._record
        if not record.active or record.completed:
            raise ValueError("session authorization is no longer active")
        if io_kind not in _VERIFICATION_IO:
            raise ValueError("verification evidence must use a read-only transport operation")
        if record.successful_io.get(io_kind, 0) < 1:
            raise ValueError("verification evidence requires a successful matching read")
        normalized = _normalize_field_set(fields, label="evidence fields")
        allowed = record.evidence_fields.get(io_kind, frozenset())
        if not normalized <= allowed:
            raise ValueError("evidence fields exceed the authorization evidence scope")
        record.successful_fields.update(normalized)


@dataclass(frozen=True)
class SessionTransactionCoordinator:
    """Core-owned coordinator for bounded recovery and verification I/O."""

    state: InstrumentSessionState

    @contextmanager
    def authorize(
        self,
        *,
        operation_id: str,
        purpose: SessionPurpose,
        allowed_io: Iterable[str],
        fields: Iterable[str],
        timeout_ms: int,
        max_steps: int,
        evidence_fields: dict[str, Iterable[str]] | None = None,
    ) -> Iterator[SessionAuthorization]:
        if purpose not in {SessionPurpose.RECOVERY, SessionPurpose.VERIFICATION}:
            raise ValueError("only recovery or verification can receive session authorization")
        if not operation_id or _SAFE_REASON.fullmatch(operation_id) is None:
            raise ValueError("operation_id must be a short safe code")
        normalized_io = frozenset(allowed_io)
        if not normalized_io or not normalized_io <= _AUTHORIZED_IO:
            raise ValueError("allowed_io contains an unsupported transport operation")
        normalized_fields = _normalize_field_set(fields, label="authorized fields")
        normalized_evidence: dict[str, frozenset[str]] = {}
        for io_kind, io_fields in (evidence_fields or {}).items():
            if io_kind not in normalized_io:
                raise ValueError("evidence_fields contains an unauthorized I/O kind")
            if purpose is SessionPurpose.VERIFICATION and io_kind not in _VERIFICATION_IO:
                raise ValueError("verification evidence must use a read-only transport operation")
            evidence = _normalize_field_set(io_fields, label="evidence fields")
            if not evidence <= normalized_fields:
                raise ValueError("evidence fields exceed the authorized field scope")
            normalized_evidence[io_kind] = evidence
        if timeout_ms < 1:
            raise ValueError("authorization timeout_ms must be >= 1")
        if max_steps < 1:
            raise ValueError("authorization max_steps must be >= 1")

        # Hold the operation lock for the complete dynamic authorization range.
        with self.state.transaction_lock:
            if self.state.health is SessionHealth.POISONED:
                raise ValueError("cannot authorize I/O on a poisoned session")
            if self.state.health is SessionHealth.CLOSED:
                raise ValueError("cannot authorize I/O on a closed session")
            if self.state._active_authorization() is not None:
                raise ValueError("nested session authorizations are not allowed")
            record = _AuthorizationRecord(
                epoch_id=self.state.epoch_id,
                operation_id=operation_id,
                purpose=purpose,
                allowed_io=normalized_io,
                fields=normalized_fields,
                evidence_fields=normalized_evidence,
                deadline=time.monotonic() + (timeout_ms / 1000.0),
                remaining_steps=max_steps,
            )
            authorization = SessionAuthorization._issue(record, self.state._authorization_nonce)
            token: Token[SessionAuthorization | None] = self.state._authorization_context.set(
                authorization
            )
            try:
                yield authorization
            finally:
                record.active = False
                self.state._authorization_context.reset(token)

    def complete_verification(
        self,
        authorization: SessionAuthorization,
    ) -> None:
        """Return ``uncertain`` to ``healthy`` only after bounded I/O succeeded."""

        with self.state.transaction_lock:
            if self.state._active_authorization() is not authorization:
                raise ValueError("verification authorization is not active")
            self.state._validate_authorization(authorization)
            if authorization.purpose is not SessionPurpose.VERIFICATION:
                raise ValueError("recovery authorization cannot complete verification")
            record = authorization._record
            if not record.active:
                raise ValueError("verification authorization is no longer active")
            if record.completed:
                raise ValueError("verification authorization is already complete")
            if time.monotonic() > record.deadline:
                raise ValueError("verification authorization expired")
            if not authorization.fields <= record.successful_fields:
                missing = ", ".join(
                    sorted(authorization.fields - record.successful_fields)
                )
                raise ValueError(f"verification did not cover fields: {missing}")
            record.completed = True
            self.state._complete_verification(
                authorization.fields,
                reason=f"verification_completed:{authorization.operation_id}",
                _issuer=self.state._authorization_nonce,
            )

    def record_evidence(
        self,
        authorization: SessionAuthorization,
        io_kind: str,
        fields: Iterable[str],
    ) -> None:
        """Mark independently validated fields for an active verification token."""

        with self.state.transaction_lock:
            if authorization.purpose is not SessionPurpose.VERIFICATION:
                raise ValueError("only verification authorization can record evidence")
            self.state._record_authorized_evidence(authorization, io_kind, fields)


__all__ = [
    "InstrumentSessionState",
    "SessionAuthorization",
    "SessionHealth",
    "SessionPurpose",
    "SessionTransactionCoordinator",
]
