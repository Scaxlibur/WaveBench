from __future__ import annotations

from dataclasses import replace

from wavebench.instruments import InstrumentDescriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Availability,
    BasicWaveFacet,
    NoiseOverlayFacet,
    Observed,
    OutputFacet,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceBasicCapabilityProfile,
    SourceConstraintApplicability,
    SourceDescriptorExtensions,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceFrequencyMode,
    SourceOutputCapabilityProfile,
    SourceOutputPolarity,
    SourceActivationPredicate,
    SourceActivationRule,
    SourceAnchorField,
    SourceHarmonicCapabilityProfile,
    ComponentAmplitudeKind,
    HarmonicCompleteness,
    SourceProtocolQueryRecord,
    SourceQueryContract,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceSafetyProfile,
    SourceTopologyContract,
    SourceSyncState,
    SourceTypedObservation,
    SourceWaveformKind,
    SupportState,
)


def missing() -> Observed[object]:
    return Observed.missing(Availability.NOT_QUERIED, SourceReasonCode.NOT_REQUESTED)


def basic_facet(
    *,
    frequency_hz: float = 1000.0,
    waveform_unknown: bool = False,
) -> BasicWaveFacet:
    return BasicWaveFacet(
        waveform_kind=(missing() if waveform_unknown else Observed.value_of(SourceWaveformKind.SINE)),
        waveform_id=Observed.value_of("sine"),
        frequency_mode=Observed.value_of(SourceFrequencyMode.FIXED),
        frequency_hz=Observed.value_of(frequency_hz),
        amplitude=Observed.value_of(SourceAmplitude(1.0, SourceAmplitudeUnit.VPP)),
        offset_v=Observed.value_of(0.0),
        phase_deg=Observed.value_of(0.0),
        square_duty_cycle_percent=missing(),
    )


def output_facet(*, enabled: bool = False) -> OutputFacet:
    return OutputFacet(
        enabled=Observed.value_of(enabled),
        display_load=missing(),
        polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
    )


def source_extensions() -> SourceDescriptorExtensions:
    applicability = SourceConstraintApplicability()
    features = (
        SourceFeatureCapability(
            feature=SourceFeature.BASIC,
            support=SupportState.SUPPORTED,
            directions=(SourceFeatureDirection.READ,),
            scope=SourceFacetScope.CHANNEL,
            channels=(1,),
            applicability=applicability,
            profile=SourceBasicCapabilityProfile(
                waveform_kinds=(SourceWaveformKind.SINE,),
                frequency_modes=(SourceFrequencyMode.FIXED,),
                amplitude_units=(SourceAmplitudeUnit.VPP,),
                offset_readable=True,
                phase_readable=True,
                square_duty_readable=False,
            ),
            evidence_refs=("evidence.basic",),
        ),
        SourceFeatureCapability(
            feature=SourceFeature.OUTPUT,
            support=SupportState.SUPPORTED,
            directions=(SourceFeatureDirection.READ,),
            scope=SourceFacetScope.CHANNEL,
            channels=(1,),
            applicability=applicability,
            profile=SourceOutputCapabilityProfile(
                output_readable=True,
                display_load_readable=False,
                polarity_readable=True,
            ),
            evidence_refs=("evidence.output",),
        ),
    )
    query_contract = SourceQueryContract(
        anchor_fields=(
            SourceFieldId.BASIC,
            SourceFieldId.OUTPUT,
            SourceFieldId.IDENTITY,
        ),
        facets=(
            SourceFacetQueryContract(
                feature=SourceFeature.BASIC,
                scope=SourceFacetScope.CHANNEL,
                fields=(SourceFieldId.BASIC,),
                activation_any=(),
                effect=SourceQueryEffect.PURE_READ,
                max_queries=1,
                required=True,
            ),
            SourceFacetQueryContract(
                feature=SourceFeature.BASIC,
                scope=SourceFacetScope.INSTRUMENT,
                fields=(SourceFieldId.IDENTITY,),
                activation_any=(),
                effect=SourceQueryEffect.PURE_READ,
                max_queries=1,
                required=True,
            ),
            SourceFacetQueryContract(
                feature=SourceFeature.OUTPUT,
                scope=SourceFacetScope.CHANNEL,
                fields=(SourceFieldId.OUTPUT,),
                activation_any=(),
                effect=SourceQueryEffect.PURE_READ,
                max_queries=1,
                required=True,
            ),
        ),
        max_queries=6,
        timeout_ms=2000,
    )
    return SourceDescriptorExtensions(
        contract_version=SOURCE_CONTRACT_VERSION,
        topology=SourceTopologyContract((1,)),
        features=features,
        query_contract=query_contract,
        safety_profile=SourceSafetyProfile(),
    )


def source_extensions_with_harmonics() -> SourceDescriptorExtensions:
    base = source_extensions()
    harmonic = SourceFeatureCapability(
        feature=SourceFeature.HARMONICS,
        support=SupportState.SUPPORTED,
        directions=(SourceFeatureDirection.READ,),
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourceHarmonicCapabilityProfile(
            minimum_order=2,
            maximum_order=16,
            amplitude_kinds=(ComponentAmplitudeKind.ABSOLUTE_VPP,),
            completeness_modes=(HarmonicCompleteness.PARTIAL,),
        ),
    )
    harmonic_query = SourceFacetQueryContract(
        feature=SourceFeature.HARMONICS,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.HARMONICS,),
        activation_any=(
            SourceActivationRule(
                (
                    SourceActivationPredicate(
                        SourceAnchorField.WAVEFORM_KIND,
                        SourceWaveformKind.SQUARE,
                    ),
                )
            ),
        ),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=False,
    )
    return replace(
        base,
        features=(base.features[0], harmonic, base.features[1]),
        query_contract=replace(
            base.query_contract,
            facets=(
                base.query_contract.facets[0],
                base.query_contract.facets[1],
                harmonic_query,
                base.query_contract.facets[2],
            ),
            max_queries=7,
        ),
    )


def source_descriptor(
    *,
    driver: object | None = None,
    extensions: SourceDescriptorExtensions | None = None,
) -> InstrumentDescriptor:
    if extensions is None:
        extensions = source_extensions()
    return InstrumentDescriptor(
        driver_id="example.source-v2",
        kind="source",
        display_name="Example Source V2",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=("source.snapshot_v2",),
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: driver if driver is not None else object(),
        wavebench_min_version="0.8.24",
        wavebench_max_version="0.9.0",
        source_extensions=extensions,
    )


class SourceV2FakeDriver:
    def __init__(
        self,
        *,
        combined: bool,
        drift: bool = False,
        harmonic_unavailable: bool = False,
        anchor_unknown: bool = False,
        sync_state: SourceSyncState | None = None,
        noise_overlay: NoiseOverlayFacet | None = None,
    ) -> None:
        self.combined = combined
        self.drift = drift
        self.harmonic_unavailable = harmonic_unavailable
        self.anchor_unknown = anchor_unknown
        self.sync_state = sync_state
        self.noise_overlay = noise_overlay
        self.plans = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def execute_source_query_plan_v2(self, plan):
        self.plans.append(plan)
        records = []
        for index, item in enumerate(plan.items):
            if item.feature is SourceFeature.HARMONICS:
                records.append(
                    SourceProtocolQueryRecord(
                        item_id=item.item_id,
                        effect=item.effect,
                        outcome=(
                            SourceQueryItemOutcome.SEMANTIC_UNAVAILABLE
                            if self.harmonic_unavailable
                            else SourceQueryItemOutcome.SKIPPED
                        ),
                        query_count=(1 if self.harmonic_unavailable else 0),
                        reason_code=(
                            SourceReasonCode.RESPONSE_MISSING_FIELD
                            if self.harmonic_unavailable
                            else SourceReasonCode.INACTIVE_BY_ANCHOR
                        ),
                    )
                )
                continue
            observations = []
            for field in item.fields:
                if field.field is SourceFieldId.IDENTITY:
                    value = SourceRuntimeIdentity(
                        manufacturer="Example",
                        model="EX1",
                        firmware_id="1.0",
                    )
                elif field.field is SourceFieldId.BASIC:
                    value = basic_facet(waveform_unknown=self.anchor_unknown)
                elif field.field is SourceFieldId.OUTPUT:
                    value = output_facet(
                        enabled=(self.drift and item.phase.value == "anchor_after")
                    )
                elif field.field is SourceFieldId.SYNC:
                    if self.sync_state is None:
                        raise AssertionError("sync state was not configured")
                    value = self.sync_state
                elif field.field is SourceFieldId.NOISE_OVERLAY:
                    if self.noise_overlay is None:
                        raise AssertionError("noise overlay was not configured")
                    value = self.noise_overlay
                else:
                    raise AssertionError(field)
                observations.append(SourceTypedObservation(field, value))
            records.append(
                SourceProtocolQueryRecord(
                    item_id=item.item_id,
                    effect=item.effect,
                    outcome=SourceQueryItemOutcome.OBSERVED,
                    query_count=(1 if not self.combined or index == 0 else 0),
                    observations=tuple(observations),
                )
            )
        query_count = 1 if self.combined else len(records)
        return SourceQueryExecutionRecord(
            contract_version=SOURCE_CONTRACT_VERSION,
            plan_id=plan.plan_id,
            items=tuple(records),
            query_count=query_count,
            device_revision_token_before="revision-1",
            device_revision_token_after="revision-1",
        )


def with_min_version(
    descriptor: InstrumentDescriptor,
    minimum: str,
) -> InstrumentDescriptor:
    return replace(descriptor, wavebench_min_version=minimum)
