"""Core-owned Source V2 mutation operation contexts.

The module intentionally does not register a capability, open a driver, or
invoke a Source method.  It binds a future feature-specific Source write to the
existing ``InstrumentSessionState`` / ``SessionTransactionCoordinator`` safety
machinery.  The coordinator is internal: drivers receive neither its handles
nor its authorizations.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from math import ceil, isfinite
import time
from typing import Iterable, Iterator
from uuid import uuid4

from wavebench.errors import ConfigError
from wavebench.instruments.source_extensions import (
    SOURCE_OPERATION_ARTIFACT_SCHEMA,
    SourceAffectedClosure,
    SourceEnergyEffect,
    SourceFieldId,
    SourceFieldRef,
    SourceOperationContract,
    SourceScopeRef,
    SourceStorageEffect,
    source_v2_digest,
)
from wavebench.transport.session import (
    InstrumentSessionState,
    SessionAuthorization,
    SessionHealth,
    SessionPurpose,
    SessionTransactionCoordinator,
)

from .operation_specs import OperationSpec


class SourceOperationPhase(StrEnum):
    PREFLIGHT = "preflight"
    MAIN = "main"
    POSTCONDITION = "postcondition"
    FAILURE_SAFE_STATE = "failure_safe_state"
    FAILURE_RESTORE = "failure_restore"
    CLEANUP_VERIFICATION = "cleanup_verification"


class SourcePhasePurpose(StrEnum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    VERIFICATION = "verification"


class SourceBaselineUseState(StrEnum):
    FRESH = "fresh"
    PASSED_TO_MAIN = "passed_to_main"
    RESTORE_ATTEMPTED = "restore_attempted"
    VERIFY_ATTEMPTED = "verify_attempted"
    CONSUMED = "consumed"
    INVALIDATED = "invalidated"


_PHASE_PURPOSE = {
    SourceOperationPhase.PREFLIGHT: SourcePhasePurpose.VERIFICATION,
    SourceOperationPhase.MAIN: SourcePhasePurpose.NORMAL,
    SourceOperationPhase.POSTCONDITION: SourcePhasePurpose.VERIFICATION,
    SourceOperationPhase.FAILURE_SAFE_STATE: SourcePhasePurpose.RECOVERY,
    SourceOperationPhase.FAILURE_RESTORE: SourcePhasePurpose.RECOVERY,
    SourceOperationPhase.CLEANUP_VERIFICATION: SourcePhasePurpose.VERIFICATION,
}
_READ_IO = frozenset({"query", "query_float_list", "query_opc"})
_WRITE_IO = frozenset({"write", "write_bytes"})
_ALL_IO = _READ_IO | _WRITE_IO
_NON_REENERGIZING_RESTORE_FIELDS = frozenset(
    {
        SourceFieldId.OUTPUT,
        SourceFieldId.ARM_STATE,
        SourceFieldId.TRIGGER_STATE,
    }
)


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


def _field_sort_key(value: SourceFieldRef) -> tuple[object, ...]:
    target = value.target
    return (
        value.field.value,
        target.scope.value,
        -1 if target.channel is None else target.channel,
        target.channels,
        "" if target.input_id is None else target.input_id,
    )


def _field_refs(
    values: Iterable[SourceFieldRef],
    *,
    label: str,
    allow_empty: bool = False,
    sorted_values: bool = True,
) -> tuple[SourceFieldRef, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{label} must be an iterable of SourceFieldRef values")
    result = tuple(values)
    if (not result and not allow_empty) or any(
        not isinstance(item, SourceFieldRef) for item in result
    ):
        raise ValueError(f"{label} must contain SourceFieldRef values")
    keys = tuple(_field_sort_key(item) for item in result)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must not contain duplicates")
    if sorted_values and tuple(sorted(keys)) != keys:
        raise ValueError(f"{label} must be sorted")
    return result


def _field_keys(values: Iterable[SourceFieldRef]) -> frozenset[str]:
    return frozenset(source_v2_digest(value) for value in values)


def _output_fields(outputs: Iterable[SourceScopeRef]) -> tuple[SourceFieldRef, ...]:
    result = tuple(SourceFieldRef(SourceFieldId.OUTPUT, output) for output in outputs)
    return tuple(sorted(result, key=_field_sort_key))


@dataclass(frozen=True, slots=True)
class SourcePhaseAuthorizationSpec:
    """One bounded, core-issued phase specification.

    It is intentionally not part of ``wavebench.instruments.source_extensions``
    and no driver accepts it as an argument.
    """

    context_id: str
    operation_id: str
    phase: SourceOperationPhase
    purpose: SourcePhasePurpose
    allowed_io: frozenset[str]
    fields: frozenset[SourceFieldRef]
    deadline: float
    max_steps: int

    def __post_init__(self) -> None:
        _safe_token(self.context_id, label="context_id")
        _safe_token(self.operation_id, label="operation_id")
        object.__setattr__(self, "phase", SourceOperationPhase(self.phase))
        object.__setattr__(self, "purpose", SourcePhasePurpose(self.purpose))
        if self.purpose is not _PHASE_PURPOSE[self.phase]:
            raise ValueError("source phase purpose does not match the fixed phase mapping")
        allowed_io = frozenset(self.allowed_io)
        fields = frozenset(self.fields)
        if not allowed_io or not allowed_io <= _ALL_IO:
            raise ValueError("source phase allowed_io is empty or unsupported")
        if not fields or any(not isinstance(item, SourceFieldRef) for item in fields):
            raise ValueError("source phase fields must contain SourceFieldRef values")
        object.__setattr__(self, "allowed_io", allowed_io)
        object.__setattr__(self, "fields", fields)
        if self.purpose is SourcePhasePurpose.VERIFICATION and allowed_io & _WRITE_IO:
            raise ValueError("source verification phases cannot write")
        if self.phase in {
            SourceOperationPhase.FAILURE_SAFE_STATE,
            SourceOperationPhase.FAILURE_RESTORE,
        } and allowed_io - _WRITE_IO:
            raise ValueError("source recovery write phases cannot issue reads")
        if isinstance(self.deadline, bool) or not isinstance(self.deadline, (int, float)):
            raise ValueError("source phase deadline must be a finite monotonic timestamp")
        if not isfinite(self.deadline):
            raise ValueError("source phase deadline must be a finite monotonic timestamp")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps < 1:
            raise ValueError("source phase max_steps must be a positive integer")


@dataclass(frozen=True, slots=True, init=False, eq=False)
class SourcePhaseAuthorization:
    """Opaque context-owned bridge to the session authorization token."""

    context_id: str
    operation_id: str
    phase: SourceOperationPhase
    purpose: SourcePhasePurpose
    allowed_io: frozenset[str]
    fields: frozenset[SourceFieldRef]
    deadline: float
    max_steps: int
    _session_authorization: SessionAuthorization = field(repr=False, compare=False)
    _owner_nonce: object = field(repr=False, compare=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("source phase authorizations are coordinator-issued")

    @classmethod
    def _issue(
        cls,
        spec: SourcePhaseAuthorizationSpec,
        session_authorization: SessionAuthorization,
        owner_nonce: object,
    ) -> "SourcePhaseAuthorization":
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
class SourceBaselineHandle:
    """Opaque core baseline identity; its nonce is never serialized in artifacts."""

    context_id: str
    operation_id: str
    session_epoch: str
    closure_digest: str
    baseline_nonce: str = field(repr=False)
    fields: tuple[SourceFieldRef, ...]
    restore_order: tuple[SourceFieldRef, ...]
    _owner_nonce: object = field(repr=False, compare=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("source baseline handles are coordinator-issued")

    @classmethod
    def _issue(
        cls,
        *,
        context_id: str,
        operation_id: str,
        session_epoch: str,
        closure_digest: str,
        baseline_nonce: str,
        fields: tuple[SourceFieldRef, ...],
        restore_order: tuple[SourceFieldRef, ...],
        owner_nonce: object,
    ) -> "SourceBaselineHandle":
        instance = object.__new__(cls)
        for name, value in (
            ("context_id", context_id),
            ("operation_id", operation_id),
            ("session_epoch", session_epoch),
            ("closure_digest", closure_digest),
            ("baseline_nonce", baseline_nonce),
            ("fields", fields),
            ("restore_order", restore_order),
            ("_owner_nonce", owner_nonce),
        ):
            object.__setattr__(instance, name, value)
        return instance


@dataclass(slots=True)
class _BaselineRecord:
    handle: SourceBaselineHandle
    state: SourceBaselineUseState = SourceBaselineUseState.FRESH
    restore_succeeded: bool | None = None
    verification_succeeded: bool | None = None


class SourceOperationContextCoordinator:
    """One Source mutation context with sequential, non-nested safe phases."""

    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        operation_spec: OperationSpec,
        operation_contract: SourceOperationContract,
        connection_timeout_ms: int,
        baseline_snapshot_digest: str | None,
        fields: tuple[SourceFieldRef, ...],
        required_off_outputs: tuple[SourceScopeRef, ...],
        emergency_off_outputs: tuple[SourceScopeRef, ...],
        restore_order: tuple[SourceFieldRef, ...],
        non_restorable_fields: tuple[SourceFieldRef, ...],
        correlation_id: str | None = None,
        caller_deadline: float | None = None,
        now: float | None = None,
    ) -> None:
        if not isinstance(session_state, InstrumentSessionState):
            raise TypeError("source operation context requires an InstrumentSessionState")
        if not isinstance(operation_spec, OperationSpec):
            raise TypeError("source operation context requires an OperationSpec")
        if not isinstance(operation_contract, SourceOperationContract):
            raise TypeError("source operation context requires a SourceOperationContract")
        self._validate_operation_pair(operation_spec, operation_contract)
        if isinstance(connection_timeout_ms, bool) or not isinstance(
            connection_timeout_ms, int
        ) or connection_timeout_ms < 1:
            raise ValueError("connection_timeout_ms must be a positive integer")
        if session_state.health is not SessionHealth.HEALTHY:
            raise ValueError("new Source operations require a healthy session")
        if baseline_snapshot_digest is not None and (
            not isinstance(baseline_snapshot_digest, str)
            or not baseline_snapshot_digest.startswith("sha256:")
        ):
            raise ValueError("source operation baseline_snapshot_digest must be a SHA-256 digest")

        current = time.monotonic() if now is None else float(now)
        if not isfinite(current):
            raise ValueError("source operation clock must be finite")
        hard_deadline = current + (operation_contract.operation_timeout_ms / 1000.0)
        if caller_deadline is not None:
            if isinstance(caller_deadline, bool) or not isinstance(
                caller_deadline, (int, float)
            ) or not isfinite(caller_deadline):
                raise ValueError("caller_deadline must be a finite monotonic timestamp")
            hard_deadline = min(hard_deadline, float(caller_deadline))
        if hard_deadline - current <= 0.001:
            raise ValueError("source operation deadline cannot retain one millisecond of main time")
        requested_reserve_ms = min(5_000, max(1_000, operation_contract.operation_timeout_ms // 5))
        reserve_s = min(
            requested_reserve_ms / 1000.0,
            (hard_deadline - current) / 2.0,
        )
        main_deadline = hard_deadline - reserve_s
        if main_deadline - current < 0.001:
            raise ValueError("source operation deadline cannot retain one millisecond of main time")

        self.context_id = uuid4().hex
        self.operation_id = operation_contract.operation
        self.correlation_id = _safe_token(correlation_id or uuid4().hex, label="correlation_id")
        self.session_epoch = session_state.epoch_id
        self.deadline = hard_deadline
        self.main_deadline = main_deadline
        self.cleanup_reserve_ms = int(reserve_s * 1000)
        self.operation_spec = operation_spec
        self.operation_contract = operation_contract
        self.session_state = session_state
        self.session_health_before = session_state.health.value
        self.connection_timeout_ms = connection_timeout_ms
        self.observed_at_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        self._session_coordinator = SessionTransactionCoordinator(session_state)
        self._owner_nonce = object()
        self._active_phase: SourcePhaseAuthorization | None = None
        self._phase_history: list[dict[str, object]] = []
        self._used_phases: set[SourceOperationPhase] = set()
        self._baseline: _BaselineRecord | None = None
        self._baseline_snapshot_bound = baseline_snapshot_digest is not None
        self._main_entered = False
        self._failure_required = False
        self._postcondition_verified = False
        self._cleanup_verified = False
        self._safe_state_verified = False
        self._terminal = False

        normalized_fields = _field_refs(fields, label="source operation closure fields")
        normalized_restore = _field_refs(
            restore_order,
            label="source operation restore_order",
            allow_empty=True,
            sorted_values=False,
        )
        normalized_non_restorable = _field_refs(
            non_restorable_fields,
            label="source operation non_restorable_fields",
            allow_empty=True,
        )
        self._validate_closure_inputs(
            fields=normalized_fields,
            required_off_outputs=required_off_outputs,
            emergency_off_outputs=emergency_off_outputs,
            restore_order=normalized_restore,
            non_restorable_fields=normalized_non_restorable,
        )
        self.closure = self._build_closure(
            baseline_snapshot_digest=(
                baseline_snapshot_digest
                if baseline_snapshot_digest is not None
                else source_v2_digest(
                    {
                        "operation": self.operation_id,
                        "context_id": self.context_id,
                        "state": "preflight_pending",
                    }
                )
            ),
            fields=normalized_fields,
            required_off_outputs=required_off_outputs,
            emergency_off_outputs=emergency_off_outputs,
            restore_order=normalized_restore,
            non_restorable_fields=normalized_non_restorable,
        )

    @property
    def terminal(self) -> bool:
        return self._terminal

    def has_phase(self, phase: SourceOperationPhase) -> bool:
        return SourceOperationPhase(phase) in self._used_phases

    def make_phase_spec(
        self,
        phase: SourceOperationPhase,
        *,
        allowed_io: Iterable[str],
        fields: Iterable[SourceFieldRef],
        max_steps: int,
        deadline: float | None = None,
    ) -> SourcePhaseAuthorizationSpec:
        phase = SourceOperationPhase(phase)
        ceiling = (
            self.deadline
            if phase
            in {
                SourceOperationPhase.FAILURE_SAFE_STATE,
                SourceOperationPhase.FAILURE_RESTORE,
                SourceOperationPhase.CLEANUP_VERIFICATION,
            }
            else self.main_deadline
        )
        chosen_deadline = ceiling if deadline is None else min(float(deadline), ceiling)
        return SourcePhaseAuthorizationSpec(
            context_id=self.context_id,
            operation_id=self.operation_id,
            phase=phase,
            purpose=_PHASE_PURPOSE[phase],
            allowed_io=frozenset(allowed_io),
            fields=frozenset(_field_refs(fields, label="source phase fields")),
            deadline=chosen_deadline,
            max_steps=max_steps,
        )

    @contextmanager
    def authorize_phase(
        self,
        phase_spec: SourcePhaseAuthorizationSpec,
    ) -> Iterator[SourcePhaseAuthorization]:
        self._validate_phase_spec(phase_spec)
        if self._active_phase is not None or self.session_state._active_authorization() is not None:
            raise ValueError("nested Source/session authorizations are not allowed")
        self._validate_phase_order(phase_spec.phase)
        if phase_spec.phase is SourceOperationPhase.MAIN:
            # Once a possible mutation phase begins, old proof for the complete
            # affected closure is stale even if a driver later reports no-op.
            self._session_coordinator.invalidate_verified_fields(_field_keys(self.closure.fields))
        remaining_ms = min(
            self.connection_timeout_ms,
            max(1, ceil((phase_spec.deadline - time.monotonic()) * 1000.0)),
        )
        if phase_spec.purpose is SourcePhasePurpose.NORMAL:
            manager = self._session_coordinator.authorize_normal(
                operation_id=self.operation_id,
                allowed_io=phase_spec.allowed_io,
                fields=_field_keys(phase_spec.fields),
                timeout_ms=remaining_ms,
                max_steps=phase_spec.max_steps,
                context_id=self.context_id,
                correlation_id=self.correlation_id,
                phase=phase_spec.phase.value,
                absolute_deadline=phase_spec.deadline,
            )
        else:
            evidence_fields = None
            if phase_spec.purpose is SourcePhasePurpose.VERIFICATION:
                evidence_fields = {
                    io_kind: _field_keys(phase_spec.fields)
                    for io_kind in phase_spec.allowed_io
                    if io_kind in _READ_IO
                }
            manager = self._session_coordinator.authorize(
                operation_id=self.operation_id,
                purpose=SessionPurpose(phase_spec.purpose.value),
                allowed_io=phase_spec.allowed_io,
                fields=_field_keys(phase_spec.fields),
                timeout_ms=remaining_ms,
                max_steps=phase_spec.max_steps,
                evidence_fields=evidence_fields,
                context_id=self.context_id,
                correlation_id=self.correlation_id,
                phase=phase_spec.phase.value,
                absolute_deadline=phase_spec.deadline,
            )
        status = "failed"
        with manager as session_authorization:
            authorization = SourcePhaseAuthorization._issue(
                phase_spec,
                session_authorization,
                self._owner_nonce,
            )
            self._active_phase = authorization
            self._used_phases.add(phase_spec.phase)
            if phase_spec.phase is SourceOperationPhase.MAIN:
                self._main_entered = True
            try:
                yield authorization
                status = "completed"
            finally:
                self._active_phase = None
                self._phase_history.append(
                    {
                        "phase": phase_spec.phase.value,
                        "purpose": phase_spec.purpose.value,
                        "allowed_io": sorted(phase_spec.allowed_io),
                        "fields": sorted(_field_keys(phase_spec.fields)),
                        "max_steps": phase_spec.max_steps,
                        "actual_steps": session_authorization._record.successful_steps,
                        "status": status,
                    }
                )

    def create_baseline(self) -> SourceBaselineHandle:
        """Issue a one-use baseline handle while the preflight phase is active."""

        self._require_phase(SourceOperationPhase.PREFLIGHT)
        if not self._baseline_snapshot_bound:
            raise ValueError("source operation baseline snapshot is not bound")
        if self._baseline is not None:
            raise ValueError("source operation already has a baseline")
        if not self.closure.restore_order:
            raise ValueError("source operation closure has no restorable fields")
        nonce = uuid4().hex
        handle = SourceBaselineHandle._issue(
            context_id=self.context_id,
            operation_id=self.operation_id,
            session_epoch=self.session_epoch,
            closure_digest=self.closure.closure_digest,
            baseline_nonce=nonce,
            fields=self.closure.restore_order,
            restore_order=self.closure.restore_order,
            owner_nonce=self._owner_nonce,
        )
        self._baseline = _BaselineRecord(handle=handle)
        return handle

    def bind_baseline_snapshot_digest(self, baseline_snapshot_digest: str) -> None:
        """Bind the preflight snapshot after its core-owned read has completed."""

        self._require_phase(SourceOperationPhase.PREFLIGHT)
        if self._baseline_snapshot_bound or self._baseline is not None:
            raise ValueError("source operation baseline snapshot is already bound")
        if not isinstance(baseline_snapshot_digest, str) or not baseline_snapshot_digest.startswith(
            "sha256:"
        ):
            raise ValueError("source operation baseline_snapshot_digest must be a SHA-256 digest")
        self.closure = self._build_closure(
            baseline_snapshot_digest=baseline_snapshot_digest,
            fields=self.closure.fields,
            required_off_outputs=self.closure.required_off_outputs,
            emergency_off_outputs=self.closure.emergency_off_outputs,
            restore_order=self.closure.restore_order,
            non_restorable_fields=self.closure.non_restorable_fields,
        )
        self._baseline_snapshot_bound = True

    def pass_baseline_to_main(self, handle: SourceBaselineHandle) -> None:
        self._require_phase(SourceOperationPhase.PREFLIGHT)
        record = self._baseline_record(handle)
        if record.state is not SourceBaselineUseState.FRESH:
            raise ValueError("source baseline is not fresh")
        record.state = SourceBaselineUseState.PASSED_TO_MAIN

    def consume_baseline_after_success(self, handle: SourceBaselineHandle) -> None:
        if not self._main_entered or self._active_phase is not None:
            raise ValueError("successful baseline consumption requires a closed main phase")
        record = self._baseline_record(handle)
        if record.state is not SourceBaselineUseState.PASSED_TO_MAIN:
            raise ValueError("source baseline cannot be consumed from its current state")
        record.state = SourceBaselineUseState.CONSUMED

    def mark_failure_required(self) -> None:
        """Enter the only permitted post-main failure route.

        A transport failure may already have degraded the session.  If it has
        not, make the health conservative before recovery authorization so the
        normal path cannot resume between failed mutation and cleanup.
        """

        if self._active_phase is not None or not self._main_entered:
            raise ValueError("Source failure cleanup requires a closed main phase")
        if self._failure_required:
            raise ValueError("Source operation cleanup is already required")
        if self._postcondition_verified:
            raise ValueError("Source operation cannot fail after postcondition completed")
        self._failure_required = True
        if self.session_state.health is SessionHealth.HEALTHY:
            self.session_state.degrade(SessionHealth.UNCERTAIN, reason="source_cleanup_required")

    def begin_restore(self, handle: SourceBaselineHandle) -> None:
        self._require_phase(SourceOperationPhase.FAILURE_RESTORE)
        record = self._baseline_record(handle)
        if record.state is not SourceBaselineUseState.PASSED_TO_MAIN:
            raise ValueError("source baseline restore slot is already consumed or unavailable")
        record.state = SourceBaselineUseState.RESTORE_ATTEMPTED

    def finish_restore(self, handle: SourceBaselineHandle, *, succeeded: bool) -> None:
        record = self._baseline_record(handle)
        if record.state is not SourceBaselineUseState.RESTORE_ATTEMPTED:
            raise ValueError("source baseline restore was not attempted")
        if record.restore_succeeded is not None:
            raise ValueError("source baseline restore outcome is already recorded")
        if not isinstance(succeeded, bool):
            raise TypeError("source restore outcome must be bool")
        record.restore_succeeded = succeeded

    def begin_cleanup_verification(self, handle: SourceBaselineHandle) -> None:
        self._require_phase(SourceOperationPhase.CLEANUP_VERIFICATION)
        record = self._baseline_record(handle)
        if record.state is not SourceBaselineUseState.RESTORE_ATTEMPTED:
            raise ValueError("source baseline verification requires exactly one restore attempt")
        if record.restore_succeeded is None:
            raise ValueError("source baseline restore outcome has not been recorded")
        record.state = SourceBaselineUseState.VERIFY_ATTEMPTED

    def finish_cleanup_verification(
        self,
        handle: SourceBaselineHandle,
        authorization: SourcePhaseAuthorization,
        *,
        io_kind: str,
        verified_fields: Iterable[SourceFieldRef],
        matched: bool,
    ) -> None:
        self._require_authorization(authorization, SourceOperationPhase.CLEANUP_VERIFICATION)
        record = self._baseline_record(handle)
        if record.state is not SourceBaselineUseState.VERIFY_ATTEMPTED:
            raise ValueError("source baseline verification slot is not active")
        verified = frozenset(_field_refs(verified_fields, label="source verified fields"))
        if verified != authorization.fields:
            raise ValueError("source cleanup verification must cover exactly its authorized fields")
        if not isinstance(matched, bool):
            raise TypeError("source verification match result must be bool")
        candidate = bool(record.restore_succeeded and matched)
        verification_succeeded = False
        try:
            if candidate:
                self._session_coordinator.record_evidence(
                    authorization._session_authorization,
                    io_kind,
                    _field_keys(verified),
                )
                self._session_coordinator.complete_verification(
                    authorization._session_authorization
                )
                verification_succeeded = True
                self._cleanup_verified = True
        finally:
            record.verification_succeeded = verification_succeeded
            record.state = SourceBaselineUseState.CONSUMED
        if not verification_succeeded:
            raise ValueError("source baseline restoration verification is incomplete or mismatched")

    def complete_phase_verification(
        self,
        authorization: SourcePhaseAuthorization,
        *,
        io_kind: str,
        fields: Iterable[SourceFieldRef],
    ) -> None:
        """Commit full fresh evidence for preflight or postcondition reads."""

        if authorization.phase is SourceOperationPhase.CLEANUP_VERIFICATION:
            if self._baseline is not None:
                raise ValueError("cleanup verification must use the baseline-aware completion path")
        if authorization.purpose is not SourcePhasePurpose.VERIFICATION:
            raise ValueError("source phase is not a verification phase")
        self._require_authorization(authorization, authorization.phase)
        verified = frozenset(_field_refs(fields, label="source verified fields"))
        if verified != authorization.fields:
            raise ValueError("source verification must cover exactly its authorized fields")
        self._session_coordinator.record_evidence(
            authorization._session_authorization,
            io_kind,
            _field_keys(verified),
        )
        self._session_coordinator.complete_verification(authorization._session_authorization)
        if authorization.phase is SourceOperationPhase.POSTCONDITION:
            self._postcondition_verified = True
        if authorization.phase is SourceOperationPhase.CLEANUP_VERIFICATION:
            self._cleanup_verified = True

    def mark_safe_state_verified(
        self,
        authorization: SourcePhaseAuthorization,
        *,
        io_kind: str,
        fields: Iterable[SourceFieldRef],
    ) -> None:
        """Record a verified emergency OFF state without restoring mutation evidence."""

        self._require_authorization(authorization, SourceOperationPhase.CLEANUP_VERIFICATION)
        if not self._failure_required:
            raise ValueError("safe-state verification requires a failed source operation")
        verified = frozenset(_field_refs(fields, label="source verified fields"))
        expected = frozenset(_output_fields(self.closure.emergency_off_outputs))
        if verified != expected or verified != authorization.fields:
            raise ValueError("safe-state verification must cover exactly emergency OFF outputs")
        self._session_coordinator.record_evidence(
            authorization._session_authorization,
            io_kind,
            _field_keys(verified),
        )
        self._safe_state_verified = True

    def complete(self) -> None:
        """Terminally close the context and poison an incomplete failure cleanup."""

        if self._active_phase is not None:
            raise ValueError("source operation context cannot terminate with an active phase")
        if self._terminal:
            return
        baseline = self._baseline
        if self._failure_required:
            safe_state_required = bool(self.closure.emergency_off_outputs)
            restore_required = bool(self.closure.restore_order)
            cleanup_ok = (
                (not safe_state_required or SourceOperationPhase.FAILURE_SAFE_STATE in self._used_phases)
                and (not restore_required or SourceOperationPhase.FAILURE_RESTORE in self._used_phases)
                and SourceOperationPhase.CLEANUP_VERIFICATION in self._used_phases
                and self._cleanup_verified
                and (baseline is None or baseline.verification_succeeded is True)
            )
            if not cleanup_ok and not self._safe_state_verified and self.session_state.health in {
                SessionHealth.HEALTHY,
                SessionHealth.UNCERTAIN,
            }:
                self.session_state.degrade(
                    SessionHealth.POISONED,
                    reason="source_cleanup_incomplete",
                )
        elif self._main_entered and not self._postcondition_verified:
            if self.session_state.health in {SessionHealth.HEALTHY, SessionHealth.UNCERTAIN}:
                self.session_state.degrade(
                    SessionHealth.POISONED,
                    reason="source_postcondition_missing",
                )
        if baseline is not None and baseline.state is not SourceBaselineUseState.CONSUMED:
            baseline.state = SourceBaselineUseState.INVALIDATED
        self._terminal = True

    def artifact(self) -> dict[str, object]:
        """Return safe operation-context evidence without nonce or transport payloads."""

        baseline = self._baseline
        return {
            "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
            "operation": self.operation_id,
            "context_id": self.context_id,
            "correlation_id": self.correlation_id,
            "session_epoch": self.session_epoch,
            "session_health": {
                "before": self.session_health_before,
                "after": self.session_state.health.value,
            },
            "closure": {
                "digest": self.closure.closure_digest,
                "baseline_snapshot_digest": self.closure.baseline_snapshot_digest,
                "field_count": len(self.closure.fields),
                "required_off_channels": [item.channel for item in self.closure.required_off_outputs],
                "emergency_off_channels": [item.channel for item in self.closure.emergency_off_outputs],
                "non_restorable_fields": sorted(_field_keys(self.closure.non_restorable_fields)),
            },
            "cleanup_reserve_ms": self.cleanup_reserve_ms,
            "safe_state_verified": self._safe_state_verified,
            "phases": [dict(item) for item in self._phase_history],
            "baseline": (
                None
                if baseline is None
                else {
                    "nonce_digest": sha256(
                        baseline.handle.baseline_nonce.encode("ascii")
                    ).hexdigest()[:16],
                    "fields": sorted(_field_keys(baseline.handle.fields)),
                    "restore_order": [
                        source_v2_digest(item) for item in baseline.handle.restore_order
                    ],
                    "consumption": baseline.state.value,
                    "restore_succeeded": baseline.restore_succeeded,
                    "verification_succeeded": baseline.verification_succeeded,
                }
            ),
            "terminal": self._terminal,
        }

    def _validate_operation_pair(
        self,
        spec: OperationSpec,
        contract: SourceOperationContract,
    ) -> None:
        if spec.instrument_kind != "source" or spec.effect != "write":
            raise ValueError("source mutation contexts require a source write OperationSpec")
        if spec.operation != contract.operation:
            raise ValueError("source OperationSpec and SourceOperationContract operations differ")
        if contract.capability not in spec.required_capabilities:
            raise ValueError("source OperationSpec must require its Source V2 capability")
        if (
            spec.timeout_source != "operation.timeout_ms"
            or spec.operation_timeout_ms != contract.operation_timeout_ms
        ):
            raise ValueError("source OperationSpec timeout must match its operation contract")
        if spec.lease_mode != "exclusive":
            raise ValueError("source mutation contexts require an exclusive operation lease")
        required_changed = {field.value for field in contract.changed_fields}
        if not required_changed <= set(spec.changed_fields):
            raise ValueError("source OperationSpec changed_fields do not cover its operation contract")
        if contract.energy_effect is SourceEnergyEffect.UNKNOWN:
            raise ConfigError("unknown Source energy effect is not authorized")
        if contract.storage_effect is SourceStorageEffect.UNKNOWN:
            raise ConfigError("unknown Source storage effect is not authorized")

    def _validate_closure_inputs(
        self,
        *,
        fields: tuple[SourceFieldRef, ...],
        required_off_outputs: tuple[SourceScopeRef, ...],
        emergency_off_outputs: tuple[SourceScopeRef, ...],
        restore_order: tuple[SourceFieldRef, ...],
        non_restorable_fields: tuple[SourceFieldRef, ...],
    ) -> None:
        contract_field_ids = {item.field for item in fields}
        required_ids = {
            *self.operation_contract.required_fields,
            *self.operation_contract.changed_fields,
            *self.operation_contract.postcondition_fields,
            *self.operation_contract.cleanup_verification_fields,
        }
        if not required_ids <= contract_field_ids:
            raise ValueError("source affected closure does not cover its operation contract fields")
        if any(not isinstance(item, SourceScopeRef) for item in required_off_outputs + emergency_off_outputs):
            raise ValueError("source operation outputs must use SourceScopeRef values")
        if any(item.scope.value != "channel" for item in required_off_outputs + emergency_off_outputs):
            raise ValueError("source operation output scopes must be channel scoped")
        expected_emergency_fields = set(_output_fields(emergency_off_outputs))
        if not expected_emergency_fields <= set(fields):
            raise ValueError("source affected closure must include every emergency output field")
        if set(restore_order) & set(non_restorable_fields):
            raise ValueError("source restore order and non-restorable fields overlap")
        if any(item.field in _NON_REENERGIZING_RESTORE_FIELDS for item in restore_order):
            raise ValueError("source failure restore cannot re-enable output, arm, or trigger fields")
        if self.operation_contract.energy_effect in {
            SourceEnergyEffect.MAY_INCREASE,
            SourceEnergyEffect.EMIT,
        } and not required_off_outputs:
            raise ValueError("energy-increasing Source operations require explicit OFF outputs")

    def _build_closure(
        self,
        *,
        baseline_snapshot_digest: str,
        fields: tuple[SourceFieldRef, ...],
        required_off_outputs: tuple[SourceScopeRef, ...],
        emergency_off_outputs: tuple[SourceScopeRef, ...],
        restore_order: tuple[SourceFieldRef, ...],
        non_restorable_fields: tuple[SourceFieldRef, ...],
    ) -> SourceAffectedClosure:
        payload = {
            "schema": "wavebench.source.affected-closure.v1",
            "operation": self.operation_id,
            "context_id": self.context_id,
            "session_epoch": self.session_epoch,
            "baseline_snapshot_digest": baseline_snapshot_digest,
            "fields": fields,
            "required_off_outputs": required_off_outputs,
            "emergency_off_outputs": emergency_off_outputs,
            "restore_order": restore_order,
            "non_restorable_fields": non_restorable_fields,
        }
        return SourceAffectedClosure(
            operation=self.operation_id,
            context_id=self.context_id,
            session_epoch=self.session_epoch,
            baseline_snapshot_digest=baseline_snapshot_digest,
            fields=fields,
            required_off_outputs=required_off_outputs,
            emergency_off_outputs=emergency_off_outputs,
            restore_order=restore_order,
            non_restorable_fields=non_restorable_fields,
            closure_digest=source_v2_digest(payload),
        )

    def _validate_phase_spec(self, phase_spec: SourcePhaseAuthorizationSpec) -> None:
        if not isinstance(phase_spec, SourcePhaseAuthorizationSpec):
            raise TypeError("source phase authorization spec has an invalid type")
        if self._terminal:
            raise ValueError("source operation context is terminal")
        if self.session_state.epoch_id != self.session_epoch:
            self.complete()
            raise ValueError("source operation context belongs to another session epoch")
        if (
            phase_spec.context_id != self.context_id
            or phase_spec.operation_id != self.operation_id
        ):
            raise ValueError("source phase authorization belongs to another operation context")
        ceiling = (
            self.deadline
            if phase_spec.phase
            in {
                SourceOperationPhase.FAILURE_SAFE_STATE,
                SourceOperationPhase.FAILURE_RESTORE,
                SourceOperationPhase.CLEANUP_VERIFICATION,
            }
            else self.main_deadline
        )
        if phase_spec.deadline > ceiling or phase_spec.deadline <= time.monotonic():
            raise ValueError("source phase deadline exceeds or exhausts the operation deadline")
        if not phase_spec.fields <= set(self.closure.fields):
            raise ValueError("source phase fields exceed the affected closure")
        if phase_spec.phase is SourceOperationPhase.FAILURE_SAFE_STATE and phase_spec.fields != set(
            _output_fields(self.closure.emergency_off_outputs)
        ):
            raise ValueError("source failure safe-state phase must cover exactly emergency OFF outputs")
        if (
            phase_spec.phase is SourceOperationPhase.FAILURE_SAFE_STATE
            and not self.closure.emergency_off_outputs
        ):
            raise ValueError("source failure safe-state phase is unavailable without emergency OFF outputs")
        if phase_spec.phase is SourceOperationPhase.FAILURE_RESTORE and phase_spec.fields != set(
            self.closure.restore_order
        ):
            raise ValueError("source failure restore phase must cover exactly the frozen restore order")
        if phase_spec.phase is SourceOperationPhase.FAILURE_RESTORE and not self.closure.restore_order:
            raise ValueError("source failure restore phase is unavailable without restorable fields")
        if phase_spec.phase is SourceOperationPhase.CLEANUP_VERIFICATION:
            expected = frozenset(
                (*_output_fields(self.closure.emergency_off_outputs), *self.closure.restore_order)
            )
            if phase_spec.fields != expected:
                raise ValueError(
                    "source cleanup verification must cover emergency OFF and restored fields"
                )

    def _validate_phase_order(self, phase: SourceOperationPhase) -> None:
        if phase in self._used_phases:
            raise ValueError("source operation phases are single use")
        if phase is SourceOperationPhase.PREFLIGHT:
            if self._used_phases:
                raise ValueError("source preflight must be the first phase")
            return
        if SourceOperationPhase.PREFLIGHT not in self._used_phases:
            raise ValueError("source operation phases require preflight")
        if phase is SourceOperationPhase.MAIN:
            if self._failure_required or SourceOperationPhase.POSTCONDITION in self._used_phases:
                raise ValueError("source main cannot run after failure or postcondition")
            return
        if phase is SourceOperationPhase.POSTCONDITION:
            if not self._main_entered or self._failure_required:
                raise ValueError("source postcondition requires a successful main path")
            return
        if phase is SourceOperationPhase.FAILURE_SAFE_STATE:
            if not self._main_entered or not self._failure_required:
                raise ValueError("source failure safe-state requires a failed main path")
            return
        if phase is SourceOperationPhase.FAILURE_RESTORE:
            if self.closure.emergency_off_outputs and (
                SourceOperationPhase.FAILURE_SAFE_STATE not in self._used_phases
            ):
                raise ValueError("source failure restore requires failure safe-state first")
            return
        if phase is SourceOperationPhase.CLEANUP_VERIFICATION:
            if self.closure.restore_order:
                if SourceOperationPhase.FAILURE_RESTORE not in self._used_phases:
                    raise ValueError("source cleanup verification requires failure restore first")
            elif self.closure.emergency_off_outputs and (
                SourceOperationPhase.FAILURE_SAFE_STATE not in self._used_phases
            ):
                raise ValueError("source cleanup verification requires failure safe-state first")

    def _baseline_record(self, handle: SourceBaselineHandle) -> _BaselineRecord:
        if not isinstance(handle, SourceBaselineHandle):
            raise TypeError("source baseline handle has an invalid type")
        if handle._owner_nonce is not self._owner_nonce:
            raise ValueError("source baseline handle is not owned by this context")
        if (
            handle.context_id != self.context_id
            or handle.operation_id != self.operation_id
            or handle.session_epoch != self.session_epoch
            or handle.closure_digest != self.closure.closure_digest
        ):
            raise ValueError("source baseline binding does not match the operation context")
        if self._baseline is None or self._baseline.handle is not handle:
            raise ValueError("source baseline nonce is unknown or replayed")
        return self._baseline

    def _require_phase(self, phase: SourceOperationPhase) -> None:
        if self._active_phase is None or self._active_phase.phase is not phase:
            raise ValueError(f"source operation requires active phase {phase.value}")

    def _require_authorization(
        self,
        authorization: SourcePhaseAuthorization,
        phase: SourceOperationPhase,
    ) -> None:
        if (
            not isinstance(authorization, SourcePhaseAuthorization)
            or authorization._owner_nonce is not self._owner_nonce
            or self._active_phase is not authorization
            or authorization.phase is not phase
        ):
            raise ValueError("source phase authorization is inactive or belongs to another context")


__all__ = [
    "SourceBaselineHandle",
    "SourceBaselineUseState",
    "SourceOperationContextCoordinator",
    "SourceOperationPhase",
    "SourcePhaseAuthorization",
    "SourcePhaseAuthorizationSpec",
    "SourcePhasePurpose",
]
