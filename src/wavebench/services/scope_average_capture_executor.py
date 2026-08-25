"""Core-owned bounded execution for RFC-0006b average capture V2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from wavebench.config import normalize_waveform_points
from wavebench.errors import ConfigError, DataError, InstrumentError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.models import ScopeChannelInputStateV2, WaveformData
from wavebench.instruments.scope_extension_capabilities import validate_scope_descriptor
from wavebench.instruments.scope_extensions import (
    ErrorCheckSpec,
    ScopeAcquisitionControlBaseline,
    ScopeAcquisitionControlProfile,
    ScopeAcquisitionControlSnapshot,
    ScopeAcquisitionCompletion,
    ScopeAcquisitionRunState,
    ScopeAverageCaptureBaseline,
    ScopeAverageCaptureProfileV2,
    ScopeAverageCaptureRequestV2,
    ScopeAverageCaptureRestoreResult,
    ScopeAverageCaptureResultV2,
    ScopeAverageCaptureStateSnapshot,
    ScopeAverageCaptureVerification,
    ScopeAverageCompletionProofV2,
    ScopeAverageConfigurationV2,
    validate_acquisition_completion,
)
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth

from .operation_specs import require_operation_spec
from .scope_error_policy import ScopeErrorPolicyExecutor
from .scope_phase_coordinator import (
    OperationPhase,
    ScopeBaselineHandle,
    ScopeBinaryLimits,
    ScopeOperationContextCoordinator,
)


_AVERAGE_MAIN_IO = {
    "query",
    "query_float_list",
    "query_opc",
    "write",
    "write_bytes",
    "query_binary",
}


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
class AverageCaptureV2ExecutionResult:
    """Private handoff preserving the public V2 result's stable shape."""

    value: ScopeAverageCaptureResultV2
    identity: str
    diagnostics: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(slots=True)
class ScopeAverageCaptureExecutor:
    """Execute one single-channel average capture under one core-owned context."""

    driver: object
    descriptor: InstrumentDescriptor
    session_state: InstrumentSessionState
    connection_timeout_ms: int
    transport: GuardedAuditedTransport | None = None

    def __post_init__(self) -> None:
        if isinstance(self.connection_timeout_ms, bool) or not isinstance(
            self.connection_timeout_ms,
            int,
        ) or self.connection_timeout_ms < 1:
            raise ValueError("connection_timeout_ms must be a positive integer")
        if self.session_state.health is not SessionHealth.HEALTHY:
            raise ConfigError("average capture V2 requires a healthy session")
        if (
            not isinstance(self.transport, GuardedAuditedTransport)
            or self.transport.session_state is not self.session_state
            or not self.transport._has_verified_bounded_binary_backend()
        ):
            raise ConfigError(
                "average capture V2 requires a factory-validated bounded transport"
            )
        if "scope.idn" not in self.descriptor.capabilities:
            raise ConfigError("average capture V2 requires scope.idn")
        if not callable(getattr(self.driver, "idn", None)):
            raise ConfigError("average capture V2 requires callable idn()")
        validate_scope_descriptor(self.descriptor, driver=self.driver)
        self._profile()

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self.descriptor.capabilities)

    def execute(
        self,
        request: ScopeAverageCaptureRequestV2,
        *,
        check_errors: bool,
    ) -> AverageCaptureV2ExecutionResult:
        profile = self._profile()
        try:
            profile.validate_request(request)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid average capture V2 request: {exc}") from exc
        if not isinstance(check_errors, bool):
            raise TypeError("average capture V2 check_errors must be bool")
        spec = require_operation_spec("scope.capture_average_v2")
        binary = profile.binary
        context = ScopeOperationContextCoordinator(
            session_state=self.session_state,
            spec=spec,
            connection_timeout_ms=self.connection_timeout_ms,
            profile_binary_limits=ScopeBinaryLimits(
                response_max_bytes=binary.response_max_bytes,
                operation_max_bytes=binary.operation_max_bytes,
                query_max_count=binary.query_max_count,
                resynchronization_max_bytes=binary.resynchronization_max_bytes,
            ),
            transport_trailing=binary.transport_trailing,
            required_binary_framing=binary.framing,
            enabled=True,
        )
        error_spec = ErrorCheckSpec(
            policy="required" if check_errors else "disabled",
            timing="before_and_after",
            max_records=16,
            on_instrument_error="fail",
        )
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
        baseline: ScopeAverageCaptureBaseline | None = None
        identity: str | None = None
        completion: ScopeAverageCompletionProofV2 | None = None
        waveform: WaveformData | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        cleanup_diagnostics: dict[str, object] = {}
        cleanup_snapshot: ScopeAverageCaptureStateSnapshot | None = None
        restore_result: ScopeAverageCaptureRestoreResult | None = None
        verification: ScopeAverageCaptureVerification | None = None
        main_entered = False
        try:
            fields = tuple(profile.restore_order)
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", *fields},
                max_steps=2 + profile.snapshot_max_steps,
            )
            with context.authorize_phase(preflight) as authorization:
                identity = self._verify_identity()
                self._read_and_validate_input_state(request)
                snapshot = self.driver.snapshot_average_capture_state(fields)
                self._validate_snapshot(snapshot, fields)
                if snapshot.run_state.phase != "stopped":
                    raise ConfigError("average capture V2 requires a fresh stopped run state")
                handle = context.create_baseline(
                    kind="average_capture",
                    fields=fields,
                    restore_order=fields,
                )
                baseline = self._make_baseline(handle, snapshot)
                context.pass_baseline_to_main(handle)
                context.complete_phase_verification(
                    authorization,
                    io_kind="query",
                    fields={"scope.identity", *fields},
                )

            error_executor.run(context, phase="before")
            main = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io=_AVERAGE_MAIN_IO,
                fields=set(spec.changed_fields),
                max_steps=profile.main_max_steps,
            )
            try:
                with context.authorize_phase(main):
                    main_entered = True
                    assert baseline is not None
                    error_executor.mark_main_sent()
                    self.driver.set_average_acquisition_type_v2(
                        profile.global_acquisition_type,
                        baseline=baseline,
                    )
                    type_readback = self.driver.get_average_configuration_v2(
                        baseline=baseline,
                    )
                    self._validate_type_readback(profile, type_readback)
                    self.driver.set_average_count_v2(
                        request.average_count,
                        baseline=baseline,
                    )
                    configuration_readback = self.driver.get_average_configuration_v2(
                        baseline=baseline,
                    )
                    self._validate_configuration_readback(
                        profile,
                        request,
                        configuration_readback,
                    )
                    stopped_recheck = self.driver.get_acquisition_run_state()
                    self._validate_stopped_recheck(stopped_recheck, baseline)
                    acquisition_completion = self.driver.acquire_average_single_v2(
                        baseline=baseline,
                        deadline=context.main_deadline,
                    )
                    self._validate_acquisition_completion(
                        acquisition_completion,
                        baseline,
                    )
                    if self.driver.get_device_average_complete_v2(baseline=baseline) is not True:
                        raise DataError("average capture completion_unproven")
                    waveform = self.driver.fetch_average_waveform_bounded(
                        request.channels[0],
                        points=normalize_waveform_points(request.points),
                        baseline=baseline,
                    )
                    if not isinstance(waveform, WaveformData) or waveform.channel != request.channels[0]:
                        raise DataError(
                            "average capture V2 driver returned a waveform with a mismatched channel"
                        )
                    self._require_binary_main_query(context)
                    completion = ScopeAverageCompletionProofV2(
                        evidence="device_average_complete",
                        mechanism=request.mechanism,
                        configured_average_count=request.average_count,
                        configuration_readback=configuration_readback,
                        acquisition_completion=acquisition_completion,
                        device_average_complete=True,
                        contract_id=profile.completion_contract_id,
                        context_id=baseline.context_id,
                        session_epoch=baseline.session_epoch,
                        acquisition_baseline_nonce_digest=sha256(
                            baseline.acquisition_baseline.baseline_nonce.encode("ascii")
                        ).hexdigest()[:16],
                    )
                error_executor.run(context, phase="after")
            except BaseException as exc:
                primary = exc
                if error_executor.wants("after") and not context.has_phase(OperationPhase.ERROR_AFTER):
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
                (
                    cleanup_error,
                    cleanup_snapshot,
                    restore_result,
                    verification,
                    cleanup_diagnostics,
                ) = self._cleanup(
                    context,
                    handle,
                    baseline,
                    profile,
                    failed=primary is not None,
                )
            context.complete()
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                    extra={"average_capture_cleanup": cleanup_diagnostics},
                )
                raise primary
            if cleanup_error is not None:
                self._attach_diagnostics(
                    cleanup_error,
                    context,
                    error_executor,
                    extra={"average_capture_cleanup": cleanup_diagnostics},
                )
                raise cleanup_error
            assert baseline is not None
            assert identity is not None
            assert completion is not None
            assert waveform is not None
            assert cleanup_snapshot is not None
            assert restore_result is not None
            assert verification is not None
            result = ScopeAverageCaptureResultV2(
                request=request,
                waveforms=(waveform,),
                configuration_before=baseline.snapshot.configuration,
                configuration_after=cleanup_snapshot.configuration,
                run_state_before=baseline.snapshot.run_state,
                run_state_after=cleanup_snapshot.run_state,
                completion=completion,
                restore=restore_result,
                verification=verification,
            )
            return AverageCaptureV2ExecutionResult(
                value=result,
                identity=identity,
                diagnostics={
                    "scope_operation": context.artifact(),
                    "error_check": dict(error_executor.artifact),
                    "average_capture_cleanup": cleanup_diagnostics,
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
        baseline: ScopeAverageCaptureBaseline | None,
        profile: ScopeAverageCaptureProfileV2,
        *,
        failed: bool,
    ) -> tuple[
        BaseException | None,
        ScopeAverageCaptureStateSnapshot | None,
        ScopeAverageCaptureRestoreResult | None,
        ScopeAverageCaptureVerification | None,
        dict[str, object],
    ]:
        assert baseline is not None
        restore_result: ScopeAverageCaptureRestoreResult | None = None
        verification: ScopeAverageCaptureVerification | None = None
        observed: ScopeAverageCaptureStateSnapshot | None = None
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
                    restore_result = self.driver.restore_average_capture_state(baseline)
                    if not isinstance(restore_result, ScopeAverageCaptureRestoreResult):
                        raise TypeError(
                            "restore_average_capture_state() returned an invalid result"
                        )
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("average capture restore did not complete")
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
                    observed = self.driver.verify_average_capture_state_restored(baseline)
                    self._validate_snapshot(observed, tuple(handle.fields))
                    matched = observed == baseline.snapshot
                    verification = ScopeAverageCaptureVerification(
                        status="verified" if matched else "mismatch",
                        verified_fields=tuple(handle.fields) if matched else (),
                        mismatched_fields=() if matched else tuple(handle.fields),
                    )
                    verification.validate_for(baseline)
                    context.finish_verification(
                        handle,
                        authorization,
                        io_kind="query",
                        verified_fields=tuple(handle.fields),
                        matched=matched,
                    )
            except BaseException as exc:
                error = error or exc
        return error, observed, restore_result, verification, {
            "restore": _json_safe(restore_result),
            "verification": _json_safe(verification),
        }

    def _profile(self) -> ScopeAverageCaptureProfileV2:
        extensions = self.descriptor.scope_extensions
        profile = extensions.average_capture_profile_v2 if extensions is not None else None
        if profile is None:
            raise ConfigError("average capture V2 requires an average capture profile")
        return profile

    @staticmethod
    def _validate_snapshot(
        snapshot: object,
        fields: tuple[str, ...],
    ) -> None:
        if not isinstance(snapshot, ScopeAverageCaptureStateSnapshot):
            raise DataError("average capture state snapshot has an invalid type")
        if tuple(snapshot.captured_fields) != fields:
            raise DataError("average capture snapshot fields do not match the profile")

    @staticmethod
    def _make_baseline(
        handle: ScopeBaselineHandle,
        snapshot: ScopeAverageCaptureStateSnapshot,
    ) -> ScopeAverageCaptureBaseline:
        assert snapshot.trigger_token is not None
        assert snapshot.acquisition_token is not None
        acquisition_baseline = ScopeAcquisitionControlBaseline(
            context_id=handle.context_id,
            session_epoch=handle.session_epoch,
            baseline_nonce=uuid4().hex,
            snapshot=ScopeAcquisitionControlSnapshot(
                run_state=snapshot.run_state,
                trigger_state_token=snapshot.trigger_token,
                acquisition_state_token=snapshot.acquisition_token,
            ),
            restore_order=("scope.run_state", "scope.trigger", "scope.acquisition"),
        )
        return ScopeAverageCaptureBaseline(
            context_id=handle.context_id,
            session_epoch=handle.session_epoch,
            baseline_nonce=handle.baseline_nonce,
            snapshot=snapshot,
            restore_order=tuple(handle.restore_order),
            acquisition_baseline=acquisition_baseline,
        )

    def _read_and_validate_input_state(self, request: ScopeAverageCaptureRequestV2) -> None:
        state = self.driver.get_channel_input_state_v2(request.channels[0])
        if not isinstance(state, ScopeChannelInputStateV2):
            raise DataError("average capture V2 input-state driver returned an invalid result")
        if state.channel != request.channels[0]:
            raise DataError("average capture V2 input-state driver returned the wrong channel")
        if state.termination == "high_z":
            return
        if state.termination == "50_ohm" and request.allow_50ohm is True:
            return
        if state.termination == "50_ohm":
            raise ConfigError(
                "average capture V2 requires high impedance unless allow_50ohm=True"
            )
        raise ConfigError("average capture V2 rejects unknown input termination")

    @staticmethod
    def _validate_type_readback(
        profile: ScopeAverageCaptureProfileV2,
        configuration: object,
    ) -> None:
        if not isinstance(configuration, ScopeAverageConfigurationV2):
            raise DataError("average capture type readback has an invalid type")
        try:
            profile.validate_configuration(configuration)
        except (TypeError, ValueError) as exc:
            raise DataError(f"average capture type readback is invalid: {exc}") from exc

    @staticmethod
    def _validate_configuration_readback(
        profile: ScopeAverageCaptureProfileV2,
        request: ScopeAverageCaptureRequestV2,
        configuration: object,
    ) -> None:
        if not isinstance(configuration, ScopeAverageConfigurationV2):
            raise DataError("average capture configuration readback has an invalid type")
        try:
            profile.validate_configuration(configuration, request=request)
        except (TypeError, ValueError) as exc:
            raise DataError(f"average capture configuration readback is invalid: {exc}") from exc

    @staticmethod
    def _validate_stopped_recheck(
        state: object,
        baseline: ScopeAverageCaptureBaseline,
    ) -> None:
        if not isinstance(state, ScopeAcquisitionRunState):
            raise DataError("average capture stopped recheck has an invalid type")
        if state.phase != "stopped" or state != baseline.snapshot.run_state:
            raise DataError("average capture stopped recheck drifted from the baseline")

    def _validate_acquisition_completion(
        self,
        completion: object,
        baseline: ScopeAverageCaptureBaseline,
    ) -> None:
        if not isinstance(completion, ScopeAcquisitionCompletion):
            raise DataError("average capture single completion has an invalid type")
        if completion.proof == "single_mode_readback_then_stopped":
            raise DataError("average capture V2 does not accept a terminal STOP completion proof")
        extensions = self.descriptor.scope_extensions
        profile = extensions.acquisition_control_profile if extensions is not None else None
        if not isinstance(profile, ScopeAcquisitionControlProfile):
            raise ConfigError("average capture V2 requires an acquisition control profile")
        try:
            validate_acquisition_completion(
                completion,
                baseline=baseline.acquisition_baseline,
                profile=profile,
            )
        except (TypeError, ValueError) as exc:
            raise DataError(f"average capture single completion is invalid: {exc}") from exc

    @staticmethod
    def _require_binary_main_query(context: ScopeOperationContextCoordinator) -> None:
        ledger = context.binary_ledger
        if ledger is None:
            raise DataError("average capture V2 has no binary ledger")
        snapshot = ledger.snapshot()
        if snapshot["remaining_query_count"] == snapshot["query_max_count"]:
            raise DataError("average capture V2 driver did not issue a binary query")

    @staticmethod
    def _verify_identity_value(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DataError("scope identity verification returned an empty response")
        return value

    def _verify_identity(self) -> str:
        return self._verify_identity_value(self.driver.idn())

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


__all__ = ["AverageCaptureV2ExecutionResult", "ScopeAverageCaptureExecutor"]
