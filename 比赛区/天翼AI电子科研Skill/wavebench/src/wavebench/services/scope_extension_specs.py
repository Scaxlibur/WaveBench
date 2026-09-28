"""Scope R1.3 operation exports and legacy-capture screenshot contracts."""

from __future__ import annotations

from types import MappingProxyType

from wavebench.instruments.scope_extensions import (
    ScopeEmbeddedScreenshotContract,
    ScopeScreenshotRequest,
)
from wavebench.scope_extension_constants import (
    SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT,
    SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES,
    SCOPE_TRACE_OPERATION_TIMEOUT_MS,
)

from .operation_specs import OperationSpec, SCOPE_OPERATION_SPECS, require_operation_spec


SCOPE_EXTENSIONS_ENABLED = True

_EMBEDDED_SCREENSHOT_CONTRACT = ScopeEmbeddedScreenshotContract(
    request=ScopeScreenshotRequest(menu_mode="exclude", color_mode="color"),
    changed_fields=("scope.display_menu", "scope.display_color"),
    verification_fields=("scope.display_menu", "scope.display_color"),
    cleanup_verification_fields=("scope.display_menu", "scope.display_color"),
)


def _embedded_capture_spec(operation: str) -> OperationSpec:
    stable = require_operation_spec(operation)
    return OperationSpec(
        operation=stable.operation,
        instrument_kind=stable.instrument_kind,
        required_capabilities=stable.required_capabilities,
        optional_capabilities=(*stable.optional_capabilities, "scope.screenshot_v2"),
        effect=stable.effect,
        lease_mode=stable.lease_mode,
        changed_fields=(
            *stable.changed_fields,
            "scope.display_menu",
            "scope.display_color",
            "output.screenshot",
        ),
        restore_coverage=stable.restore_coverage,
        session_purpose=stable.session_purpose,
        required_verified_fields=stable.required_verified_fields,
        verification_fields=(
            *stable.verification_fields,
            "scope.display_menu",
            "scope.display_color",
        ),
        cleanup_verification_fields=("scope.display_menu", "scope.display_color"),
        timeout_source="operation.timeout_ms",
        operation_timeout_ms=SCOPE_TRACE_OPERATION_TIMEOUT_MS,
        binary_response_max_bytes=SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
        binary_operation_max_bytes=SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
        binary_query_max_count=SCOPE_SCREENSHOT_BINARY_QUERY_MAX_COUNT,
        binary_resynchronization_max_bytes=(
            SCOPE_SCREENSHOT_BINARY_RESYNCHRONIZATION_MAX_BYTES
        ),
        risk_flags=(*stable.risk_flags, "embedded_screenshot"),
        safe_alternatives=stable.safe_alternatives,
        embedded_screenshot_contract=_EMBEDDED_SCREENSHOT_CONTRACT,
    )


EMBEDDED_SCREENSHOT_CAPTURE_SPECS = MappingProxyType(
    {
        operation: _embedded_capture_spec(operation)
        for operation in (
            "scope.capture",
            "scope.capture_waveforms",
            "scope.capture_multiple",
        )
    }
)

# Import-compatible names retained for code written against the internal draft.
EXPERIMENTAL_SCOPE_EXTENSIONS_ENABLED = SCOPE_EXTENSIONS_ENABLED
EXPERIMENTAL_SCOPE_OPERATION_SPECS = SCOPE_OPERATION_SPECS
EXPERIMENTAL_EMBEDDED_SCREENSHOT_CAPTURE_SPECS = EMBEDDED_SCREENSHOT_CAPTURE_SPECS


__all__ = [
    "EMBEDDED_SCREENSHOT_CAPTURE_SPECS",
    "EXPERIMENTAL_SCOPE_EXTENSIONS_ENABLED",
    "EXPERIMENTAL_EMBEDDED_SCREENSHOT_CAPTURE_SPECS",
    "EXPERIMENTAL_SCOPE_OPERATION_SPECS",
    "SCOPE_EXTENSIONS_ENABLED",
    "SCOPE_OPERATION_SPECS",
]
