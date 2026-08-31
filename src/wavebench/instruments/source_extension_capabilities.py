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
    SourceArbitraryWorkspaceCapabilityProfile,
    SourceCouplingCapabilityProfile,
    SourceCounterCapabilityProfile,
    SourceCrossChannelCapabilityProfile,
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
        "source.basic_live_configure_v2": (
            "configure_source_basic_live_v2",
        ),
        "source.harmonics_configure_v2": ("configure_source_harmonics_v2",),
        "source.harmonics_disable_v2": ("disable_source_harmonics_v2",),
        "source.modulation_configure_v2": ("configure_source_modulation_v2",),
        "source.pulse_configure_v2": ("configure_source_pulse_v2",),
        "source.modulation_pm_configure_v2": ("configure_source_pm_modulation_v2",),
        "source.burst_configure_v2": ("configure_source_burst_v2",),
        "source.burst_fire_v2": ("fire_source_burst_v2",),
        "source.modulation_fm_configure_v2": ("configure_source_fm_modulation_v2",),
        "source.modulation_pwm_configure_v2": ("configure_source_pwm_modulation_v2",),
        "source.sweep_configure_v2": ("configure_source_sweep_v2",),
        "source.sweep_fire_v2": ("fire_source_sweep_v2",),
        "source.output_v2": ("set_source_output_v2",),
        "source.arbitrary_storage_v2": (
            "read_source_arbitrary_storage_v2",
            "mutate_source_arbitrary_storage_v2",
        ),
        "source.arbitrary_select_v2": ("select_source_arbitrary_v2",),
        "source.arbitrary_volatile_replace_v2": (
            "replace_source_arbitrary_volatile_v2",
        ),
        "source.arbitrary_workspace_volatile_replace_v2": (
            "replace_source_arbitrary_workspace_volatile_v2",
        ),
        "source.counter_configure_v2": ("configure_source_counter_v2",),
        "source.counter_enable_v2": ("set_source_counter_enabled_v2",),
        "source.counter_measure_v2": ("measure_source_counter_v2",),
        "source.combine_configure_v2": ("configure_source_combine_v2",),
        "source.coupling_configure_v2": ("configure_source_coupling_v2",),
        "source.tracking_configure_v2": ("configure_source_tracking_v2",),
        "source.phase_relation_configure_v2": (
            "configure_source_phase_relation_v2",
        ),
    }
)

_SOURCE_WRITE_CAPABILITIES = frozenset(
    {
        "source.basic_configure_v2",
        "source.basic_live_configure_v2",
        "source.harmonics_configure_v2",
        "source.harmonics_disable_v2",
        "source.modulation_configure_v2",
        "source.pulse_configure_v2",
        "source.modulation_pm_configure_v2",
        "source.burst_configure_v2",
        "source.burst_fire_v2",
        "source.modulation_fm_configure_v2",
        "source.modulation_pwm_configure_v2",
        "source.sweep_configure_v2",
        "source.sweep_fire_v2",
        "source.output_v2",
        "source.arbitrary_storage_v2",
        "source.arbitrary_select_v2",
        "source.arbitrary_volatile_replace_v2",
        "source.arbitrary_workspace_volatile_replace_v2",
        "source.counter_configure_v2",
        "source.counter_enable_v2",
        "source.combine_configure_v2",
        "source.coupling_configure_v2",
        "source.tracking_configure_v2",
        "source.phase_relation_configure_v2",
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
    _validate_counter_capabilities(extensions, frozenset(declared))
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
        unreadable_workspace = (
            feature.feature is SourceFeature.ARBITRARY_WORKSPACE
            and feature.scope is SourceFacetScope.INSTRUMENT
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.CONFIGURE in feature.directions
            and SourceFeatureDirection.READ not in feature.directions
        )
        if feature.support.value == "supported" and (
            SourceFeatureDirection.READ not in feature.directions
        ) and not unreadable_workspace:
            raise ConfigError(
                f"supported Source V2 feature {feature.feature.value!r} must declare read"
            )
        if feature.support.value == "supported" and not unreadable_workspace and not any(
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
        SourceFieldId.NOISE_OVERLAY: frozenset({SourceFeature.NOISE_OVERLAY}),
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


def _validate_counter_capabilities(
    extensions: SourceDescriptorExtensions,
    capabilities: frozenset[str],
) -> None:
    counter_capabilities = {
        "source.counter_configure_v2",
        "source.counter_enable_v2",
        "source.counter_measure_v2",
    }
    if not counter_capabilities & capabilities:
        return
    features = tuple(
        feature
        for feature in extensions.features
        if (
            feature.feature is SourceFeature.COUNTER
            and feature.scope is SourceFacetScope.INPUT
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, SourceCounterCapabilityProfile)
        )
    )
    if len(features) != 1:
        raise ConfigError(
            "Counter V2 capabilities require one readable INPUT counter feature"
        )
    feature = features[0]
    profile = feature.profile
    assert isinstance(profile, SourceCounterCapabilityProfile)
    if not any(
        facet.feature is SourceFeature.COUNTER
        and facet.scope is SourceFacetScope.INPUT
        and facet.fields == (SourceFieldId.COUNTER,)
        for facet in extensions.query_contract.facets
    ):
        raise ConfigError("Counter V2 capabilities require a readable counter query facet")
    if "source.counter_configure_v2" in capabilities:
        if SourceFeatureDirection.CONFIGURE not in feature.directions:
            raise ConfigError(
                "source.counter_configure_v2 requires counter CONFIGURE direction"
            )
        if not profile.configuration_readable or not profile.configurable_fields:
            raise ConfigError(
                "source.counter_configure_v2 requires readable configurable counter fields"
            )
    if "source.counter_enable_v2" in capabilities:
        if not {
            SourceFeatureDirection.ENABLE,
            SourceFeatureDirection.DISABLE,
        } <= set(feature.directions):
            raise ConfigError(
                "source.counter_enable_v2 requires counter ENABLE and DISABLE directions"
            )
        if not profile.enabled_configurable:
            raise ConfigError(
                "source.counter_enable_v2 requires an enabled_configurable counter profile"
            )
    if "source.counter_measure_v2" in capabilities:
        if not profile.measurement_kinds:
            raise ConfigError(
                "source.counter_measure_v2 requires declared counter measurement kinds"
            )
        if profile.query_effect not in {
            SourceQueryEffect.PURE_READ,
            SourceQueryEffect.STATEFUL_CONSUMING_READ,
        }:
            raise ConfigError(
                "source.counter_measure_v2 requires a known read-only counter query effect"
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
    output_disabled = _channels_with_direction(
        extensions,
        SourceFeature.OUTPUT,
        SourceFeatureDirection.DISABLE,
    )

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

    if "source.basic_live_configure_v2" in capabilities:
        required = {"source.basic_configure_v2", "source.output_v2"}
        missing = required - capabilities
        if missing:
            raise ConfigError(
                "source.basic_live_configure_v2 requires source.basic_configure_v2 "
                "and source.output_v2"
            )
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.BASIC,
            SourceFeatureDirection.CONFIGURE,
        )
        live_configurable = frozenset(
            channel
            for feature in extensions.features
            if (
                feature.feature is SourceFeature.BASIC
                and feature.scope is SourceFacetScope.CHANNEL
                and feature.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in feature.directions
                and isinstance(feature.profile, SourceBasicCapabilityProfile)
                and SourceFrequencyMode.FIXED in feature.profile.frequency_modes
                and (
                    feature.profile.live_frequency_configurable
                    or feature.profile.live_amplitude_vpp_configurable
                )
            )
            for channel in feature.channels
        )
        if not configurable or not configurable <= live_configurable:
            raise ConfigError(
                "source.basic_live_configure_v2 requires per-channel fixed-mode live "
                "frequency or Vpp declarations"
            )
        if not configurable <= basic_readable:
            raise ConfigError(
                "source.basic_live_configure_v2 requires readable final VPP and Offset "
                "on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.basic_live_configure_v2 requires readable output state on every channel"
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

    if "source.harmonics_disable_v2" in capabilities:
        disableable = _channels_with_direction(
            extensions,
            SourceFeature.HARMONICS,
            SourceFeatureDirection.DISABLE,
        )
        if not disableable:
            raise ConfigError(
                "source.harmonics_disable_v2 requires harmonics feature DISABLE directions"
            )
        readable = _channels_with_harmonic_enabled_readback(extensions)
        if not disableable <= readable:
            raise ConfigError(
                "source.harmonics_disable_v2 requires readable harmonic state on every channel"
            )
        if not disableable <= output_readable:
            raise ConfigError(
                "source.harmonics_disable_v2 requires readable output state on every channel"
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
        readable = _channels_with_burst_triggered_configuration_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.burst_configure_v2 requires readable internal or manual triggered burst "
                "configuration on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.burst_configure_v2 requires readable output state on every channel"
            )

    if "source.burst_fire_v2" in capabilities:
        required = {"source.burst_configure_v2", "source.output_v2"}
        if not required <= capabilities:
            raise ConfigError(
                "source.burst_fire_v2 requires source.burst_configure_v2 and source.output_v2"
            )
        fireable = _channels_with_direction(
            extensions,
            SourceFeature.BURST,
            SourceFeatureDirection.FIRE,
        )
        readable = _channels_with_burst_triggered_configuration_readback(
            extensions,
            trigger_source=SourceTriggerSource.MANUAL,
        )
        if not fireable or not fireable <= readable:
            raise ConfigError(
                "source.burst_fire_v2 requires readable manual triggered burst "
                "configuration on every FIRE channel"
            )
        if not fireable <= basic_readable or not fireable <= output_readable:
            raise ConfigError(
                "source.burst_fire_v2 requires readable final VPP, Offset and output state"
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
                "source.sweep_configure_v2 requires readable internal or manual sweep configuration "
                "and sweep frequency mode on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.sweep_configure_v2 requires readable output state on every channel"
            )
        _validate_sweep_implicit_disable_features(extensions, configurable)

    if "source.sweep_fire_v2" in capabilities:
        required = {"source.sweep_configure_v2", "source.output_v2"}
        if not required <= capabilities:
            raise ConfigError(
                "source.sweep_fire_v2 requires source.sweep_configure_v2 and source.output_v2"
            )
        fireable = _channels_with_direction(
            extensions,
            SourceFeature.SWEEP,
            SourceFeatureDirection.FIRE,
        )
        readable = _channels_with_sweep_configuration_readback(
            extensions,
            trigger_source=SourceTriggerSource.MANUAL,
        )
        if not fireable or not fireable <= readable:
            raise ConfigError(
                "source.sweep_fire_v2 requires readable manual sweep configuration "
                "on every FIRE channel"
            )
        if not fireable <= basic_readable or not fireable <= output_readable:
            raise ConfigError(
                "source.sweep_fire_v2 requires readable final VPP, Offset and output state"
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

    if "source.arbitrary_volatile_replace_v2" in capabilities:
        if "source.output_v2" not in capabilities:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 requires source.output_v2"
            )
        configurable = _channels_with_direction(
            extensions,
            SourceFeature.ARBITRARY,
            SourceFeatureDirection.CONFIGURE,
        )
        if not configurable:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 requires arbitrary feature CONFIGURE directions"
            )
        readable = _channels_with_arbitrary_volatile_replace_readback(extensions)
        if not configurable <= readable:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 requires readable selected state and "
                "volatile replace limits on every channel"
            )
        arbitrary_basic = _channels_with_arbitrary_basic_readback(extensions)
        if not configurable <= arbitrary_basic:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 requires readable arbitrary basic waveform "
                "state on every channel"
            )
        if not configurable <= output_readable:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 requires readable output state on every "
                "channel"
            )

    if "source.arbitrary_workspace_volatile_replace_v2" in capabilities:
        if "source.output_v2" not in capabilities:
            raise ConfigError(
                "source.arbitrary_workspace_volatile_replace_v2 requires source.output_v2"
            )
        profiles = tuple(
            feature.profile
            for feature in extensions.features
            if (
                feature.feature is SourceFeature.ARBITRARY_WORKSPACE
                and feature.scope is SourceFacetScope.INSTRUMENT
                and feature.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in feature.directions
                and isinstance(feature.profile, SourceArbitraryWorkspaceCapabilityProfile)
            )
        )
        if len(profiles) != 1:
            raise ConfigError(
                "source.arbitrary_workspace_volatile_replace_v2 requires one configured "
                "instrument arbitrary workspace profile"
            )
        if len(extensions.topology.channels) > 8:
            raise ConfigError(
                "source.arbitrary_workspace_volatile_replace_v2 supports at most eight "
                "protected output channels"
            )
        if not set(extensions.topology.channels) <= output_readable:
            raise ConfigError(
                "source.arbitrary_workspace_volatile_replace_v2 requires readable output "
                "state on every topology channel"
            )
        if not set(extensions.topology.channels) <= output_disabled:
            raise ConfigError(
                "source.arbitrary_workspace_volatile_replace_v2 requires output DISABLE "
                "support on every topology channel"
            )

    _validate_cross_channel_write_capability(
        extensions,
        capabilities,
        capability="source.combine_configure_v2",
        feature_kind=SourceFeature.COMBINE,
    )
    _validate_cross_channel_write_capability(
        extensions,
        capabilities,
        capability="source.coupling_configure_v2",
        feature_kind=SourceFeature.COUPLING,
    )
    _validate_cross_channel_write_capability(
        extensions,
        capabilities,
        capability="source.tracking_configure_v2",
        feature_kind=SourceFeature.TRACKING,
    )
    _validate_cross_channel_write_capability(
        extensions,
        capabilities,
        capability="source.phase_relation_configure_v2",
        feature_kind=SourceFeature.PHASE_RELATION,
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
        if not disabled:
            raise ConfigError(
                "source.output_v2 requires output DISABLE directions"
            )
        if not enabled <= disabled:
            raise ConfigError(
                "source.output_v2 requires every output ENABLE direction to have matching "
                "DISABLE support"
            )
        if not disabled <= output_readable:
            raise ConfigError(
                "source.output_v2 requires readable output state on every channel"
            )


def _validate_cross_channel_write_capability(
    extensions: SourceDescriptorExtensions,
    capabilities: frozenset[str],
    *,
    capability: str,
    feature_kind: SourceFeature,
) -> None:
    if capability not in capabilities:
        return
    profile_type = (
        SourceCouplingCapabilityProfile
        if feature_kind is SourceFeature.COUPLING
        else SourceCrossChannelCapabilityProfile
    )
    configurable = tuple(
        feature
        for feature in extensions.features
        if (
            feature.feature is feature_kind
            and feature.scope is SourceFacetScope.CHANNEL_SET
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.CONFIGURE in feature.directions
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, profile_type)
        )
    )
    if not configurable:
        raise ConfigError(
            f"{capability} requires {feature_kind.value} CHANNEL_SET read/configure directions"
        )
    for feature in configurable:
        profile = feature.profile
        if isinstance(profile, SourceCouplingCapabilityProfile):
            readable = (
                feature.channels in profile.supported_channel_sets
                and profile.global_state_readable
                and profile.configuration_readable
            )
        else:
            readable = (
                feature_kind in profile.relation_kinds
                and feature.channels in profile.supported_channel_sets
                and profile.configuration_readable
            )
        if not readable:
            raise ConfigError(
                f"{capability} requires readable declared {feature_kind.value} relation state"
            )
    graph_features = tuple(
        feature
        for feature in extensions.features
        if (
            feature.feature is feature_kind
            and feature.scope is SourceFacetScope.INSTRUMENT
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in feature.directions
            and isinstance(feature.profile, profile_type)
            and feature.profile.relation_graph_readable
        )
    )
    graph_facets = tuple(
        facet
        for facet in extensions.query_contract.facets
        if (
            facet.feature is feature_kind
            and facet.scope is SourceFacetScope.INSTRUMENT
            and SourceFieldId.RELATION_GRAPH in facet.fields
        )
    )
    if not graph_features or len(graph_facets) != 1:
        raise ConfigError(
            f"{capability} requires one readable instrument relation graph"
        )
    output_readable = _channels_with_output_readback(extensions)
    if any(not set(feature.channels) <= output_readable for feature in configurable):
        raise ConfigError(
            f"{capability} requires readable output state on every declared relation channel"
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


def _channels_with_harmonic_enabled_readback(
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


def _channels_with_burst_triggered_configuration_readback(
    extensions: SourceDescriptorExtensions,
    *,
    trigger_source: SourceTriggerSource | None = None,
) -> frozenset[int]:
    allowed_sources = (
        {SourceTriggerSource.INTERNAL, SourceTriggerSource.MANUAL}
        if trigger_source is None
        else {trigger_source}
    )
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
            and feature.profile.timing_readable
            and (
                (
                    SourceTriggerSource.INTERNAL in allowed_sources
                    and SourceTriggerSource.INTERNAL in feature.profile.trigger_sources
                    and feature.profile.triggered_internal_configuration_readable
                )
                or (
                    SourceTriggerSource.MANUAL in allowed_sources
                    and SourceTriggerSource.MANUAL in feature.profile.trigger_sources
                    and feature.profile.triggered_manual_configuration_readable
                )
            )
        )
        for channel in feature.channels
    )


def _channels_with_sweep_configuration_readback(
    extensions: SourceDescriptorExtensions,
    *,
    trigger_source: SourceTriggerSource | None = None,
) -> frozenset[int]:
    allowed_sources = (
        {SourceTriggerSource.INTERNAL, SourceTriggerSource.MANUAL}
        if trigger_source is None
        else {trigger_source}
    )
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
            and any(
                source in feature.profile.trigger_sources
                for source in allowed_sources
            )
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


def _validate_sweep_implicit_disable_features(
    extensions: SourceDescriptorExtensions,
    configurable: frozenset[int],
) -> None:
    readable_channels = {
        feature: _channels_with_inactive_feature_readback(extensions, feature)
        for feature in (SourceFeature.BURST, SourceFeature.MODULATION)
    }
    for sweep in extensions.features:
        if (
            sweep.feature is not SourceFeature.SWEEP
            or sweep.scope is not SourceFacetScope.CHANNEL
            or sweep.support is not SupportState.SUPPORTED
            or SourceFeatureDirection.CONFIGURE not in sweep.directions
            or not isinstance(sweep.profile, SourceSweepCapabilityProfile)
        ):
            continue
        for channel in set(sweep.channels) & configurable:
            for feature in sweep.profile.implicit_disable_features:
                if channel not in readable_channels[feature]:
                    raise ConfigError(
                        "source.sweep_configure_v2 requires readable inactive "
                        f"{feature.value} state on every configured channel"
                    )


def _channels_with_inactive_feature_readback(
    extensions: SourceDescriptorExtensions,
    feature: SourceFeature,
) -> frozenset[int]:
    field = {
        SourceFeature.BURST: SourceFieldId.BURST,
        SourceFeature.MODULATION: SourceFieldId.MODULATION,
    }.get(feature)
    if field is None:
        raise ValueError("sweep implicit disable feature is unsupported")
    has_required_unconditional_query = any(
        facet.feature is feature
        and facet.scope is SourceFacetScope.CHANNEL
        and facet.fields == (field,)
        and not facet.activation_any
        and facet.required
        for facet in extensions.query_contract.facets
    )
    if not has_required_unconditional_query:
        return frozenset()
    return frozenset(
        channel
        for candidate in extensions.features
        if (
            candidate.feature is feature
            and candidate.scope is SourceFacetScope.CHANNEL
            and candidate.support is SupportState.SUPPORTED
            and SourceFeatureDirection.READ in candidate.directions
            and (
                isinstance(candidate.profile, SourceBurstCapabilityProfile)
                and candidate.profile.inactive_readable
                if feature is SourceFeature.BURST
                else isinstance(candidate.profile, SourceModulationCapabilityProfile)
                and candidate.profile.inactive_readable
            )
        )
        for channel in candidate.channels
    )


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


def _channels_with_arbitrary_volatile_replace_readback(
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
            and feature.profile.volatile_replace_min_points is not None
            and feature.profile.volatile_replace_max_points is not None
            and feature.profile.volatile_replace_max_payload_bytes is not None
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
            {
                "source.basic_configure_v2",
                "source.basic_live_configure_v2",
            }
        ),
        (SourceFeature.HARMONICS, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.harmonics_configure_v2"}
        ),
        (SourceFeature.HARMONICS, SourceFeatureDirection.DISABLE): frozenset(
            {"source.harmonics_disable_v2"}
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
        (SourceFeature.BURST, SourceFeatureDirection.FIRE): frozenset(
            {"source.burst_fire_v2"}
        ),
        (SourceFeature.SWEEP, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.sweep_configure_v2"}
        ),
        (SourceFeature.SWEEP, SourceFeatureDirection.FIRE): frozenset(
            {"source.sweep_fire_v2"}
        ),
        (SourceFeature.ARBITRARY, SourceFeatureDirection.CONFIGURE): frozenset(
            {
                "source.arbitrary_storage_v2",
                "source.arbitrary_select_v2",
                "source.arbitrary_volatile_replace_v2",
            }
        ),
        (SourceFeature.ARBITRARY_WORKSPACE, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.arbitrary_workspace_volatile_replace_v2"}
        ),
        (SourceFeature.COUNTER, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.counter_configure_v2"}
        ),
        (SourceFeature.COUNTER, SourceFeatureDirection.ENABLE): frozenset(
            {"source.counter_enable_v2"}
        ),
        (SourceFeature.COUNTER, SourceFeatureDirection.DISABLE): frozenset(
            {"source.counter_enable_v2"}
        ),
        (SourceFeature.COMBINE, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.combine_configure_v2"}
        ),
        (SourceFeature.COUPLING, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.coupling_configure_v2"}
        ),
        (SourceFeature.TRACKING, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.tracking_configure_v2"}
        ),
        (SourceFeature.PHASE_RELATION, SourceFeatureDirection.CONFIGURE): frozenset(
            {"source.phase_relation_configure_v2"}
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
