from __future__ import annotations

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments import InstrumentDescriptor
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.scope_extension_capabilities import (
    EXPERIMENTAL_SCOPE_CAPABILITY_METHODS,
    validate_experimental_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeScreenshotProfile,
    ScopeScreenshotRequest,
    ScopeScreenshotVariant,
)
from wavebench.services.operation_specs import OperationSpec, get_operation_spec
from wavebench.services.scope_error_policy import legacy_scope_error_artifact
from wavebench.services.scope_extension_specs import (
    EXPERIMENTAL_EMBEDDED_SCREENSHOT_CAPTURE_SPECS,
    EXPERIMENTAL_SCOPE_OPERATION_SPECS,
)
from wavebench.transport.contracts import BinaryResponseFraming


def _profile() -> ScopeScreenshotProfile:
    return ScopeScreenshotProfile(
        (
            ScopeScreenshotVariant(
                request=ScopeScreenshotRequest(),
                media_type="image/png",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                response_max_bytes=1_024,
                operation_max_bytes=1_024,
                resynchronization_max_bytes=0,
                changed_fields=(),
                restore_order=(),
                snapshot_max_steps=0,
                restore_max_steps=0,
                verify_max_steps=0,
            ),
        )
    )


def _descriptor(*, capabilities: tuple[str, ...], extensions=True) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.scope",
        kind="scope",
        display_name="Example",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: object(),
        scope_extensions=(
            ScopeDescriptorExtensions(screenshot_profile=_profile()) if extensions else None
        ),
    )


def test_draft_capabilities_and_operations_remain_out_of_public_registries() -> None:
    for capability in EXPERIMENTAL_SCOPE_CAPABILITY_METHODS:
        assert capability not in CAPABILITY_METHODS
    for operation in EXPERIMENTAL_SCOPE_OPERATION_SPECS:
        assert get_operation_spec(operation) is None


def test_public_capability_validator_rejects_draft_capability_before_factory() -> None:
    descriptor = _descriptor(capabilities=("scope.screenshot_profile",))
    class Driver:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="unknown capability"):
        validate_declared_capabilities(descriptor, Driver())


def test_private_descriptor_gate_requires_explicit_enable_profile_and_methods() -> None:
    descriptor = _descriptor(capabilities=("scope.screenshot_profile",))
    with pytest.raises(ConfigError, match="disabled"):
        validate_experimental_scope_descriptor(descriptor)
    with pytest.raises(ConfigError, match="requires callable"):
        validate_experimental_scope_descriptor(descriptor, driver=object(), enabled=True)

    class Driver:
        def get_screenshot_profile(self):
            return _profile()

    validate_experimental_scope_descriptor(descriptor, driver=Driver(), enabled=True)

    missing_profile = _descriptor(
        capabilities=("scope.screenshot_profile",),
        extensions=False,
    )
    with pytest.raises(ConfigError, match="requires scope_extensions"):
        validate_experimental_scope_descriptor(missing_profile, enabled=True)


def test_scope_extension_operation_specs_freeze_timeout_and_binary_limits() -> None:
    screenshot = EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.screenshot_v2"]
    assert screenshot.timeout_source == "operation.timeout_ms"
    assert screenshot.operation_timeout_ms == 5_000
    assert (
        screenshot.binary_response_max_bytes,
        screenshot.binary_operation_max_bytes,
        screenshot.binary_query_max_count,
        screenshot.binary_resynchronization_max_bytes,
    ) == (262_144, 262_144, 1, 0)

    trace = EXPERIMENTAL_SCOPE_OPERATION_SPECS["scope.fetch_trace"]
    assert trace.operation_timeout_ms == 60_000
    assert (
        trace.binary_response_max_bytes,
        trace.binary_operation_max_bytes,
        trace.binary_query_max_count,
        trace.binary_resynchronization_max_bytes,
    ) == (8_388_608, 67_108_864, 256, 65_536)
    assert "scope.query_response_header" in trace.verification_fields
    assert "scope.waveform_byte_order" in trace.verification_fields
    assert "scope.waveform_transfer_window" in trace.verification_fields


def test_operation_spec_rejects_partial_binary_or_ambiguous_timeout_metadata() -> None:
    with pytest.raises(ValueError, match="all four"):
        OperationSpec(
            "scope.bad",
            "scope",
            binary_response_max_bytes=1,
        )
    with pytest.raises(ValueError, match="explicit operation_timeout_ms"):
        OperationSpec(
            "scope.bad",
            "scope",
            timeout_source="operation.timeout_ms",
        )
    with pytest.raises(ValueError, match="timeout_source"):
        OperationSpec(
            "scope.bad",
            "scope",
            operation_timeout_ms=1,
        )


def test_legacy_scope_errors_is_explicitly_consumptive_without_typed_proof() -> None:
    legacy = get_operation_spec("scope.errors")
    assert legacy is not None
    assert legacy.changed_fields == ("scope.error_queue",)
    assert legacy.verification_fields == ()
    artifact = legacy_scope_error_artifact(["one", "two"], requested_limit=16)
    assert artifact["returned_record_count"] == 2
    assert artifact["terminated"] is None
    assert artifact["query_count"] is None


def test_embedded_screenshot_parent_specs_have_complete_static_field_closure() -> None:
    for operation, spec in EXPERIMENTAL_EMBEDDED_SCREENSHOT_CAPTURE_SPECS.items():
        assert get_operation_spec(operation) is not spec
        assert spec.embedded_screenshot_contract is not None
        assert {
            "scope.display_menu",
            "scope.display_color",
            "output.screenshot",
        } <= set(spec.changed_fields)
        assert {"scope.display_menu", "scope.display_color"} <= set(
            spec.verification_fields
        )
        assert {"scope.display_menu", "scope.display_color"} <= set(
            spec.cleanup_verification_fields
        )
        assert "scope.screenshot_v2" in spec.optional_capabilities
