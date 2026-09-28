"""Pure, conservative Source V2 output-budget evaluation.

This module deliberately has no dependency on a driver, transport, session, or
write capability.  It evaluates an already captured consistent snapshot with
typed descriptor constraints and returns port-voltage bounds plus stable
blockers.  A caller may use a positive result as one input to a later write
authorization, but evaluating a budget can never send instrument I/O.

The first implementation supports only a finite-resistance Thevenin model.
Unknown state, a non-resistive load, an unmodelled Combine path, or an
incomplete shared-power envelope is a blocker rather than an invitation to
guess a nominal value.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from wavebench.instruments.source_extensions import (
    ArbitraryFacet,
    Availability,
    BasicWaveFacet,
    BudgetEvidenceSource,
    BudgetProofStrength,
    ComponentAmplitudeKind,
    CompositeOutputBudget,
    HarmonicCompleteness,
    HarmonicFacet,
    ModulationFacet,
    Observed,
    OutputFacet,
    PortVoltageBounds,
    ResistanceBounds,
    SafetyContributor,
    SnapshotConsistencyState,
    SourceAmplitudeUnit,
    SourceArbitraryOvershootConstraint,
    SourceBudgetBlockerCode,
    SourceChannelStateV2,
    SourceConstraintApplicability,
    SourceDescriptorExtensions,
    SourceDisplayLoad,
    SourceFeature,
    SourceFrequencyDeratingBand,
    SourceFrequencyDeratingConstraint,
    SourceFrequencyMode,
    SourceLoadKind,
    SourceModulationEnvelopeConstraint,
    SourceNoisePeakConstraint,
    SourceOutputPolarity,
    SourceReasonCode,
    SourceResistanceConstraint,
    SourceRuntimeIdentity,
    SourceSafetyConstraint,
    SourceSafetyConstraintKind,
    SourceSharedPowerBudget,
    SourceSharedPowerConstraint,
    SourceSignalPathKind,
    SourceSnapshotV2,
    SourceTerminationEvidence,
    SourceVoltageReferenceConstraint,
    SourceWaveformKind,
    SupportState,
    TerminationKind,
    TerminationSpec,
    VoltageReferenceBasis,
    source_v2_digest,
)
from wavebench.services.source_safety import (
    SourceEnergySafetyLimits,
    SourceTerminationEvidenceContext,
    SourceTerminationEvidenceStatus,
    validate_source_termination_evidence,
)


@dataclass(frozen=True, slots=True)
class SourceOutputBudgetRequest:
    """Input for one future energy-increasing Source V2 operation.

    ``target_channel`` is always treated as a projected active output, even if
    it is currently OFF.  ``projected_active_channels`` adds other direct
    outputs that a future operation expects to be active.  It only affects a
    declared shared-power envelope; it does not change the snapshot or issue
    a write.

    Termination contexts must carry the snapshot correlation ID.  This keeps a
    valid-looking config or manual evidence object from a different operation
    out of the calculation.
    """

    snapshot: SourceSnapshotV2
    descriptor_extensions: SourceDescriptorExtensions
    limits: SourceEnergySafetyLimits
    target_channel: int
    termination_evidence: tuple[SourceTerminationEvidence, ...]
    termination_contexts: tuple[SourceTerminationEvidenceContext, ...]
    projected_active_channels: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SourceSnapshotV2):
            raise ValueError("source budget snapshot has an invalid type")
        if not isinstance(self.descriptor_extensions, SourceDescriptorExtensions):
            raise ValueError("source budget descriptor_extensions has an invalid type")
        if not isinstance(self.limits, SourceEnergySafetyLimits):
            raise ValueError("source budget limits has an invalid type")
        _require_channel(self.target_channel, "source budget target_channel")
        if not isinstance(self.termination_evidence, tuple) or any(
            not isinstance(item, SourceTerminationEvidence) for item in self.termination_evidence
        ):
            raise ValueError("source budget termination_evidence has an invalid type")
        if not isinstance(self.termination_contexts, tuple) or any(
            not isinstance(item, SourceTerminationEvidenceContext)
            for item in self.termination_contexts
        ):
            raise ValueError("source budget termination_contexts has an invalid type")
        _require_channels(
            self.projected_active_channels,
            "source budget projected_active_channels",
            allow_empty=True,
        )
        evidence_channels = tuple(_context_channel(item.target) for item in self.termination_evidence)
        context_channels = tuple(_context_channel(item.target) for item in self.termination_contexts)
        if len(set(evidence_channels)) != len(evidence_channels):
            raise ValueError("source budget termination_evidence channels must be unique")
        if len(set(context_channels)) != len(context_channels):
            raise ValueError("source budget termination_contexts channels must be unique")


@dataclass(frozen=True, slots=True)
class _TerminationResolution:
    observed: Observed[TerminationSpec]
    evidence_source: BudgetEvidenceSource | None


@dataclass(frozen=True, slots=True)
class _ChannelFacts:
    channel: int
    basic: BasicWaveFacet
    output: OutputFacet
    waveform_kind: SourceWaveformKind
    frequency_mode: SourceFrequencyMode | None
    frequency_min_hz: float | None
    frequency_max_hz: float | None
    amplitude_vpp: float | None
    offset_v: float
    polarity: SourceOutputPolarity


@dataclass(frozen=True, slots=True)
class _ChannelPortEvaluation:
    channel: int
    physical_port_channel: int
    facts: _ChannelFacts | None
    bounds: PortVoltageBounds | None
    contributors: tuple[SafetyContributor, ...]
    blockers: tuple[SourceBudgetBlockerCode, ...]
    proof_strength: BudgetProofStrength
    voltage_reference_basis: Observed[VoltageReferenceBasis]
    display_load: Observed[TerminationSpec]
    source_resistance: Observed[ResistanceBounds]
    actual_termination: Observed[TerminationSpec]
    actual_termination_evidence_source: BudgetEvidenceSource | None


@dataclass(frozen=True, slots=True)
class _GainEvaluation:
    gain_upper: float
    proof_strength: BudgetProofStrength
    constraint_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SharedPowerEvaluation:
    observed: Observed[SourceSharedPowerBudget]
    contributors: tuple[SafetyContributor, ...]
    blockers: tuple[SourceBudgetBlockerCode, ...]
    proof_strength: BudgetProofStrength


def evaluate_source_output_budget(request: SourceOutputBudgetRequest) -> CompositeOutputBudget:
    """Return a conservative target-port budget without any instrument I/O."""

    if not isinstance(request, SourceOutputBudgetRequest):
        raise TypeError("source budget request has an invalid type")

    blockers: set[SourceBudgetBlockerCode] = set()
    contributors: list[SafetyContributor] = []
    proof_strength = BudgetProofStrength.HARD_CONSERVATIVE
    if request.snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
        blockers.add(SourceBudgetBlockerCode.SNAPSHOT_NOT_CONSISTENT)
    if request.snapshot.runtime_profile.descriptor_digest != source_v2_digest(
        request.descriptor_extensions
    ):
        blockers.add(SourceBudgetBlockerCode.DESCRIPTOR_MISMATCH)

    channels = {state.channel: state for state in request.snapshot.channels}
    target = channels.get(request.target_channel)
    if target is None:
        blockers.add(SourceBudgetBlockerCode.TARGET_CHANNEL_UNKNOWN)
        return _incomplete_budget(blockers=blockers)

    combine_sources = _combine_source_channels(request, blockers)
    main_evaluations: dict[int, _ChannelPortEvaluation] = {}
    for channel_id in sorted(combine_sources):
        channel = channels.get(channel_id)
        if channel is None:
            blockers.add(SourceBudgetBlockerCode.TARGET_CHANNEL_UNKNOWN)
            continue
        # An INTERNAL_WAVEFORM Combine source is not a separately terminated
        # physical port.  Its waveform is converted through the selected target
        # port, whose actual termination is therefore the relevant evidence.
        evaluation = _evaluate_channel_port(
            request,
            channel=channel,
            physical_port_channel=request.target_channel,
        )
        main_evaluations[channel_id] = evaluation
        blockers.update(evaluation.blockers)
        contributors.extend(evaluation.contributors)
        proof_strength = _weaker_proof(proof_strength, evaluation.proof_strength)
        if (
            channel_id != request.target_channel
            and evaluation.facts is not None
            and evaluation.facts.polarity is not SourceOutputPolarity.NORMAL
        ):
            # The relation graph names an internal waveform path but does not
            # define how an auxiliary channel's output-polarity setting maps
            # into that path.  Do not reinterpret it electrically.
            blockers.add(SourceBudgetBlockerCode.COMBINE_PATH_UNSUPPORTED)

    target_evaluation = main_evaluations.get(request.target_channel)
    if target_evaluation is None:
        blockers.add(SourceBudgetBlockerCode.TARGET_CHANNEL_UNKNOWN)
        return _incomplete_budget(blockers=blockers)

    combined_bounds = _sum_evaluation_bounds(tuple(main_evaluations.values()))
    if combined_bounds is None:
        proof_strength = BudgetProofStrength.INCOMPLETE

    active_direct_channels = _active_direct_channels(
        request,
        channels=channels,
        blockers=blockers,
    )
    shared = _evaluate_shared_power(
        request,
        channels=channels,
        active_direct_channels=active_direct_channels,
        combine_sources=combine_sources,
        main_evaluations=main_evaluations,
    )
    blockers.update(shared.blockers)
    contributors.extend(shared.contributors)
    proof_strength = _weaker_proof(proof_strength, shared.proof_strength)

    if combined_bounds is not None:
        if combined_bounds.vpp_upper_v > request.limits.max_source_vpp:
            blockers.add(SourceBudgetBlockerCode.VPP_LIMIT_EXCEEDED)
        if (
            combined_bounds.minimum_v_lower < request.limits.min_source_port_voltage_v
            or combined_bounds.maximum_v_upper > request.limits.max_source_port_voltage_v
        ):
            blockers.add(SourceBudgetBlockerCode.PORT_VOLTAGE_LIMIT_EXCEEDED)

    if blockers:
        proof_strength = BudgetProofStrength.INCOMPLETE
    return CompositeOutputBudget(
        bounds=_bounds_observed(combined_bounds),
        voltage_reference_basis=target_evaluation.voltage_reference_basis,
        display_load=target_evaluation.display_load,
        output_source_resistance=target_evaluation.source_resistance,
        actual_termination=target_evaluation.actual_termination,
        shared_power=shared.observed,
        proof_strength=proof_strength,
        evidence_sources=_evidence_sources(contributors, shared.observed),
        contributors=tuple(sorted(contributors, key=lambda item: item.contributor_id)),
        blockers=tuple(sorted(blockers, key=lambda item: item.value)),
    )


def _evaluate_channel_port(
    request: SourceOutputBudgetRequest,
    *,
    channel: SourceChannelStateV2,
    physical_port_channel: int,
) -> _ChannelPortEvaluation:
    blockers: set[SourceBudgetBlockerCode] = set()
    contributors: list[SafetyContributor] = []
    proof = BudgetProofStrength.HARD_CONSERVATIVE
    facts = _channel_facts(channel, blockers)
    termination = _actual_termination(request, physical_port_channel, blockers)
    if facts is None:
        return _channel_incomplete(
            channel=channel.channel,
            physical_port_channel=physical_port_channel,
            facts=None,
            blockers=blockers,
            actual_termination=termination,
        )

    voltage_constraint, voltage_proof = _single_constraint(
        request,
        SourceSafetyConstraintKind.VOLTAGE_REFERENCE,
        facts,
        blockers,
        SourceBudgetBlockerCode.VOLTAGE_REFERENCE_MISSING,
    )
    resistance_constraint, resistance_proof = _single_constraint(
        request,
        SourceSafetyConstraintKind.SOURCE_RESISTANCE,
        facts,
        blockers,
        SourceBudgetBlockerCode.SOURCE_RESISTANCE_MISSING,
    )
    proof = _weaker_proof(proof, voltage_proof, resistance_proof)
    voltage_reference = _constraint_observed(
        voltage_constraint,
        SourceVoltageReferenceConstraint,
        lambda profile: profile.basis,
    )
    source_resistance = _constraint_observed(
        resistance_constraint,
        SourceResistanceConstraint,
        lambda profile: profile.resistance_ohm,
    )
    display_load = _display_load(facts.output, blockers)
    if (
        voltage_reference.availability is not Availability.VALUE
        or source_resistance.availability is not Availability.VALUE
        or termination.observed.availability is not Availability.VALUE
    ):
        return _channel_incomplete(
            channel=channel.channel,
            physical_port_channel=physical_port_channel,
            facts=facts,
            blockers=blockers,
            actual_termination=termination,
            voltage_reference=voltage_reference,
            display_load=display_load,
            source_resistance=source_resistance,
            proof=proof,
        )

    reference_bounds, reference_contributors, reference_blockers, reference_proof = _reference_bounds(
        request,
        channel,
        facts,
    )
    blockers.update(reference_blockers)
    contributors.extend(reference_contributors)
    proof = _weaker_proof(proof, reference_proof)
    if reference_bounds is None:
        return _channel_incomplete(
            channel=channel.channel,
            physical_port_channel=physical_port_channel,
            facts=facts,
            blockers=blockers,
            actual_termination=termination,
            voltage_reference=voltage_reference,
            display_load=display_load,
            source_resistance=source_resistance,
            contributors=contributors,
            proof=proof,
        )

    factor = _conversion_factor(
        basis=voltage_reference.value,
        source_resistance=source_resistance.value,
        display_load=display_load,
        actual_termination=termination.observed,
        blockers=blockers,
    )
    if factor is None:
        return _channel_incomplete(
            channel=channel.channel,
            physical_port_channel=physical_port_channel,
            facts=facts,
            blockers=blockers,
            actual_termination=termination,
            voltage_reference=voltage_reference,
            display_load=display_load,
            source_resistance=source_resistance,
            contributors=contributors,
            proof=proof,
        )
    minimum, maximum = _scaled_interval(reference_bounds, factor)
    bounds = _make_bounds(minimum, maximum)
    constraint_ids = tuple(
        sorted(
            {
                *(
                    item.constraint_id
                    for item in (voltage_constraint, resistance_constraint)
                    if item is not None
                ),
                *(item.constraint_id for item in _all_reference_constraints(request, facts)),
            }
        )
    )
    evidence_sources = {
        BudgetEvidenceSource.INSTRUMENT_READBACK,
        BudgetEvidenceSource.DEVICE_HARD_LIMIT,
    }
    if termination.evidence_source is not None:
        evidence_sources.add(termination.evidence_source)
    contributors.append(
        SafetyContributor(
            contributor_id=(
                f"channel-{channel.channel}.port"
                if physical_port_channel == channel.channel
                else f"channel-{channel.channel}.to-channel-{physical_port_channel}.port"
            ),
            feature=SourceFeature.BASIC,
            channels=tuple(sorted({channel.channel, physical_port_channel})),
            minimum_v=minimum,
            maximum_v=maximum,
            constraint_ids=constraint_ids,
            proof_strength=proof,
            evidence_sources=tuple(sorted(evidence_sources, key=lambda item: item.value)),
        )
    )
    return _ChannelPortEvaluation(
        channel=channel.channel,
        physical_port_channel=physical_port_channel,
        facts=facts,
        bounds=bounds,
        contributors=tuple(contributors),
        blockers=tuple(sorted(blockers, key=lambda item: item.value)),
        proof_strength=proof,
        voltage_reference_basis=voltage_reference,
        display_load=display_load,
        source_resistance=source_resistance,
        actual_termination=termination.observed,
        actual_termination_evidence_source=termination.evidence_source,
    )


def _channel_facts(
    channel: SourceChannelStateV2,
    blockers: set[SourceBudgetBlockerCode],
) -> _ChannelFacts | None:
    if channel.basic.availability is not Availability.VALUE or channel.output.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.BASIC_STATE_UNAVAILABLE)
        return None
    basic = channel.basic.value
    output = channel.output.value
    if (
        basic.waveform_kind.availability is not Availability.VALUE
        or basic.offset_v.availability is not Availability.VALUE
    ):
        blockers.add(SourceBudgetBlockerCode.BASIC_STATE_UNAVAILABLE)
        return None
    waveform = basic.waveform_kind.value
    if waveform in {SourceWaveformKind.OTHER, SourceWaveformKind.PULSE}:
        blockers.add(SourceBudgetBlockerCode.WAVEFORM_UNSUPPORTED)
        return None
    if output.polarity.availability is not Availability.VALUE or output.polarity.value is SourceOutputPolarity.UNKNOWN:
        blockers.add(SourceBudgetBlockerCode.OUTPUT_POLARITY_UNAVAILABLE)
        return None

    frequency_mode: SourceFrequencyMode | None = None
    frequency_min_hz: float | None = None
    frequency_max_hz: float | None = None
    if basic.frequency_mode.availability is Availability.VALUE:
        frequency_mode = basic.frequency_mode.value
        if frequency_mode in {SourceFrequencyMode.UNKNOWN, SourceFrequencyMode.LIST}:
            blockers.add(SourceBudgetBlockerCode.FREQUENCY_MODE_UNSUPPORTED)
            return None
        if frequency_mode is SourceFrequencyMode.FIXED:
            if basic.frequency_hz.availability is Availability.VALUE:
                frequency_min_hz = basic.frequency_hz.value
                frequency_max_hz = basic.frequency_hz.value
            elif waveform not in {SourceWaveformKind.DC, SourceWaveformKind.NOISE}:
                blockers.add(SourceBudgetBlockerCode.BASIC_STATE_UNAVAILABLE)
                return None
        elif frequency_mode is SourceFrequencyMode.SWEEP:
            if channel.sweep.availability is not Availability.VALUE:
                blockers.add(SourceBudgetBlockerCode.SWEEP_DERATING_MISSING)
                return None
            sweep = channel.sweep.value
            if (
                sweep.enabled.availability is not Availability.VALUE
                or not sweep.enabled.value
                or sweep.start_hz.availability is not Availability.VALUE
                or sweep.stop_hz.availability is not Availability.VALUE
            ):
                blockers.add(SourceBudgetBlockerCode.SWEEP_DERATING_MISSING)
                return None
            frequency_min_hz = min(sweep.start_hz.value, sweep.stop_hz.value)
            frequency_max_hz = max(sweep.start_hz.value, sweep.stop_hz.value)
    elif waveform not in {SourceWaveformKind.DC, SourceWaveformKind.NOISE}:
        blockers.add(SourceBudgetBlockerCode.FREQUENCY_MODE_UNSUPPORTED)
        return None

    amplitude_vpp: float | None = None
    if basic.amplitude.availability is Availability.VALUE:
        amplitude = basic.amplitude.value
        if amplitude.unit is SourceAmplitudeUnit.VPP:
            amplitude_vpp = amplitude.value
        elif waveform not in {SourceWaveformKind.NOISE, SourceWaveformKind.DC}:
            blockers.add(SourceBudgetBlockerCode.AMPLITUDE_UNIT_UNSUPPORTED)
            return None
    elif waveform not in {SourceWaveformKind.DC, SourceWaveformKind.NOISE}:
        blockers.add(SourceBudgetBlockerCode.BASIC_STATE_UNAVAILABLE)
        return None
    if waveform is SourceWaveformKind.DC and amplitude_vpp not in {None, 0.0}:
        # The V2 basic facet has no independent DC low/high-level model.  A
        # nonzero Vpp alongside DC therefore cannot be safely interpreted as
        # merely redundant display information.
        blockers.add(SourceBudgetBlockerCode.DC_LEVEL_UNAVAILABLE)
        return None

    return _ChannelFacts(
        channel=channel.channel,
        basic=basic,
        output=output,
        waveform_kind=waveform,
        frequency_mode=frequency_mode,
        frequency_min_hz=frequency_min_hz,
        frequency_max_hz=frequency_max_hz,
        amplitude_vpp=amplitude_vpp,
        offset_v=basic.offset_v.value,
        polarity=output.polarity.value,
    )


def _reference_bounds(
    request: SourceOutputBudgetRequest,
    channel: SourceChannelStateV2,
    facts: _ChannelFacts,
) -> tuple[
    tuple[float, float] | None,
    list[SafetyContributor],
    set[SourceBudgetBlockerCode],
    BudgetProofStrength,
]:
    blockers: set[SourceBudgetBlockerCode] = set()
    contributors: list[SafetyContributor] = []
    proof = BudgetProofStrength.HARD_CONSERVATIVE
    constraint_ids: set[str] = set()

    if channel.noise_overlay.availability not in {
        Availability.UNSUPPORTED,
        Availability.NOT_APPLICABLE,
    }:
        if channel.noise_overlay.availability is not Availability.VALUE:
            blockers.add(SourceBudgetBlockerCode.NOISE_OVERLAY_BOUND_MISSING)
        else:
            noise_overlay = channel.noise_overlay.value
            if (
                noise_overlay.enabled.availability is not Availability.VALUE
                or noise_overlay.enabled.value
            ):
                blockers.add(SourceBudgetBlockerCode.NOISE_OVERLAY_BOUND_MISSING)

    if facts.waveform_kind is SourceWaveformKind.NOISE:
        constraints, noise_proof = _constraints(
            request,
            SourceSafetyConstraintKind.NOISE_PEAK,
            facts,
        )
        proof = _weaker_proof(proof, noise_proof)
        noise = tuple(
            item for item in constraints if isinstance(item.profile, SourceNoisePeakConstraint)
        )
        if not noise:
            blockers.add(SourceBudgetBlockerCode.NOISE_PEAK_MISSING)
            return None, contributors, blockers, BudgetProofStrength.INCOMPLETE
        if noise_proof is not BudgetProofStrength.HARD_CONSERVATIVE:
            blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
            return None, contributors, blockers, BudgetProofStrength.INCOMPLETE
        peak = max(item.profile.absolute_peak_upper_v for item in noise)
        constraint_ids.update(item.constraint_id for item in noise)
    else:
        peak = (facts.amplitude_vpp or 0.0) / 2.0
    ac_minimum, ac_maximum = -peak, peak
    contributors.append(
        _contributor(
            contributor_id=f"channel-{channel.channel}.base_reference",
            feature=SourceFeature.BASIC,
            channels=(channel.channel,),
            minimum=ac_minimum,
            maximum=ac_maximum,
            constraint_ids=tuple(sorted(constraint_ids)),
            proof_strength=proof,
        )
    )

    harmonic_bounds, harmonic_contributors, harmonic_blockers = _harmonic_bounds(channel)
    blockers.update(harmonic_blockers)
    contributors.extend(harmonic_contributors)
    if harmonic_bounds is not None:
        ac_minimum += harmonic_bounds[0]
        ac_maximum += harmonic_bounds[1]

    modulation = _modulation_gain(request, channel, facts, blockers)
    arbitrary = _arbitrary_gain(request, channel, facts, blockers)
    sweep = _sweep_gain(request, channel, facts, blockers)
    proof = _weaker_proof(
        proof,
        modulation.proof_strength,
        arbitrary.proof_strength,
        sweep.proof_strength,
    )
    gain = modulation.gain_upper * arbitrary.gain_upper * sweep.gain_upper
    if not isfinite(gain) or gain < 1.0:
        blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
        return None, contributors, blockers, BudgetProofStrength.INCOMPLETE
    if gain != 1.0:
        ac_minimum, ac_maximum = _scaled_interval(
            (ac_minimum, ac_maximum),
            (gain, gain),
        )
        applied = tuple(
            item
            for item in (SourceFeature.MODULATION, SourceFeature.ARBITRARY, SourceFeature.SWEEP)
            if (
                (item is SourceFeature.MODULATION and modulation.gain_upper != 1.0)
                or (item is SourceFeature.ARBITRARY and arbitrary.gain_upper != 1.0)
                or (item is SourceFeature.SWEEP and sweep.gain_upper != 1.0)
            )
        )
        feature = applied[0]
        contributors.append(
            _contributor(
                contributor_id=f"channel-{channel.channel}.{feature.value}_ac_envelope",
                feature=feature,
                channels=(channel.channel,),
                minimum=ac_minimum,
                maximum=ac_maximum,
                constraint_ids=tuple(
                    sorted(
                        set(modulation.constraint_ids)
                        | set(arbitrary.constraint_ids)
                        | set(sweep.constraint_ids)
                    )
                ),
                proof_strength=proof,
            )
        )
    if blockers:
        return None, contributors, blockers, proof

    # gain_upper is an AC-envelope bound.  It applies around the configured
    # DC offset, rather than multiplying the offset itself.  That distinction
    # is essential for asymmetric absolute-voltage limits.
    minimum = facts.offset_v + ac_minimum
    maximum = facts.offset_v + ac_maximum
    if facts.polarity is SourceOutputPolarity.INVERTED:
        minimum, maximum = -maximum, -minimum
    return (minimum, maximum), contributors, blockers, proof


def _harmonic_bounds(
    channel: SourceChannelStateV2,
) -> tuple[
    tuple[float, float] | None,
    list[SafetyContributor],
    set[SourceBudgetBlockerCode],
]:
    blockers: set[SourceBudgetBlockerCode] = set()
    contributors: list[SafetyContributor] = []
    if channel.harmonics.availability in {Availability.UNSUPPORTED, Availability.NOT_APPLICABLE}:
        return (0.0, 0.0), contributors, blockers
    if channel.harmonics.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.HARMONIC_STATE_UNAVAILABLE)
        return None, contributors, blockers
    facet: HarmonicFacet = channel.harmonics.value
    if facet.enabled.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.HARMONIC_STATE_UNAVAILABLE)
        return None, contributors, blockers
    if not facet.enabled.value:
        return (0.0, 0.0), contributors, blockers
    if (
        facet.completeness.availability is not Availability.VALUE
        or facet.completeness.value is not HarmonicCompleteness.COMPLETE
        or facet.maximum_supported_order.availability is not Availability.VALUE
        or facet.components.availability is not Availability.VALUE
    ):
        blockers.add(SourceBudgetBlockerCode.HARMONIC_COMPLETENESS_INSUFFICIENT)
        return None, contributors, blockers
    peak = 0.0
    for component in facet.components.value:
        if component.amplitude.availability is not Availability.VALUE:
            blockers.add(SourceBudgetBlockerCode.HARMONIC_STATE_UNAVAILABLE)
            return None, contributors, blockers
        amplitude = component.amplitude.value
        if amplitude.kind is not ComponentAmplitudeKind.ABSOLUTE_VPP:
            blockers.add(SourceBudgetBlockerCode.HARMONIC_AMPLITUDE_UNSUPPORTED)
            return None, contributors, blockers
        peak += amplitude.value / 2.0
    contributors.append(
        _contributor(
            contributor_id=f"channel-{channel.channel}.harmonics_reference",
            feature=SourceFeature.HARMONICS,
            channels=(channel.channel,),
            minimum=-peak,
            maximum=peak,
            constraint_ids=(),
            proof_strength=BudgetProofStrength.HARD_CONSERVATIVE,
        )
    )
    return (-peak, peak), contributors, blockers


def _modulation_gain(
    request: SourceOutputBudgetRequest,
    channel: SourceChannelStateV2,
    facts: _ChannelFacts,
    blockers: set[SourceBudgetBlockerCode],
) -> _GainEvaluation:
    if channel.modulation.availability in {Availability.UNSUPPORTED, Availability.NOT_APPLICABLE}:
        return _GainEvaluation(1.0, BudgetProofStrength.HARD_CONSERVATIVE, ())
    if channel.modulation.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.MODULATION_CONSTRAINT_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    facet: ModulationFacet = channel.modulation.value
    if (
        facet.enabled.availability is not Availability.VALUE
        or facet.kind.availability is not Availability.VALUE
        or facet.source.availability is not Availability.VALUE
        or facet.source.value.value != "internal"
    ):
        blockers.add(SourceBudgetBlockerCode.MODULATION_CONSTRAINT_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    if not facet.enabled.value:
        return _GainEvaluation(1.0, BudgetProofStrength.HARD_CONSERVATIVE, ())
    constraints, proof = _constraints(
        request,
        SourceSafetyConstraintKind.MODULATION_ENVELOPE,
        facts,
    )
    matching = tuple(
        item
        for item in constraints
        if isinstance(item.profile, SourceModulationEnvelopeConstraint)
        and item.profile.kind is facet.kind.value
    )
    if not matching:
        blockers.add(SourceBudgetBlockerCode.MODULATION_CONSTRAINT_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    if proof is not BudgetProofStrength.HARD_CONSERVATIVE:
        blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
    return _GainEvaluation(
        max(item.profile.gain_upper for item in matching),
        proof,
        tuple(sorted(item.constraint_id for item in matching)),
    )


def _arbitrary_gain(
    request: SourceOutputBudgetRequest,
    channel: SourceChannelStateV2,
    facts: _ChannelFacts,
    blockers: set[SourceBudgetBlockerCode],
) -> _GainEvaluation:
    if facts.waveform_kind is not SourceWaveformKind.ARBITRARY:
        return _GainEvaluation(1.0, BudgetProofStrength.HARD_CONSERVATIVE, ())
    if channel.arbitrary.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.ARBITRARY_OVERSHOOT_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    facet: ArbitraryFacet = channel.arbitrary.value
    if (
        facet.playback_mode.availability is not Availability.VALUE
        or facet.selected_waveform_id.availability is not Availability.VALUE
    ):
        blockers.add(SourceBudgetBlockerCode.ARBITRARY_OVERSHOOT_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    constraints, proof = _constraints(
        request,
        SourceSafetyConstraintKind.ARBITRARY_OVERSHOOT,
        facts,
    )
    matching = tuple(
        item for item in constraints if isinstance(item.profile, SourceArbitraryOvershootConstraint)
    )
    if not matching:
        blockers.add(SourceBudgetBlockerCode.ARBITRARY_OVERSHOOT_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    if proof is not BudgetProofStrength.HARD_CONSERVATIVE:
        blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
    return _GainEvaluation(
        max(item.profile.gain_upper for item in matching),
        proof,
        tuple(sorted(item.constraint_id for item in matching)),
    )


def _sweep_gain(
    request: SourceOutputBudgetRequest,
    channel: SourceChannelStateV2,
    facts: _ChannelFacts,
    blockers: set[SourceBudgetBlockerCode],
) -> _GainEvaluation:
    if facts.frequency_mode is not SourceFrequencyMode.SWEEP:
        return _GainEvaluation(1.0, BudgetProofStrength.HARD_CONSERVATIVE, ())
    if facts.frequency_min_hz is None or facts.frequency_max_hz is None:
        blockers.add(SourceBudgetBlockerCode.SWEEP_DERATING_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    constraints, proof = _constraints(
        request,
        SourceSafetyConstraintKind.FREQUENCY_DERATING,
        facts,
    )
    bands = tuple(
        band
        for item in constraints
        if isinstance(item.profile, SourceFrequencyDeratingConstraint)
        for band in item.profile.bands
    )
    covered, maximum_gain = _cover_frequency_range(
        tuple(sorted(bands, key=lambda item: item.frequency_hz.minimum)),
        facts.frequency_min_hz,
        facts.frequency_max_hz,
    )
    if not covered:
        blockers.add(SourceBudgetBlockerCode.SWEEP_DERATING_MISSING)
        return _GainEvaluation(1.0, BudgetProofStrength.INCOMPLETE, ())
    if proof is not BudgetProofStrength.HARD_CONSERVATIVE:
        blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
    return _GainEvaluation(
        maximum_gain,
        proof,
        tuple(sorted(item.constraint_id for item in constraints)),
    )


def _single_constraint(
    request: SourceOutputBudgetRequest,
    kind: SourceSafetyConstraintKind,
    facts: _ChannelFacts,
    blockers: set[SourceBudgetBlockerCode],
    missing: SourceBudgetBlockerCode,
) -> tuple[SourceSafetyConstraint | None, BudgetProofStrength]:
    constraints, proof = _constraints(request, kind, facts)
    if not constraints:
        blockers.add(missing)
        return None, BudgetProofStrength.INCOMPLETE
    profiles = {source_v2_digest(item.profile) for item in constraints}
    if len(profiles) != 1:
        blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
        return None, BudgetProofStrength.INCOMPLETE
    if proof is not BudgetProofStrength.HARD_CONSERVATIVE:
        blockers.add(SourceBudgetBlockerCode.CONSTRAINT_NOT_HARD)
    return constraints[0], proof


def _constraints(
    request: SourceOutputBudgetRequest,
    kind: SourceSafetyConstraintKind,
    facts: _ChannelFacts,
) -> tuple[tuple[SourceSafetyConstraint, ...], BudgetProofStrength]:
    matching = tuple(
        constraint
        for constraint in request.descriptor_extensions.safety_profile.constraints
        if constraint.kind is kind
        and _applicability_matches(constraint.applicability, request.snapshot.runtime_profile.identity, facts)
    )
    if not matching:
        return (), BudgetProofStrength.INCOMPLETE
    hard = tuple(
        item for item in matching if item.proof_strength is BudgetProofStrength.HARD_CONSERVATIVE
    )
    if hard:
        return hard, BudgetProofStrength.HARD_CONSERVATIVE
    return matching, _strongest_nonhard(item.proof_strength for item in matching)


def _all_reference_constraints(
    request: SourceOutputBudgetRequest,
    facts: _ChannelFacts,
) -> tuple[SourceSafetyConstraint, ...]:
    relevant = {
        SourceSafetyConstraintKind.NOISE_PEAK,
        SourceSafetyConstraintKind.MODULATION_ENVELOPE,
        SourceSafetyConstraintKind.ARBITRARY_OVERSHOOT,
        SourceSafetyConstraintKind.FREQUENCY_DERATING,
    }
    return tuple(
        constraint
        for constraint in request.descriptor_extensions.safety_profile.constraints
        if constraint.kind in relevant
        and _applicability_matches(constraint.applicability, request.snapshot.runtime_profile.identity, facts)
        and constraint.proof_strength is BudgetProofStrength.HARD_CONSERVATIVE
    )


def _applicability_matches(
    applicability: SourceConstraintApplicability,
    identity: SourceRuntimeIdentity,
    facts: _ChannelFacts,
) -> bool:
    if applicability.models and identity.model not in applicability.models:
        return False
    if applicability.firmware_ids and identity.firmware_id not in applicability.firmware_ids:
        return False
    if applicability.option_ids and not set(applicability.option_ids) <= set(identity.option_ids):
        return False
    if applicability.waveform_kinds and facts.waveform_kind not in applicability.waveform_kinds:
        return False
    if applicability.frequency_hz is not None:
        if facts.frequency_min_hz is None or facts.frequency_max_hz is None:
            return False
        if not (
            applicability.frequency_hz.minimum <= facts.frequency_min_hz
            and facts.frequency_max_hz <= applicability.frequency_hz.maximum
        ):
            return False
    if applicability.amplitude_vpp is not None:
        if facts.amplitude_vpp is None or not (
            applicability.amplitude_vpp.minimum
            <= facts.amplitude_vpp
            <= applicability.amplitude_vpp.maximum
        ):
            return False
    return applicability.offset_v is None or (
        applicability.offset_v.minimum <= facts.offset_v <= applicability.offset_v.maximum
    )


def _actual_termination(
    request: SourceOutputBudgetRequest,
    channel: int,
    blockers: set[SourceBudgetBlockerCode],
) -> _TerminationResolution:
    evidence = next(
        (item for item in request.termination_evidence if _context_channel(item.target) == channel),
        None,
    )
    context = next(
        (item for item in request.termination_contexts if _context_channel(item.target) == channel),
        None,
    )
    if evidence is None or context is None:
        blockers.add(SourceBudgetBlockerCode.ACTUAL_TERMINATION_MISSING)
        return _TerminationResolution(_missing(), None)
    if context.correlation_id != request.snapshot.correlation_id:
        blockers.add(SourceBudgetBlockerCode.TERMINATION_EVIDENCE_INVALID)
        return _TerminationResolution(_missing(), None)
    validation = validate_source_termination_evidence(evidence, context=context)
    if validation.status is not SourceTerminationEvidenceStatus.VALID:
        blockers.add(SourceBudgetBlockerCode.TERMINATION_EVIDENCE_INVALID)
        return _TerminationResolution(_missing(), None)
    assert validation.evidence is not None
    if (
        validation.evidence.termination.kind is not TerminationKind.RESISTIVE
        or validation.evidence.termination.resistance_bounds is None
    ):
        blockers.add(SourceBudgetBlockerCode.TERMINATION_NOT_RESISTIVE)
        return _TerminationResolution(Observed.value_of(validation.evidence.termination), None)
    evidence_source = (
        BudgetEvidenceSource.EXTERNAL_MEASUREMENT
        if validation.evidence.source.value == "external_measurement"
        else BudgetEvidenceSource.EXPLICIT_TERMINATION
    )
    return _TerminationResolution(Observed.value_of(validation.evidence.termination), evidence_source)


def _display_load(
    output: OutputFacet,
    blockers: set[SourceBudgetBlockerCode],
) -> Observed[TerminationSpec]:
    if output.display_load.availability is not Availability.VALUE:
        return Observed(
            availability=output.display_load.availability,
            reason_code=output.display_load.reason_code,
            evidence_refs=output.display_load.evidence_refs,
        )
    load: SourceDisplayLoad = output.display_load.value
    if load.kind is SourceLoadKind.RESISTIVE:
        assert load.resistance_ohm is not None
        return Observed.value_of(
            TerminationSpec(
                TerminationKind.RESISTIVE,
                ResistanceBounds(load.resistance_ohm, load.resistance_ohm),
            ),
            evidence_refs=output.display_load.evidence_refs,
        )
    if load.kind is SourceLoadKind.HIGH_IMPEDANCE:
        return Observed.value_of(
            TerminationSpec(TerminationKind.HIGH_IMPEDANCE),
            evidence_refs=output.display_load.evidence_refs,
        )
    blockers.add(SourceBudgetBlockerCode.DISPLAY_LOAD_UNSUPPORTED)
    return Observed.missing(Availability.UNKNOWN, SourceReasonCode.SUPPORT_UNKNOWN)


def _conversion_factor(
    *,
    basis: VoltageReferenceBasis,
    source_resistance: ResistanceBounds,
    display_load: Observed[TerminationSpec],
    actual_termination: Observed[TerminationSpec],
    blockers: set[SourceBudgetBlockerCode],
) -> tuple[float, float] | None:
    if actual_termination.availability is not Availability.VALUE:
        return None
    actual = actual_termination.value
    if actual.kind is not TerminationKind.RESISTIVE or actual.resistance_bounds is None:
        blockers.add(SourceBudgetBlockerCode.TERMINATION_NOT_RESISTIVE)
        return None
    if basis is VoltageReferenceBasis.DELIVERED_INTO_DISPLAY_LOAD:
        if display_load.availability is not Availability.VALUE:
            blockers.add(SourceBudgetBlockerCode.DISPLAY_LOAD_UNAVAILABLE)
            return None
        display = display_load.value
        if display.kind is not TerminationKind.RESISTIVE or display.resistance_bounds is None:
            blockers.add(SourceBudgetBlockerCode.DISPLAY_LOAD_UNSUPPORTED)
            return None
        factors = tuple(
            ((source + shown) / shown) * (actual_load / (source + actual_load))
            for source in _endpoints(source_resistance)
            for shown in _endpoints(display.resistance_bounds)
            for actual_load in _endpoints(actual.resistance_bounds)
        )
    else:
        factors = tuple(
            actual_load / (source + actual_load)
            for source in _endpoints(source_resistance)
            for actual_load in _endpoints(actual.resistance_bounds)
        )
    if not factors or any(not isfinite(item) or item <= 0 for item in factors):
        blockers.add(SourceBudgetBlockerCode.SOURCE_RESISTANCE_MISSING)
        return None
    return min(factors), max(factors)


def _combine_source_channels(
    request: SourceOutputBudgetRequest,
    blockers: set[SourceBudgetBlockerCode],
) -> set[int]:
    """Return waveform sources proven to feed the target's physical port.

    M3 only handles explicitly mapped ``INTERNAL_WAVEFORM`` edges.  A generic
    output-port or otherwise unknown Combine path cannot be safely translated
    through one physical termination, so it remains a blocker until a later
    feature-specific contract adds its electrical model.
    """

    target = request.target_channel
    sources = {target}
    if not _feature_declared(request, SourceFeature.COMBINE):
        return sources
    if request.snapshot.cross_channel.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.COMBINE_STATE_UNAVAILABLE)
        return sources
    cross_channel = request.snapshot.cross_channel.value
    declared_sets = {
        feature.channels
        for feature in request.descriptor_extensions.features
        if feature.feature is SourceFeature.COMBINE
        and feature.support is not SupportState.UNSUPPORTED
    }
    relations = tuple(
        relation for relation in cross_channel.relations if relation.feature is SourceFeature.COMBINE
    )
    if not declared_sets <= {relation.channels for relation in relations}:
        blockers.add(SourceBudgetBlockerCode.COMBINE_STATE_UNAVAILABLE)
        return sources
    if any(relation.enabled.availability is not Availability.VALUE for relation in relations):
        blockers.add(SourceBudgetBlockerCode.COMBINE_STATE_UNAVAILABLE)
        return sources
    active_relations = tuple(relation for relation in relations if relation.enabled.value)
    if not active_relations:
        return sources
    if cross_channel.relation_graph.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.COMBINE_STATE_UNAVAILABLE)
        return sources
    graph = cross_channel.relation_graph.value
    active_edges = tuple(
        edge
        for edge in graph.edges
        if edge.feature is SourceFeature.COMBINE
        and any(
            set(edge.sources + edge.targets) <= set(relation.channels)
            for relation in active_relations
        )
    )
    changed = True
    while changed:
        changed = False
        for edge in active_edges:
            if not set(edge.targets) & sources:
                continue
            if edge.signal_path is not SourceSignalPathKind.INTERNAL_WAVEFORM:
                blockers.add(SourceBudgetBlockerCode.COMBINE_PATH_UNSUPPORTED)
                continue
            before = len(sources)
            sources.update(edge.sources)
            changed = changed or len(sources) != before

    # If an enabled relation claims the target as a destination but the graph
    # supplies no active compatible edge, there is no trustworthy way to know
    # which waveform reaches the port.
    for relation in active_relations:
        if target not in relation.channels:
            continue
        destination_edges = tuple(
            edge
            for edge in active_edges
            if target in edge.targets and set(edge.sources + edge.targets) <= set(relation.channels)
        )
        if not destination_edges:
            blockers.add(SourceBudgetBlockerCode.COMBINE_STATE_UNAVAILABLE)
    return sources


def _active_direct_channels(
    request: SourceOutputBudgetRequest,
    *,
    channels: dict[int, SourceChannelStateV2],
    blockers: set[SourceBudgetBlockerCode],
) -> set[int]:
    active = {request.target_channel, *request.projected_active_channels}
    shared_power_declared = _feature_declared(request, SourceFeature.SHARED_POWER)
    for channel in request.snapshot.channels:
        if channel.output.availability is not Availability.VALUE:
            if shared_power_declared:
                blockers.add(SourceBudgetBlockerCode.SHARED_POWER_STATE_UNAVAILABLE)
            continue
        if channel.output.value.enabled.availability is not Availability.VALUE:
            if shared_power_declared:
                blockers.add(SourceBudgetBlockerCode.SHARED_POWER_STATE_UNAVAILABLE)
            continue
        if channel.output.value.enabled.value:
            active.add(channel.channel)
    if any(channel not in channels for channel in active):
        blockers.add(SourceBudgetBlockerCode.ACTIVE_CHANNEL_UNKNOWN)
    return active


def _evaluate_shared_power(
    request: SourceOutputBudgetRequest,
    *,
    channels: dict[int, SourceChannelStateV2],
    active_direct_channels: set[int],
    combine_sources: set[int],
    main_evaluations: dict[int, _ChannelPortEvaluation],
) -> _SharedPowerEvaluation:
    if not _feature_declared(request, SourceFeature.SHARED_POWER):
        return _SharedPowerEvaluation(_not_applicable(), (), (), BudgetProofStrength.HARD_CONSERVATIVE)

    blockers: set[SourceBudgetBlockerCode] = set()
    contributors: list[SafetyContributor] = []
    if len(combine_sources) > 1:
        # A shared-power device must provide a dedicated envelope for internal
        # Combine.  Target-port voltage alone cannot bound each amplifier's
        # supply power, so M3 deliberately does not extrapolate it.
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
        return _SharedPowerEvaluation(_missing(), (), tuple(sorted(blockers, key=lambda x: x.value)), BudgetProofStrength.INCOMPLETE)
    if request.snapshot.cross_channel.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_STATE_UNAVAILABLE)
        return _SharedPowerEvaluation(_missing(), (), tuple(sorted(blockers, key=lambda x: x.value)), BudgetProofStrength.INCOMPLETE)
    shared_observed = request.snapshot.cross_channel.value.shared_power
    if shared_observed.availability is not Availability.VALUE:
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_STATE_UNAVAILABLE)
        return _SharedPowerEvaluation(_missing(), (), tuple(sorted(blockers, key=lambda x: x.value)), BudgetProofStrength.INCOMPLETE)
    shared_state = shared_observed.value
    if (
        shared_state.active_power_upper_w.availability is not Availability.VALUE
        or shared_state.hard_limit_w.availability is not Availability.VALUE
    ):
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_STATE_UNAVAILABLE)
        return _SharedPowerEvaluation(_missing(), (), tuple(sorted(blockers, key=lambda x: x.value)), BudgetProofStrength.INCOMPLETE)
    if not active_direct_channels <= set(shared_state.participants):
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)

    evaluations: dict[int, _ChannelPortEvaluation] = dict(main_evaluations)
    for channel_id in sorted(active_direct_channels):
        if channel_id not in channels:
            blockers.add(SourceBudgetBlockerCode.ACTIVE_CHANNEL_UNKNOWN)
            continue
        if channel_id not in evaluations:
            evaluations[channel_id] = _evaluate_channel_port(
                request,
                channel=channels[channel_id],
                physical_port_channel=channel_id,
            )
            contributors.extend(evaluations[channel_id].contributors)
        evaluation = evaluations[channel_id]
        blockers.update(evaluation.blockers)

    facts = tuple(
        evaluations[channel_id].facts
        for channel_id in sorted(active_direct_channels)
        if channel_id in evaluations and evaluations[channel_id].facts is not None
    )
    if len(facts) != len(active_direct_channels):
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
    constraints = tuple(
        item
        for item in request.descriptor_extensions.safety_profile.constraints
        if item.kind is SourceSafetyConstraintKind.SHARED_POWER
        and isinstance(item.profile, SourceSharedPowerConstraint)
        and item.proof_strength is BudgetProofStrength.HARD_CONSERVATIVE
        and active_direct_channels <= set(item.profile.participants)
        and all(
            _applicability_matches(
                item.applicability,
                request.snapshot.runtime_profile.identity,
                fact,
            )
            for fact in facts
        )
    )
    if not constraints:
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
        return _SharedPowerEvaluation(_missing(), tuple(contributors), tuple(sorted(blockers, key=lambda x: x.value)), BudgetProofStrength.INCOMPLETE)

    descriptor_limit = min(item.profile.maximum_power_w for item in constraints)
    runtime_limit = shared_state.hard_limit_w.value
    if runtime_limit > descriptor_limit:
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
    effective_limit = min(descriptor_limit, runtime_limit)

    projected = 0.0
    for channel_id in active_direct_channels:
        evaluation = evaluations.get(channel_id)
        if evaluation is None or evaluation.bounds is None:
            blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
            continue
        power = _power_upper(evaluation)
        if power is None:
            blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
            continue
        projected += power
    if not isfinite(projected):
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_CONSTRAINT_MISSING)
        return _SharedPowerEvaluation(_missing(), tuple(contributors), tuple(sorted(blockers, key=lambda x: x.value)), BudgetProofStrength.INCOMPLETE)
    projected = max(projected, shared_state.active_power_upper_w.value)
    budget = SourceSharedPowerBudget(
        participants=tuple(sorted(active_direct_channels)),
        observed_active_power_upper_w=shared_state.active_power_upper_w.value,
        projected_power_upper_w=projected,
        effective_hard_limit_w=effective_limit,
        constraint_ids=tuple(sorted(item.constraint_id for item in constraints)),
        evidence_sources=(
            BudgetEvidenceSource.DEVICE_HARD_LIMIT,
            BudgetEvidenceSource.INSTRUMENT_READBACK,
        ),
    )
    if (
        shared_state.active_power_upper_w.value > effective_limit
        or projected > effective_limit
    ):
        blockers.add(SourceBudgetBlockerCode.SHARED_POWER_LIMIT_EXCEEDED)
    proof = BudgetProofStrength.HARD_CONSERVATIVE
    if blockers:
        proof = BudgetProofStrength.INCOMPLETE
    return _SharedPowerEvaluation(
        Observed.value_of(budget),
        tuple(contributors),
        tuple(sorted(blockers, key=lambda x: x.value)),
        proof,
    )


def _power_upper(evaluation: _ChannelPortEvaluation) -> float | None:
    if (
        evaluation.bounds is None
        or evaluation.bounds.rms_upper_v is None
        or evaluation.actual_termination.availability is not Availability.VALUE
    ):
        return None
    termination = evaluation.actual_termination.value
    if termination.kind is not TerminationKind.RESISTIVE or termination.resistance_bounds is None:
        return None
    value = (evaluation.bounds.rms_upper_v**2) / termination.resistance_bounds.minimum_ohm
    return value if isfinite(value) and value >= 0.0 else None


def _feature_declared(request: SourceOutputBudgetRequest, feature: SourceFeature) -> bool:
    return any(
        item.feature is feature and item.support is not SupportState.UNSUPPORTED
        for item in request.descriptor_extensions.features
    )


def _constraint_observed(
    constraint: SourceSafetyConstraint | None,
    expected_type: type[object],
    value_getter,
) -> Observed[object]:
    if constraint is None or not isinstance(constraint.profile, expected_type):
        return _missing()
    return Observed.value_of(value_getter(constraint.profile), evidence_refs=constraint.evidence_refs)


def _cover_frequency_range(
    bands: tuple[SourceFrequencyDeratingBand, ...],
    minimum: float,
    maximum: float,
) -> tuple[bool, float]:
    current = minimum
    maximum_gain = 1.0
    for band in bands:
        if band.frequency_hz.maximum < current:
            continue
        if band.frequency_hz.minimum > current:
            return False, maximum_gain
        maximum_gain = max(maximum_gain, band.gain_upper)
        current = max(current, band.frequency_hz.maximum)
        if current >= maximum:
            return True, maximum_gain
    return False, maximum_gain


def _sum_evaluation_bounds(
    evaluations: tuple[_ChannelPortEvaluation, ...],
) -> PortVoltageBounds | None:
    bounds: PortVoltageBounds | None = None
    for evaluation in evaluations:
        if evaluation.bounds is None:
            return None
        bounds = evaluation.bounds if bounds is None else _sum_bounds(bounds, evaluation.bounds)
    return bounds


def _sum_bounds(first: PortVoltageBounds, second: PortVoltageBounds) -> PortVoltageBounds:
    return _make_bounds(
        first.minimum_v_lower + second.minimum_v_lower,
        first.maximum_v_upper + second.maximum_v_upper,
    )


def _make_bounds(minimum: float, maximum: float) -> PortVoltageBounds:
    absolute = max(abs(minimum), abs(maximum))
    return PortVoltageBounds(
        minimum_v_lower=minimum,
        maximum_v_upper=maximum,
        vpp_upper_v=maximum - minimum,
        absolute_peak_upper_v=absolute,
        # Peak is a conservative upper bound for RMS when no waveform-specific
        # hard RMS model is available.
        rms_upper_v=absolute,
    )


def _scaled_interval(
    interval: tuple[float, float],
    factor: tuple[float, float],
) -> tuple[float, float]:
    values = tuple(left * right for left in interval for right in factor)
    return min(values), max(values)


def _endpoints(bounds: ResistanceBounds) -> tuple[float, float]:
    return bounds.minimum_ohm, bounds.maximum_ohm


def _contributor(
    *,
    contributor_id: str,
    feature: SourceFeature,
    channels: tuple[int, ...],
    minimum: float,
    maximum: float,
    constraint_ids: tuple[str, ...],
    proof_strength: BudgetProofStrength,
) -> SafetyContributor:
    return SafetyContributor(
        contributor_id=contributor_id,
        feature=feature,
        channels=channels,
        minimum_v=minimum,
        maximum_v=maximum,
        constraint_ids=constraint_ids,
        proof_strength=proof_strength,
        evidence_sources=(BudgetEvidenceSource.INSTRUMENT_READBACK,),
    )


def _bounds_observed(bounds: PortVoltageBounds | None) -> Observed[PortVoltageBounds]:
    return Observed.value_of(bounds) if bounds is not None else _missing()


def _channel_incomplete(
    *,
    channel: int,
    physical_port_channel: int,
    facts: _ChannelFacts | None,
    blockers: set[SourceBudgetBlockerCode],
    actual_termination: _TerminationResolution,
    voltage_reference: Observed[VoltageReferenceBasis] | None = None,
    display_load: Observed[TerminationSpec] | None = None,
    source_resistance: Observed[ResistanceBounds] | None = None,
    contributors: list[SafetyContributor] | None = None,
    proof: BudgetProofStrength = BudgetProofStrength.INCOMPLETE,
) -> _ChannelPortEvaluation:
    return _ChannelPortEvaluation(
        channel=channel,
        physical_port_channel=physical_port_channel,
        facts=facts,
        bounds=None,
        contributors=tuple(contributors or ()),
        blockers=tuple(sorted(blockers, key=lambda item: item.value)),
        proof_strength=proof,
        voltage_reference_basis=voltage_reference or _missing(),
        display_load=display_load or _missing(),
        source_resistance=source_resistance or _missing(),
        actual_termination=actual_termination.observed,
        actual_termination_evidence_source=actual_termination.evidence_source,
    )


def _incomplete_budget(*, blockers: set[SourceBudgetBlockerCode]) -> CompositeOutputBudget:
    missing = _missing()
    return CompositeOutputBudget(
        bounds=missing,
        voltage_reference_basis=missing,
        display_load=missing,
        output_source_resistance=missing,
        actual_termination=missing,
        shared_power=missing,
        proof_strength=BudgetProofStrength.INCOMPLETE,
        evidence_sources=(),
        contributors=(),
        blockers=tuple(sorted(blockers, key=lambda item: item.value)),
    )


def _evidence_sources(
    contributors: list[SafetyContributor],
    shared_power: Observed[SourceSharedPowerBudget],
) -> tuple[BudgetEvidenceSource, ...]:
    values = {
        source
        for contributor in contributors
        for source in contributor.evidence_sources
    }
    if shared_power.availability is Availability.VALUE:
        values.update(shared_power.value.evidence_sources)
    return tuple(sorted(values, key=lambda item: item.value))


def _missing() -> Observed:
    return Observed.missing(Availability.NOT_QUERIED, SourceReasonCode.NOT_REQUESTED)


def _not_applicable() -> Observed[SourceSharedPowerBudget]:
    return Observed.missing(Availability.NOT_APPLICABLE, SourceReasonCode.DESCRIPTOR_UNSUPPORTED)


def _strongest_nonhard(values) -> BudgetProofStrength:
    available = tuple(values)
    if BudgetProofStrength.STATISTICAL_ONLY in available:
        return BudgetProofStrength.STATISTICAL_ONLY
    if BudgetProofStrength.MEASURED_ONLY in available:
        return BudgetProofStrength.MEASURED_ONLY
    return BudgetProofStrength.INCOMPLETE


def _weaker_proof(*values: BudgetProofStrength) -> BudgetProofStrength:
    ranks = {
        BudgetProofStrength.HARD_CONSERVATIVE: 3,
        BudgetProofStrength.STATISTICAL_ONLY: 2,
        BudgetProofStrength.MEASURED_ONLY: 1,
        BudgetProofStrength.INCOMPLETE: 0,
    }
    return min(values, key=lambda item: ranks[item])


def _context_channel(target) -> int:
    assert target.channel is not None
    return target.channel


def _require_channel(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")


def _require_channels(values: object, label: str, *, allow_empty: bool) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if not allow_empty and not values:
        raise ValueError(f"{label} must not be empty")
    for value in values:
        _require_channel(value, label)
    if len(set(values)) != len(values) or tuple(sorted(values)) != values:
        raise ValueError(f"{label} must be sorted and unique")


__all__ = ["SourceOutputBudgetRequest", "evaluate_source_output_budget"]
