"""Descriptor and capability validation for the scope R1.3 public contract."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from packaging.version import InvalidVersion, Version

from wavebench.errors import ConfigError

from .api import InstrumentDescriptor


SCOPE_EXTENSIONS_MIN_CORE_VERSION = "0.8.23"
SCOPE_WAVEFORM_BINARY_MIN_CORE_VERSION = "0.8.24"
SCOPE_PORTABILITY_V2_MIN_CORE_VERSION = "0.8.24"
SCOPE_STRICT_V2_CAPABILITIES = frozenset(
    {
        "scope.channel_input_state_v2",
        "scope.digital_status_v2",
        "scope.snapshot_v2",
        "scope.acquisition_status_v2",
    }
)

_WAVEFORM_BINARY_CAPABILITY_BY_OPERATION = {
    "fetch": "scope.fetch_waveform",
    "capture_single": "scope.capture_waveform",
    "capture_multiple": "scope.capture_waveforms",
}
_WAVEFORM_BINARY_METHOD_BY_OPERATION = {
    "fetch": "fetch_waveform_bounded",
    "capture_single": "capture_waveform_bounded",
    "capture_multiple": "capture_waveforms_bounded",
}
_WAVEFORM_BINARY_RECOVERY_METHODS = (
    "snapshot_waveform_transfer_state",
    "restore_waveform_transfer_state",
    "verify_waveform_transfer_state_restored",
)


SCOPE_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "scope.screenshot_profile": ("get_screenshot_profile",),
        "scope.screenshot_v2": (
            "get_screenshot_profile",
            "capture_screenshot",
            "snapshot_screenshot_state",
            "restore_screenshot_state",
            "verify_screenshot_state_restored",
        ),
        "scope.acquisition_run_state": ("get_acquisition_run_state",),
        "scope.acquisition_control": (
            "get_acquisition_run_state",
            "start_continuous",
            "stop_acquisition",
            "acquire_single",
            "snapshot_acquisition_control",
            "restore_acquisition_control",
            "verify_acquisition_control_restored",
        ),
        "scope.trace_metadata": ("get_trace_metadata",),
        "scope.fetch_trace": (
            "get_trace_metadata",
            "fetch_trace",
            "snapshot_trace_transfer_state",
            "restore_trace_transfer_state",
            "verify_trace_transfer_state_restored",
        ),
        "scope.error_drain_v1": ("drain_errors",),
        "scope.channel_input_state_v2": ("get_channel_input_state_v2",),
        "scope.digital_status_v2": ("get_digital_status_v2",),
        "scope.snapshot_v2": ("get_snapshot_v2",),
        "scope.acquisition_status_v2": ("get_acquisition_status_v2",),
    }
)

# Kept as an import-compatible alias for the internal implementation branch.
EXPERIMENTAL_SCOPE_CAPABILITY_METHODS = SCOPE_CAPABILITY_METHODS


def validate_scope_descriptor(
    descriptor: InstrumentDescriptor,
    *,
    driver: object | None = None,
    _require_public_version: bool = True,
) -> None:
    """Validate scope extension declarations before instrument I/O."""

    extensions = descriptor.scope_extensions
    waveform_profile = (
        extensions.waveform_binary_profile if extensions is not None else None
    )
    declared = set(descriptor.capabilities) & set(SCOPE_CAPABILITY_METHODS)
    if not declared and waveform_profile is None:
        return
    if descriptor.kind != "scope":
        raise ConfigError("scope extension capabilities require a scope descriptor")
    if _require_public_version:
        try:
            minimum = Version(descriptor.wavebench_min_version)
            contract_minimum = Version(SCOPE_EXTENSIONS_MIN_CORE_VERSION)
        except InvalidVersion as exc:
            raise ConfigError("scope extension descriptor has an invalid core version") from exc
        if minimum < contract_minimum:
            raise ConfigError(
                "scope extension capabilities require wavebench_min_version "
                f">= {SCOPE_EXTENSIONS_MIN_CORE_VERSION}"
            )
        if declared & SCOPE_STRICT_V2_CAPABILITIES and minimum < Version(
            SCOPE_PORTABILITY_V2_MIN_CORE_VERSION
        ):
            raise ConfigError(
                "scope portability V2 capabilities require wavebench_min_version "
                f">= {SCOPE_PORTABILITY_V2_MIN_CORE_VERSION}"
            )
    dependencies = {
        "scope.acquisition_control": {"scope.acquisition_run_state"},
    }
    for capability, required in dependencies.items():
        if capability in declared and not required <= declared:
            raise ConfigError(
                f"instrument {descriptor.driver_id!r} capability {capability!r} requires "
                + ", ".join(sorted(required))
            )
    profile_requirements = {
        "scope.screenshot_profile": "screenshot_profile",
        "scope.screenshot_v2": "screenshot_profile",
        "scope.acquisition_control": "acquisition_control_profile",
        "scope.trace_metadata": "trace_profile",
        "scope.fetch_trace": "trace_profile",
        "scope.snapshot_v2": "snapshot_profile_v2",
        "scope.acquisition_status_v2": "acquisition_status_profile_v2",
    }
    for capability in sorted(declared):
        profile_name = profile_requirements.get(capability)
        if profile_name is not None and (
            extensions is None or getattr(extensions, profile_name) is None
        ):
            raise ConfigError(
                f"instrument {descriptor.driver_id!r} capability {capability!r} "
                f"requires scope_extensions.{profile_name}"
            )
        if driver is None:
            continue
        missing = tuple(
            method
            for method in SCOPE_CAPABILITY_METHODS[capability]
            if not callable(getattr(driver, method, None))
        )
        if missing:
            raise ConfigError(
                f"instrument {descriptor.driver_id!r} capability {capability!r} "
                f"requires callable method(s): {', '.join(missing)}"
            )
    acquisition_status_profile = (
        extensions.acquisition_status_profile_v2 if extensions is not None else None
    )
    if (
        "scope.acquisition_status_v2" in declared
        and acquisition_status_profile is not None
        and "run_state" in acquisition_status_profile.readable_fields
        and "scope.acquisition_run_state" not in declared
    ):
        raise ConfigError(
            f"instrument {descriptor.driver_id!r} capability 'scope.acquisition_status_v2' "
            "requires scope.acquisition_run_state when its profile reads run_state"
        )
    _validate_waveform_binary_profile(
        descriptor,
        driver=driver,
        waveform_profile=waveform_profile,
        require_public_version=_require_public_version,
    )


def _validate_waveform_binary_profile(
    descriptor: InstrumentDescriptor,
    *,
    driver: object | None,
    waveform_profile: object | None,
    require_public_version: bool,
) -> None:
    """Validate optional standard-waveform bounded-contract declarations."""

    if waveform_profile is None:
        return
    if descriptor.kind != "scope":
        raise ConfigError("waveform binary profiles require a scope descriptor")
    if require_public_version:
        try:
            minimum = Version(descriptor.wavebench_min_version)
            contract_minimum = Version(SCOPE_WAVEFORM_BINARY_MIN_CORE_VERSION)
        except InvalidVersion as exc:
            raise ConfigError("waveform binary descriptor has an invalid core version") from exc
        if minimum < contract_minimum:
            raise ConfigError(
                "waveform binary profiles require wavebench_min_version "
                f">= {SCOPE_WAVEFORM_BINARY_MIN_CORE_VERSION}"
            )
    declared = set(descriptor.capabilities)
    waveform_capabilities = set(_WAVEFORM_BINARY_CAPABILITY_BY_OPERATION.values())
    declared_waveform = declared & waveform_capabilities
    expected_by_operation = _WAVEFORM_BINARY_CAPABILITY_BY_OPERATION
    profile_operations = {item.operation_kind for item in waveform_profile.operations}
    expected_operations = {
        operation
        for operation, capability in expected_by_operation.items()
        if capability in declared_waveform
    }
    if profile_operations != expected_operations:
        raise ConfigError(
            f"instrument {descriptor.driver_id!r} waveform binary profile operations must "
            "match declared standard waveform capabilities exactly"
        )
    if "scope.idn" not in declared:
        raise ConfigError(
            f"instrument {descriptor.driver_id!r} waveform binary profile requires "
            "capability 'scope.idn'"
        )
    if driver is None:
        return
    missing = list(_WAVEFORM_BINARY_RECOVERY_METHODS)
    missing.extend(
        _WAVEFORM_BINARY_METHOD_BY_OPERATION[operation]
        for operation in sorted(profile_operations)
    )
    unavailable = tuple(
        method for method in missing if not callable(getattr(driver, method, None))
    )
    if unavailable:
        raise ConfigError(
            f"instrument {descriptor.driver_id!r} waveform binary profile requires "
            f"callable method(s): {', '.join(unavailable)}"
        )


def validate_experimental_scope_descriptor(
    descriptor: InstrumentDescriptor,
    *,
    driver: object | None = None,
    enabled: bool = False,
) -> None:
    """Validate candidate capabilities without publishing them to the main registry."""

    if not enabled:
        raise ConfigError("experimental scope extensions are disabled")
    validate_scope_descriptor(
        descriptor,
        driver=driver,
        _require_public_version=False,
    )


__all__ = [
    "EXPERIMENTAL_SCOPE_CAPABILITY_METHODS",
    "SCOPE_CAPABILITY_METHODS",
    "SCOPE_EXTENSIONS_MIN_CORE_VERSION",
    "SCOPE_PORTABILITY_V2_MIN_CORE_VERSION",
    "SCOPE_STRICT_V2_CAPABILITIES",
    "SCOPE_WAVEFORM_BINARY_MIN_CORE_VERSION",
    "validate_experimental_scope_descriptor",
    "validate_scope_descriptor",
]
