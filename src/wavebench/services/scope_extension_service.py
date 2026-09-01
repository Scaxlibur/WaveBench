"""Service orchestration for the scope R1.3 extension contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar

from wavebench.errors import ConfigError, DataError, InstrumentError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.scope_extension_capabilities import (
    validate_scope_descriptor,
    validate_experimental_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ErrorCheckSpec,
    ScopeAcquisitionCompletion,
    ScopeAcquisitionControlBaseline,
    ScopeAcquisitionControlProfile,
    ScopeAcquisitionControlSnapshot,
    ScopeAcquisitionRunState,
    ScopeBaselineRestoreResult,
    ScopeBaselineVerification,
    ScopeChannelDisplayBaseline,
    ScopeChannelDisplayProfileV2,
    ScopeChannelDisplayRequest,
    ScopeChannelDisplayRestoreResult,
    ScopeChannelDisplayResult,
    ScopeChannelDisplayState,
    ScopeContinuousAcquisitionRequest,
    ScopeFocusBaseline,
    ScopeFocusProfileV2,
    ScopeFocusRequest,
    ScopeFocusRestoreResult,
    ScopeFocusResult,
    ScopeFocusState,
    ScopeScreenshot,
    ScopeScreenshotBaseline,
    ScopeScreenshotProfile,
    ScopeScreenshotRequest,
    ScopeScreenshotRestoreResult,
    ScopeScreenshotStateSnapshot,
    ScopeScreenshotVariant,
    ScopeScreenshotVerification,
    ScopeTraceData,
    ScopeTraceMetadata,
    ScopeTraceProfile,
    ScopeTraceRef,
    ScopeTraceTransferBaseline,
    ScopeTraceTransferRestoreResult,
    ScopeTraceTransferStateSnapshot,
    ScopeTraceTransferVerification,
    validate_acquisition_completion,
)
from wavebench.services.access_policy import AccessMode, access_policy
from wavebench.transport.session import InstrumentSessionState, SessionHealth

from .operation_specs import OperationSpec, require_operation_spec
from .scope_error_policy import ScopeErrorPolicyExecutor, resolve_error_check
from .scope_extension_specs import EXPERIMENTAL_SCOPE_OPERATION_SPECS
from .scope_phase_coordinator import (
    OperationPhase,
    ScopeBaselineHandle,
    ScopeBinaryLimits,
    ScopeOperationContextCoordinator,
)


_T = TypeVar("_T")
_TEXT_READ_IO = {"query", "query_float_list", "query_opc"}
_MAIN_TEXT_IO = {*_TEXT_READ_IO, "write", "write_bytes"}
_TRACE_MAIN_IO = {*_MAIN_TEXT_IO, "query_binary"}
_SCREENSHOT_MAIN_IO = {*_MAIN_TEXT_IO, "query_binary"}
SCOPE_EXTENSION_RESULT_SCHEMA = "wavebench.scope.result.v1"


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
class ScopeExtensionOperationResult:
    value: object
    diagnostics: Mapping[str, object]
    observed_state: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))
        if self.observed_state is not None:
            object.__setattr__(
                self,
                "observed_state",
                MappingProxyType(dict(self.observed_state)),
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCOPE_EXTENSION_RESULT_SCHEMA,
            "result": _public_result_summary(self.value),
            "diagnostics": _json_safe(self.diagnostics),
            "observed_state": _json_safe(self.observed_state),
        }


def _public_result_summary(value: object) -> object:
    if isinstance(value, ScopeScreenshot):
        return {
            "media_type": value.media_type,
            "dimensions": {"width_px": value.width_px, "height_px": value.height_px},
            "requested": _json_safe(value.requested),
            "effective_request": _json_safe(value.effective),
            "framing": value.framing.value,
            "payload_bytes": len(value.data),
            "payload_sha256": sha256(value.data).hexdigest(),
        }
    if isinstance(value, ScopeTraceData):
        raw = value.values.tobytes(order="C")
        return {
            "metadata": _json_safe(value.metadata),
            "integrity": {
                "points": int(value.values.size),
                "dtype": str(value.values.dtype),
                "payload_bytes": len(raw),
                "payload_sha256": sha256(raw).hexdigest(),
            },
        }
    return _json_safe(value)


@dataclass(slots=True)
class ExperimentalScopeExtensionService:
    """Internal R1.3 Service; construction itself requires an explicit feature gate."""

    driver: object
    descriptor: InstrumentDescriptor
    session_state: InstrumentSessionState
    connection_timeout_ms: int
    access: AccessMode = "read_write"
    instrument_error_default: ErrorCheckSpec | None = None
    enabled: bool = False

    def __post_init__(self) -> None:
        if not self.enabled:
            raise ConfigError("experimental scope extensions are disabled")
        if isinstance(self.connection_timeout_ms, bool) or not isinstance(
            self.connection_timeout_ms, int
        ) or self.connection_timeout_ms < 1:
            raise ValueError("connection_timeout_ms must be a positive integer")
        if "scope.idn" not in self.descriptor.capabilities:
            raise ConfigError("experimental scope operations require scope.idn")
        if not callable(getattr(self.driver, "idn", None)):
            raise ConfigError("experimental scope operations require callable idn()")
        validate_experimental_scope_descriptor(
            self.descriptor,
            driver=self.driver,
            enabled=True,
        )

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self.descriptor.capabilities)

    def screenshot_profile(
        self,
        *,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        spec = self._require("scope.screenshot_profile")
        static = self._screenshot_profile()
        context = self._context(spec, deadline=deadline)
        try:
            self._identity_preflight(context)
            phase = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io=_TEXT_READ_IO,
                fields={"scope.identity"},
                max_steps=2,
            )
            with context.authorize_phase(phase):
                runtime = self.driver.get_screenshot_profile()
            profile = self._validate_screenshot_profile_narrowing(static, runtime)
            context.complete()
            return self._result(context, value=profile)
        except BaseException as exc:
            context.complete()
            self._attach_diagnostics(exc, context, None)
            raise

    def screenshot_v2(
        self,
        request: ScopeScreenshotRequest,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        if not isinstance(request, ScopeScreenshotRequest):
            raise DataError("screenshot request has an invalid type")
        spec = self._require("scope.screenshot_v2")
        static_profile = self._screenshot_profile()
        variant = static_profile.select(request)
        profile_limits = ScopeBinaryLimits(
            response_max_bytes=variant.response_max_bytes,
            operation_max_bytes=variant.operation_max_bytes,
            query_max_count=variant.query_max_count,
            resynchronization_max_bytes=variant.resynchronization_max_bytes,
        )
        context = self._context(
            spec,
            deadline=deadline,
            profile_binary_limits=profile_limits,
            transport_trailing=bytes.fromhex(variant.transport_trailing_hex),
        )
        try:
            error_executor = self._error_executor(spec, error_check, context)
        except BaseException:
            context.complete()
            raise
        baseline: ScopeScreenshotBaseline | None = None
        handle: ScopeBaselineHandle | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        screenshot: ScopeScreenshot | None = None
        main_entered = False
        cleanup_diagnostics: dict[str, object] = {}
        try:
            fields = tuple(variant.changed_fields)
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", *fields},
                max_steps=2 + variant.snapshot_max_steps,
            )
            with context.authorize_phase(preflight) as authorization:
                self._verify_identity()
                runtime_profile = self.driver.get_screenshot_profile()
                runtime_profile = self._validate_screenshot_profile_narrowing(
                    static_profile,
                    runtime_profile,
                )
                runtime_variant = runtime_profile.select(request)
                self._assert_same_screenshot_state_contract(variant, runtime_variant)
                snapshot = None
                if fields:
                    snapshot = self.driver.snapshot_screenshot_state(fields)
                    self._validate_screenshot_snapshot(snapshot, fields)
                    handle = context.create_baseline(
                        kind="screenshot",
                        fields=fields,
                        restore_order=variant.restore_order,
                    )
                    baseline = ScopeScreenshotBaseline(
                        context_id=handle.context_id,
                        session_epoch=handle.session_epoch,
                        baseline_nonce=handle.baseline_nonce,
                        snapshot=snapshot,
                        restore_order=variant.restore_order,
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
                allowed_io=_SCREENSHOT_MAIN_IO,
                fields=set(spec.changed_fields),
                max_steps=64,
            )
            try:
                with context.authorize_phase(main):
                    main_entered = True
                    error_executor.mark_main_sent()
                    screenshot = self.driver.capture_screenshot(
                        request,
                        baseline=baseline,
                    )
                    self._validate_screenshot_result(screenshot, variant)
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
            if handle is not None and main_entered:
                context.mark_cleanup_required()
                cleanup_error, cleanup_diagnostics = self._cleanup_screenshot(
                    context,
                    handle,
                    baseline,
                    variant,
                    failed=primary is not None,
                )
            context.complete()
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                    extra={"screenshot": cleanup_diagnostics},
                )
                raise primary
            if cleanup_error is not None:
                self._attach_diagnostics(
                    cleanup_error,
                    context,
                    error_executor,
                    extra={"screenshot": cleanup_diagnostics},
                )
                raise cleanup_error
            assert screenshot is not None
            return self._result(
                context,
                value=screenshot,
                error_executor=error_executor,
                extra={"screenshot": cleanup_diagnostics},
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(exc, context, error_executor)
            raise

    def acquisition_run_state(
        self,
        *,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        spec = self._require("scope.acquisition_run_state")
        context = self._context(spec, deadline=deadline)
        try:
            self._identity_preflight(context)
            main = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io={"query"},
                fields={"scope.identity"},
                max_steps=1,
            )
            with context.authorize_phase(main):
                state = self.driver.get_acquisition_run_state()
            if not isinstance(state, ScopeAcquisitionRunState):
                raise DataError("get_acquisition_run_state() returned an invalid result")
            context.complete()
            return self._result(
                context,
                value=state,
                observed_state={"run_state": asdict(state)},
            )
        except BaseException as exc:
            context.complete()
            self._attach_diagnostics(exc, context, None)
            raise

    def configure_channel_display_v2(
        self,
        request: ScopeChannelDisplayRequest,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        if not isinstance(request, ScopeChannelDisplayRequest):
            raise DataError("channel display request has an invalid type")
        spec = self._require("scope.channel_display_configure_v2")
        profile = self._channel_display_profile_v2()
        try:
            profile.validate_request(request)
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc
        context = self._context(spec, deadline=deadline)
        try:
            error_executor = self._error_executor(spec, error_check, context)
        except BaseException:
            context.complete()
            raise
        handle: ScopeBaselineHandle | None = None
        baseline: ScopeChannelDisplayBaseline | None = None
        before: ScopeChannelDisplayState | None = None
        after: ScopeChannelDisplayState | None = None
        value: ScopeChannelDisplayResult | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        cleanup_diagnostics: dict[str, object] = {}
        write_attempted = False
        try:
            fields = ("scope.channel_display",)
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", *fields},
                max_steps=1 + profile.snapshot_max_steps,
            )
            with context.authorize_phase(preflight) as authorization:
                self._verify_identity()
                before = self.driver.get_channel_display_state_v2(request.channel)
                self._validate_channel_display_state(before, profile, request.channel)
                if before.enabled is not request.enabled:
                    handle = context.create_baseline(
                        kind="channel_display",
                        fields=fields,
                        restore_order=fields,
                    )
                    baseline = ScopeChannelDisplayBaseline(
                        context_id=handle.context_id,
                        session_epoch=handle.session_epoch,
                        baseline_nonce=handle.baseline_nonce,
                        snapshot=before,
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
                allowed_io={"write", "query", "query_opc"},
                fields=set(spec.changed_fields),
                max_steps=profile.configure_max_steps,
            )
            try:
                with context.authorize_phase(main):
                    assert before is not None
                    if baseline is not None:
                        write_attempted = True
                        error_executor.mark_main_sent()
                        self.driver.configure_channel_display_v2(
                            request,
                            baseline=baseline,
                        )
                        after = self.driver.get_channel_display_state_v2(request.channel)
                        self._validate_channel_display_state(after, profile, request.channel)
                        if after.enabled is not request.enabled:
                            raise DataError(
                                "channel display postcondition does not match the request"
                            )
                    else:
                        after = before
                    value = ScopeChannelDisplayResult(
                        request=request,
                        before=before,
                        after=after,
                        write_performed=write_attempted,
                    )
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
            if primary is None and handle is not None:
                context.consume_baseline_after_success(handle)
            elif primary is not None and handle is not None and write_attempted:
                context.mark_cleanup_required()
                cleanup_error, cleanup_diagnostics = self._cleanup_channel_display(
                    context,
                    handle,
                    baseline,
                    profile,
                )
            context.complete()
            operation_details = {
                "request": _json_safe(request),
                "before": _json_safe(before),
                "after": _json_safe(after),
            }
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                    extra={
                        "channel_display": operation_details,
                        "cleanup": cleanup_diagnostics,
                    },
                )
                raise primary
            assert value is not None and after is not None and before is not None
            return self._result(
                context,
                value=value,
                error_executor=error_executor,
                observed_state={
                    "before": asdict(before),
                    "after": asdict(after),
                },
                extra={
                    "postcondition": {
                        "status": "verified",
                        "fields": ["scope.channel_display"],
                        "write_performed": value.write_performed,
                    },
                    "cleanup": cleanup_diagnostics,
                },
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(
                    exc,
                    context,
                    error_executor,
                    extra={
                        "channel_display": {
                            "request": _json_safe(request),
                            "before": _json_safe(before),
                            "after": _json_safe(after),
                        }
                    },
                )
            raise

    def configure_focus_v2(
        self,
        request: ScopeFocusRequest,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        if not isinstance(request, ScopeFocusRequest):
            raise DataError("focus request has an invalid type")
        spec = self._require("scope.focus_configure_v2")
        profile = self._focus_profile_v2()
        try:
            profile.validate_request(request)
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc
        context = self._context(spec, deadline=deadline)
        try:
            error_executor = self._error_executor(spec, error_check, context)
        except BaseException:
            context.complete()
            raise
        handle: ScopeBaselineHandle | None = None
        baseline: ScopeFocusBaseline | None = None
        before: ScopeFocusState | None = None
        after: ScopeFocusState | None = None
        value: ScopeFocusResult | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        cleanup_diagnostics: dict[str, object] = {}
        write_attempted = False
        all_fields = (
            "scope.timebase",
            "scope.channel_vertical",
            "scope.channel_display",
        )
        try:
            restore_order = profile.restore_order_for(request)
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", *all_fields},
                max_steps=1 + profile.snapshot_max_steps,
            )
            with context.authorize_phase(preflight) as authorization:
                self._verify_identity()
                before = self.driver.get_focus_state_v2()
                self._validate_focus_state(before, profile)
                if not profile.request_satisfied(before, request):
                    handle = context.create_baseline(
                        kind="focus",
                        fields=restore_order,
                        restore_order=restore_order,
                    )
                    baseline = ScopeFocusBaseline(
                        context_id=handle.context_id,
                        session_epoch=handle.session_epoch,
                        baseline_nonce=handle.baseline_nonce,
                        snapshot=before,
                        restore_order=restore_order,
                    )
                    context.pass_baseline_to_main(handle)
                context.complete_phase_verification(
                    authorization,
                    io_kind="query",
                    fields={"scope.identity", *all_fields},
                )

            error_executor.run(context, phase="before")
            main = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io={"write", "query", "query_opc"},
                fields=set(spec.changed_fields),
                max_steps=profile.configure_max_steps,
            )
            try:
                with context.authorize_phase(main):
                    assert before is not None
                    if baseline is not None:
                        write_attempted = True
                        error_executor.mark_main_sent()
                        self.driver.configure_focus_v2(request, baseline=baseline)
                        after = self.driver.get_focus_state_v2()
                        self._validate_focus_state(after, profile)
                        if not profile.transition_matches(before, after, request):
                            raise DataError("focus postcondition does not match the request")
                    else:
                        after = before
                    value = ScopeFocusResult(
                        request=request,
                        before=before,
                        after=after,
                        write_performed=write_attempted,
                    )
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
            if primary is None and handle is not None:
                context.consume_baseline_after_success(handle)
            elif primary is not None and handle is not None and write_attempted:
                context.mark_cleanup_required()
                cleanup_error, cleanup_diagnostics = self._cleanup_focus(
                    context,
                    handle,
                    baseline,
                    profile,
                )
            context.complete()
            operation_details = {
                "request": _json_safe(request),
                "before": _json_safe(before),
                "after": _json_safe(after),
            }
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                    extra={
                        "focus": operation_details,
                        "cleanup": cleanup_diagnostics,
                    },
                )
                raise primary
            assert value is not None and after is not None and before is not None
            return self._result(
                context,
                value=value,
                error_executor=error_executor,
                observed_state={
                    "before": asdict(before),
                    "after": asdict(after),
                },
                extra={
                    "postcondition": {
                        "status": "verified",
                        "fields": list(all_fields),
                        "write_performed": value.write_performed,
                    },
                    "cleanup": cleanup_diagnostics,
                },
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(
                    exc,
                    context,
                    error_executor,
                    extra={
                        "focus": {
                            "request": _json_safe(request),
                            "before": _json_safe(before),
                            "after": _json_safe(after),
                        }
                    },
                )
            raise

    def start_acquisition(
        self,
        request: ScopeContinuousAcquisitionRequest,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        if not isinstance(request, ScopeContinuousAcquisitionRequest):
            raise DataError("continuous acquisition request has an invalid type")
        if request.trigger_mode not in self._acquisition_profile().supported_continuous_modes:
            raise ConfigError("requested continuous trigger mode is unsupported")
        return self._run_acquisition_change(
            operation="scope.acquisition_start",
            action=lambda baseline, _deadline: self.driver.start_continuous(
                trigger_mode=request.trigger_mode,
                baseline=baseline,
            ),
            validate=lambda value, baseline, profile: self._validate_start_result(
                value,
                request,
                baseline,
                profile,
            ),
            error_check=error_check,
            deadline=deadline,
        )

    def acquire_single(
        self,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        return self._run_acquisition_change(
            operation="scope.acquisition_single",
            action=lambda baseline, main_deadline: self.driver.acquire_single(
                baseline=baseline,
                deadline=main_deadline,
            ),
            validate=self._validate_single_result,
            error_check=error_check,
            deadline=deadline,
        )

    def stop_acquisition(
        self,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        spec = self._require("scope.acquisition_stop")
        context = self._context(spec, deadline=deadline)
        try:
            error_executor = self._error_executor(spec, error_check, context)
        except BaseException:
            context.complete()
            raise
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        result: ScopeAcquisitionRunState | None = None
        main_entered = False
        try:
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", "scope.run_state"},
                max_steps=2,
            )
            with context.authorize_phase(preflight) as authorization:
                self._verify_identity()
                before = self.driver.get_acquisition_run_state()
                if not isinstance(before, ScopeAcquisitionRunState):
                    raise DataError("get_acquisition_run_state() returned an invalid result")
                if before.phase in {"unknown", "error"}:
                    raise ConfigError("normal acquisition stop requires a known non-error phase")
                context.complete_phase_verification(
                    authorization,
                    io_kind="query",
                    fields={"scope.identity", "scope.run_state"},
                )
            error_executor.run(context, phase="before")
            main = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io=_MAIN_TEXT_IO,
                fields={"scope.run_state", "scope.error_queue"},
                max_steps=8,
            )
            try:
                with context.authorize_phase(main):
                    main_entered = True
                    error_executor.mark_main_sent()
                    result = self.driver.stop_acquisition()
                    if not isinstance(result, ScopeAcquisitionRunState) or result.phase != "stopped":
                        raise DataError("acquisition stop postcondition is not proven")
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
            if primary is not None and main_entered:
                context.mark_cleanup_required()
                cleanup_error = self._cleanup_stop(context)
            context.complete()
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                )
                raise primary
            if cleanup_error is not None:
                raise cleanup_error
            assert result is not None
            return self._result(
                context,
                value=result,
                error_executor=error_executor,
                observed_state={"run_state": asdict(result)},
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(exc, context, error_executor)
            raise

    def trace_metadata(
        self,
        source: ScopeTraceRef,
        *,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        if not isinstance(source, ScopeTraceRef):
            raise DataError("trace source has an invalid type")
        spec = self._require("scope.trace_metadata")
        profile = self._trace_profile()
        self._validate_trace_source(source, profile, require_fetchable=False)
        context = self._context(spec, deadline=deadline)
        try:
            self._identity_preflight(context)
            main = context.make_phase_spec(
                OperationPhase.MAIN,
                allowed_io={"query"},
                fields={"scope.identity"},
                max_steps=16,
            )
            with context.authorize_phase(main):
                metadata = self.driver.get_trace_metadata(source)
            if not isinstance(metadata, ScopeTraceMetadata) or metadata.source != source:
                raise DataError("get_trace_metadata() returned an inconsistent result")
            context.complete()
            return self._result(context, value=metadata)
        except BaseException as exc:
            context.complete()
            self._attach_diagnostics(exc, context, None)
            raise

    def fetch_trace(
        self,
        source: ScopeTraceRef,
        *,
        points: str | int = "dmax",
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        if not isinstance(source, ScopeTraceRef):
            raise DataError("trace source has an invalid type")
        spec = self._require("scope.fetch_trace")
        profile = self._trace_profile()
        self._validate_trace_source(source, profile, require_fetchable=True)
        self._validate_trace_points(points, profile)
        if set(profile.restore_order) != set(spec.verification_fields):
            raise ConfigError(
                "trace profile must close every transfer verification field before registration"
            )
        context = self._context(spec, deadline=deadline)
        try:
            error_executor = self._error_executor(spec, error_check, context)
        except BaseException:
            context.complete()
            raise
        handle: ScopeBaselineHandle | None = None
        baseline: ScopeTraceTransferBaseline | None = None
        trace: ScopeTraceData | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        cleanup_diagnostics: dict[str, object] = {}
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
                self._verify_identity()
                metadata = self.driver.get_trace_metadata(source)
                if (
                    not isinstance(metadata, ScopeTraceMetadata)
                    or metadata.source != source
                    or not metadata.fetchable
                ):
                    raise DataError("trace metadata does not prove a fetchable requested source")
                snapshot = self.driver.snapshot_trace_transfer_state(fields)
                self._validate_trace_snapshot(snapshot, fields)
                handle = context.create_baseline(
                    kind="trace_transfer",
                    fields=fields,
                    restore_order=fields,
                )
                baseline = ScopeTraceTransferBaseline(
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
                allowed_io=_TRACE_MAIN_IO,
                fields=set(spec.changed_fields),
                max_steps=512,
            )
            try:
                with context.authorize_phase(main):
                    main_entered = True
                    error_executor.mark_main_sent()
                    trace = self.driver.fetch_trace(
                        source,
                        points=points,
                        baseline=baseline,
                    )
                    self._validate_trace_result(trace, source, profile, points)
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
            if handle is not None and main_entered:
                context.mark_cleanup_required()
                cleanup_error, cleanup_diagnostics = self._cleanup_trace(
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
                    extra={"trace_cleanup": cleanup_diagnostics},
                )
                raise primary
            if cleanup_error is not None:
                self._attach_diagnostics(
                    cleanup_error,
                    context,
                    error_executor,
                    extra={"trace_cleanup": cleanup_diagnostics},
                )
                raise cleanup_error
            assert trace is not None
            return self._result(
                context,
                value=trace,
                error_executor=error_executor,
                extra={"trace_cleanup": cleanup_diagnostics},
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(exc, context, error_executor)
            raise

    def _run_acquisition_change(
        self,
        *,
        operation: str,
        action: Callable[[ScopeAcquisitionControlBaseline, float], _T],
        validate: Callable[
            [_T, ScopeAcquisitionControlBaseline, ScopeAcquisitionControlProfile],
            None,
        ],
        error_check: ErrorCheckSpec | None,
        deadline: float | None,
    ) -> ScopeExtensionOperationResult:
        spec = self._require(operation)
        profile = self._acquisition_profile()
        context = self._context(spec, deadline=deadline)
        try:
            error_executor = self._error_executor(spec, error_check, context)
        except BaseException:
            context.complete()
            raise
        handle: ScopeBaselineHandle | None = None
        baseline: ScopeAcquisitionControlBaseline | None = None
        value: _T | None = None
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        cleanup_diagnostics: dict[str, object] = {}
        main_entered = False
        try:
            fields = ("scope.run_state", "scope.trigger", "scope.acquisition")
            restore_order = ("scope.run_state", *profile.failure_restore_order)
            preflight = context.make_phase_spec(
                OperationPhase.PREFLIGHT,
                allowed_io={"query"},
                fields={"scope.identity", *fields},
                max_steps=1 + profile.snapshot_max_steps,
            )
            with context.authorize_phase(preflight) as authorization:
                self._verify_identity()
                snapshot = self.driver.snapshot_acquisition_control()
                self._validate_acquisition_snapshot(snapshot)
                if snapshot.run_state.phase not in {"stopped", "ready", "complete"}:
                    raise ConfigError("acquisition control precondition phase is not allowed")
                handle = context.create_baseline(
                    kind="acquisition_control",
                    fields=fields,
                    restore_order=restore_order,
                )
                baseline = ScopeAcquisitionControlBaseline(
                    context_id=handle.context_id,
                    session_epoch=handle.session_epoch,
                    baseline_nonce=handle.baseline_nonce,
                    snapshot=snapshot,
                    restore_order=restore_order,
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
                allowed_io=_MAIN_TEXT_IO,
                fields=set(spec.changed_fields),
                max_steps=128,
            )
            try:
                with context.authorize_phase(main):
                    main_entered = True
                    error_executor.mark_main_sent()
                    assert baseline is not None
                    value = action(baseline, context.main_deadline)
                    validate(value, baseline, profile)
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
            if primary is None:
                assert handle is not None
                context.consume_baseline_after_success(handle)
            elif handle is not None and main_entered:
                context.mark_cleanup_required()
                cleanup_error, cleanup_diagnostics = self._cleanup_acquisition(
                    context,
                    handle,
                    baseline,
                    profile,
                )
            context.complete()
            if primary is not None:
                self._attach_diagnostics(
                    primary,
                    context,
                    error_executor,
                    cleanup_error=cleanup_error,
                    extra={"cleanup": cleanup_diagnostics},
                )
                raise primary
            assert value is not None
            return self._result(
                context,
                value=value,
                error_executor=error_executor,
                extra={"postcondition": {"status": "verified"}},
            )
        except BaseException as exc:
            if not context.terminal:
                context.complete()
            if not hasattr(exc, "scope_operation_diagnostics"):
                self._attach_diagnostics(exc, context, error_executor)
            raise

    def _cleanup_screenshot(
        self,
        context: ScopeOperationContextCoordinator,
        handle: ScopeBaselineHandle,
        baseline: ScopeScreenshotBaseline | None,
        variant: ScopeScreenshotVariant,
        *,
        failed: bool,
    ) -> tuple[BaseException | None, dict[str, object]]:
        assert baseline is not None
        restore_result: ScopeScreenshotRestoreResult | None = None
        verification: ScopeScreenshotVerification | None = None
        error: BaseException | None = None
        phase = OperationPhase.FAILURE_CLEANUP if failed else OperationPhase.SUCCESS_RESTORE
        try:
            restore = context.make_phase_spec(
                phase,
                allowed_io={"write"},
                fields=handle.fields,
                max_steps=variant.restore_max_steps,
            )
            with context.authorize_phase(restore):
                context.begin_restore(handle)
                try:
                    restore_result = self.driver.restore_screenshot_state(baseline)
                    if not isinstance(restore_result, ScopeScreenshotRestoreResult):
                        raise TypeError("restore_screenshot_state() returned an invalid result")
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("screenshot state restore did not complete")
        except BaseException as exc:
            error = exc

        if self.session_state.health is not SessionHealth.POISONED:
            try:
                verify = context.make_phase_spec(
                    OperationPhase.CLEANUP_VERIFICATION,
                    allowed_io={"query"},
                    fields=handle.fields,
                    max_steps=variant.verify_max_steps,
                )
                with context.authorize_phase(verify) as authorization:
                    context.begin_verification(handle)
                    observed = self.driver.verify_screenshot_state_restored(
                        tuple(handle.fields),
                        baseline,
                    )
                    self._validate_screenshot_snapshot(observed, tuple(handle.fields))
                    matched = observed == baseline.snapshot
                    verification = ScopeScreenshotVerification(
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

    def _cleanup_trace(
        self,
        context: ScopeOperationContextCoordinator,
        handle: ScopeBaselineHandle,
        baseline: ScopeTraceTransferBaseline | None,
        profile: ScopeTraceProfile,
        *,
        failed: bool,
    ) -> tuple[BaseException | None, dict[str, object]]:
        assert baseline is not None
        restore_result: ScopeTraceTransferRestoreResult | None = None
        verification: ScopeTraceTransferVerification | None = None
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
                    restore_result = self.driver.restore_trace_transfer_state(baseline)
                    if not isinstance(restore_result, ScopeTraceTransferRestoreResult):
                        raise TypeError("restore_trace_transfer_state() returned an invalid result")
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("trace transfer restore did not complete")
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
                    observed = self.driver.verify_trace_transfer_state_restored(baseline)
                    self._validate_trace_snapshot(observed, tuple(handle.fields))
                    matched = observed == baseline.snapshot
                    verification = ScopeTraceTransferVerification(
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

    def _cleanup_acquisition(
        self,
        context: ScopeOperationContextCoordinator,
        handle: ScopeBaselineHandle,
        baseline: ScopeAcquisitionControlBaseline | None,
        profile: ScopeAcquisitionControlProfile,
    ) -> tuple[BaseException | None, dict[str, object]]:
        assert baseline is not None
        restore_result: ScopeBaselineRestoreResult | None = None
        verification: ScopeBaselineVerification | None = None
        error: BaseException | None = None
        try:
            restore = context.make_phase_spec(
                OperationPhase.FAILURE_CLEANUP,
                allowed_io={"write"},
                fields=handle.fields,
                max_steps=profile.restore_max_steps,
            )
            with context.authorize_phase(restore):
                context.begin_restore(handle)
                try:
                    restore_result = self.driver.restore_acquisition_control(baseline)
                    if not isinstance(restore_result, ScopeBaselineRestoreResult):
                        raise TypeError("restore_acquisition_control() returned an invalid result")
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("acquisition control restore did not complete")
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
                    observed = self.driver.verify_acquisition_control_restored(baseline)
                    self._validate_acquisition_snapshot(observed)
                    matched = (
                        observed.run_state.phase == "stopped"
                        and observed.trigger_state_token
                        == baseline.snapshot.trigger_state_token
                        and observed.acquisition_state_token
                        == baseline.snapshot.acquisition_state_token
                    )
                    mismatched = tuple(
                        field_name
                        for field_name, field_matches in (
                            ("scope.run_state", observed.run_state.phase == "stopped"),
                            (
                                "scope.trigger",
                                observed.trigger_state_token
                                == baseline.snapshot.trigger_state_token,
                            ),
                            (
                                "scope.acquisition",
                                observed.acquisition_state_token
                                == baseline.snapshot.acquisition_state_token,
                            ),
                        )
                        if not field_matches
                    )
                    verification = ScopeBaselineVerification(
                        status="verified" if matched else "mismatch",
                        verified_fields=tuple(handle.fields) if matched else (),
                        mismatched_fields=mismatched,
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

    def _cleanup_channel_display(
        self,
        context: ScopeOperationContextCoordinator,
        handle: ScopeBaselineHandle,
        baseline: ScopeChannelDisplayBaseline | None,
        profile: ScopeChannelDisplayProfileV2,
    ) -> tuple[BaseException | None, dict[str, object]]:
        assert baseline is not None
        restore_result: ScopeChannelDisplayRestoreResult | None = None
        verification: dict[str, object] | None = None
        error: BaseException | None = None
        try:
            restore = context.make_phase_spec(
                OperationPhase.FAILURE_CLEANUP,
                allowed_io={"write"},
                fields=handle.fields,
                max_steps=profile.restore_max_steps,
            )
            with context.authorize_phase(restore):
                context.begin_restore(handle)
                try:
                    restore_result = self.driver.restore_channel_display_v2(baseline)
                    if not isinstance(restore_result, ScopeChannelDisplayRestoreResult):
                        raise TypeError(
                            "restore_channel_display_v2() returned an invalid result"
                        )
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("channel display restore did not complete")
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
                    observed = self.driver.get_channel_display_state_v2(
                        baseline.snapshot.channel
                    )
                    self._validate_channel_display_state(
                        observed,
                        profile,
                        baseline.snapshot.channel,
                    )
                    matched = observed == baseline.snapshot
                    verification = {
                        "status": "verified" if matched else "mismatch",
                        "expected": _json_safe(baseline.snapshot),
                        "observed": _json_safe(observed),
                    }
                    context.finish_verification(
                        handle,
                        authorization,
                        io_kind="query",
                        verified_fields=handle.fields,
                        matched=matched,
                    )
            except BaseException as exc:
                error = error or exc
        return error, {
            "restore": _json_safe(restore_result),
            "verification": verification,
        }

    def _cleanup_focus(
        self,
        context: ScopeOperationContextCoordinator,
        handle: ScopeBaselineHandle,
        baseline: ScopeFocusBaseline | None,
        profile: ScopeFocusProfileV2,
    ) -> tuple[BaseException | None, dict[str, object]]:
        assert baseline is not None
        restore_result: ScopeFocusRestoreResult | None = None
        verification: dict[str, object] | None = None
        error: BaseException | None = None
        try:
            restore = context.make_phase_spec(
                OperationPhase.FAILURE_CLEANUP,
                allowed_io={"write"},
                fields=handle.fields,
                max_steps=profile.restore_max_steps,
            )
            with context.authorize_phase(restore):
                context.begin_restore(handle)
                try:
                    restore_result = self.driver.restore_focus_v2(baseline)
                    if not isinstance(restore_result, ScopeFocusRestoreResult):
                        raise TypeError("restore_focus_v2() returned an invalid result")
                    restore_result.validate_for(baseline)
                    succeeded = restore_result.status == "completed"
                except BaseException:
                    context.finish_restore(handle, succeeded=False)
                    raise
                context.finish_restore(handle, succeeded=succeeded)
                if not succeeded:
                    raise InstrumentError("focus restore did not complete")
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
                    observed = self.driver.get_focus_state_v2()
                    self._validate_focus_state(observed, profile)
                    matched = profile.states_equivalent(baseline.snapshot, observed)
                    verification = {
                        "status": "verified" if matched else "mismatch",
                        "expected": _json_safe(baseline.snapshot),
                        "observed": _json_safe(observed),
                    }
                    context.finish_verification(
                        handle,
                        authorization,
                        io_kind="query",
                        verified_fields=handle.fields,
                        matched=matched,
                    )
            except BaseException as exc:
                error = error or exc
        return error, {
            "restore": _json_safe(restore_result),
            "verification": verification,
        }

    def _cleanup_stop(
        self,
        context: ScopeOperationContextCoordinator,
    ) -> BaseException | None:
        if self.session_state.health is SessionHealth.POISONED:
            return InstrumentError("poisoned session cannot run acquisition STOP cleanup")
        try:
            restore = context.make_phase_spec(
                OperationPhase.FAILURE_CLEANUP,
                allowed_io=_MAIN_TEXT_IO,
                fields={"scope.run_state"},
                max_steps=2,
            )
            with context.authorize_phase(restore):
                stopped = self.driver.stop_acquisition()
                if not isinstance(stopped, ScopeAcquisitionRunState) or stopped.phase != "stopped":
                    raise DataError("recovery STOP postcondition is not proven")
            verify = context.make_phase_spec(
                OperationPhase.CLEANUP_VERIFICATION,
                allowed_io={"query"},
                fields={"scope.run_state"},
                max_steps=1,
            )
            with context.authorize_phase(verify) as authorization:
                observed = self.driver.get_acquisition_run_state()
                if not isinstance(observed, ScopeAcquisitionRunState) or observed.phase != "stopped":
                    raise DataError("recovery STOP verification is not proven")
                context.complete_phase_verification(
                    authorization,
                    io_kind="query",
                    fields={"scope.run_state"},
                )
            return None
        except BaseException as exc:
            return exc

    def _identity_preflight(self, context: ScopeOperationContextCoordinator) -> None:
        phase = context.make_phase_spec(
            OperationPhase.PREFLIGHT,
            allowed_io={"query"},
            fields={"scope.identity"},
            max_steps=1,
        )
        with context.authorize_phase(phase) as authorization:
            self._verify_identity()
            context.complete_phase_verification(
                authorization,
                io_kind="query",
                fields={"scope.identity"},
            )

    def _verify_identity(self) -> str:
        identity = self.driver.idn()
        if not isinstance(identity, str) or not identity.strip():
            raise DataError("scope identity verification returned an empty response")
        return identity

    def _require(self, operation: str) -> OperationSpec:
        spec = EXPERIMENTAL_SCOPE_OPERATION_SPECS.get(operation)
        if spec is None:
            raise ConfigError(f"unknown experimental scope operation: {operation!r}")
        missing = sorted(set(spec.required_capabilities) - self.capabilities)
        if missing:
            raise ConfigError(
                f"operation {operation!r} is missing capabilities: {', '.join(missing)}"
            )
        access_policy(self.access, "scope.access").require(spec, operation=operation)
        return spec

    def _context(
        self,
        spec: OperationSpec,
        *,
        deadline: float | None,
        profile_binary_limits: ScopeBinaryLimits | None = None,
        transport_trailing: bytes = b"",
    ) -> ScopeOperationContextCoordinator:
        return ScopeOperationContextCoordinator(
            session_state=self.session_state,
            spec=spec,
            connection_timeout_ms=self.connection_timeout_ms,
            caller_deadline=deadline,
            profile_binary_limits=profile_binary_limits,
            transport_trailing=transport_trailing,
            enabled=True,
        )

    def _error_executor(
        self,
        spec: OperationSpec,
        override: ErrorCheckSpec | None,
        context: ScopeOperationContextCoordinator,
    ) -> ScopeErrorPolicyExecutor:
        resolved = resolve_error_check(
            spec,
            override,
            instrument_default=self.instrument_error_default,
        )
        return ScopeErrorPolicyExecutor(
            driver=self.driver,
            capabilities=self.capabilities,
            operation_spec=spec,
            error_spec=resolved,
            correlation_id=context.correlation_id,
        )

    def _screenshot_profile(self) -> ScopeScreenshotProfile:
        extensions = self.descriptor.scope_extensions
        profile = extensions.screenshot_profile if extensions is not None else None
        if profile is None:
            raise ConfigError("scope screenshot capability requires a descriptor profile")
        return profile

    def _acquisition_profile(self) -> ScopeAcquisitionControlProfile:
        extensions = self.descriptor.scope_extensions
        profile = extensions.acquisition_control_profile if extensions is not None else None
        if profile is None:
            raise ConfigError("scope acquisition control requires a descriptor profile")
        return profile

    def _channel_display_profile_v2(self) -> ScopeChannelDisplayProfileV2:
        extensions = self.descriptor.scope_extensions
        profile = extensions.channel_display_profile_v2 if extensions is not None else None
        if profile is None:
            raise ConfigError("scope channel display capability requires a descriptor profile")
        return profile

    def _focus_profile_v2(self) -> ScopeFocusProfileV2:
        extensions = self.descriptor.scope_extensions
        profile = extensions.focus_profile_v2 if extensions is not None else None
        if profile is None:
            raise ConfigError("scope focus capability requires a descriptor profile")
        return profile

    def _trace_profile(self) -> ScopeTraceProfile:
        extensions = self.descriptor.scope_extensions
        profile = extensions.trace_profile if extensions is not None else None
        if profile is None:
            raise ConfigError("scope trace capability requires a descriptor profile")
        return profile

    @staticmethod
    def _validate_screenshot_profile_narrowing(
        static: ScopeScreenshotProfile,
        runtime: object,
    ) -> ScopeScreenshotProfile:
        if not isinstance(runtime, ScopeScreenshotProfile):
            raise DataError("get_screenshot_profile() returned an invalid result")
        runtime.require_public_source()
        static_variants = {item.request: item for item in static.variants}
        for candidate in runtime.variants:
            expected = static_variants.get(candidate.request)
            if expected is None:
                raise DataError("runtime screenshot profile expanded descriptor requests")
            ExperimentalScopeExtensionService._assert_same_screenshot_state_contract(
                expected,
                candidate,
            )
            if (
                candidate.response_max_bytes > expected.response_max_bytes
                or candidate.operation_max_bytes > expected.operation_max_bytes
                or candidate.query_max_count > expected.query_max_count
                or candidate.resynchronization_max_bytes
                > expected.resynchronization_max_bytes
            ):
                raise DataError("runtime screenshot profile expanded descriptor limits")
            if (
                candidate.snapshot_max_steps > expected.snapshot_max_steps
                or candidate.restore_max_steps > expected.restore_max_steps
                or candidate.verify_max_steps > expected.verify_max_steps
            ):
                raise DataError("runtime screenshot profile expanded descriptor step limits")
            for expected_bounds, candidate_bounds in (
                (expected.width_px, candidate.width_px),
                (expected.height_px, candidate.height_px),
            ):
                if expected_bounds is None:
                    continue
                if candidate_bounds is None or (
                    candidate_bounds[0] < expected_bounds[0]
                    or candidate_bounds[1] > expected_bounds[1]
                ):
                    raise DataError("runtime screenshot profile expanded descriptor dimensions")
        return runtime

    @staticmethod
    def _assert_same_screenshot_state_contract(
        expected: ScopeScreenshotVariant,
        candidate: ScopeScreenshotVariant,
    ) -> None:
        fixed = (
            "media_type",
            "framing",
            "changed_fields",
            "restore_order",
            "transport_trailing_hex",
            "content_trailing_hex",
        )
        if any(getattr(expected, name) != getattr(candidate, name) for name in fixed):
            raise DataError("runtime screenshot profile changed a descriptor state contract")

    @staticmethod
    def _validate_screenshot_snapshot(
        snapshot: object,
        fields: tuple[str, ...],
    ) -> None:
        if not isinstance(snapshot, ScopeScreenshotStateSnapshot):
            raise DataError("screenshot state snapshot has an invalid type")
        if tuple(snapshot.captured_fields) != fields:
            raise DataError("screenshot state snapshot fields do not match the profile")

    @staticmethod
    def _validate_screenshot_result(
        screenshot: object,
        variant: ScopeScreenshotVariant,
    ) -> None:
        if not isinstance(screenshot, ScopeScreenshot):
            raise DataError("capture_screenshot() returned an invalid result")
        if screenshot.requested != variant.request or screenshot.effective != variant.request:
            raise DataError("screenshot effective request differs from its exact profile variant")
        if screenshot.media_type != variant.media_type or screenshot.framing is not variant.framing:
            raise DataError("screenshot media/framing differs from its profile variant")
        for value, bounds, label in (
            (screenshot.width_px, variant.width_px, "width"),
            (screenshot.height_px, variant.height_px, "height"),
        ):
            if bounds is not None and not bounds[0] <= value <= bounds[1]:
                raise DataError(f"screenshot {label} is outside its profile bounds")

    @staticmethod
    def _validate_acquisition_snapshot(snapshot: object) -> None:
        if not isinstance(snapshot, ScopeAcquisitionControlSnapshot):
            raise DataError("acquisition control snapshot has an invalid type")

    @staticmethod
    def _validate_channel_display_state(
        state: object,
        profile: ScopeChannelDisplayProfileV2,
        channel: int,
    ) -> None:
        try:
            profile.validate_state(state, channel=channel)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise DataError(f"channel display driver returned an invalid state: {exc}") from exc

    @staticmethod
    def _validate_focus_state(state: object, profile: ScopeFocusProfileV2) -> None:
        try:
            profile.validate_state(state)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise DataError(f"focus driver returned an invalid state: {exc}") from exc

    @staticmethod
    def _validate_start_result(
        value: object,
        request: ScopeContinuousAcquisitionRequest,
        baseline: ScopeAcquisitionControlBaseline,
        profile: ScopeAcquisitionControlProfile,
    ) -> None:
        if request.trigger_mode not in profile.supported_continuous_modes:
            raise ConfigError("requested continuous trigger mode is unsupported")
        if not isinstance(value, ScopeAcquisitionRunState):
            raise DataError("start_continuous() returned an invalid result")
        if value.trigger_mode != request.trigger_mode or value.phase not in {
            "ready",
            "arming",
            "waiting",
            "acquiring",
            "rolling",
        }:
            raise DataError("continuous acquisition postcondition is not proven")

    @staticmethod
    def _validate_single_result(
        value: object,
        baseline: ScopeAcquisitionControlBaseline,
        profile: ScopeAcquisitionControlProfile,
    ) -> None:
        if not isinstance(value, ScopeAcquisitionCompletion):
            raise DataError("acquire_single() returned an invalid completion")
        validate_acquisition_completion(value, baseline=baseline, profile=profile)

    @staticmethod
    def _validate_trace_source(
        source: ScopeTraceRef,
        profile: ScopeTraceProfile,
        *,
        require_fetchable: bool,
    ) -> None:
        if require_fetchable and source.kind not in profile.fetchable_kinds:
            raise ConfigError("trace kind is not fetchable in the descriptor profile")
        if require_fetchable and source.kind == "digital" and source.index is None:
            raise ConfigError("R1.3 digital trace fetch requires an indexed single-line source")
        if source.index is not None and source.kind != "digital" and (
            source.index > profile.source_index_max
        ):
            raise ConfigError("trace source index exceeds the descriptor profile")

    @staticmethod
    def _validate_trace_points(points: str | int, profile: ScopeTraceProfile) -> None:
        if points == "dmax":
            return
        if isinstance(points, bool) or not isinstance(points, int) or not 1 <= points <= profile.max_points:
            raise ConfigError("trace points must be 'dmax' or a positive value within the profile")

    @staticmethod
    def _validate_trace_snapshot(
        snapshot: object,
        fields: tuple[str, ...],
    ) -> None:
        if not isinstance(snapshot, ScopeTraceTransferStateSnapshot):
            raise DataError("trace transfer snapshot has an invalid type")
        if tuple(snapshot.captured_fields) != fields:
            raise DataError("trace transfer snapshot fields do not match the profile")

    @staticmethod
    def _validate_trace_result(
        trace: object,
        source: ScopeTraceRef,
        profile: ScopeTraceProfile,
        points: str | int,
    ) -> None:
        if not isinstance(trace, ScopeTraceData) or trace.metadata.source != source:
            raise DataError("fetch_trace() returned an inconsistent result")
        if trace.values.size > profile.max_points:
            raise DataError("trace result exceeds the descriptor point limit")
        if isinstance(points, int) and not isinstance(points, bool) and trace.values.size != points:
            raise DataError("trace result point count differs from the explicit request")

    def _result(
        self,
        context: ScopeOperationContextCoordinator,
        *,
        value: object,
        error_executor: ScopeErrorPolicyExecutor | None = None,
        observed_state: Mapping[str, object] | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> ScopeExtensionOperationResult:
        diagnostics = context.artifact()
        diagnostics["error_check"] = (
            _json_safe(error_executor.artifact) if error_executor is not None else None
        )
        diagnostics.update(_json_safe(extra or {}))
        return ScopeExtensionOperationResult(
            value=value,
            diagnostics=diagnostics,
            observed_state=observed_state,
        )

    def _attach_diagnostics(
        self,
        error: BaseException,
        context: ScopeOperationContextCoordinator,
        error_executor: ScopeErrorPolicyExecutor | None,
        *,
        cleanup_error: BaseException | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        diagnostics = context.artifact()
        diagnostics["error_check"] = (
            _json_safe(error_executor.artifact) if error_executor is not None else None
        )
        diagnostics["cleanup_error"] = (
            type(cleanup_error).__name__ if cleanup_error is not None else None
        )
        diagnostics.update(_json_safe(extra or {}))
        try:
            setattr(error, "scope_operation_diagnostics", diagnostics)
        except Exception:
            pass


class ScopeExtensionService(ExperimentalScopeExtensionService):
    """Stable public Service for descriptors that opt into scope R1.3 capabilities."""

    def __init__(
        self,
        *,
        driver: object,
        descriptor: InstrumentDescriptor,
        session_state: InstrumentSessionState,
        connection_timeout_ms: int,
        access: AccessMode = "read_write",
        instrument_error_default: ErrorCheckSpec | None = None,
    ) -> None:
        super().__init__(
            driver=driver,
            descriptor=descriptor,
            session_state=session_state,
            connection_timeout_ms=connection_timeout_ms,
            access=access,
            instrument_error_default=instrument_error_default,
            enabled=True,
        )
        validate_scope_descriptor(descriptor, driver=driver)

    def _require(self, operation: str) -> OperationSpec:
        spec = require_operation_spec(operation)
        if operation not in EXPERIMENTAL_SCOPE_OPERATION_SPECS:
            raise ConfigError(f"operation is not a scope R1.3 extension: {operation!r}")
        missing = sorted(set(spec.required_capabilities) - self.capabilities)
        if missing:
            raise ConfigError(
                f"operation {operation!r} is missing capabilities: {', '.join(missing)}"
            )
        access_policy(self.access, "scope.access").require(spec, operation=operation)
        return spec


__all__ = [
    "ExperimentalScopeExtensionService",
    "SCOPE_EXTENSION_RESULT_SCHEMA",
    "ScopeExtensionService",
    "ScopeExtensionOperationResult",
]
