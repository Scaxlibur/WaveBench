"""Core-owned bounded execution for opt-in standard scope waveform operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
from hashlib import sha256
from types import MappingProxyType
from typing import Any

import numpy as np

from wavebench.errors import ConfigError, DataError, InstrumentError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.models import WaveformData
from wavebench.instruments.scope_extension_capabilities import validate_scope_descriptor
from wavebench.instruments.scope_extensions import (
    ErrorCheckSpec,
    ScopeWaveformBinaryOperationKind,
    ScopeWaveformBinaryOperationProfile,
    ScopeWaveformBinaryProfile,
    ScopeWaveformTransferBaseline,
    ScopeWaveformTransferRestoreResult,
    ScopeWaveformTransferStateSnapshot,
    ScopeWaveformTransferVerification,
)
from wavebench.scope_extension_constants import (
    SCOPE_WAVEFORM_BINARY_OPERATION_MAX_BYTES,
    SCOPE_WAVEFORM_BINARY_QUERY_MAX_COUNT,
    SCOPE_WAVEFORM_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_WAVEFORM_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_WAVEFORM_OPERATION_TIMEOUT_MS,
)
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth

from .operation_specs import OperationSpec, require_operation_spec
from .scope_error_policy import ScopeErrorPolicyExecutor
from .scope_phase_coordinator import (
    OperationPhase,
    ScopeBaselineHandle,
    ScopeBinaryLimits,
    ScopeOperationContextCoordinator,
)


_TEXT_READ_IO = {"query", "query_float_list", "query_opc"}
_WAVEFORM_MAIN_IO = {*_TEXT_READ_IO, "write", "write_bytes", "query_binary"}
_WAVEFORM_MAIN_MAX_STEPS = 512
_OPERATION_ID_BY_KIND = {
    "fetch": "scope.fetch_waveform",
    "capture_single": "scope.capture",
    "capture_multiple": "scope.capture_multiple",
}
_WaveformCallbackEvidence = tuple[object, str, tuple[int, ...], bytes]


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


@dataclass(frozen=True, slots=True)
class BoundedWaveformExecutionResult:
    """Private handoff preserving the stable public waveform return values."""

    value: object
    identity: str
    diagnostics: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(slots=True)
class BoundedWaveformExecutor:
    """Run one descriptor-opted-in standard waveform operation under one ledger."""

    driver: object
    descriptor: InstrumentDescriptor
    session_state: InstrumentSessionState
    connection_timeout_ms: int
    transport: GuardedAuditedTransport | None = None

    def __post_init__(self) -> None:
        if isinstance(self.connection_timeout_ms, bool) or not isinstance(
            self.connection_timeout_ms, int
        ) or self.connection_timeout_ms < 1:
            raise ValueError("connection_timeout_ms must be a positive integer")
        if self.session_state.health is not SessionHealth.HEALTHY:
            raise ConfigError("bounded waveform operations require a healthy session")
        if (
            not isinstance(self.transport, GuardedAuditedTransport)
            or self.transport.session_state is not self.session_state
            or not self.transport._has_verified_bounded_binary_backend()
        ):
            raise ConfigError(
                "bounded waveform operations require a factory-validated bounded transport"
            )
        if "scope.idn" not in self.descriptor.capabilities:
            raise ConfigError("bounded waveform operations require scope.idn")
        if not callable(getattr(self.driver, "idn", None)):
            raise ConfigError("bounded waveform operations require callable idn()")
        validate_scope_descriptor(self.descriptor, driver=self.driver)
        self._profile()

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self.descriptor.capabilities)

    def fetch(
        self,
        *,
        channel: int,
        points: str,
        check_errors: bool,
    ) -> BoundedWaveformExecutionResult:
        requested_channel = self._validate_requested_channels((channel,))[0]
        return self._execute(
            operation_kind="fetch",
            check_errors=check_errors,
            expected_channel=requested_channel,
            action=lambda baseline: self.driver.fetch_waveform_bounded(
                requested_channel,
                points,
                baseline=baseline,
            ),
        )

    def capture_single(
        self,
        *,
        channel: int,
        points: str,
        time_range_s: float | None,
        vertical_scale_v_per_div: float | None,
        check_errors: bool,
    ) -> BoundedWaveformExecutionResult:
        requested_channel = self._validate_requested_channels((channel,))[0]
        return self._execute(
            operation_kind="capture_single",
            check_errors=check_errors,
            expected_channel=requested_channel,
            action=lambda baseline: self.driver.capture_waveform_bounded(
                requested_channel,
                points,
                time_range_s=time_range_s,
                vertical_scale_v_per_div=vertical_scale_v_per_div,
                baseline=baseline,
            ),
        )

    def capture_multiple(
        self,
        *,
        channels: list[int],
        points: str,
        time_range_s: float | None,
        vertical_scale_v_per_div: float | None,
        check_errors: bool,
        on_channel_start: Callable[[int | None], None] | None,
        on_waveform: Callable[[int, WaveformData], None] | None,
    ) -> BoundedWaveformExecutionResult:
        requested_channels = self._validate_requested_channels(tuple(channels))
        callback_evidence: dict[int, _WaveformCallbackEvidence] = {}
        checked_on_channel_start, checked_on_waveform = self._validated_multi_callbacks(
            requested_channels,
            on_channel_start=on_channel_start,
            on_waveform=on_waveform,
            callback_evidence=callback_evidence,
        )
        return self._execute(
            operation_kind="capture_multiple",
            check_errors=check_errors,
            expected_channels=requested_channels,
            callback_evidence=callback_evidence,
            require_waveform_callbacks=on_waveform is not None,
            action=lambda baseline: self.driver.capture_waveforms_bounded(
                list(requested_channels),
                points,
                time_range_s=time_range_s,
                vertical_scale_v_per_div=vertical_scale_v_per_div,
                on_channel_start=checked_on_channel_start,
                on_waveform=checked_on_waveform,
                baseline=baseline,
            ),
        )

    def _execute(
        self,
        *,
        operation_kind: ScopeWaveformBinaryOperationKind,
        check_errors: bool,
        action: Callable[[ScopeWaveformTransferBaseline], object],
        expected_channel: int | None = None,
        expected_channels: tuple[int, ...] | None = None,
        callback_evidence: Mapping[int, _WaveformCallbackEvidence] | None = None,
        require_waveform_callbacks: bool = False,
    ) -> BoundedWaveformExecutionResult:
        profile = self._profile()
        operation_profile = profile.operation_for(operation_kind)
        spec = self._bounded_operation_spec(operation_kind)
        context = ScopeOperationContextCoordinator(
            session_state=self.session_state,
            spec=spec,
            connection_timeout_ms=self.connection_timeout_ms,
            profile_binary_limits=ScopeBinaryLimits(
                response_max_bytes=operation_profile.response_max_bytes,
                operation_max_bytes=operation_profile.operation_max_bytes,
                query_max_count=operation_profile.query_max_count,
                resynchronization_max_bytes=operation_profile.resynchronization_max_bytes,
            ),
            transport_trailing=profile.transport_trailing,
            required_binary_framing=profile.framing,
            enabled=True,
        )
        error_spec = ErrorCheckSpec(policy="required" if check_errors else "disabled")
        try:
            error_executor = ScopeErrorPolicyExecutor(
                driver=self.driver,
                capabilities=self.capabilities,
                operation_spec=spec,
                error_spec=error_spec,
                correlation_id=context.correlation_id,
            )
        except BaseException:
            context.complete()
            raise

        handle: ScopeBaselineHandle | None = None
        baseline: ScopeWaveformTransferBaseline | None = None
        identity: str | None = None
        value: object | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        cleanup_diagnostics: dict[str, object] = {}
        main_entered = False
        try:
            fields = tuple(operation_profile.restore_order)
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", *fields},
                max_steps=1 + operation_profile.snapshot_max_steps,
            )
            with context.authorize_phase(preflight) as authorization:
                identity = self._verify_identity()
                snapshot = self.driver.snapshot_waveform_transfer_state(fields)
                self._validate_snapshot(snapshot, fields)
                handle = context.create_baseline(
                    kind="waveform_transfer",
                    fields=fields,
                    restore_order=fields,
                )
                baseline = ScopeWaveformTransferBaseline(
                    context_id=handle.context_id,
                    session_epoch=handle.session_epoch,
                    baseline_nonce=handle.baseline_nonce,
                    snapshot=snapshot,
                    restore_order=fields,
                )
                context.pass_baseline_to_main(handle)
                context.complete_phase_verification(
                    authorization,
                    io_kind="query",
                    fields={"scope.identity", *fields},
                )
            error_executor.run(context, phase="before")
            main = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io=_WAVEFORM_MAIN_IO,
                fields=set(spec.changed_fields),
                max_steps=_WAVEFORM_MAIN_MAX_STEPS,
            )
            try:
                with context.authorize_phase(main):
                    main_entered = True
                    error_executor.mark_main_sent()
                    assert baseline is not None
                    value = action(baseline)
                    self._validate_value(
                        operation_kind,
                        value,
                        expected_channel=expected_channel,
                        expected_channels=expected_channels,
                        callback_evidence=callback_evidence,
                        require_waveform_callbacks=require_waveform_callbacks,
                    )
                    self._require_binary_main_query(context)
                error_executor.run(context, phase="after")
            except BaseException as exc:
                primary = exc
                if error_executor.wants("after") and not context.has_phase(
                    OperationPhase.ERROR_AFTER
                ):
                    error_executor.omit_after(
                        "session_unhealthy"
                        if self.session_state.health is not SessionHealth.HEALTHY
                        else "main_operation_failed"
                    )
            if (
                handle is not None
                and main_entered
                and self.session_state.health is not SessionHealth.POISONED
            ):
                context.mark_cleanup_required()
                cleanup_error, cleanup_diagnostics = self._cleanup(
                    context,
                    handle,
                    baseline,
                    operation_profile,
                    failed=primary is not None,
                )
            context.complete()
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                    extra={"waveform_cleanup": cleanup_diagnostics},
                )
                raise primary
            if cleanup_error is not None:
                self._attach_diagnostics(
                    cleanup_error,
                    context,
                    error_executor,
                    extra={"waveform_cleanup": cleanup_diagnostics},
                )
                raise cleanup_error
            assert identity is not None
            return BoundedWaveformExecutionResult(
                value=value,
                identity=identity,
                diagnostics={
                    "scope_operation": context.artifact(),
                    "error_check": dict(error_executor.artifact),
                    "waveform_cleanup": cleanup_diagnostics,
                },
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(exc, context, error_executor)
            raise

    def _cleanup(
        self,
        context: ScopeOperationContextCoordinator,
        handle: ScopeBaselineHandle,
        baseline: ScopeWaveformTransferBaseline | None,
        profile: ScopeWaveformBinaryOperationProfile,
        *,
        failed: bool,
    ) -> tuple[BaseException | None, dict[str, object]]:
        assert baseline is not None
        restore_result: ScopeWaveformTransferRestoreResult | None = None
        verification: ScopeWaveformTransferVerification | None = None
        error: BaseException | None = None
        phase = OperationPhase.FAILURE_CLEANUP if failed else OperationPhase.SUCCESS_RESTORE
        try:
            restore = context.make_phase_spec(
                phase,
                allowed_io={"write"},
                fields=handle.fields,
                max_steps=profile.restore_max_steps,
            )
            with context.authorize_phase(restore):
                context.begin_restore(handle)
                try:
                    restore_result = self.driver.restore_waveform_transfer_state(baseline)
                    if not isinstance(restore_result, ScopeWaveformTransferRestoreResult):
                        raise TypeError("restore_waveform_transfer_state() returned an invalid result")
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("waveform transfer restore did not complete")
        except BaseException as exc:
            error = exc

        if self.session_state.health is not SessionHealth.POISONED:
            try:
                verify = context.make_phase_spec(
                    OperationPhase.CLEANUP_VERIFICATION,
                    allowed_io={"query"},
                    fields=handle.fields,
                    max_steps=profile.verify_max_steps,
                )
                with context.authorize_phase(verify) as authorization:
                    context.begin_verification(handle)
                    observed = self.driver.verify_waveform_transfer_state_restored(baseline)
                    self._validate_snapshot(observed, tuple(handle.fields))
                    matched = observed == baseline.snapshot
                    verification = ScopeWaveformTransferVerification(
                        status="verified" if matched else "mismatch",
                        verified_fields=tuple(handle.fields) if matched else (),
                        mismatched_fields=() if matched else tuple(handle.fields),
                    )
                    context.finish_verification(
                        handle,
                        authorization,
                        io_kind="query",
                        verified_fields=tuple(handle.fields),
                        matched=matched,
                    )
            except BaseException as exc:
                error = error or exc
        return error, {
            "restore": _json_safe(restore_result),
            "verification": _json_safe(verification),
        }

    def _profile(self) -> ScopeWaveformBinaryProfile:
        extensions = self.descriptor.scope_extensions
        profile = extensions.waveform_binary_profile if extensions is not None else None
        if profile is None:
            raise ConfigError("bounded waveform executor requires a waveform binary profile")
        return profile

    @staticmethod
    def _validate_snapshot(
        snapshot: object,
        fields: tuple[str, ...],
    ) -> None:
        if not isinstance(snapshot, ScopeWaveformTransferStateSnapshot):
            raise DataError("waveform transfer snapshot has an invalid type")
        if tuple(snapshot.captured_fields) != fields:
            raise DataError("waveform transfer snapshot fields do not match the profile")

    @staticmethod
    def _validate_value(
        operation_kind: ScopeWaveformBinaryOperationKind,
        value: object,
        *,
        expected_channel: int | None,
        expected_channels: tuple[int, ...] | None,
        callback_evidence: Mapping[int, _WaveformCallbackEvidence] | None,
        require_waveform_callbacks: bool,
    ) -> None:
        if operation_kind in {"fetch", "capture_single"}:
            if not isinstance(value, WaveformData):
                raise DataError("bounded waveform driver returned an invalid waveform")
            if expected_channel is None or value.channel != expected_channel:
                raise DataError("bounded waveform driver returned a waveform with a mismatched channel")
            return
        if expected_channels is None or not isinstance(value, dict):
            raise DataError("bounded multi-waveform driver returned an invalid waveform map")
        if any(isinstance(channel, bool) or not isinstance(channel, int) for channel in value):
            raise DataError("bounded multi-waveform driver returned an invalid waveform map")
        expected = frozenset(expected_channels)
        actual = frozenset(value)
        if actual != expected:
            raise DataError(
                "bounded multi-waveform driver returned a channel set that does not match the request"
            )
        if require_waveform_callbacks and (
            callback_evidence is None or frozenset(callback_evidence) != expected
        ):
            raise DataError(
                "bounded multi-waveform driver did not emit exactly one waveform callback for every channel"
            )
        for channel, waveform in value.items():
            if not isinstance(waveform, WaveformData) or waveform.channel != channel:
                raise DataError(
                    "bounded multi-waveform driver returned a waveform with a mismatched channel"
                )
            if (
                callback_evidence is not None
                and channel in callback_evidence
                and callback_evidence[channel] != BoundedWaveformExecutor._waveform_evidence(waveform)
            ):
                raise DataError(
                    "bounded multi-waveform driver returned a waveform that does not match its callback"
                )

    @staticmethod
    def _validate_requested_channels(channels: tuple[object, ...]) -> tuple[int, ...]:
        if not channels:
            raise ConfigError("at least one channel is required")
        if any(isinstance(channel, bool) or not isinstance(channel, int) or channel < 1 for channel in channels):
            raise ConfigError("bounded multi-waveform channels must be positive integers")
        typed_channels = tuple(channels)
        if len(set(typed_channels)) != len(typed_channels):
            raise ConfigError("duplicate channels are not allowed")
        return typed_channels

    @staticmethod
    def _validated_multi_callbacks(
        requested_channels: tuple[int, ...],
        *,
        on_channel_start: Callable[[int | None], None] | None,
        on_waveform: Callable[[int, WaveformData], None] | None,
        callback_evidence: dict[int, _WaveformCallbackEvidence],
    ) -> tuple[
        Callable[[int | None], None] | None,
        Callable[[int, WaveformData], None] | None,
    ]:
        expected = frozenset(requested_channels)

        checked_start: Callable[[int | None], None] | None = None
        if on_channel_start is not None:

            def checked_start(channel: int | None) -> None:
                if channel is not None and (
                    isinstance(channel, bool) or not isinstance(channel, int) or channel not in expected
                ):
                    raise DataError("bounded multi-waveform callback started an unrequested channel")
                on_channel_start(channel)

        checked_waveform: Callable[[int, WaveformData], None] | None = None
        if on_waveform is not None:

            def checked_waveform(channel: int, waveform: WaveformData) -> None:
                if (
                    isinstance(channel, bool)
                    or not isinstance(channel, int)
                    or channel not in expected
                    or not isinstance(waveform, WaveformData)
                    or waveform.channel != channel
                ):
                    raise DataError("bounded multi-waveform callback returned an invalid waveform")
                if channel in callback_evidence:
                    raise DataError("bounded multi-waveform callback emitted a channel more than once")
                callback_evidence[channel] = BoundedWaveformExecutor._waveform_evidence(waveform)
                on_waveform(channel, waveform)

        return checked_start, checked_waveform

    @staticmethod
    def _waveform_evidence(waveform: WaveformData) -> _WaveformCallbackEvidence:
        values = np.asarray(waveform.voltages_v)
        contiguous = np.ascontiguousarray(values)
        return (
            waveform.header,
            contiguous.dtype.str,
            tuple(contiguous.shape),
            sha256(contiguous.tobytes()).digest(),
        )

    @staticmethod
    def _require_binary_main_query(context: ScopeOperationContextCoordinator) -> None:
        ledger = context.binary_ledger
        if ledger is None:
            raise DataError("bounded waveform operation has no binary ledger")
        snapshot = ledger.snapshot()
        if snapshot["remaining_query_count"] == snapshot["query_max_count"]:
            raise DataError("bounded waveform driver did not issue a binary query")

    @staticmethod
    def _verify_identity_value(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DataError("scope identity verification returned an empty response")
        return value

    def _verify_identity(self) -> str:
        return self._verify_identity_value(self.driver.idn())

    @staticmethod
    def _bounded_operation_spec(
        operation_kind: ScopeWaveformBinaryOperationKind,
    ) -> OperationSpec:
        operation = _OPERATION_ID_BY_KIND[operation_kind]
        base = require_operation_spec(operation)
        return replace(
            base,
            timeout_source="operation.timeout_ms",
            operation_timeout_ms=SCOPE_WAVEFORM_OPERATION_TIMEOUT_MS,
            binary_response_max_bytes=SCOPE_WAVEFORM_BINARY_RESPONSE_MAX_BYTES,
            binary_operation_max_bytes=SCOPE_WAVEFORM_BINARY_OPERATION_MAX_BYTES,
            binary_query_max_count=SCOPE_WAVEFORM_BINARY_QUERY_MAX_COUNT,
            binary_resynchronization_max_bytes=(
                SCOPE_WAVEFORM_BINARY_RESYNCHRONIZATION_MAX_BYTES
            ),
            error_check_minimum="disabled",
        )

    @staticmethod
    def _attach_diagnostics(
        exc: BaseException,
        context: ScopeOperationContextCoordinator,
        error_executor: ScopeErrorPolicyExecutor,
        *,
        cleanup_error: BaseException | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        diagnostics: dict[str, object] = {
            "scope_operation": context.artifact(),
            "error_check": dict(error_executor.artifact),
        }
        if cleanup_error is not None:
            diagnostics["cleanup_error_type"] = type(cleanup_error).__name__
        if extra:
            diagnostics.update(extra)
        try:
            setattr(exc, "scope_operation_diagnostics", diagnostics)
        except Exception:
            pass


__all__ = ["BoundedWaveformExecutionResult", "BoundedWaveformExecutor"]
