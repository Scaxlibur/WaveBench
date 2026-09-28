"""Capability registration and descriptor validation for RF signal sources."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from wavebench.errors import ConfigError

from .rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION,
    RfCwProfile,
    RfFeature,
    RfFeatureDirection,
    RfModulatedOutputProfile,
    RfModulationProfile,
    RfOutputProfile,
    RfPulseProfile,
    RfPulseOutputProfile,
    RfSourceDescriptorExtensions,
    RfSweepProfile,
    RfTriggerProfile,
)


RF_SOURCE_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "rf_source.idn": ("idn",),
        "rf_source.snapshot": ("get_rf_snapshot",),
        "rf_source.trigger_snapshot": ("get_rf_trigger_snapshot",),
        "rf_source.cw_configure": ("configure_cw",),
        "rf_source.modulation_configure": (
            "get_rf_modulation_state",
            "get_rf_modulation_snapshot",
            "configure_rf_modulation",
        ),
        "rf_source.modulation_disable": (
            "get_rf_modulation_state",
            "disable_rf_modulation",
        ),
        "rf_source.modulated_output_enable": (
            "get_rf_modulation_snapshot",
            "set_rf_output",
        ),
        "rf_source.pulse_configure": (
            "get_rf_pulse_snapshot",
            "configure_rf_pulse",
        ),
        "rf_source.pulse_output": (
            "get_rf_pulse_output_snapshot",
            "set_rf_pulse_output",
        ),
        "rf_source.sweep_configure": (
            "get_rf_sweep_snapshot",
            "configure_rf_sweep",
        ),
        "rf_source.output": ("set_rf_output",),
    }
)


def validate_rf_source_descriptor(descriptor: object, driver: object | None = None) -> None:
    """Validate the static RF-source descriptor contract."""

    capabilities = tuple(getattr(descriptor, "capabilities", ()))
    rf_capabilities = tuple(
        capability for capability in capabilities if isinstance(capability, str) and capability.startswith("rf_source.")
    )
    unknown = sorted(set(rf_capabilities) - set(RF_SOURCE_CAPABILITY_METHODS))
    if unknown:
        raise ConfigError(
            "RF source descriptor declares unknown capabilities: " + ", ".join(unknown)
        )

    kind = getattr(descriptor, "kind", None)
    extensions = getattr(descriptor, "rf_source_extensions", None)
    if kind != "rf_source":
        if extensions is not None:
            raise ConfigError("rf_source_extensions can only be declared by rf_source descriptors")
        if rf_capabilities:
            raise ConfigError("rf_source capabilities require kind='rf_source'")
        return
    if extensions is None:
        raise ConfigError("rf_source descriptors require rf_source_extensions")
    if not isinstance(extensions, RfSourceDescriptorExtensions):
        raise ConfigError("rf_source_extensions has an invalid type")
    if extensions.contract_version != RF_SOURCE_CONTRACT_VERSION:
        raise ConfigError("rf_source_extensions uses an unsupported contract version")
    if any(not isinstance(capability, str) or not capability.startswith("rf_source.") for capability in capabilities):
        raise ConfigError("rf_source descriptors can only declare rf_source capabilities")
    if "rf_source.idn" not in rf_capabilities:
        raise ConfigError("rf_source descriptors require the rf_source.idn capability")
    if "rf_source.cw_configure" in rf_capabilities:
        _validate_cw_configure_feature(extensions)
    if "rf_source.trigger_snapshot" in rf_capabilities:
        _validate_trigger_snapshot_feature(extensions)
    if "rf_source.modulation_configure" in rf_capabilities:
        _validate_modulation_configure_feature(extensions)
    if "rf_source.modulation_disable" in rf_capabilities:
        _validate_modulation_disable_feature(extensions)
    if "rf_source.modulated_output_enable" in rf_capabilities:
        if "rf_source.output" not in rf_capabilities:
            raise ConfigError(
                "rf_source.modulated_output_enable requires the rf_source.output capability"
            )
        if "rf_source.modulation_configure" not in rf_capabilities:
            raise ConfigError(
                "rf_source.modulated_output_enable requires the rf_source.modulation_configure capability"
            )
        _validate_modulated_output_enable_feature(extensions)
    if "rf_source.pulse_configure" in rf_capabilities:
        _validate_pulse_configure_feature(extensions)
    if "rf_source.pulse_output" in rf_capabilities:
        if "rf_source.snapshot" not in rf_capabilities:
            raise ConfigError("rf_source.pulse_output requires the rf_source.snapshot capability")
        if "rf_source.pulse_configure" not in rf_capabilities:
            raise ConfigError(
                "rf_source.pulse_output requires the rf_source.pulse_configure capability"
            )
        _validate_pulse_output_feature(extensions)
    if "rf_source.sweep_configure" in rf_capabilities:
        _validate_sweep_configure_feature(extensions)
    if "rf_source.output" in rf_capabilities:
        _validate_output_feature(extensions)
    _validate_rf_source_version_range(descriptor)
    if driver is not None:
        for capability in rf_capabilities:
            for method_name in RF_SOURCE_CAPABILITY_METHODS[capability]:
                method = getattr(driver, method_name, None)
                if not callable(method):
                    raise TypeError(
                        f"descriptor declares capability {capability!r}, but driver lacks "
                        f"callable method {method_name}"
                    )


def _validate_cw_configure_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.CW),
        None,
    )
    if feature is None or RfFeatureDirection.CONFIGURE not in feature.directions:
        raise ConfigError(
            "rf_source.cw_configure requires an RF CW feature with configure direction"
        )
    if not isinstance(feature.profile, RfCwProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.cw_configure requires an RF CW profile")
    if not (feature.profile.frequency_configurable or feature.profile.power_configurable):
        raise ConfigError(
            "rf_source.cw_configure requires a configurable RF CW frequency or power field"
    )


def _validate_trigger_snapshot_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.TRIGGER),
        None,
    )
    if feature is None or RfFeatureDirection.READ not in feature.directions:
        raise ConfigError(
            "rf_source.trigger_snapshot requires an RF trigger feature with read direction"
        )
    if not isinstance(feature.profile, RfTriggerProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.trigger_snapshot requires an RF trigger profile")
    if not feature.profile.state_readable:
        raise ConfigError("rf_source.trigger_snapshot requires readable RF trigger state")


def _validate_output_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.OUTPUT),
        None,
    )
    if (
        feature is None
        or RfFeatureDirection.ENABLE not in feature.directions
        or RfFeatureDirection.DISABLE not in feature.directions
    ):
        raise ConfigError(
            "rf_source.output requires matching RF output ENABLE and DISABLE directions"
        )
    if not isinstance(feature.profile, RfOutputProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.output requires an RF output profile")
    if not feature.profile.output_readable:
        raise ConfigError("rf_source.output requires readable RF output state")


def _validate_modulation_configure_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.MODULATION),
        None,
    )
    if (
        feature is None
        or RfFeatureDirection.CONFIGURE not in feature.directions
        or RfFeatureDirection.READ not in feature.directions
    ):
        raise ConfigError(
            "rf_source.modulation_configure requires an RF modulation feature with "
            "configure and read directions"
        )
    if not isinstance(feature.profile, RfModulationProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.modulation_configure requires an RF modulation profile")
    if not feature.profile.configuration_readable or not feature.profile.mode_profiles:
        raise ConfigError(
            "rf_source.modulation_configure requires readable bounded modulation mode profiles"
        )


def _validate_modulation_disable_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.MODULATION),
        None,
    )
    if (
        feature is None
        or RfFeatureDirection.DISABLE not in feature.directions
        or RfFeatureDirection.READ not in feature.directions
    ):
        raise ConfigError(
            "rf_source.modulation_disable requires an RF modulation feature with "
            "disable and read directions"
        )
    if not isinstance(feature.profile, RfModulationProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.modulation_disable requires an RF modulation profile")
    if not feature.profile.state_readable:
        raise ConfigError("rf_source.modulation_disable requires readable RF modulation state")


def _validate_modulated_output_enable_feature(extensions: RfSourceDescriptorExtensions) -> None:
    output_feature = next(
        (item for item in extensions.features if item.feature is RfFeature.OUTPUT),
        None,
    )
    modulation_feature = next(
        (item for item in extensions.features if item.feature is RfFeature.MODULATION),
        None,
    )
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.MODULATED_OUTPUT),
        None,
    )
    if (
        output_feature is None
        or RfFeatureDirection.ENABLE not in output_feature.directions
        or RfFeatureDirection.DISABLE not in output_feature.directions
        or not isinstance(output_feature.profile, RfOutputProfile)
        or not output_feature.profile.output_readable
    ):
        raise ConfigError(
            "rf_source.modulated_output_enable requires a readable base RF output profile"
        )
    if (
        modulation_feature is None
        or RfFeatureDirection.CONFIGURE not in modulation_feature.directions
        or RfFeatureDirection.READ not in modulation_feature.directions
        or not isinstance(modulation_feature.profile, RfModulationProfile)
        or not modulation_feature.profile.configuration_readable
        or not modulation_feature.profile.mode_profiles
    ):
        raise ConfigError(
            "rf_source.modulated_output_enable requires a readable configurable modulation profile"
        )
    if (
        feature is None
        or RfFeatureDirection.ENABLE not in feature.directions
        or not isinstance(feature.profile, RfModulatedOutputProfile)
    ):
        raise ConfigError(
            "rf_source.modulated_output_enable requires a modulated-output feature with enable direction"
        )
    if not set(feature.port_ids) <= set(output_feature.port_ids):
        raise ConfigError(
            "rf_source.modulated_output_enable ports must also declare the base output feature"
        )
    if not set(feature.port_ids) <= set(modulation_feature.port_ids):
        raise ConfigError(
            "rf_source.modulated_output_enable ports must also declare the modulation feature"
        )
    base_profiles = {
        profile.kind: profile for profile in modulation_feature.profile.mode_profiles
    }
    for profile in feature.profile.mode_profiles:
        base_profile = base_profiles.get(profile.kind)
        if base_profile is None:
            raise ConfigError(
                "rf_source.modulated_output_enable profile must be declared by modulation"
            )
        if (
            profile.source is not base_profile.source
            or profile.waveform is not base_profile.waveform
            or profile.value_unit is not base_profile.value_unit
            or profile.value_min < base_profile.value_min
            or profile.value_max > base_profile.value_max
            or profile.internal_frequency_min_hz < base_profile.internal_frequency_min_hz
            or profile.internal_frequency_max_hz > base_profile.internal_frequency_max_hz
        ):
            raise ConfigError(
                "rf_source.modulated_output_enable profile must be a subset of modulation"
            )
    topology = {port.port_id: port for port in extensions.topology.ports}
    for port_id in feature.port_ids:
        port = topology[port_id]
        if not port.power_min_dbm <= feature.profile.maximum_power_dbm <= port.power_max_dbm:
            raise ConfigError(
                "rf_source.modulated_output_enable maximum_power_dbm must be within each port range"
            )


def _validate_pulse_configure_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.PULSE),
        None,
    )
    if (
        feature is None
        or RfFeatureDirection.CONFIGURE not in feature.directions
        or RfFeatureDirection.READ not in feature.directions
    ):
        raise ConfigError(
            "rf_source.pulse_configure requires an RF pulse feature with configure and read directions"
        )
    if not isinstance(feature.profile, RfPulseProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.pulse_configure requires an RF pulse profile")
    if not feature.profile.configuration_readable or not feature.profile.mode_profiles:
        raise ConfigError(
            "rf_source.pulse_configure requires readable bounded RF pulse mode profiles"
        )


def _validate_pulse_output_feature(extensions: RfSourceDescriptorExtensions) -> None:
    pulse_feature = next(
        (item for item in extensions.features if item.feature is RfFeature.PULSE),
        None,
    )
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.PULSE_OUTPUT),
        None,
    )
    if (
        pulse_feature is None
        or RfFeatureDirection.CONFIGURE not in pulse_feature.directions
        or RfFeatureDirection.READ not in pulse_feature.directions
        or not isinstance(pulse_feature.profile, RfPulseProfile)
        or not pulse_feature.profile.configuration_readable
    ):
        raise ConfigError(
            "rf_source.pulse_output requires a readable configurable base pulse profile"
        )
    if (
        feature is None
        or RfFeatureDirection.READ not in feature.directions
        or RfFeatureDirection.ENABLE not in feature.directions
        or RfFeatureDirection.DISABLE not in feature.directions
        or not isinstance(feature.profile, RfPulseOutputProfile)
        or not feature.profile.output_readable
    ):
        raise ConfigError(
            "rf_source.pulse_output requires a readable Pulse-output feature with "
            "enable and disable directions"
        )
    if not set(feature.port_ids) <= set(pulse_feature.port_ids):
        raise ConfigError(
            "rf_source.pulse_output ports must also declare the base pulse feature"
        )
    profile = feature.profile
    mode_profile = next(
        (
            item
            for item in pulse_feature.profile.mode_profiles
            if item.source is profile.source and item.mode is profile.mode
        ),
        None,
    )
    if mode_profile is None:
        raise ConfigError(
            "rf_source.pulse_output profile must reference a declared base pulse mode"
        )
    if profile.polarity not in mode_profile.polarities:
        raise ConfigError(
            "rf_source.pulse_output profile polarity must be declared by the base pulse mode"
        )
    if not mode_profile.period_min_s <= profile.period_s <= mode_profile.period_max_s:
        raise ConfigError(
            "rf_source.pulse_output profile period_s must be within the base pulse range"
        )
    if not mode_profile.width_min_s <= profile.width_s <= mode_profile.width_max_s:
        raise ConfigError(
            "rf_source.pulse_output profile width_s must be within the base pulse range"
        )
    if profile.width_s > profile.period_s - mode_profile.minimum_off_time_s:
        raise ConfigError(
            "rf_source.pulse_output profile violates the base pulse minimum off time"
        )


def _validate_sweep_configure_feature(extensions: RfSourceDescriptorExtensions) -> None:
    feature = next(
        (item for item in extensions.features if item.feature is RfFeature.SWEEP),
        None,
    )
    if (
        feature is None
        or RfFeatureDirection.CONFIGURE not in feature.directions
        or RfFeatureDirection.READ not in feature.directions
    ):
        raise ConfigError(
            "rf_source.sweep_configure requires an RF Sweep feature with configure and read directions"
        )
    if not isinstance(feature.profile, RfSweepProfile):  # defensive: extensions validates this.
        raise ConfigError("rf_source.sweep_configure requires an RF Sweep profile")
    if not feature.profile.configuration_readable or not feature.profile.mode_profiles:
        raise ConfigError(
            "rf_source.sweep_configure requires readable bounded RF Sweep mode profiles"
        )


def validate_rf_source_plugin_dependencies(
    descriptor: object,
    dependencies: Iterable[str],
) -> None:
    """Cross-check an RF-source descriptor against its wheel metadata."""

    if getattr(descriptor, "kind", None) != "rf_source":
        return
    _validate_rf_source_version_range(descriptor)

    requirements: list[Requirement] = []
    for dependency in dependencies:
        if not isinstance(dependency, str):
            raise ConfigError("RF source wheel dependency metadata must contain strings")
        try:
            requirement = Requirement(dependency)
        except InvalidRequirement as exc:
            raise ConfigError("RF source wheel has an invalid Requires-Dist entry") from exc
        if canonicalize_name(requirement.name) == "wavebench" and (
            requirement.marker is None or requirement.marker.evaluate()
        ):
            requirements.append(requirement)
    if len(requirements) != 1:
        raise ConfigError(
            "RF source wheel must declare exactly one active WaveBench dependency for its descriptor"
        )
    requirement = requirements[0]

    try:
        minimum = Version(getattr(descriptor, "wavebench_min_version", ""))
        maximum = Version(getattr(descriptor, "wavebench_max_version", ""))
    except (InvalidVersion, TypeError) as exc:  # pragma: no cover - checked above
        raise ConfigError("RF source descriptor versions must use valid PEP 440 syntax") from exc
    specifiers = tuple(requirement.specifier)
    has_floor = any(
        item.operator == ">=" and Version(item.version) == minimum
        for item in specifiers
    )
    has_ceiling = any(
        item.operator == "<" and Version(item.version) == maximum
        for item in specifiers
    )
    if not has_floor or not has_ceiling:
        raise ConfigError(
            "RF source wheel WaveBench dependency must explicitly include "
            f">={minimum},<{maximum} to match the descriptor"
        )
    if minimum not in requirement.specifier or maximum in requirement.specifier:
        raise ConfigError(
            "RF source wheel WaveBench dependency expands or excludes its descriptor interval"
        )


def _validate_rf_source_version_range(descriptor: object) -> None:
    minimum_text = getattr(descriptor, "wavebench_min_version", "")
    maximum_text = getattr(descriptor, "wavebench_max_version", "")
    try:
        minimum = Version(minimum_text)
        maximum = Version(maximum_text)
        required = Version(RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION)
    except (InvalidVersion, TypeError) as exc:
        raise ConfigError("RF source descriptor versions must use valid PEP 440 syntax") from exc
    if minimum >= maximum:
        raise ConfigError("RF source descriptor version range must satisfy min < max")
    if minimum < required:
        raise ConfigError(
            "RF source descriptors require wavebench_min_version >= "
            f"{RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION}"
        )


__all__ = [
    "RF_SOURCE_CAPABILITY_METHODS",
    "validate_rf_source_descriptor",
    "validate_rf_source_plugin_dependencies",
]
