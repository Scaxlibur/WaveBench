"""Capability registration and descriptor validation for RF signal sources."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from packaging.version import InvalidVersion, Version

from wavebench.errors import ConfigError

from .rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_SNAPSHOT_MIN_CORE_VERSION,
    RfSourceDescriptorExtensions,
)


RF_SOURCE_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "rf_source.idn": ("idn",),
        "rf_source.snapshot": ("get_rf_snapshot",),
    }
)


def validate_rf_source_descriptor(descriptor: object, driver: object | None = None) -> None:
    """Validate the static, read-only RF-source M0 descriptor contract."""

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


def validate_rf_source_plugin_dependencies(
    descriptor: object,
    dependencies: Iterable[str],
) -> None:
    """Reserve a public dependency-validation hook for RF-source plugin wheels.

    M0 only freezes the descriptor's version interval.  The general plugin
    lifecycle already proves one active WaveBench dependency before entry-point
    import; later RF-specific releases can tighten this hook without changing
    the descriptor schema.
    """

    del dependencies
    if getattr(descriptor, "kind", None) == "rf_source":
        _validate_rf_source_version_range(descriptor)


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
