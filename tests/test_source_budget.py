from __future__ import annotations

from dataclasses import replace

import pytest

from wavebench.instruments.source_extensions import (
    ArbitraryFacet,
    Availability,
    BasicWaveFacet,
    BudgetProofStrength,
    ClosedFloatInterval,
    ComponentAmplitudeKind,
    HarmonicCompleteness,
    HarmonicFacet,
    ModulationFacet,
    Observed,
    OutputFacet,
    ResistanceBounds,
    SnapshotConsistencyState,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceArbitraryOvershootConstraint,
    SourceArbitraryPlaybackMode,
    SourceChannelStateV2,
    SourceComponentAmplitude,
    SourceConstraintApplicability,
    SourceCrossChannelCapabilityProfile,
    SourceCrossChannelStateV2,
    SourceDescriptorExtensions,
    SourceDisplayLoad,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFacetScope,
    SourceFieldId,
    SourceFieldRef,
    SourceFrequencyDeratingBand,
    SourceFrequencyDeratingConstraint,
    SourceFrequencyMode,
    SourceLoadKind,
    SourceModulationEnvelopeConstraint,
    SourceModulationKind,
    SourceModulationSource,
    SourceOutputPolarity,
    SourceNoisePeakConstraint,
    SourceReasonCode,
    SourceRelationEdge,
    SourceRelationGraph,
    SourceRelationState,
    SourceRuntimeCapabilityProfile,
    SourceRuntimeIdentity,
    SourceResistanceConstraint,
    SourceSafetyConstraint,
    SourceSafetyConstraintKind,
    SourceSafetyProfile,
    SourceScopeRef,
    SourceSharedPowerConstraint,
    SourceSharedPowerState,
    SourceSignalPathKind,
    SourceSnapshotConsistency,
    SourceSnapshotV2,
    SourceTerminationEvidence,
    SourceTopologyContract,
    SourceVoltageReferenceConstraint,
    SourceWaveformKind,
    SupportState,
    SweepFacet,
    TerminationEvidenceLifetime,
    TerminationEvidenceSource,
    TerminationKind,
    TerminationSpec,
    VoltageReferenceBasis,
    SourceHarmonicComponentV2,
    source_v2_digest,
)
from wavebench.services.source_budget import (
    SourceOutputBudgetRequest,
    evaluate_source_output_budget,
)
from wavebench.services.source_safety import (
    SourceEnergySafetyLimits,
    SourceTerminationEvidenceContext,
    source_termination_binding_digest,
)

from tests.source_v2_fixtures import source_extensions


def _missing(
    availability: Availability = Availability.UNSUPPORTED,
) -> Observed[object]:
    reason = (
        SourceReasonCode.DESCRIPTOR_UNSUPPORTED
        if availability is Availability.UNSUPPORTED
        else SourceReasonCode.NOT_REQUESTED
    )
    return Observed.missing(availability, reason)


def _basic(
    *,
    waveform: SourceWaveformKind = SourceWaveformKind.SINE,
    amplitude_vpp: float | None = 2.0,
    offset_v: float = 0.0,
    frequency_mode: SourceFrequencyMode | None = SourceFrequencyMode.FIXED,
    frequency_hz: float | None = 1_000.0,
) -> BasicWaveFacet:
    return BasicWaveFacet(
        waveform_kind=Observed.value_of(waveform),
        waveform_id=Observed.value_of(waveform.value),
        frequency_mode=(
            Observed.value_of(frequency_mode)
            if frequency_mode is not None
            else _missing(Availability.NOT_QUERIED)
        ),
        frequency_hz=(
            Observed.value_of(frequency_hz)
            if frequency_hz is not None
            else _missing(Availability.NOT_QUERIED)
        ),
        amplitude=(
            Observed.value_of(SourceAmplitude(amplitude_vpp, SourceAmplitudeUnit.VPP))
            if amplitude_vpp is not None
            else _missing(Availability.NOT_QUERIED)
        ),
        offset_v=Observed.value_of(offset_v),
        phase_deg=_missing(Availability.NOT_QUERIED),
        square_duty_cycle_percent=_missing(Availability.NOT_APPLICABLE),
    )


def _channel(
    channel: int,
    *,
    waveform: SourceWaveformKind = SourceWaveformKind.SINE,
    amplitude_vpp: float | None = 2.0,
    offset_v: float = 0.0,
    frequency_mode: SourceFrequencyMode | None = SourceFrequencyMode.FIXED,
    frequency_hz: float | None = 1_000.0,
    display_load: SourceDisplayLoad | None = None,
    polarity: SourceOutputPolarity | None = SourceOutputPolarity.NORMAL,
    enabled: bool = False,
    harmonics: Observed[HarmonicFacet] | None = None,
    modulation: Observed[ModulationFacet] | None = None,
    sweep: Observed[SweepFacet] | None = None,
    arbitrary: Observed[ArbitraryFacet] | None = None,
) -> SourceChannelStateV2:
    return SourceChannelStateV2(
        channel=channel,
        basic=Observed.value_of(
            _basic(
                waveform=waveform,
                amplitude_vpp=amplitude_vpp,
                offset_v=offset_v,
                frequency_mode=frequency_mode,
                frequency_hz=frequency_hz,
            )
        ),
        output=Observed.value_of(
            OutputFacet(
                enabled=Observed.value_of(enabled),
                display_load=(
                    Observed.value_of(display_load)
                    if display_load is not None
                    else Observed.value_of(SourceDisplayLoad(SourceLoadKind.RESISTIVE, 50.0))
                ),
                polarity=(
                    Observed.value_of(polarity)
                    if polarity is not None
                    else _missing(Availability.UNKNOWN)
                ),
            )
        ),
        harmonics=harmonics or _missing(),
        modulation=modulation or _missing(),
        sweep=sweep or _missing(),
        burst=_missing(),
        pulse=_missing(),
        arbitrary=arbitrary or _missing(),
        sync=_missing(),
    )


def _constraint(
    constraint_id: str,
    kind: SourceSafetyConstraintKind,
    profile: object,
    *,
    proof: BudgetProofStrength = BudgetProofStrength.HARD_CONSERVATIVE,
) -> SourceSafetyConstraint:
    return SourceSafetyConstraint(
        constraint_id=constraint_id,
        kind=kind,
        applicability=SourceConstraintApplicability(),
        profile=profile,  # type: ignore[arg-type]
        proof_strength=proof,
        evidence_refs=(f"evidence.{constraint_id}",),
    )


def _constraints(
    *extra: SourceSafetyConstraint,
    basis: VoltageReferenceBasis = VoltageReferenceBasis.OPEN_CIRCUIT,
) -> SourceSafetyProfile:
    values = (
        _constraint(
            "safety.reference",
            SourceSafetyConstraintKind.VOLTAGE_REFERENCE,
            SourceVoltageReferenceConstraint(basis),
        ),
        _constraint(
            "safety.resistance",
            SourceSafetyConstraintKind.SOURCE_RESISTANCE,
            SourceResistanceConstraint(ResistanceBounds(50.0, 50.0)),
        ),
        *extra,
    )
    return SourceSafetyProfile(tuple(sorted(values, key=lambda item: item.constraint_id)))


def _extensions(
    *,
    safety_profile: SourceSafetyProfile,
    topology: SourceTopologyContract | None = None,
    features: tuple[SourceFeatureCapability, ...] | None = None,
) -> SourceDescriptorExtensions:
    base = source_extensions()
    return replace(
        base,
        topology=topology or base.topology,
        features=features or base.features,
        safety_profile=safety_profile,
    )


def _snapshot(
    extensions: SourceDescriptorExtensions,
    channels: tuple[SourceChannelStateV2, ...],
    *,
    correlation_id: str = "budget-correlation",
    cross_channel: Observed[SourceCrossChannelStateV2] | None = None,
    consistency: SnapshotConsistencyState = SnapshotConsistencyState.CONSISTENT,
) -> SourceSnapshotV2:
    identity = SourceRuntimeIdentity("Example", "EX1", "1.0")
    anchor = SourceFieldRef(
        SourceFieldId.IDENTITY,
        SourceScopeRef(SourceFacetScope.INSTRUMENT),
    )
    digest = source_v2_digest("budget-anchor")
    return SourceSnapshotV2(
        snapshot_id="budget-snapshot",
        context_id="budget-context",
        correlation_id=correlation_id,
        captured_at_utc="2026-08-22T00:00:00.000Z",
        runtime_profile=SourceRuntimeCapabilityProfile(
            session_epoch="budget-epoch",
            descriptor_digest=source_v2_digest(extensions),
            identity=identity,
            features=extensions.features,
        ),
        channels=channels,
        system=_missing(Availability.NOT_APPLICABLE),
        cross_channel=cross_channel or _missing(Availability.NOT_APPLICABLE),
        consistency=SourceSnapshotConsistency(
            state=consistency,
            session_epoch="budget-epoch",
            anchor_fields=(anchor,),
            anchor_digest_before=digest,
            anchor_digest_after=digest if consistency is SnapshotConsistencyState.CONSISTENT else None,
            device_revision_token_before=None,
            device_revision_token_after=None,
            reason_code=(
                None
                if consistency is SnapshotConsistencyState.CONSISTENT
                else SourceReasonCode.CONSISTENCY_UNPROVEN
            ),
        ),
        plan_digest=source_v2_digest("budget-plan"),
        query_count=1,
        session_health_before="healthy",
        session_health_after="healthy",
    )


def _termination(
    snapshot: SourceSnapshotV2,
    channel: int,
    *,
    bounds: ResistanceBounds | None = ResistanceBounds(50.0, 50.0),
) -> tuple[SourceTerminationEvidence, SourceTerminationEvidenceContext]:
    target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
    context = SourceTerminationEvidenceContext(
        target=target,
        resource_fingerprint="sha256:" + "0" * 64,
        config_digest="sha256:" + "1" * 64,
        correlation_id=snapshot.correlation_id,
        observed_at_utc="2026-08-22T00:00:00.000Z",
    )
    termination = (
        TerminationSpec(TerminationKind.HIGH_IMPEDANCE)
        if bounds is None
        else TerminationSpec(TerminationKind.RESISTIVE, bounds)
    )
    evidence = SourceTerminationEvidence(
        target=target,
        termination=termination,
        source=TerminationEvidenceSource.CONFIG,
        lifetime=TerminationEvidenceLifetime.CONFIG_DIGEST,
        resource_fingerprint=context.resource_fingerprint,
        binding_digest=source_termination_binding_digest(
            context,
            source=TerminationEvidenceSource.CONFIG,
            lifetime=TerminationEvidenceLifetime.CONFIG_DIGEST,
        ),
        observed_at_utc=context.observed_at_utc,
        expires_at_utc=None,
        evidence_ref=f"test.termination.{channel}",
    )
    return evidence, context


def _request(
    snapshot: SourceSnapshotV2,
    extensions: SourceDescriptorExtensions,
    *,
    target_channel: int = 1,
    terminations: tuple[tuple[SourceTerminationEvidence, SourceTerminationEvidenceContext], ...] = (),
    limits: SourceEnergySafetyLimits | None = None,
    projected_active_channels: tuple[int, ...] = (),
) -> SourceOutputBudgetRequest:
    return SourceOutputBudgetRequest(
        snapshot=snapshot,
        descriptor_extensions=extensions,
        limits=limits or SourceEnergySafetyLimits(3.0, -2.0, 2.0),
        target_channel=target_channel,
        termination_evidence=tuple(item[0] for item in terminations),
        termination_contexts=tuple(item[1] for item in terminations),
        projected_active_channels=projected_active_channels,
    )


def test_basic_open_circuit_budget_is_pure_and_authorizable() -> None:
    extensions = _extensions(safety_profile=_constraints())
    snapshot = _snapshot(extensions, (_channel(1),))
    budget = evaluate_source_output_budget(
        _request(snapshot, extensions, terminations=(_termination(snapshot, 1),))
    )

    assert budget.can_authorize_energy
    assert budget.bounds.value.minimum_v_lower == pytest.approx(-0.5)
    assert budget.bounds.value.maximum_v_upper == pytest.approx(0.5)
    assert budget.bounds.value.vpp_upper_v == pytest.approx(1.0)
    assert budget.shared_power.availability is Availability.NOT_APPLICABLE


def test_missing_or_non_resistive_termination_fails_closed() -> None:
    extensions = _extensions(safety_profile=_constraints())
    snapshot = _snapshot(extensions, (_channel(1),))

    missing = evaluate_source_output_budget(_request(snapshot, extensions))
    high_impedance = evaluate_source_output_budget(
        _request(
            snapshot,
            extensions,
            terminations=(_termination(snapshot, 1, bounds=None),),
        )
    )

    assert not missing.can_authorize_energy
    assert {item.value for item in missing.blockers} >= {"actual_termination_missing"}
    assert not high_impedance.can_authorize_energy
    assert {item.value for item in high_impedance.blockers} >= {"termination_not_resistive"}


def test_dc_and_pulse_take_distinct_conservative_paths() -> None:
    extensions = _extensions(safety_profile=_constraints())
    dc = _snapshot(
        extensions,
        (
            _channel(
                1,
                waveform=SourceWaveformKind.DC,
                amplitude_vpp=None,
                offset_v=2.0,
                frequency_mode=None,
                frequency_hz=None,
            ),
        ),
    )
    dc_budget = evaluate_source_output_budget(
        _request(
            dc,
            extensions,
            terminations=(_termination(dc, 1),),
            limits=SourceEnergySafetyLimits(1.0, 0.9, 1.1),
        )
    )
    pulse = _snapshot(
        extensions,
        (_channel(1, waveform=SourceWaveformKind.PULSE),),
    )
    pulse_budget = evaluate_source_output_budget(
        _request(pulse, extensions, terminations=(_termination(pulse, 1),))
    )
    ambiguous_dc = _snapshot(
        extensions,
        (_channel(1, waveform=SourceWaveformKind.DC, amplitude_vpp=2.0),),
    )
    ambiguous_dc_budget = evaluate_source_output_budget(
        _request(
            ambiguous_dc,
            extensions,
            terminations=(_termination(ambiguous_dc, 1),),
        )
    )

    assert dc_budget.can_authorize_energy
    assert dc_budget.bounds.value.minimum_v_lower == pytest.approx(1.0)
    assert "waveform_unsupported" in {item.value for item in pulse_budget.blockers}
    assert not pulse_budget.can_authorize_energy
    assert "dc_level_unavailable" in {item.value for item in ambiguous_dc_budget.blockers}
    assert not ambiguous_dc_budget.can_authorize_energy


def test_polarity_and_unknown_frequency_mode_cannot_bypass_absolute_limits() -> None:
    extensions = _extensions(safety_profile=_constraints())
    inverted = _snapshot(
        extensions,
        (_channel(1, offset_v=1.0, polarity=SourceOutputPolarity.INVERTED),),
    )
    inverted_budget = evaluate_source_output_budget(
        _request(inverted, extensions, terminations=(_termination(inverted, 1),))
    )
    unknown_polarity = _snapshot(extensions, (_channel(1, polarity=None),))
    polarity_budget = evaluate_source_output_budget(
        _request(unknown_polarity, extensions, terminations=(_termination(unknown_polarity, 1),))
    )
    unknown_frequency = _snapshot(
        extensions,
        (_channel(1, frequency_mode=None),),
    )
    frequency_budget = evaluate_source_output_budget(
        _request(unknown_frequency, extensions, terminations=(_termination(unknown_frequency, 1),))
    )

    assert inverted_budget.can_authorize_energy
    assert inverted_budget.bounds.value.minimum_v_lower == pytest.approx(-1.0)
    assert inverted_budget.bounds.value.maximum_v_upper == pytest.approx(0.0)
    assert "output_polarity_unavailable" in {item.value for item in polarity_budget.blockers}
    assert "frequency_mode_unsupported" in {item.value for item in frequency_budget.blockers}


def test_modulation_gain_expands_only_the_ac_component() -> None:
    modulation = Observed.value_of(
        ModulationFacet(
            enabled=Observed.value_of(True),
            kind=Observed.value_of(SourceModulationKind.AM),
            source=Observed.value_of(SourceModulationSource.INTERNAL),
            parameters=_missing(Availability.NOT_QUERIED),
            internal_frequency_hz=_missing(Availability.NOT_APPLICABLE),
            internal_waveform_kind=_missing(Availability.NOT_APPLICABLE),
        )
    )
    extensions = _extensions(
        safety_profile=_constraints(
            _constraint(
                "safety.modulation",
                SourceSafetyConstraintKind.MODULATION_ENVELOPE,
                SourceModulationEnvelopeConstraint(SourceModulationKind.AM, 2.0),
            )
        )
    )
    snapshot = _snapshot(
        extensions,
        (_channel(1, offset_v=10.0, modulation=modulation),),
    )
    budget = evaluate_source_output_budget(
        _request(
            snapshot,
            extensions,
            terminations=(_termination(snapshot, 1, bounds=ResistanceBounds(1_000_000_000.0, 1_000_000_000.0)),),
            limits=SourceEnergySafetyLimits(10.0, -20.0, 20.0),
        )
    )

    assert budget.can_authorize_energy
    assert budget.bounds.value.minimum_v_lower == pytest.approx(8.0, abs=1e-6)
    assert budget.bounds.value.maximum_v_upper == pytest.approx(12.0, abs=1e-6)


def test_noise_harmonic_arb_and_sweep_require_their_hard_contributors() -> None:
    noise_extensions = _extensions(
        safety_profile=_constraints(
            _constraint(
                "safety.noise",
                SourceSafetyConstraintKind.NOISE_PEAK,
                SourceNoisePeakConstraint(2.0),
            )
        )
    )
    noise = _snapshot(
        noise_extensions,
        (
            _channel(
                1,
                waveform=SourceWaveformKind.NOISE,
                amplitude_vpp=None,
                frequency_mode=None,
                frequency_hz=None,
            ),
        ),
    )
    noise_budget = evaluate_source_output_budget(
        _request(noise, noise_extensions, terminations=(_termination(noise, 1),))
    )

    harmonics = Observed.value_of(
        HarmonicFacet(
            enabled=Observed.value_of(True),
            completeness=Observed.value_of(HarmonicCompleteness.COMPLETE),
            maximum_supported_order=Observed.value_of(2),
            components=Observed.value_of(
                (
                    SourceHarmonicComponentV2(
                        2,
                        Observed.value_of(
                            SourceComponentAmplitude(ComponentAmplitudeKind.ABSOLUTE_VPP, 2.0)
                        ),
                        Observed.value_of(0.0),
                    ),
                )
            ),
        )
    )
    harmonic_extensions = _extensions(safety_profile=_constraints())
    harmonic = _snapshot(harmonic_extensions, (_channel(1, harmonics=harmonics),))
    harmonic_budget = evaluate_source_output_budget(
        _request(harmonic, harmonic_extensions, terminations=(_termination(harmonic, 1),))
    )

    arbitrary = Observed.value_of(
        ArbitraryFacet(
            selected_waveform_id=Observed.value_of("arb-1"),
            playback_mode=Observed.value_of(SourceArbitraryPlaybackMode.DDS),
            playback_frequency_hz=Observed.value_of(1_000.0),
            sample_rate_hz=Observed.value_of(10_000.0),
            point_count=Observed.value_of(16),
            storage_digest=Observed.value_of("sha256:" + "2" * 64),
        )
    )
    arbitrary_extensions = _extensions(
        safety_profile=_constraints(
            _constraint(
                "safety.arb",
                SourceSafetyConstraintKind.ARBITRARY_OVERSHOOT,
                SourceArbitraryOvershootConstraint(1.5),
            )
        )
    )
    arbitrary_snapshot = _snapshot(
        arbitrary_extensions,
        (_channel(1, waveform=SourceWaveformKind.ARBITRARY, arbitrary=arbitrary),),
    )
    arbitrary_budget = evaluate_source_output_budget(
        _request(
            arbitrary_snapshot,
            arbitrary_extensions,
            terminations=(_termination(arbitrary_snapshot, 1),),
        )
    )

    sweep = Observed.value_of(
        SweepFacet(
            enabled=Observed.value_of(True),
            start_hz=Observed.value_of(100.0),
            stop_hz=Observed.value_of(2_000.0),
            spacing=_missing(Availability.NOT_APPLICABLE),
            steps=_missing(Availability.NOT_APPLICABLE),
            sweep_time_s=_missing(Availability.NOT_APPLICABLE),
            start_hold_s=_missing(Availability.NOT_APPLICABLE),
            stop_hold_s=_missing(Availability.NOT_APPLICABLE),
            return_time_s=_missing(Availability.NOT_APPLICABLE),
            trigger=_missing(Availability.NOT_APPLICABLE),
            marker=_missing(Availability.NOT_APPLICABLE),
        )
    )
    sweep_extensions = _extensions(
        safety_profile=_constraints(
            _constraint(
                "safety.sweep",
                SourceSafetyConstraintKind.FREQUENCY_DERATING,
                SourceFrequencyDeratingConstraint(
                    (SourceFrequencyDeratingBand(ClosedFloatInterval(100.0, 2_000.0), 2.0),)
                ),
            )
        )
    )
    sweep_snapshot = _snapshot(
        sweep_extensions,
        (
            _channel(
                1,
                frequency_mode=SourceFrequencyMode.SWEEP,
                sweep=sweep,
            ),
        ),
    )
    sweep_budget = evaluate_source_output_budget(
        _request(sweep_snapshot, sweep_extensions, terminations=(_termination(sweep_snapshot, 1),))
    )

    assert noise_budget.can_authorize_energy
    assert noise_budget.bounds.value.absolute_peak_upper_v == pytest.approx(1.0)
    assert harmonic_budget.can_authorize_energy
    assert harmonic_budget.bounds.value.absolute_peak_upper_v == pytest.approx(1.0)
    assert arbitrary_budget.can_authorize_energy
    assert arbitrary_budget.bounds.value.absolute_peak_upper_v == pytest.approx(0.75)
    assert sweep_budget.can_authorize_energy
    assert sweep_budget.bounds.value.absolute_peak_upper_v == pytest.approx(1.0)


def _two_channel_features(
    *,
    combine: bool = False,
    shared_power: bool = False,
) -> tuple[SourceFeatureCapability, ...]:
    base = source_extensions()
    basic_1, output_1 = base.features
    basic_2 = replace(basic_1, channels=(2,))
    output_2 = replace(output_1, channels=(2,))
    features = [basic_1, basic_2, output_1, output_2]
    profile = SourceCrossChannelCapabilityProfile(
        relation_kinds=tuple(
            sorted(
                {
                    *((SourceFeature.COMBINE,) if combine else ()),
                    *((SourceFeature.SHARED_POWER,) if shared_power else ()),
                },
                key=lambda item: item.value,
            )
        ),
        supported_channel_sets=((1, 2),),
        relation_graph_readable=combine,
        shared_power_constraint_readable=shared_power,
    )
    if combine:
        features.append(
            SourceFeatureCapability(
                feature=SourceFeature.COMBINE,
                support=SupportState.SUPPORTED,
                directions=(SourceFeatureDirection.READ,),
                scope=SourceFacetScope.CHANNEL_SET,
                channels=(1, 2),
                applicability=SourceConstraintApplicability(),
                profile=profile,
            )
        )
    if shared_power:
        features.append(
            SourceFeatureCapability(
                feature=SourceFeature.SHARED_POWER,
                support=SupportState.SUPPORTED,
                directions=(SourceFeatureDirection.READ,),
                scope=SourceFacetScope.INSTRUMENT,
                channels=(),
                applicability=SourceConstraintApplicability(),
                profile=profile,
            )
        )
    return tuple(sorted(features, key=lambda item: (item.feature.value, item.scope.value, item.channels)))


def test_internal_combine_uses_the_target_port_termination_and_unknown_path_blocks() -> None:
    features = _two_channel_features(combine=True)
    extensions = _extensions(
        safety_profile=_constraints(),
        topology=SourceTopologyContract((1, 2)),
        features=features,
    )
    relation = SourceRelationState(
        SourceFeature.COMBINE,
        (1, 2),
        Observed.value_of(True),
    )
    internal_edge = SourceRelationEdge(
        relation_id="combine-1-to-2",
        feature=SourceFeature.COMBINE,
        sources=(1,),
        targets=(2,),
        signal_path=SourceSignalPathKind.INTERNAL_WAVEFORM,
        affected_fields=(SourceFieldId.BASIC,),
    )
    cross = Observed.value_of(
        SourceCrossChannelStateV2(
            relations=(relation,),
            relation_graph=Observed.value_of(SourceRelationGraph((1, 2), (internal_edge,))),
            shared_power=_missing(Availability.NOT_APPLICABLE),
        )
    )
    snapshot = _snapshot(extensions, (_channel(1), _channel(2)), cross_channel=cross)
    combined = evaluate_source_output_budget(
        _request(
            snapshot,
            extensions,
            target_channel=2,
            terminations=(_termination(snapshot, 2),),
        )
    )

    output_edge = replace(internal_edge, signal_path=SourceSignalPathKind.OUTPUT_PORT)
    unsupported = _snapshot(
        extensions,
        (_channel(1), _channel(2)),
        cross_channel=Observed.value_of(
            SourceCrossChannelStateV2(
                relations=(relation,),
                relation_graph=Observed.value_of(SourceRelationGraph((1, 2), (output_edge,))),
                shared_power=_missing(Availability.NOT_APPLICABLE),
            )
        ),
    )
    unsupported_budget = evaluate_source_output_budget(
        _request(
            unsupported,
            extensions,
            target_channel=2,
            terminations=(_termination(unsupported, 2),),
        )
    )

    assert combined.can_authorize_energy
    assert combined.bounds.value.absolute_peak_upper_v == pytest.approx(1.0)
    assert any(item.channels == (1, 2) for item in combined.contributors)
    assert "combine_path_unsupported" in {item.value for item in unsupported_budget.blockers}


def test_shared_power_compares_runtime_and_descriptor_hard_limits() -> None:
    features = _two_channel_features(shared_power=True)
    extensions = _extensions(
        safety_profile=_constraints(
            _constraint(
                "safety.shared",
                SourceSafetyConstraintKind.SHARED_POWER,
                SourceSharedPowerConstraint((1, 2), 1.0),
            )
        ),
        topology=SourceTopologyContract((1, 2)),
        features=features,
    )
    shared = SourceSharedPowerState(
        participants=(1, 2),
        active_power_upper_w=Observed.value_of(2.0),
        hard_limit_w=Observed.value_of(1.0),
    )
    snapshot = _snapshot(
        extensions,
        (_channel(1), _channel(2)),
        cross_channel=Observed.value_of(
            SourceCrossChannelStateV2(
                relations=(),
                relation_graph=_missing(Availability.NOT_APPLICABLE),
                shared_power=Observed.value_of(shared),
            )
        ),
    )
    budget = evaluate_source_output_budget(
        _request(snapshot, extensions, terminations=(_termination(snapshot, 1),))
    )

    assert not budget.can_authorize_energy
    assert "shared_power_limit_exceeded" in {item.value for item in budget.blockers}
    assert budget.shared_power.value.observed_active_power_upper_w == 2.0


def test_delivered_display_load_and_context_binding_are_explicit() -> None:
    extensions = _extensions(
        safety_profile=_constraints(basis=VoltageReferenceBasis.DELIVERED_INTO_DISPLAY_LOAD)
    )
    snapshot = _snapshot(extensions, (_channel(1),))
    termination = _termination(snapshot, 1, bounds=ResistanceBounds(1_000.0, 1_000.0))
    budget = evaluate_source_output_budget(
        _request(
            snapshot,
            extensions,
            terminations=(termination,),
            limits=SourceEnergySafetyLimits(5.0, -3.0, 3.0),
        )
    )
    wrong_context = replace(termination[1], correlation_id="different-operation")
    wrong_context_budget = evaluate_source_output_budget(
        _request(snapshot, extensions, terminations=((termination[0], wrong_context),))
    )

    assert budget.can_authorize_energy
    assert budget.bounds.value.absolute_peak_upper_v == pytest.approx(1.9047619047619047)
    assert not wrong_context_budget.can_authorize_energy
    assert "termination_evidence_invalid" in {
        item.value for item in wrong_context_budget.blockers
    }


def test_non_consistent_snapshot_remains_a_budget_blocker() -> None:
    extensions = _extensions(safety_profile=_constraints())
    snapshot = _snapshot(
        extensions,
        (_channel(1),),
        consistency=SnapshotConsistencyState.UNPROVEN,
    )
    budget = evaluate_source_output_budget(
        _request(snapshot, extensions, terminations=(_termination(snapshot, 1),))
    )

    assert not budget.can_authorize_energy
    assert "snapshot_not_consistent" in {item.value for item in budget.blockers}
