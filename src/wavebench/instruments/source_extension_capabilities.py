"""Capability registration and descriptor validation for Source V2."""

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
    SourceAmplitudeUnit,
    SourceArbitraryCapabilityProfile,
    SourceDescriptorExtensions,
    SourceAnchorField,
    SourceBasicCapabilityProfile,
    SourceBurstCapabilityProfile,
    SourceBurstMode,
    SourceFieldId,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureDirection,
    SourceFrequencyMode,
    SourceHarmonicCapabilityProfile,
    SourceModulationCapabilityProfile,
    SourceModulationKind,
    SourceModulationParameterKind,
    SourceModulationSource,
    SourceOutputCapabilityProfile,
    SourcePulseCapabilityProfile,
    SourcePulseHoldBasis,
    SourceQueryEffect,
    SourceSweepCapabilityProfile,
    SourceArbitraryPlaybackMode,
    SourceTriggerSource,
    SourceWaveformKind,
    SupportState,
)


SOURCE_EXTENSION_CAPABILITY_METHODS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "source.snapshot_v2": ("execute_source_query_plan_v2",),
        "source.basic_configure_v2": ("configure_source_basic_v2",),
        "source.harmonics_configure_v2": ("configure_source_harmonics_v2",),
        "source.modulation_configure_v2": ("configure_source_modulation_v2",),
        "source.pulse_configure_v2": ("configure_source_pulse_v2",),
        "source.modulation_pm_configure_v2": ("configure_source_pm_modulation_v2",),
        "source.burst_configure_v2": ("configure_source_burst_v2",),
        "source.modulation_fm_configure_v2": ("configure_source_fm_modulation_v2",),
        "source.modulation_pwm_configure_v2": ("configure_source_pwm_modulation_v2",),
        "source.sweep_configure_v2": ("configure_source_sweep_v2",),
        "source.output_v2": ("set_source_output_v2",),
        "source.arbitrary_storage_v2": (
            "read_source_arbitrary_storage_v2",
            "mutate_source_arbitrary_storage_v2",
        ),
        "source.arbitrary_select_v2": ("select_source_arbitrary_v2",),
    }
)

_SOURCE_WRITE_CAPABILITIES = frozenset(
    {
        "source.basic_configure_v2",
        "source.harmonics_configure_v2",
        "source.modulation_configure_v2",
        "source.pulse_configure_v2",
        "source.modulation_pm_configure_v2",
        "source.burst_configure_v2",
        "source.modulation_fm_configure_v2",
        "source.modulation_pwm_configure_v2",
        "source.sweep_configure_v2",
        "source.output_v2",
        "source.arbitrary_storage_v2",
        "source.arbitrary_select_v2",
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
    if "source.snapshot_v2" not in declared:
        raise ConfigError(
            "source_extensions require the source.snapshot_v2 capability"
        )
    _validate_source_version_range(descriptor)
    _validate_read_contract(extensions)
    _validate_write_contract(extensions, frozenset(declared) & _SOURCE_WRITE_CAPABILITIES)
    if driver is not None:
        for capability in declared:
            for method_name in SOURCE_EXTENSION_CAPABILITY_METHODS[capability]:
                method = getattr(driver, method_name, None)
                if not callable(method):
                    raise TypeError(
                        f"descriptor declares capability {capability!r}, but driver lacks "
                        f"callable method {method_name}"
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
    if not set(capabilities) & set(SOURCE_EXTENSION_CAPABILITY_METHODS):
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


def _validate_write_contract(
    extensions: SourceDescriptorExtensions,
    capabilities: frozenset[str],
) -> None:
    _validate_declared_write_directions(extensions, capabilities)
    if not capabilities:
        return

    basic_readable = _channels_with_basic_final_vpp(extensions)
    output_readable = _channels_with_output_readback(extensions)

    if "source.basic_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.BASIC,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.basic_configure_v2 requires basic feature CONFIGURE directions"
            )
        if not configurable <= basic_readable:
            raise ConfigError(
                "source.basic_configure_v2 requires readable final VPP and Offset on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.basic_configure_v2 requires readable output state on every channel"
            )

    if "source.harmonics_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.HARMONICS,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.harmonics_configure_v2 requires harmonics feature CONFIGURE directions"
            )
        readable = _channels_with_harmonic_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.harmonics_configure_v2 requires readable configured order and preset "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.harmonics_configure_v2 requires readable output state on every channel"
            )

    if "source.modulation_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.MODULATION,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.modulation_configure_v2 requires modulation feature CONFIGURE directions"
            )
        readable = _channels_with_modulation_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.modulation_configure_v2 requires readable internal AM configuration "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.modulation_configure_v2 requires readable output state on every channel"
            )

    if "source.modulation_pm_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.MODULATION,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.modulation_pm_configure_v2 requires modulation feature CONFIGURE directions"
            )
        readable = _channels_with_pm_modulation_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.modulation_pm_configure_v2 requires readable internal PM configuration "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.modulation_pm_configure_v2 requires readable output state on every channel"
            )

    if "source.modulation_fm_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.MODULATION,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.modulation_fm_configure_v2 requires modulation feature CONFIGURE directions"
            )
        readable = _channels_with_fm_modulation_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.modulation_fm_configure_v2 requires readable internal FM configuration "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.modulation_fm_configure_v2 requires readable output state on every channel"
            )

    if "source.modulation_pwm_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.MODULATION,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.modulation_pwm_configure_v2 requires modulation feature CONFIGURE directions"
            )
        readable = _channels_with_pwm_modulation_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.modulation_pwm_configure_v2 requires readable internal PWM configuration "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.modulation_pwm_configure_v2 requires readable output state on every channel"
            )

    if "source.pulse_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.PULSE,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.pulse_configure_v2 requires pulse feature CONFIGURE directions"
            )
        readable = _channels_with_pulse_width_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.pulse_configure_v2 requires readable WIDTH pulse configuration "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.pulse_configure_v2 requires readable output state on every channel"
            )

    if "source.burst_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.BURST,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.burst_configure_v2 requires burst feature CONFIGURE directions"
            )
        readable = _channels_with_burst_triggered_internal_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.burst_configure_v2 requires readable internal triggered burst "
                "configuration on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.burst_configure_v2 requires readable output state on every channel"
            )

    if "source.sweep_configure_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.SWEEP,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.sweep_configure_v2 requires sweep feature CONFIGURE directions"
            )
        readable = _channels_with_sweep_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.sweep_configure_v2 requires readable internal sweep configuration "
                "and sweep frequency mode on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.sweep_configure_v2 requires readable output state on every channel"
            )

    if "source.arbitrary_storage_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.ARBITRARY,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.arbitrary_storage_v2 requires arbitrary feature CONFIGURE directions"
            )
        readable = _channels_with_arbitrary_storage_mutation_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.arbitrary_storage_v2 requires readable selected state and authoritative "
                "slot metadata on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.arbitrary_storage_v2 requires readable output state on every channel"
            )

    if "source.arbitrary_select_v2" in capabilities:
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.ARBITRARY,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.arbitrary_select_v2 requires arbitrary feature CONFIGURE directions"
            )
        readable = _channels_with_arbitrary_selection_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.arbitrary_select_v2 requires readable selected waveform, playback mode, "
                "and storage digest on every channel"
            )
        arbitrary_basic = _channels_with_arbitrary_basic_readback(extensions)
        if not configurable <= arbitrary_basic:
            raise ConfigError(
                "source.arbitrary_select_v2 requires readable arbitrary basic waveform state "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.arbitrary_select_v2 requires readable output state on every channel"
            )

    if "source.output_v2" in capabilities:
        enabled = _channels_with_direction(
            extensions,
            SourceFeature.OUTPUT,
            SourceFeatureDirection.ENABLE,
        )
        disabled = _channels_with_direction(
            extensions,
            SourceFeature.OUTPUT,
            SourceFeatureDirection.DISABLE,
        )
        if not enabled or enabled != disabled:
            raise ConfigError(
                "source.output_v2 requires matching output ENABLE and DISABLE directions"
            )
        if not enabled <= output_readable:
            raise ConfigError(
                "source.output_v2 requires readable output state on every channel"
            )


def _channels_with_direction(
    extensions: SourceDescriptorExtensions,
    feature_kind: SourceFeature,
    direction: SourceFeatureDirection,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is feature_kind
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and direction in feature.directions
        )
        for channel in feature.channels
    )


def _channels_with_basic_final_vpp(extensions: SourceDescriptorExtensions) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.BASIC
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceBasicCapabilityProfile)
            and feature.profile.offset_readable
            and SourceAmplitudeUnit.VPP in feature.profile.amplitude_units
        )
        for channel in feature.channels
    )


def _channels_with_harmonic_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.HARMONICS
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceHarmonicCapabilityProfile)
            and feature.profile.presets
            and feature.profile.configured_order_readable
            and feature.profile.preset_readable
        )
        for channel in feature.channels
    )


def _channels_with_output_readback(extensions: SourceDescriptorExtensions) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.OUTPUT
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceOutputCapabilityProfile)
            and feature.profile.output_readable
        )
        for channel in feature.channels
    )


def _channels_with_modulation_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.MODULATION
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceModulationCapabilityProfile)
            and SourceModulationKind.AM in feature.profile.kinds
            and SourceModulationSource.INTERNAL in feature.profile.sources
            and SourceModulationParameterKind.DEPTH_PERCENT in feature.profile.parameter_kinds
            and feature.profile.configuration_readable
        )
        for channel in feature.channels
    )


def _channels_with_pm_modulation_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.MODULATION
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceModulationCapabilityProfile)
            and SourceModulationKind.PM in feature.profile.kinds
            and SourceModulationSource.INTERNAL in feature.profile.sources
            and SourceModulationParameterKind.PHASE_DEVIATION_DEG
            in feature.profile.parameter_kinds
            and feature.profile.configuration_readable
        )
        for channel in feature.channels
    )


def _channels_with_fm_modulation_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.MODULATION
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceModulationCapabilityProfile)
            and SourceModulationKind.FM in feature.profile.kinds
            and SourceModulationSource.INTERNAL in feature.profile.sources
            and SourceModulationParameterKind.FREQUENCY_DEVIATION_HZ
            in feature.profile.parameter_kinds
            and feature.profile.configuration_readable
        )
        for channel in feature.channels
    )


def _channels_with_pwm_modulation_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    supported_parameter_kinds = {
        SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
        SourceModulationParameterKind.WIDTH_DEVIATION_S,
    }
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.MODULATION
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceModulationCapabilityProfile)
            and SourceModulationKind.PWM in feature.profile.kinds
            and SourceModulationSource.INTERNAL in feature.profile.sources
            and supported_parameter_kinds & set(feature.profile.parameter_kinds)
            and feature.profile.configuration_readable
        )
        for channel in feature.channels
    )


def _channels_with_pulse_width_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.PULSE
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourcePulseCapabilityProfile)
            and SourcePulseHoldBasis.WIDTH in feature.profile.hold_modes
            and feature.profile.delay_readable
            and feature.profile.transitions_readable
            and feature.profile.width_configuration_readable
        )
        for channel in feature.channels
    )


def _channels_with_burst_triggered_internal_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.BURST
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceBurstCapabilityProfile)
            and SourceBurstMode.TRIGGERED in feature.profile.modes
            and SourceTriggerSource.INTERNAL in feature.profile.trigger_sources
            and feature.profile.timing_readable
            and feature.profile.triggered_internal_configuration_readable
        )
        for channel in feature.channels
    )


def _channels_with_sweep_configuration_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    sweep_channels = frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.SWEEP
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceSweepCapabilityProfile)
            and bool(feature.profile.spacing_modes)
            and SourceTriggerSource.INTERNAL in feature.profile.trigger_sources
            and feature.profile.timing_readable
            and feature.profile.marker_readable
            and feature.profile.configuration_readable
        )
        for channel in feature.channels
    )
    basic_sweep_channels = frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.BASIC
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceBasicCapabilityProfile)
            and SourceFrequencyMode.SWEEP in feature.profile.frequency_modes
        )
        for channel in feature.channels
    )
    return sweep_channels & basic_sweep_channels


def _channels_with_arbitrary_storage_mutation_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.ARBITRARY
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceArbitraryCapabilityProfile)
            and feature.profile.selection_readable
            and feature.profile.storage_metadata_readable
            and feature.profile.storage_slot_metadata_readable
            and bool(feature.profile.storage_write_modes)
            and feature.profile.storage_max_payload_bytes is not None
        )
        for channel in feature.channels
    )


def _channels_with_arbitrary_selection_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.ARBITRARY
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceArbitraryCapabilityProfile)
            and feature.profile.selection_readable
            and feature.profile.storage_metadata_readable
            and bool(feature.profile.playback_modes)
            and (
                SourceArbitraryPlaybackMode.TRUE_ARB not in feature.profile.playback_modes
                or feature.profile.sample_rate_readable
            )
        )
        for channel in feature.channels
    )


def _channels_with_arbitrary_basic_readback(
    extensions: SourceDescriptorExtensions,
) -> frozenset[int]:
    return frozenset(
        channel
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.BASIC
            and feature.scope is SourceFacetScope.CHANNEL
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceBasicCapabilityProfile)
            and SourceWaveformKind.ARBITRARY in feature.profile.waveform_kinds
        )
        for channel in feature.channels
    )


def _validate_declared_write_directions(
    extensions: SourceDescriptorExtensions,
    capabilities: frozenset[str],
) -> None:
    capabilities_by_direction = {
        (SourceFeature.BASIC, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.basic_configure_v2"}
        ),
        (SourceFeature.HARMONICS, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.harmonics_configure_v2"}
        ),
        (SourceFeature.MODULATION, SourceFeatureDirection.CONFIGURE): frozenset(
            {
                "source.modulation_configure_v2",
                "source.modulation_pm_configure_v2",
                "source.modulation_fm_configure_v2",
                "source.modulation_pwm_configure_v2",
            }
        ),
        (SourceFeature.PULSE, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.pulse_configure_v2"}
        ),
        (SourceFeature.BURST, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.burst_configure_v2"}
        ),
        (SourceFeature.SWEEP, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.sweep_configure_v2"}
        ),
        (SourceFeature.ARBITRARY, SourceFeatureDirection.CONFIGURE): frozenset(
            {
                "source.arbitrary_storage_v2",
                "source.arbitrary_select_v2",
            }
        ),
        (SourceFeature.OUTPUT, SourceFeatureDirection.ENABLE): frozenset(
            {"source.output_v2"}
        ),
        (SourceFeature.OUTPUT, SourceFeatureDirection.DISABLE): frozenset(
            {"source.output_v2"}
        ),
    }
    for feature in extensions.features:
        for direction in feature.directions:
            if direction is SourceFeatureDirection.READ:
                continue
            required_capabilities = capabilities_by_direction.get((feature.feature, direction))
            if required_capabilities is None or not (required_capabilities & capabilities):
                raise ConfigError(
                    "Source V2 write directions require their matching declared capability"
                )


__all__ = [
    "SOURCE_EXTENSION_CAPABILITY_METHODS",
    "validate_source_descriptor",
    "validate_source_plugin_dependencies",
]
