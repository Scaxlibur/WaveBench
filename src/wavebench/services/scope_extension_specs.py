"""Default-off operation specifications for the Draft scope R1.3 RFC."""

from __future__ import annotations

from types import MappingProxyType

from wavebench.instruments.scope_extensions import (
    SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS,
    SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
    SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT,
    SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_SCREENSHOT_OPERATION_TIMEOUT_MS,
    SCOPE_TRACE_BINARY_OPERATION_MAX_BYTES,
    SCOPE_TRACE_BINARY_QUERY_MAX_COUNT,
    SCOPE_TRACE_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_TRACE_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_TRACE_OPERATION_TIMEOUT_MS,
    ScopeEmbeddedScreenshotContract,
    ScopeScreenshotRequest,
)

from .operation_specs import OperationSpec, require_operation_spec


EXPERIMENTAL_SCOPE_EXTENSIONS_ENABLED = False

_ERROR_CAPABILITY = ("scope.error_drain_v1",)
_TRACE_TRANSFER_FIELDS = (
    "scope.run_state",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
)


def _operation(
    operation: str,
    *,
    required_capabilities: tuple[str, ...],
    effect: str,
    timeout_ms: int,
    changed_fields: tuple[str, ...] = (),
    restore_coverage: str = "none",
    verification_fields: tuple[str, ...] = (),
    postcondition_fields: tuple[str, ...] = (),
    cleanup_verification_fields: tuple[str, ...] = (),
    risk_flags: tuple[str, ...] = (),
    binary_limits: tuple[int, int, int, int] | None = None,
    error_check_minimum: str | None = None,
) -> OperationSpec:
    binary = binary_limits or (None, None, None, None)
    return OperationSpec(
        operation=operation,
        instrument_kind="scope",
        required_capabilities=required_capabilities,
        optional_capabilities=_ERROR_CAPABILITY if error_check_minimum is not None else (),
        effect=effect,  # type: ignore[arg-type]
        lease_mode="exclusive",
        changed_fields=changed_fields,
        restore_coverage=restore_coverage,
        required_verified_fields=("scope.identity",),
        verification_fields=verification_fields,
        postcondition_fields=postcondition_fields,
        cleanup_verification_fields=cleanup_verification_fields,
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=timeout_ms,
        binary_response_max_bytes=binary[0],
        binary_operation_max_bytes=binary[1],
        binary_query_max_count=binary[2],
        binary_resynchronization_max_bytes=binary[3],
        error_check_minimum=error_check_minimum,  # type: ignore[arg-type]
        risk_flags=risk_flags,
    )


_SPECS = (
    _operation(
        "scope.screenshot_profile",
        required_capabilities=("scope.screenshot_profile",),
        effect="stateful_read",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        risk_flags=("profile_query",),
    ),
    _operation(
        "scope.screenshot_v2",
        required_capabilities=("scope.screenshot_v2",),
        effect="write",
        timeout_ms=SCOPE_SCREENSHOT_OPERATION_TIMEOUT_MS,
        changed_fields=(
            "scope.display_menu",
            "scope.display_color",
            "scope.error_queue",
            "output.screenshot",
        ),
        restore_coverage="screenshot-baseline-only",
        verification_fields=("scope.display_menu", "scope.display_color"),
        cleanup_verification_fields=("scope.display_menu", "scope.display_color"),
        risk_flags=("front_panel_state", "binary_response", "temporary_display_setup"),
        binary_limits=(
            SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
            SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
            SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT,
            SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES,
        ),
        error_check_minimum="disabled",
    ),
    _operation(
        "scope.acquisition_run_state",
        required_capabilities=("scope.acquisition_run_state",),
        effect="stateful_read",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        risk_flags=("state_observation",),
    ),
    _operation(
        "scope.acquisition_start",
        required_capabilities=("scope.acquisition_control", "scope.acquisition_run_state"),
        effect="write",
        timeout_ms=SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS,
        changed_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
            "scope.error_queue",
        ),
        restore_coverage="failure-cleanup-only",
        verification_fields=("scope.trigger", "scope.acquisition"),
        postcondition_fields=("scope.run_state", "scope.trigger", "scope.acquisition"),
        cleanup_verification_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
        ),
        risk_flags=("trigger", "acquisition_state", "recovery_required"),
        error_check_minimum="disabled",
    ),
    _operation(
        "scope.acquisition_single",
        required_capabilities=("scope.acquisition_control", "scope.acquisition_run_state"),
        effect="acquire",
        timeout_ms=SCOPE_ACQUISITION_OPERATION_TIMEOUT_MS,
        changed_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
            "scope.error_queue",
        ),
        restore_coverage="failure-cleanup-only",
        verification_fields=("scope.trigger", "scope.acquisition"),
        postcondition_fields=("scope.run_state", "scope.trigger", "scope.acquisition"),
        cleanup_verification_fields=(
            "scope.run_state",
            "scope.trigger",
            "scope.acquisition",
        ),
        risk_flags=("trigger", "acquisition_state", "recovery_required"),
        error_check_minimum="disabled",
    ),
    _operation(
        "scope.acquisition_stop",
        required_capabilities=("scope.acquisition_control", "scope.acquisition_run_state"),
        effect="write",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        changed_fields=("scope.run_state", "scope.error_queue"),
        restore_coverage="failure-cleanup-only",
        postcondition_fields=("scope.run_state",),
        cleanup_verification_fields=("scope.run_state",),
        risk_flags=("acquisition_state", "recovery_required"),
        error_check_minimum="disabled",
    ),
    _operation(
        "scope.trace_metadata",
        required_capabilities=("scope.trace_metadata",),
        effect="stateful_read",
        timeout_ms=SCOPE_PROFILE_OPERATION_TIMEOUT_MS,
        risk_flags=("analysis_state",),
        error_check_minimum="disabled",
    ),
    _operation(
        "scope.fetch_trace",
        required_capabilities=("scope.fetch_trace",),
        effect="acquire",
        timeout_ms=SCOPE_TRACE_OPERATION_TIMEOUT_MS,
        changed_fields=(*_TRACE_TRANSFER_FIELDS, "scope.error_queue", "output.trace"),
        restore_coverage="trace-baseline-only",
        verification_fields=_TRACE_TRANSFER_FIELDS,
        cleanup_verification_fields=_TRACE_TRANSFER_FIELDS,
        risk_flags=("acquisition_state", "temporary_transfer_setup", "binary_response"),
        binary_limits=(
            SCOPE_TRACE_BINARY_RESPONSE_MAX_BYTES,
            SCOPE_TRACE_BINARY_OPERATION_MAX_BYTES,
            SCOPE_TRACE_BINARY_QUERY_MAX_COUNT,
            SCOPE_TRACE_BINARY_RESYNCHRONIZATION_MAX_BYTES,
        ),
        error_check_minimum="disabled",
    ),
)

EXPERIMENTAL_SCOPE_OPERATION_SPECS = MappingProxyType(
    {spec.operation: spec for spec in _SPECS}
)

_EMBEDDED_SCREENSHOT_CONTRACT = ScopeEmbeddedScreenshotContract(
    request=ScopeScreenshotRequest(menu_mode="exclude", color_mode="color"),
    changed_fields=("scope.display_menu", "scope.display_color"),
    verification_fields=("scope.display_menu", "scope.display_color"),
    cleanup_verification_fields=("scope.display_menu", "scope.display_color"),
)


def _embedded_capture_spec(operation: str) -> OperationSpec:
    stable = require_operation_spec(operation)
    changed = (
        *stable.changed_fields,
        "scope.display_menu",
        "scope.display_color",
        "output.screenshot",
    )
    verification = (
        *stable.verification_fields,
        "scope.display_menu",
        "scope.display_color",
    )
    return OperationSpec(
        operation=stable.operation,
        instrument_kind=stable.instrument_kind,
        required_capabilities=stable.required_capabilities,
        optional_capabilities=(*stable.optional_capabilities, "scope.screenshot_v2"),
        effect=stable.effect,
        lease_mode=stable.lease_mode,
        changed_fields=changed,
        restore_coverage=stable.restore_coverage,
        session_purpose=stable.session_purpose,
        required_verified_fields=stable.required_verified_fields,
        verification_fields=verification,
        cleanup_verification_fields=("scope.display_menu", "scope.display_color"),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_TRACE_OPERATION_TIMEOUT_MS,
        risk_flags=(*stable.risk_flags, "embedded_screenshot"),
        safe_alternatives=stable.safe_alternatives,
        embedded_screenshot_contract=_EMBEDDED_SCREENSHOT_CONTRACT,
    )


EXPERIMENTAL_EMBEDDED_SCREENSHOT_CAPTURE_SPECS = MappingProxyType(
    {
        operation: _embedded_capture_spec(operation)
        for operation in (
            "scope.capture",
            "scope.capture_waveforms",
            "scope.capture_multiple",
        )
    }
)


__all__ = [
    "EXPERIMENTAL_SCOPE_EXTENSIONS_ENABLED",
    "EXPERIMENTAL_EMBEDDED_SCREENSHOT_CAPTURE_SPECS",
    "EXPERIMENTAL_SCOPE_OPERATION_SPECS",
]
