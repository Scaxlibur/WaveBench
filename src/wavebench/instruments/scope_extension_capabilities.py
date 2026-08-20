"""Default-off descriptor/capability validation for the Draft scope R1.3 RFC."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from wavebench.errors import ConfigError

from .api import InstrumentDescriptor


EXPERIMENTAL_SCOPE_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
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


def validate_experimental_scope_descriptor(
    descriptor: InstrumentDescriptor,
    *,
    driver: object | None = None,
    enabled: bool = False,
) -> None:
    """Validate candidate capabilities without publishing them to the main registry."""

    if not enabled:
        raise ConfigError("experimental scope extensions are disabled")
    if descriptor.kind != "scope":
        raise ConfigError("experimental scope capabilities require a scope descriptor")
    declared = set(descriptor.capabilities) & set(EXPERIMENTAL_SCOPE_CAPABILITY_METHODS)
    if not declared:
        return
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
            for method in EXPERIMENTAL_SCOPE_CAPABILITY_METHODS[capability]
            if not callable(getattr(driver, method, None))
        )
        if missing:
            raise ConfigError(
                f"instrument {descriptor.driver_id!r} capability {capability!r} "
                f"requires callable method(s): {', '.join(missing)}"
            )


__all__ = [
    "EXPERIMENTAL_SCOPE_CAPABILITY_METHODS",
    "validate_experimental_scope_descriptor",
]
