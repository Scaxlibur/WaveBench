from __future__ import annotations

from dataclasses import fields, replace

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments import InstrumentDescriptor
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    validate_experimental_scope_descriptor,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeDescriptorExtensions,
    ScopeScreenshotProfile,
    ScopeScreenshotRequest,
    ScopeScreenshotVariant,
    ScopeWaveformBinaryOperationProfile,
    ScopeWaveformBinaryProfile,
)
from wavebench.services.operation_specs import OperationSpec, get_operation_spec
from wavebench.services.scope_error_policy import legacy_scope_error_artifact
from wavebench.services.scope_extension_specs import (
    EMBEDDED_SCREENSHOT_CAPTURE_SPECS,
    SCOPE_OPERATION_SPECS,
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


def _waveform_profile(*, operation_kind: str = "fetch") -> ScopeWaveformBinaryProfile:
    fields = (
        ("scope.waveform_source",)
        if operation_kind == "fetch"
        else (
            "scope.run_state",
            "scope.acquisition",
            "scope.trigger",
            "scope.timebase",
            "scope.channel_display",
            "scope.channel_vertical",
            "scope.waveform_source",
            "scope.waveform_mode",
            "scope.query_response_header",
            "scope.waveform_format",
            "scope.waveform_byte_order",
            "scope.waveform_points",
            "scope.waveform_transfer_window",
        )
    )
    return ScopeWaveformBinaryProfile(
        operations=(
            ScopeWaveformBinaryOperationProfile(
                operation_kind=operation_kind,  # type: ignore[arg-type]
                response_max_bytes=1_024,
                operation_max_bytes=4_096,
                query_max_count=4,
                resynchronization_max_bytes=0,
                restore_order=fields,
                snapshot_max_steps=len(fields),
                restore_max_steps=len(fields),
                verify_max_steps=len(fields),
            ),
        )
    )


def _descriptor(
    *,
    capabilities: tuple[str, ...],
    extensions=True,
    wavebench_min_version: str = "0.8.23",
) -> InstrumentDescriptor:
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
        wavebench_min_version=wavebench_min_version,
        scope_extensions=(
            ScopeDescriptorExtensions(screenshot_profile=_profile()) if extensions else None
        ),
    )


def test_scope_capabilities_and_operations_are_in_public_registries() -> None:
    for capability in SCOPE_CAPABILITY_METHODS:
        assert CAPABILITY_METHODS[capability] == SCOPE_CAPABILITY_METHODS[capability]
    for operation, spec in SCOPE_OPERATION_SPECS.items():
        assert get_operation_spec(operation) is spec


def test_public_capability_validator_accepts_complete_scope_extension() -> None:
    descriptor = _descriptor(capabilities=("scope.screenshot_profile",))
    class Driver:
        def close(self) -> None:
            pass

        def get_screenshot_profile(self):
            return _profile()

    validate_declared_capabilities(descriptor, Driver())


def test_public_scope_capability_requires_new_core_floor() -> None:
    descriptor = replace(
        _descriptor(capabilities=("scope.screenshot_profile",)),
        wavebench_min_version="0.8.22",
    )

    with pytest.raises(ConfigError, match="0.8.23"):
        validate_scope_descriptor(descriptor)


def test_scope_descriptor_extension_is_append_only_for_positional_compatibility() -> None:
    names = [field.name for field in fields(InstrumentDescriptor)]

    assert names[-4:] == [
        "config_fields",
        "resource_schemes",
        "scope_extensions",
        "source_extensions",
    ]
    assert [field.name for field in fields(ScopeDescriptorExtensions)] == [
        "screenshot_profile",
        "acquisition_control_profile",
        "trace_profile",
        "waveform_binary_profile",
        "snapshot_profile_v2",
    ]


def test_new_old_core_plugin_capability_matrix_is_fail_closed() -> None:
    legacy_capabilities = set(CAPABILITY_METHODS) - set(SCOPE_CAPABILITY_METHODS)
    old_plugin = {"scope.idn", "scope.fetch_waveform"}
    new_plugin = {"scope.idn", "scope.screenshot_profile"}

    assert old_plugin <= legacy_capabilities
    assert old_plugin <= set(CAPABILITY_METHODS)
    assert new_plugin <= set(CAPABILITY_METHODS)
    assert new_plugin - legacy_capabilities == {"scope.screenshot_profile"}


def test_extra_scope_methods_do_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",))

    class Driver:
        def close(self) -> None:
            pass

        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def get_screenshot_profile(self):
            return _profile()

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.screenshot_profile" not in descriptor.capabilities


def test_waveform_binary_profile_selects_bounded_v2_methods_without_creating_capabilities() -> None:
    descriptor = _descriptor(
        capabilities=("scope.idn", "scope.fetch_waveform"),
        extensions=False,
        wavebench_min_version="0.8.24",
    )
    descriptor = replace(
        descriptor,
        scope_extensions=ScopeDescriptorExtensions(
            waveform_binary_profile=_waveform_profile()
        ),
    )

    class Driver:
        def close(self) -> None:
            pass

        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def snapshot_waveform_transfer_state(self, fields):
            return object()

        def restore_waveform_transfer_state(self, baseline):
            return object()

        def verify_waveform_transfer_state_restored(self, baseline):
            return object()

        def fetch_waveform_bounded(self, channel, points="dmax", *, baseline):
            return object()

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.fetch_waveform_bounded" not in descriptor.capabilities


def test_waveform_binary_profile_must_match_capabilities_and_bounded_methods() -> None:
    descriptor = _descriptor(
        capabilities=("scope.idn", "scope.capture_waveform"),
        extensions=False,
        wavebench_min_version="0.8.24",
    )
    descriptor = replace(
        descriptor,
        scope_extensions=ScopeDescriptorExtensions(
            waveform_binary_profile=_waveform_profile(operation_kind="fetch")
        ),
    )
    with pytest.raises(ConfigError, match="operations must match"):
        validate_scope_descriptor(descriptor)

    descriptor = _descriptor(
        capabilities=("scope.idn", "scope.fetch_waveform"),
        extensions=False,
        wavebench_min_version="0.8.24",
    )
    descriptor = replace(
        descriptor,
        scope_extensions=ScopeDescriptorExtensions(
            waveform_binary_profile=_waveform_profile()
        ),
    )

    class MissingBoundedMethod:
        def close(self) -> None:
            pass

        def idn(self) -> str:
            return "EXAMPLE,EX1"

        def snapshot_waveform_transfer_state(self, fields):
            return object()

        def restore_waveform_transfer_state(self, baseline):
            return object()

        def verify_waveform_transfer_state_restored(self, baseline):
            return object()

        def fetch_waveform(self, channel, points="dmax", check_errors=True):
            return object()

    with pytest.raises(ConfigError, match="fetch_waveform_bounded"):
        validate_declared_capabilities(descriptor, MissingBoundedMethod())


def test_waveform_binary_profile_requires_its_own_core_version_floor() -> None:
    descriptor = _descriptor(
        capabilities=("scope.idn", "scope.fetch_waveform"),
        extensions=False,
    )
    descriptor = replace(
        descriptor,
        scope_extensions=ScopeDescriptorExtensions(
            waveform_binary_profile=_waveform_profile()
        ),
    )

    with pytest.raises(ConfigError, match="0.8.24"):
        validate_scope_descriptor(descriptor)


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
    screenshot = SCOPE_OPERATION_SPECS["scope.screenshot_v2"]
    assert screenshot.timeout_source == "operation.timeout_ms"
    assert screenshot.operation_timeout_ms == 5_000
    assert (
        screenshot.binary_response_max_bytes,
        screenshot.binary_operation_max_bytes,
        screenshot.binary_query_max_count,
        screenshot.binary_resynchronization_max_bytes,
    ) == (262_144, 262_144, 1, 0)

    trace = SCOPE_OPERATION_SPECS["scope.fetch_trace"]
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
    for operation, spec in EMBEDDED_SCREENSHOT_CAPTURE_SPECS.items():
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
        assert spec.binary_response_max_bytes == 262_144
