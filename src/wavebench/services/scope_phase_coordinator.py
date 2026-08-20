"""Private operation-context and phase bridge for the Draft scope R1.3 RFC."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from math import ceil
import time
from typing import Iterable, Iterator
from uuid import uuid4

from wavebench.errors import ConfigError
from wavebench.transport.binary import BinaryQueryBudget, BinaryQueryLedger
from wavebench.transport.session import (
    InstrumentSessionState,
    SessionAuthorization,
    SessionHealth,
    SessionPurpose,
    SessionTransactionCoordinator,
)

from .operation_specs import OperationSpec


class OperationPhase(StrEnum):
    PREFLIGHT = "preflight"
    ERROR_BEFORE = "error_before"
    MAIN = "main"
    SUCCESS_RESTORE = "success_restore"
    ERROR_AFTER = "error_after"
    FAILURE_CLEANUP = "failure_cleanup"
    CLEANUP_VERIFICATION = "cleanup_verification"


class ScopePhasePurpose(StrEnum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    VERIFICATION = "verification"


class BaselineUseState(StrEnum):
    FRESH = "fresh"
    PASSED_TO_MAIN = "passed_to_main"
    RESTORE_ATTEMPTED = "restore_attempted"
    VERIFY_ATTEMPTED = "verify_attempted"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


_PHASE_PURPOSE = {
    OperationPhase.PREFLIGHT: ScopePhasePurpose.VERIFICATION,
    OperationPhase.ERROR_BEFORE: ScopePhasePurpose.VERIFICATION,
    OperationPhase.MAIN: ScopePhasePurpose.NORMAL,
    OperationPhase.SUCCESS_RESTORE: ScopePhasePurpose.RECOVERY,
    OperationPhase.ERROR_AFTER: ScopePhasePurpose.VERIFICATION,
    OperationPhase.FAILURE_CLEANUP: ScopePhasePurpose.RECOVERY,
    OperationPhase.CLEANUP_VERIFICATION: ScopePhasePurpose.VERIFICATION,
}
_READ_IO = frozenset({"query", "query_float_list", "query_opc"})
_BINARY_IO = frozenset({"query_binary", "query_bin_block"})
_WRITE_IO = frozenset({"write", "write_bytes"})
_ALL_IO = _READ_IO | _BINARY_IO | _WRITE_IO


def _safe_token(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 96
        or any(not (char.isalnum() or char in "_.:-") for char in value)
    ):
        raise ValueError(f"{label} must be a short safe token")
    return value


def _field_tuple(values: Iterable[str], *, label: str, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be an iterable of fields")
    result = tuple(values)
    if (not result and not allow_empty) or any(
        not isinstance(item, str) or not item or item.strip() != item for item in result
    ):
        raise ValueError(f"{label} contains an invalid field")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class ScopeBinaryLimits:
    response_max_bytes: int
    operation_max_bytes: int
    query_max_count: int
    resynchronization_max_bytes: int

    def __post_init__(self) -> None:
        for label, value in (
            ("response_max_bytes", self.response_max_bytes),
            ("operation_max_bytes", self.operation_max_bytes),
            ("query_max_count", self.query_max_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if (
            isinstance(self.resynchronization_max_bytes, bool)
            or not isinstance(self.resynchronization_max_bytes, int)
            or self.resynchronization_max_bytes < 0
        ):
            raise ValueError("resynchronization_max_bytes must be a non-negative integer")
        if self.operation_max_bytes < self.response_max_bytes:
            raise ValueError("operation byte limit must cover one full response")

    def intersect(self, other: "ScopeBinaryLimits") -> "ScopeBinaryLimits":
        return ScopeBinaryLimits(
            response_max_bytes=min(self.response_max_bytes, other.response_max_bytes),
            operation_max_bytes=min(self.operation_max_bytes, other.operation_max_bytes),
            query_max_count=min(self.query_max_count, other.query_max_count),
            resynchronization_max_bytes=min(
                self.resynchronization_max_bytes,
                other.resynchronization_max_bytes,
            ),
        )

    @classmethod
    def from_spec(cls, spec: OperationSpec) -> "ScopeBinaryLimits | None":
        values = (
            spec.binary_response_max_bytes,
            spec.binary_operation_max_bytes,
            spec.binary_query_max_count,
            spec.binary_resynchronization_max_bytes,
        )
        if values == (None, None, None, None):
            return None
        if any(value is None for value in values):
            raise ValueError("operation spec has an incomplete binary limit set")
        return cls(*values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ScopePhaseAuthorizationSpec:
    context_id: str
    operation_id: str
    phase: OperationPhase
    purpose: ScopePhasePurpose
    allowed_io: frozenset[str]
    fields: frozenset[str]
    deadline: float
    max_steps: int

    def __post_init__(self) -> None:
        _safe_token(self.context_id, label="context_id")
        _safe_token(self.operation_id, label="operation_id")
        object.__setattr__(self, "phase", OperationPhase(self.phase))
        object.__setattr__(self, "purpose", ScopePhasePurpose(self.purpose))
        if self.purpose is not _PHASE_PURPOSE[self.phase]:
            raise ValueError("phase purpose does not match the fixed scope phase mapping")
        allowed_io = frozenset(self.allowed_io)
        fields = frozenset(self.fields)
        if not allowed_io or not allowed_io <= _ALL_IO:
            raise ValueError("phase allowed_io is empty or unsupported")
        if not fields or any(
            not isinstance(item, str) or not item or item.strip() != item for item in fields
        ):
            raise ValueError("phase fields must contain non-empty trimmed names")
        object.__setattr__(self, "allowed_io", allowed_io)
        object.__setattr__(self, "fields", fields)
        if self.phase is not OperationPhase.MAIN and allowed_io & _BINARY_IO:
            raise ValueError("only the main phase can use binary I/O")
        if self.purpose is ScopePhasePurpose.VERIFICATION and allowed_io & _WRITE_IO:
            raise ValueError("verification phases cannot write")
        if isinstance(self.deadline, bool) or not isinstance(self.deadline, (int, float)):
            raise ValueError("phase deadline must be a monotonic timestamp")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps < 1:
            raise ValueError("phase max_steps must be a positive integer")


@dataclass(frozen=True, slots=True, init=False, eq=False)
class ScopePhaseAuthorization:
    """Concrete core-only phase handle; it is never passed to a driver."""

    context_id: str
    operation_id: str
    phase: OperationPhase
    purpose: ScopePhasePurpose
    allowed_io: frozenset[str]
    fields: frozenset[str]
    deadline: float
    max_steps: int
    _session_authorization: SessionAuthorization = field(repr=False, compare=False)
    _owner_nonce: object = field(repr=False, compare=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("scope phase authorizations are coordinator-issued")

    @classmethod
    def _issue(
        cls,
        spec: ScopePhaseAuthorizationSpec,
        session_authorization: SessionAuthorization,
        owner_nonce: object,
    ) -> "ScopePhaseAuthorization":
        instance = object.__new__(cls)
        for name in (
            "context_id",
            "operation_id",
            "phase",
            "purpose",
            "allowed_io",
            "fields",
            "deadline",
            "max_steps",
        ):
            object.__setattr__(instance, name, getattr(spec, name))
        object.__setattr__(instance, "_session_authorization", session_authorization)
        object.__setattr__(instance, "_owner_nonce", owner_nonce)
        return instance


@dataclass(frozen=True, slots=True, init=False, eq=False)
class ScopeBaselineHandle:
    context_id: str
    operation_id: str
    session_epoch: str
    kind: str
    baseline_nonce: str = field(repr=False)
    fields: tuple[str, ...]
    restore_order: tuple[str, ...]
    _owner_nonce: object = field(repr=False, compare=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("scope baseline handles are coordinator-issued")

    @classmethod
    def _issue(
        cls,
        *,
        context_id: str,
        operation_id: str,
        session_epoch: str,
        kind: str,
        baseline_nonce: str,
        fields: tuple[str, ...],
        restore_order: tuple[str, ...],
        owner_nonce: object,
    ) -> "ScopeBaselineHandle":
        instance = object.__new__(cls)
        for name, value in (
            ("context_id", context_id),
            ("operation_id", operation_id),
            ("session_epoch", session_epoch),
            ("kind", kind),
            ("baseline_nonce", baseline_nonce),
            ("fields", fields),
            ("restore_order", restore_order),
            ("_owner_nonce", owner_nonce),
        ):
            object.__setattr__(instance, name, value)
        return instance


@dataclass(slots=True)
class _BaselineRecord:
    handle: ScopeBaselineHandle
    state: BaselineUseState = BaselineUseState.FRESH
    restore_succeeded: bool | None = None
    verification_succeeded: bool | None = None


class ScopeOperationContextCoordinator:
    """One default-off operation context with sequential, non-nested phases."""

    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        spec: OperationSpec,
        connection_timeout_ms: int,
        correlation_id: str | None = None,
        caller_deadline: float | None = None,
        profile_binary_limits: ScopeBinaryLimits | None = None,
        connection_binary_limits: ScopeBinaryLimits | None = None,
        transport_trailing: bytes = b"",
        enabled: bool = False,
        now: float | None = None,
    ) -> None:
        if not enabled:
            raise ConfigError("experimental scope extensions are disabled")
        if spec.instrument_kind != "scope" or spec.operation_timeout_ms is None:
            raise ValueError("scope operation contexts require an explicit scope operation timeout")
        if isinstance(connection_timeout_ms, bool) or not isinstance(
            connection_timeout_ms, int
        ) or connection_timeout_ms < 1:
            raise ValueError("connection_timeout_ms must be a positive integer")
        if session_state.health is not SessionHealth.HEALTHY:
            raise ValueError("new scope operations require a healthy session")
        current = time.monotonic() if now is None else float(now)
        hard_deadline = current + (spec.operation_timeout_ms / 1000.0)
        if caller_deadline is not None:
            if isinstance(caller_deadline, bool) or not isinstance(
                caller_deadline, (int, float)
            ):
                raise ValueError("caller_deadline must be a monotonic timestamp")
            hard_deadline = min(hard_deadline, float(caller_deadline))
        if hard_deadline <= current:
            raise ValueError("scope operation deadline is exhausted")
        reserve_ms = min(5_000, max(1_000, spec.operation_timeout_ms // 5))
        reserve_s = min(reserve_ms / 1000.0, max((hard_deadline - current) / 2.0, 0.0))
        self.context_id = uuid4().hex
        self.operation_id = spec.operation
        self.correlation_id = _safe_token(
            correlation_id or uuid4().hex,
            label="correlation_id",
        )
        self.session_epoch = session_state.epoch_id
        self.deadline = hard_deadline
        self.main_deadline = hard_deadline - reserve_s
        self.cleanup_reserve_ms = int(reserve_s * 1000)
        self.spec = spec
        self.session_state = session_state
        self.session_health_before = session_state.health.value
        self.connection_timeout_ms = connection_timeout_ms
        self._session_coordinator = SessionTransactionCoordinator(session_state)
        self._owner_nonce = object()
        self._active_phase: ScopePhaseAuthorization | None = None
        self._phase_history: list[dict[str, object]] = []
        self._used_phases: set[OperationPhase] = set()
        self._baselines: dict[str, _BaselineRecord] = {}
        self._main_entered = False
        self._cleanup_required = False
        self._cleanup_verified_without_baseline = False
        self._terminal = False

        limits = ScopeBinaryLimits.from_spec(spec)
        if limits is not None and profile_binary_limits is not None:
            limits = limits.intersect(profile_binary_limits)
        if limits is not None and connection_binary_limits is not None:
            limits = limits.intersect(connection_binary_limits)
        self._binary_ledger: BinaryQueryLedger | None = None
        self._binary_budget: BinaryQueryBudget | None = None
        if limits is not None:
            self._binary_ledger = BinaryQueryLedger(
                context_id=self.context_id,
                operation_id=self.operation_id,
                correlation_id=self.correlation_id,
                session_epoch=self.session_epoch,
                deadline=self.deadline,
                per_response_max_bytes=limits.response_max_bytes,
                operation_max_bytes=limits.operation_max_bytes,
                query_max_count=limits.query_max_count,
                resynchronization_max_bytes=limits.resynchronization_max_bytes,
                transport_trailing=transport_trailing,
            )
            self._binary_budget = self._binary_ledger.issue_budget()

    @property
    def binary_ledger(self) -> BinaryQueryLedger | None:
        return self._binary_ledger

    @property
    def terminal(self) -> bool:
        return self._terminal

    def has_phase(self, phase: OperationPhase) -> bool:
        return OperationPhase(phase) in self._used_phases

    def make_phase_spec(
        self,
        phase: OperationPhase,
        *,
        allowed_io: Iterable[str],
        fields: Iterable[str],
        max_steps: int,
        deadline: float | None = None,
    ) -> ScopePhaseAuthorizationSpec:
        phase = OperationPhase(phase)
        ceiling = (
            self.deadline
            if phase
            in {
                OperationPhase.SUCCESS_RESTORE,
                OperationPhase.FAILURE_CLEANUP,
                OperationPhase.CLEANUP_VERIFICATION,
            }
            else self.main_deadline
        )
        chosen_deadline = ceiling if deadline is None else min(float(deadline), ceiling)
        return ScopePhaseAuthorizationSpec(
            context_id=self.context_id,
            operation_id=self.operation_id,
            phase=phase,
            purpose=_PHASE_PURPOSE[phase],
            allowed_io=frozenset(allowed_io),
            fields=frozenset(fields),
            deadline=chosen_deadline,
            max_steps=max_steps,
        )

    @contextmanager
    def authorize_phase(
        self,
        phase_spec: ScopePhaseAuthorizationSpec,
    ) -> Iterator[ScopePhaseAuthorization]:
        self._validate_phase_spec(phase_spec)
        if self._active_phase is not None or self.session_state._active_authorization() is not None:
            raise ValueError("nested scope/session authorizations are not allowed")
        self._validate_phase_order(phase_spec.phase)
        remaining_ms = min(
            self.connection_timeout_ms,
            max(1, ceil((phase_spec.deadline - time.monotonic()) * 1000.0)),
        )
        if phase_spec.phase is OperationPhase.MAIN:
            manager = self._session_coordinator.authorize_normal(
                operation_id=self.operation_id,
                allowed_io=phase_spec.allowed_io,
                fields=phase_spec.fields,
                timeout_ms=remaining_ms,
                max_steps=phase_spec.max_steps,
                context_id=self.context_id,
                correlation_id=self.correlation_id,
                phase=phase_spec.phase.value,
                absolute_deadline=phase_spec.deadline,
                binary_budget=self._binary_budget,
            )
        else:
            evidence_fields = None
            if phase_spec.purpose is ScopePhasePurpose.VERIFICATION and phase_spec.phase not in {
                OperationPhase.ERROR_BEFORE,
                OperationPhase.ERROR_AFTER,
            }:
                evidence_fields = {
                    io_kind: phase_spec.fields
                    for io_kind in phase_spec.allowed_io
                    if io_kind in _READ_IO
                }
            manager = self._session_coordinator.authorize(
                operation_id=self.operation_id,
                purpose=SessionPurpose(phase_spec.purpose.value),
                allowed_io=phase_spec.allowed_io,
                fields=phase_spec.fields,
                timeout_ms=remaining_ms,
                max_steps=phase_spec.max_steps,
                evidence_fields=evidence_fields,
                context_id=self.context_id,
                correlation_id=self.correlation_id,
                phase=phase_spec.phase.value,
                absolute_deadline=phase_spec.deadline,
            )
        budget_before = self._binary_ledger.snapshot() if self._binary_ledger else None
        status = "failed"
        with manager as session_authorization:
            authorization = ScopePhaseAuthorization._issue(
                phase_spec,
                session_authorization,
                self._owner_nonce,
            )
            self._active_phase = authorization
            self._used_phases.add(phase_spec.phase)
            if phase_spec.phase is OperationPhase.MAIN:
                self._main_entered = True
            try:
                yield authorization
                status = "completed"
            finally:
                if phase_spec.phase is OperationPhase.MAIN:
                    self._session_coordinator.invalidate_verified_fields(
                        self.spec.changed_fields
                    )
                self._active_phase = None
                self._phase_history.append(
                    {
                        "phase": phase_spec.phase.value,
                        "purpose": phase_spec.purpose.value,
                        "allowed_io": sorted(phase_spec.allowed_io),
                        "fields": sorted(phase_spec.fields),
                        "max_steps": phase_spec.max_steps,
                        "actual_steps": session_authorization._record.successful_steps,
                        "status": status,
                        "budget_before": budget_before,
                        "budget_after": (
                            self._binary_ledger.snapshot() if self._binary_ledger else None
                        ),
                    }
                )

    def create_baseline(
        self,
        *,
        kind: str,
        fields: Iterable[str],
        restore_order: Iterable[str],
    ) -> ScopeBaselineHandle:
        self._require_phase(OperationPhase.PREFLIGHT)
        normalized_fields = _field_tuple(fields, label="baseline fields")
        normalized_order = _field_tuple(restore_order, label="restore_order")
        if set(normalized_fields) != set(normalized_order):
            raise ValueError("baseline restore order must cover its fields exactly")
        if not set(normalized_fields) <= self._field_universe():
            raise ValueError("baseline fields exceed the operation specification")
        nonce = uuid4().hex
        handle = ScopeBaselineHandle._issue(
            context_id=self.context_id,
            operation_id=self.operation_id,
            session_epoch=self.session_epoch,
            kind=_safe_token(kind, label="baseline kind"),
            baseline_nonce=nonce,
            fields=normalized_fields,
            restore_order=normalized_order,
            owner_nonce=self._owner_nonce,
        )
        self._baselines[nonce] = _BaselineRecord(handle=handle)
        return handle

    def pass_baseline_to_main(self, handle: ScopeBaselineHandle) -> None:
        self._require_phase(OperationPhase.PREFLIGHT)
        record = self._baseline_record(handle)
        if record.state is not BaselineUseState.FRESH:
            raise ValueError("baseline is not fresh")
        record.state = BaselineUseState.PASSED_TO_MAIN

    def begin_restore(self, handle: ScopeBaselineHandle) -> None:
        if self._active_phase is None or self._active_phase.phase not in {
            OperationPhase.SUCCESS_RESTORE,
            OperationPhase.FAILURE_CLEANUP,
        }:
            raise ValueError("baseline restore is outside a restore phase")
        record = self._baseline_record(handle)
        if record.state is not BaselineUseState.PASSED_TO_MAIN:
            raise ValueError("baseline restore slot is already consumed or unavailable")
        record.state = BaselineUseState.RESTORE_ATTEMPTED

    def finish_restore(self, handle: ScopeBaselineHandle, *, succeeded: bool) -> None:
        record = self._baseline_record(handle)
        if record.state is not BaselineUseState.RESTORE_ATTEMPTED:
            raise ValueError("baseline restore was not attempted")
        if record.restore_succeeded is not None:
            raise ValueError("baseline restore outcome is already recorded")
        if not isinstance(succeeded, bool):
            raise TypeError("restore outcome must be bool")
        record.restore_succeeded = succeeded

    def begin_verification(self, handle: ScopeBaselineHandle) -> None:
        self._require_phase(OperationPhase.CLEANUP_VERIFICATION)
        record = self._baseline_record(handle)
        if record.state is not BaselineUseState.RESTORE_ATTEMPTED:
            raise ValueError("baseline verification requires exactly one prior restore attempt")
        if record.restore_succeeded is None:
            raise ValueError("baseline restore outcome has not been recorded")
        record.state = BaselineUseState.VERIFY_ATTEMPTED

    def finish_verification(
        self,
        handle: ScopeBaselineHandle,
        authorization: ScopePhaseAuthorization,
        *,
        io_kind: str,
        verified_fields: Iterable[str],
        matched: bool,
    ) -> None:
        self._require_authorization(authorization, OperationPhase.CLEANUP_VERIFICATION)
        record = self._baseline_record(handle)
        if record.state is not BaselineUseState.VERIFY_ATTEMPTED:
            raise ValueError("baseline verify slot is not active")
        fields = frozenset(_field_tuple(verified_fields, label="verified_fields"))
        complete = fields == set(handle.fields)
        if not isinstance(matched, bool):
            raise TypeError("verification match result must be bool")
        candidate_succeeded = bool(record.restore_succeeded and matched and complete)
        verification_succeeded = False
        try:
            if candidate_succeeded:
                self._session_coordinator.record_evidence(
                    authorization._session_authorization,
                    io_kind,
                    fields,
                )
                self._session_coordinator.complete_verification(
                    authorization._session_authorization
                )
                verification_succeeded = True
        finally:
            record.verification_succeeded = verification_succeeded
            record.state = BaselineUseState.CONSUMED
        if not verification_succeeded:
            raise ValueError("baseline restoration verification is incomplete or mismatched")

    def complete_phase_verification(
        self,
        authorization: ScopePhaseAuthorization,
        *,
        io_kind: str,
        fields: Iterable[str],
    ) -> None:
        """Commit fresh readback evidence for a non-baseline verification phase."""

        if not isinstance(authorization, ScopePhaseAuthorization):
            raise TypeError("scope phase authorization has an invalid type")
        if (
            authorization._owner_nonce is not self._owner_nonce
            or self._active_phase is not authorization
            or authorization.purpose is not ScopePhasePurpose.VERIFICATION
        ):
            raise ValueError("verification phase authorization is inactive")
        verified = frozenset(_field_tuple(fields, label="verified fields"))
        if not verified <= authorization.fields:
            raise ValueError("verified fields exceed the phase authorization")
        self._session_coordinator.record_evidence(
            authorization._session_authorization,
            io_kind,
            verified,
        )
        self._session_coordinator.complete_verification(
            authorization._session_authorization
        )
        if authorization.phase is OperationPhase.CLEANUP_VERIFICATION:
            self._cleanup_verified_without_baseline = True

    def consume_baseline_after_success(self, handle: ScopeBaselineHandle) -> None:
        if not self._main_entered or self._active_phase is not None:
            raise ValueError("successful baseline consumption requires a closed main phase")
        record = self._baseline_record(handle)
        if record.state is not BaselineUseState.PASSED_TO_MAIN:
            raise ValueError("baseline cannot be consumed from its current state")
        record.state = BaselineUseState.CONSUMED

    def mark_cleanup_required(self) -> None:
        if self._active_phase is not None or not self._main_entered:
            raise ValueError("cleanup can only be required after the main phase closes")
        if self._cleanup_required:
            raise ValueError("operation cleanup is already required")
        self._cleanup_required = True
        if self._binary_ledger is not None:
            self._binary_ledger.invalidate()
        if self.session_state.health is SessionHealth.HEALTHY:
            self.session_state.degrade(
                SessionHealth.UNCERTAIN,
                reason="scope_cleanup_required",
            )

    def complete(self) -> None:
        if self._active_phase is not None:
            raise ValueError("operation context cannot terminate with an active phase")
        if self._terminal:
            return
        incomplete = [
            record
            for record in self._baselines.values()
            if record.state not in {BaselineUseState.CONSUMED, BaselineUseState.INVALIDATED}
        ]
        verified = (
            all(record.verification_succeeded is True for record in self._baselines.values())
            if self._baselines
            else self._cleanup_verified_without_baseline
        )
        if self._cleanup_required and (incomplete or not verified):
            if self.session_state.health in {SessionHealth.HEALTHY, SessionHealth.UNCERTAIN}:
                self.session_state.degrade(
                    SessionHealth.POISONED,
                    reason="scope_cleanup_incomplete",
                )
        if self._binary_ledger is not None:
            self._binary_ledger.invalidate()
        for record in self._baselines.values():
            if record.state is not BaselineUseState.CONSUMED:
                record.state = BaselineUseState.INVALIDATED
        self._terminal = True

    def artifact(self) -> dict[str, object]:
        return {
            "operation": self.operation_id,
            "correlation_id": self.correlation_id,
            "context_id": self.context_id,
            "session_epoch": self.session_epoch,
            "session_health_before": self.session_health_before,
            "session_health_after": self.session_state.health.value,
            "deadline_source": self.spec.timeout_source,
            "cleanup_reserve_ms": self.cleanup_reserve_ms,
            "phases": [dict(item) for item in self._phase_history],
            "binary_budget": self._binary_ledger.snapshot() if self._binary_ledger else None,
            "baselines": [
                {
                    "kind": record.handle.kind,
                    "context_id": record.handle.context_id,
                    "session_epoch": record.handle.session_epoch,
                    "nonce_digest": sha256(
                        record.handle.baseline_nonce.encode("ascii")
                    ).hexdigest()[:16],
                    "fields": list(record.handle.fields),
                    "restore_order": list(record.handle.restore_order),
                    "consumption": record.state.value,
                    "restore_succeeded": record.restore_succeeded,
                    "verification_succeeded": record.verification_succeeded,
                }
                for record in self._baselines.values()
            ],
            "terminal": self._terminal,
        }

    def _validate_phase_spec(self, phase_spec: ScopePhaseAuthorizationSpec) -> None:
        if not isinstance(phase_spec, ScopePhaseAuthorizationSpec):
            raise TypeError("phase authorization spec has an invalid type")
        if self._terminal:
            raise ValueError("scope operation context is terminal")
        if self.session_state.epoch_id != self.session_epoch:
            self.complete()
            raise ValueError("scope operation context belongs to another session epoch")
        if (
            phase_spec.context_id != self.context_id
            or phase_spec.operation_id != self.operation_id
        ):
            raise ValueError("phase authorization belongs to another operation context")
        ceiling = (
            self.deadline
            if phase_spec.phase
            in {
                OperationPhase.SUCCESS_RESTORE,
                OperationPhase.FAILURE_CLEANUP,
                OperationPhase.CLEANUP_VERIFICATION,
            }
            else self.main_deadline
        )
        if phase_spec.deadline > ceiling or phase_spec.deadline <= time.monotonic():
            raise ValueError("phase deadline exceeds or exhausts the operation deadline")
        if not phase_spec.fields <= self._field_universe():
            raise ValueError("phase fields exceed the operation specification")
        if phase_spec.allowed_io & _BINARY_IO and self._binary_budget is None:
            raise ValueError("phase requests binary I/O without an operation budget")

    def _validate_phase_order(self, phase: OperationPhase) -> None:
        if phase in self._used_phases:
            raise ValueError("scope operation phases are single use")
        if phase is OperationPhase.PREFLIGHT and self._used_phases:
            raise ValueError("preflight must be the first scope operation phase")
        if phase is not OperationPhase.PREFLIGHT and (
            OperationPhase.PREFLIGHT not in self._used_phases
        ):
            raise ValueError("scope operation main/error phases require preflight")
        if phase is OperationPhase.ERROR_BEFORE and self._main_entered:
            raise ValueError("error_before cannot run after the main phase")
        if phase is OperationPhase.MAIN and OperationPhase.ERROR_AFTER in self._used_phases:
            raise ValueError("main cannot run after error_after")
        if phase in {
            OperationPhase.ERROR_AFTER,
            OperationPhase.SUCCESS_RESTORE,
            OperationPhase.FAILURE_CLEANUP,
        } and not self._main_entered:
            raise ValueError("post-main phases require an entered main phase")
        if phase in {OperationPhase.SUCCESS_RESTORE, OperationPhase.FAILURE_CLEANUP} and (
            {
                OperationPhase.SUCCESS_RESTORE,
                OperationPhase.FAILURE_CLEANUP,
            }
            & self._used_phases
        ):
            raise ValueError("an operation can enter only one restore phase")
        if phase is OperationPhase.ERROR_AFTER and (
            {
                OperationPhase.SUCCESS_RESTORE,
                OperationPhase.FAILURE_CLEANUP,
            }
            & self._used_phases
        ):
            raise ValueError("error_after cannot run after restore begins")
        if phase is OperationPhase.MAIN and (
            OperationPhase.ERROR_AFTER in self._used_phases
            or OperationPhase.SUCCESS_RESTORE in self._used_phases
            or OperationPhase.FAILURE_CLEANUP in self._used_phases
        ):
            raise ValueError("main cannot run after a cleanup phase")
        if phase is OperationPhase.CLEANUP_VERIFICATION and not (
            {
                OperationPhase.SUCCESS_RESTORE,
                OperationPhase.FAILURE_CLEANUP,
            }
            & self._used_phases
        ):
            raise ValueError("cleanup verification requires one restore phase")

    def _field_universe(self) -> frozenset[str]:
        return frozenset(
            (
                *self.spec.changed_fields,
                *self.spec.required_verified_fields,
                *self.spec.verification_fields,
                *self.spec.postcondition_fields,
                *self.spec.cleanup_verification_fields,
            )
        )

    def _baseline_record(self, handle: ScopeBaselineHandle) -> _BaselineRecord:
        if not isinstance(handle, ScopeBaselineHandle):
            raise TypeError("baseline handle has an invalid type")
        if handle._owner_nonce is not self._owner_nonce:
            raise ValueError("baseline handle is not owned by this context")
        if (
            handle.context_id != self.context_id
            or handle.operation_id != self.operation_id
            or handle.session_epoch != self.session_epoch
        ):
            raise ValueError("baseline handle binding does not match the operation context")
        record = self._baselines.get(handle.baseline_nonce)
        if record is None or record.handle is not handle:
            raise ValueError("baseline nonce is unknown or replayed")
        return record

    def _require_phase(self, phase: OperationPhase) -> None:
        if self._active_phase is None or self._active_phase.phase is not phase:
            raise ValueError(f"operation requires active phase {phase.value}")

    def _require_authorization(
        self,
        authorization: ScopePhaseAuthorization,
        phase: OperationPhase,
    ) -> None:
        if (
            not isinstance(authorization, ScopePhaseAuthorization)
            or authorization._owner_nonce is not self._owner_nonce
            or self._active_phase is not authorization
            or authorization.phase is not phase
        ):
            raise ValueError("scope phase authorization is inactive or belongs to another context")


__all__ = [
    "BaselineUseState",
    "OperationPhase",
    "ScopeBaselineHandle",
    "ScopeBinaryLimits",
    "ScopeOperationContextCoordinator",
    "ScopePhaseAuthorization",
    "ScopePhaseAuthorizationSpec",
    "ScopePhasePurpose",
]
