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
    RfModulationProfile,
    RfOutputProfile,
    RfSourceDescriptorExtensions,
)


RF_SOURCE_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "rf_source.idn": ("idn",),
        "rf_source.snapshot": ("get_rf_snapshot",),
        "rf_source.cw_configure": ("configure_cw",),
        "rf_source.modulation_configure": (
            "get_rf_modulation_state",
            "get_rf_modulation_snapshot",
            "configure_rf_modulation",
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
    if "rf_source.modulation_configure" in rf_capabilities:
        _validate_modulation_configure_feature(extensions)
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
