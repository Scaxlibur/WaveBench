"""Descriptor and capability validation for the scope R1.3 public contract."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from packaging.version import InvalidVersion, Version

from wavebench.errors import ConfigError

from .api import InstrumentDescriptor


SCOPE_EXTENSIONS_MIN_CORE_VERSION = "0.8.23"


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

    declared = set(descriptor.capabilities) & set(SCOPE_CAPABILITY_METHODS)
    if not declared:
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
    dependencies = {
        "scope.acquisition_control": {"scope.acquisition_run_state"},
    }
    for capability, required in dependencies.items():
        if capability in declared and not required <= declared:
            raise ConfigError(
                f"instrument {descriptor.driver_id!r} capability {capability!r} requires "
                + ", ".join(sorted(required))
            )
    extensions = descriptor.scope_extensions
    profile_requirements = {
        "scope.screenshot_profile": "screenshot_profile",
        "scope.screenshot_v2": "screenshot_profile",
        "scope.acquisition_control": "acquisition_control_profile",
        "scope.trace_metadata": "trace_profile",
        "scope.fetch_trace": "trace_profile",
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
    "validate_experimental_scope_descriptor",
    "validate_scope_descriptor",
]
