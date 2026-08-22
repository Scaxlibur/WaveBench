"""Capability registration and validation for Source V2 snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from wavebench.errors import ConfigError

from .source_extensions import (
    SOURCE_CONTRACT_VERSION,
    SOURCE_SNAPSHOT_MIN_CORE_VERSION,
    SourceDescriptorExtensions,
    SourceAnchorField,
    SourceFieldId,
    SourceFeature,
    SourceFeatureDirection,
    SourceQueryEffect,
)


SOURCE_EXTENSION_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "source.snapshot_v2": ("execute_source_query_plan_v2",),
    }
)


def validate_source_descriptor(descriptor: object, driver: object | None = None) -> None:
    capabilities = tuple(getattr(descriptor, "capabilities", ()))
    declared = tuple(
        capability
        for capability in capabilities
        if capability in SOURCE_EXTENSION_CAPABILITY_METHODS
    )
    extensions = getattr(descriptor, "source_extensions", None)
    if extensions is None:
        if declared:
            raise ConfigError("Source V2 capabilities require descriptor source_extensions")
        return
    if getattr(descriptor, "kind", None) != "source":
        raise ConfigError("source_extensions can only be declared by source descriptors")
    if not isinstance(extensions, SourceDescriptorExtensions):
        raise ConfigError("source_extensions has an invalid type")
    if extensions.contract_version != SOURCE_CONTRACT_VERSION:
        raise ConfigError("source_extensions uses an unsupported contract version")
    if declared != ("source.snapshot_v2",):
        raise ConfigError(
            "source_extensions require the source.snapshot_v2 capability and no other "
            "Source V2 capability is registered in this core revision"
        )
    _validate_source_version_range(descriptor)
    _validate_read_contract(extensions)
    if driver is not None:
        method = getattr(driver, "execute_source_query_plan_v2", None)
        if not callable(method):
            raise TypeError(
                "descriptor declares capability 'source.snapshot_v2', but driver lacks "
                "callable method execute_source_query_plan_v2"
            )


def validate_source_plugin_dependencies(
    descriptor: object,
    dependencies: Iterable[str],
) -> None:
    """Cross-check a Source V2 descriptor against its wheel metadata.

    The generic wheel gate proves that the current core satisfies one active
    ``wavebench`` requirement before importing an entry point.  This second,
    post-import gate is deliberately limited to Source V2: it makes the
    descriptor's declared PEP 440 interval and that wheel requirement describe
    the same first-supported floor and exclusive ceiling.  V1-only plugins
    retain their existing package semantics.
    """

    capabilities = tuple(getattr(descriptor, "capabilities", ()))
    if "source.snapshot_v2" not in capabilities:
        return
    _validate_source_version_range(descriptor)

    requirements: list[Requirement] = []
    for dependency in dependencies:
        if not isinstance(dependency, str):
            raise ConfigError("Source V2 wheel dependency metadata must contain strings")
        try:
            requirement = Requirement(dependency)
        except InvalidRequirement as exc:
            raise ConfigError("Source V2 wheel has an invalid Requires-Dist entry") from exc
        if canonicalize_name(requirement.name) == "wavebench" and (
            requirement.marker is None or requirement.marker.evaluate()
        ):
            requirements.append(requirement)
    if len(requirements) != 1:
        raise ConfigError(
            "Source V2 wheel must declare exactly one active WaveBench dependency for its descriptor"
        )
    requirement = requirements[0]

    try:
        minimum = Version(getattr(descriptor, "wavebench_min_version", ""))
        maximum = Version(getattr(descriptor, "wavebench_max_version", ""))
    except (InvalidVersion, TypeError) as exc:  # pragma: no cover - checked above
        raise ConfigError("Source V2 descriptor versions must use valid PEP 440 syntax") from exc
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
            "Source V2 wheel WaveBench dependency must explicitly include "
            f">={minimum},<{maximum} to match the descriptor"
        )
    if minimum not in requirement.specifier or maximum in requirement.specifier:
        raise ConfigError(
            "Source V2 wheel WaveBench dependency expands or excludes its descriptor interval"
        )


def _validate_source_version_range(descriptor: object) -> None:
    minimum_text = getattr(descriptor, "wavebench_min_version", "")
    maximum_text = getattr(descriptor, "wavebench_max_version", "")
    try:
        minimum = Version(minimum_text)
        maximum = Version(maximum_text)
        required = Version(SOURCE_SNAPSHOT_MIN_CORE_VERSION)
    except (InvalidVersion, TypeError) as exc:
        raise ConfigError("Source V2 descriptor versions must use valid PEP 440 syntax") from exc
    if minimum >= maximum:
        raise ConfigError("Source V2 descriptor version range must satisfy min < max")
    if minimum < required:
        raise ConfigError(
            "Source V2 descriptors require wavebench_min_version >= "
            f"{SOURCE_SNAPSHOT_MIN_CORE_VERSION}"
        )


def _validate_read_contract(extensions: SourceDescriptorExtensions) -> None:
    feature_keys = {
        (feature.feature, feature.scope, feature.channels): feature
        for feature in extensions.features
    }
    for feature in extensions.features:
        if feature.support.value == "supported" and (
            SourceFeatureDirection.READ not in feature.directions
        ):
            raise ConfigError(
                f"supported Source V2 feature {feature.feature.value!r} must declare read"
            )
        if any(direction is not SourceFeatureDirection.READ for direction in feature.directions):
            raise ConfigError(
                "the accepted Source V2 snapshot revision only allows read directions"
            )
        if feature.support.value == "supported" and not any(
            facet.feature is feature.feature and facet.scope is feature.scope
            for facet in extensions.query_contract.facets
        ):
            raise ConfigError(
                f"supported Source V2 feature {feature.feature.value!r} lacks a query contract"
            )
    identity_facets = tuple(
        facet
        for facet in extensions.query_contract.facets
        if SourceFieldId.IDENTITY in facet.fields
    )
    if len(identity_facets) != 1 or not identity_facets[0].required:
        raise ConfigError("Source V2 query contracts require one required identity facet")
    activation_fields = {
        SourceAnchorField.WAVEFORM_KIND: SourceFieldId.BASIC,
        SourceAnchorField.FREQUENCY_MODE: SourceFieldId.BASIC,
        SourceAnchorField.OUTPUT_ENABLED: SourceFieldId.OUTPUT,
        SourceAnchorField.HARMONICS_ENABLED: SourceFieldId.HARMONICS,
        SourceAnchorField.MODULATION_ENABLED: SourceFieldId.MODULATION,
        SourceAnchorField.SWEEP_ENABLED: SourceFieldId.SWEEP,
        SourceAnchorField.BURST_ENABLED: SourceFieldId.BURST,
        SourceAnchorField.ARBITRARY_PLAYBACK_MODE: SourceFieldId.ARBITRARY_SELECTION,
        SourceAnchorField.COMBINE_ENABLED: SourceFieldId.COMBINE,
        SourceAnchorField.COUPLING_ENABLED: SourceFieldId.COUPLING,
        SourceAnchorField.TRACKING_ENABLED: SourceFieldId.TRACKING,
    }
    field_features = {
        SourceFieldId.BASIC: frozenset({SourceFeature.BASIC}),
        SourceFieldId.OUTPUT: frozenset({SourceFeature.OUTPUT}),
        SourceFieldId.DISPLAY_LOAD: frozenset({SourceFeature.OUTPUT}),
        SourceFieldId.HARMONICS: frozenset({SourceFeature.HARMONICS}),
        SourceFieldId.MODULATION: frozenset({SourceFeature.MODULATION}),
        SourceFieldId.SWEEP: frozenset({SourceFeature.SWEEP}),
        SourceFieldId.BURST: frozenset({SourceFeature.BURST}),
        SourceFieldId.PULSE: frozenset({SourceFeature.PULSE}),
        SourceFieldId.ARBITRARY_SELECTION: frozenset({SourceFeature.ARBITRARY}),
        SourceFieldId.ARBITRARY_STORAGE: frozenset({SourceFeature.ARBITRARY}),
        SourceFieldId.ARM_STATE: frozenset({SourceFeature.BURST, SourceFeature.SWEEP}),
        SourceFieldId.TRIGGER_STATE: frozenset({SourceFeature.BURST, SourceFeature.SWEEP}),
        SourceFieldId.COMBINE: frozenset({SourceFeature.COMBINE}),
        SourceFieldId.COUPLING: frozenset({SourceFeature.COUPLING}),
        SourceFieldId.TRACKING: frozenset({SourceFeature.TRACKING}),
        SourceFieldId.COPY: frozenset({SourceFeature.COPY}),
        SourceFieldId.PHASE_RELATION: frozenset({SourceFeature.PHASE_RELATION}),
        SourceFieldId.RELATION_GRAPH: frozenset(
            {
                SourceFeature.COMBINE,
                SourceFeature.COUPLING,
                SourceFeature.TRACKING,
                SourceFeature.COPY,
                SourceFeature.PHASE_RELATION,
            }
        ),
        SourceFieldId.REFERENCE_CLOCK: frozenset({SourceFeature.REFERENCE_CLOCK}),
        SourceFieldId.SYNC: frozenset({SourceFeature.SYNC}),
        SourceFieldId.CASCADE: frozenset({SourceFeature.CASCADE}),
        SourceFieldId.SHARED_POWER: frozenset({SourceFeature.SHARED_POWER}),
        SourceFieldId.COUNTER: frozenset({SourceFeature.COUNTER}),
    }
    for facet in extensions.query_contract.facets:
        if facet.effect is not SourceQueryEffect.PURE_READ:
            raise ConfigError("source.snapshot_v2 query contracts only allow pure_read")
        if SourceFieldId.IDENTITY in facet.fields:
            if (
                facet.feature is not SourceFeature.BASIC
                or facet.fields != (SourceFieldId.IDENTITY,)
                or facet.scope.value != "instrument"
            ):
                raise ConfigError("Source V2 identity facet must contain only source.identity")
            continue
        if any(facet.feature not in field_features[field] for field in facet.fields):
            raise ConfigError(
                f"Source V2 query field does not belong to feature {facet.feature.value!r}"
            )
        matching = [
            feature
            for (kind, scope, _channels), feature in feature_keys.items()
            if kind is facet.feature and scope is facet.scope
        ]
        if not matching:
            raise ConfigError(
                f"Source V2 query contract references undeclared feature {facet.feature.value!r}"
            )
        if not any(feature.support.value == "supported" for feature in matching):
            raise ConfigError(
                "Source V2 query contracts cannot probe unsupported or unknown features"
            )
        for rule in facet.activation_any:
            for predicate in rule.predicates:
                if activation_fields[predicate.field] not in extensions.query_contract.anchor_fields:
                    raise ConfigError(
                        "Source V2 activation predicates must reference declared anchor fields"
                    )


__all__ = [
    "SOURCE_EXTENSION_CAPABILITY_METHODS",
    "validate_source_descriptor",
    "validate_source_plugin_dependencies",
]
