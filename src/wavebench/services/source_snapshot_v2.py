"""Core-owned planning and validation for Source V2 snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import time
from uuid import uuid4

from wavebench.errors import ConfigError
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    ArbitraryFacet,
    Availability,
    BasicWaveFacet,
    BurstFacet,
    HarmonicFacet,
    ModulationFacet,
    NoiseOverlayFacet,
    Observed,
    OutputFacet,
    SnapshotConsistencyState,
    SourceActivationPredicate,
    SourceActivationRule,
    SourceAnchorField,
    SourceChannelStateV2,
    SourceCounterInputState,
    SourceCouplingCapabilityProfile,
    SourceCouplingDimensionState,
    SourceCouplingState,
    SourceCrossChannelStateV2,
    SourceDescriptorExtensions,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFieldId,
    SourceFieldRef,
    SourceNoiseOverlayCapabilityProfile,
    SourceQueryExecutionRecord,
    SourceQueryEffect,
    SourceQueryItemOutcome,
    SourceQueryPhase,
    SourceReasonCode,
    SourceRelationState,
    SourceRuntimeCapabilityProfile,
    SourceRuntimeIdentity,
    SourceScopeRef,
    SourceSemanticQueryItem,
    SourceSemanticQueryPlan,
    SourceSnapshotConsistency,
    SourceSnapshotV2,
    SourceSyncCapabilityProfile,
    SourceSyncState,
    SourceSystemStateV2,
    SourceTypedObservation,
    SweepFacet,
    source_snapshot_timestamp_utc,
    source_v2_digest,
)


SOURCE_SNAPSHOT_OPERATION_TIMEOUT_MS = 5_000


class SourceSnapshotContractError(ConfigError):
    code = "source_snapshot_contract_error"


@dataclass(frozen=True, slots=True)
class SourceSnapshotContext:
    context_id: str
    correlation_id: str
    session_epoch: str
    session_health_before: str
    descriptor_extensions: SourceDescriptorExtensions
    deadline_monotonic: float


def new_source_snapshot_context(
    *,
    session_epoch: str,
    session_health_before: str,
    descriptor_extensions: SourceDescriptorExtensions,
    timeout_ms: int,
    correlation_id: str | None = None,
) -> SourceSnapshotContext:
    if timeout_ms < 1:
        raise SourceSnapshotContractError("source snapshot timeout must be positive")
    return SourceSnapshotContext(
        context_id=uuid4().hex,
        correlation_id=correlation_id or uuid4().hex,
        session_epoch=session_epoch,
        session_health_before=session_health_before,
        descriptor_extensions=descriptor_extensions,
        deadline_monotonic=time.monotonic() + (timeout_ms / 1000.0),
    )


def build_source_snapshot_plan(context: SourceSnapshotContext) -> SourceSemanticQueryPlan:
    extensions = context.descriptor_extensions
    query_contract = extensions.query_contract
    items: list[SourceSemanticQueryItem] = []

    def append_item(
        *,
        phase: SourceQueryPhase,
        contract: SourceFacetQueryContract,
        target: SourceScopeRef,
        field_ids: tuple[SourceFieldId, ...],
    ) -> None:
        if not field_ids:
            return
        item_id = f"q{len(items) + 1:04d}"
        items.append(
            SourceSemanticQueryItem(
                item_id=item_id,
                phase=phase,
                feature=contract.feature,
                target=target,
                fields=tuple(
                    SourceFieldRef(field=field_id, target=target)
                    for field_id in field_ids
                ),
                activation_any=(
                    contract.activation_any if phase is SourceQueryPhase.FACET else ()
                ),
                required=(contract.required or phase is not SourceQueryPhase.FACET),
                effect=contract.effect,
                max_queries=contract.max_queries,
            )
        )

    for phase in (
        SourceQueryPhase.ANCHOR_BEFORE,
        SourceQueryPhase.FACET,
        SourceQueryPhase.ANCHOR_AFTER,
    ):
        for contract in query_contract.facets:
            anchor_ids = tuple(
                field for field in contract.fields if field in query_contract.anchor_fields
            )
            facet_ids = tuple(
                field for field in contract.fields if field not in query_contract.anchor_fields
            )
            field_ids = anchor_ids if phase is not SourceQueryPhase.FACET else facet_ids
            for target in _targets_for_contract(extensions, contract):
                append_item(
                    phase=phase,
                    contract=contract,
                    target=target,
                    field_ids=field_ids,
                )
    max_queries = sum(item.max_queries for item in items)
    if max_queries > query_contract.max_queries:
        raise SourceSnapshotContractError(
            "expanded Source V2 query plan exceeds descriptor max_queries"
        )
    return SourceSemanticQueryPlan(
        contract_version=SOURCE_CONTRACT_VERSION,
        plan_id=uuid4().hex,
        items=tuple(items),
        # The first accepted snapshot revision intentionally has no consuming reads.
        allowed_effects=(SourceQueryEffect.PURE_READ,),
        max_queries=query_contract.max_queries,
        deadline_monotonic=context.deadline_monotonic,
    )


def build_source_snapshot(
    *,
    context: SourceSnapshotContext,
    plan: SourceSemanticQueryPlan,
    execution: SourceQueryExecutionRecord,
    session_health_after: str,
    allow_uncertain_session: bool = False,
) -> SourceSnapshotV2:
    if time.monotonic() > plan.deadline_monotonic:
        raise SourceSnapshotContractError("source snapshot query deadline was exceeded")
    accepted_health = {"healthy", "uncertain"} if allow_uncertain_session else {"healthy"}
    if session_health_after not in accepted_health:
        raise SourceSnapshotContractError(
            "source snapshot session health changed before validation completed"
        )
    records = _validate_execution(plan, execution)
    before = _phase_observations(plan, records, SourceQueryPhase.ANCHOR_BEFORE)
    facet = _phase_observations(plan, records, SourceQueryPhase.FACET, anchors=before)
    after = _phase_observations(plan, records, SourceQueryPhase.ANCHOR_AFTER)

    identity_ref = SourceFieldRef(
        field=SourceFieldId.IDENTITY,
        target=SourceScopeRef(SourceFacetScope.INSTRUMENT),
    )
    identity_observed = before.get(identity_ref)
    if identity_observed is None or identity_observed.availability is not Availability.VALUE:
        raise SourceSnapshotContractError("source snapshot requires a readable runtime identity")
    if not isinstance(identity_observed.value, SourceRuntimeIdentity):
        raise SourceSnapshotContractError("source snapshot identity observation has an invalid type")
    identity = identity_observed.value
    runtime_features = _narrow_runtime_features(
        context.descriptor_extensions.features,
        identity,
    )
    runtime_profile = SourceRuntimeCapabilityProfile(
        session_epoch=context.session_epoch,
        descriptor_digest=source_v2_digest(context.descriptor_extensions),
        identity=identity,
        features=runtime_features,
    )

    values = dict(before)
    values.update(facet)
    consistency = _build_consistency(
        session_epoch=context.session_epoch,
        before=before,
        after=after,
        device_revision_token_before=execution.device_revision_token_before,
        device_revision_token_after=execution.device_revision_token_after,
    )
    channels = tuple(
        _channel_state(
            channel,
            values,
            runtime_features,
        )
        for channel in context.descriptor_extensions.topology.channels
    )
    system = Observed.value_of(
        _system_state(
            context.descriptor_extensions,
            values,
            runtime_features,
        )
    )
    cross_channel = Observed.value_of(
        _cross_channel_state(
            context.descriptor_extensions,
            values,
            runtime_features,
        )
    )
    return SourceSnapshotV2(
        snapshot_id=uuid4().hex,
        context_id=context.context_id,
        correlation_id=context.correlation_id,
        captured_at_utc=source_snapshot_timestamp_utc(),
        runtime_profile=runtime_profile,
        channels=channels,
        system=system,
        cross_channel=cross_channel,
        consistency=consistency,
        plan_digest=source_v2_digest(plan),
        query_count=execution.query_count,
        session_health_before=context.session_health_before,
        session_health_after=session_health_after,
    )


def _targets_for_contract(
    extensions: SourceDescriptorExtensions,
    contract: SourceFacetQueryContract,
) -> tuple[SourceScopeRef, ...]:
    if contract.scope is SourceFacetScope.INSTRUMENT:
        return (SourceScopeRef(SourceFacetScope.INSTRUMENT),)
    if contract.scope is SourceFacetScope.INPUT:
        return tuple(
            SourceScopeRef(SourceFacetScope.INPUT, input_id=input_id)
            for input_id in extensions.topology.input_ids
        )
    matching = tuple(
        feature
        for feature in extensions.features
        if feature.feature is contract.feature
        and feature.scope is contract.scope
        and feature.support.value == "supported"
    )
    if contract.scope is SourceFacetScope.CHANNEL:
        channels = sorted({channel for feature in matching for channel in feature.channels})
        return tuple(
            SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
            for channel in channels
        )
    channel_sets = sorted({feature.channels for feature in matching})
    return tuple(
        SourceScopeRef(SourceFacetScope.CHANNEL_SET, channels=channels)
        for channels in channel_sets
    )


def _validate_execution(
    plan: SourceSemanticQueryPlan,
    execution: SourceQueryExecutionRecord,
) -> dict[str, object]:
    if not isinstance(execution, SourceQueryExecutionRecord):
        raise SourceSnapshotContractError(
            "source driver returned an invalid query execution record"
        )
    if execution.plan_id != plan.plan_id:
        raise SourceSnapshotContractError("source query execution plan_id does not match")
    if execution.query_count > plan.max_queries:
        raise SourceSnapshotContractError("source query execution exceeded max_queries")
    expected_ids = tuple(item.item_id for item in plan.items)
    actual_ids = tuple(item.item_id for item in execution.items)
    if actual_ids != expected_ids:
        raise SourceSnapshotContractError(
            "source query execution records do not match the semantic plan"
        )
    records: dict[str, object] = {}
    for item, record in zip(plan.items, execution.items, strict=True):
        if record.effect is not item.effect or record.query_count > item.max_queries:
            raise SourceSnapshotContractError("source query execution exceeded an item contract")
        if record.outcome is SourceQueryItemOutcome.OBSERVED:
            expected_fields = set(item.fields)
            actual_fields = {observation.field for observation in record.observations}
            if actual_fields != expected_fields:
                raise SourceSnapshotContractError(
                    "source query execution observations do not match item fields"
                )
        elif item.required:
            raise SourceSnapshotContractError("a required Source V2 query item was not observed")
        records[item.item_id] = record
    return records


def _phase_observations(
    plan: SourceSemanticQueryPlan,
    records: dict[str, object],
    phase: SourceQueryPhase,
    *,
    anchors: dict[SourceFieldRef, Observed[object]] | None = None,
) -> dict[SourceFieldRef, Observed[object]]:
    values: dict[SourceFieldRef, Observed[object]] = {}
    for item in plan.items:
        if item.phase is not phase:
            continue
        record = records[item.item_id]
        outcome = getattr(record, "outcome")
        if outcome is SourceQueryItemOutcome.OBSERVED:
            for observation in getattr(record, "observations"):
                assert isinstance(observation, SourceTypedObservation)
                values[observation.field] = Observed.value_of(
                    observation.value,
                    evidence_refs=observation.evidence_refs,
                )
            continue
        reason = getattr(record, "reason_code")
        if outcome is SourceQueryItemOutcome.SEMANTIC_UNAVAILABLE:
            availability = Availability.UNAVAILABLE
        elif item.activation_any:
            active = _activation_state(item.activation_any, item.target, anchors or {})
            if active is None:
                raise SourceSnapshotContractError(
                    "source query item was skipped without proven activation state"
                )
            availability = Availability.NOT_QUERIED if active else Availability.NOT_APPLICABLE
            if active and reason is not SourceReasonCode.DRIVER_SKIPPED_OPTIONAL:
                raise SourceSnapshotContractError(
                    "active optional Source V2 query item used an invalid skip reason"
                )
            if not active and reason is not SourceReasonCode.INACTIVE_BY_ANCHOR:
                raise SourceSnapshotContractError(
                    "inactive Source V2 query item used an invalid skip reason"
                )
        else:
            availability = Availability.NOT_QUERIED
        for field in item.fields:
            values[field] = Observed.missing(availability, reason)
    return values


def _activation_state(
    rules: tuple[SourceActivationRule, ...],
    target: SourceScopeRef,
    anchors: dict[SourceFieldRef, Observed[object]],
) -> bool | None:
    unknown = False
    for rule in rules:
        matched = True
        for predicate in rule.predicates:
            actual = _anchor_predicate_value(predicate, target, anchors)
            if actual is None:
                unknown = True
                matched = False
                break
            if actual != predicate.equals:
                matched = False
                break
        if matched:
            return True
    return None if unknown else False


def _anchor_predicate_value(
    predicate: SourceActivationPredicate,
    target: SourceScopeRef,
    anchors: dict[SourceFieldRef, Observed[object]],
) -> object | None:
    mapping = {
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
    field_id = mapping[predicate.field]
    observed = anchors.get(SourceFieldRef(field=field_id, target=target))
    if observed is None or observed.availability is not Availability.VALUE:
        return None
    value = observed.value
    if predicate.field is SourceAnchorField.WAVEFORM_KIND and isinstance(value, BasicWaveFacet):
        return _observed_value(value.waveform_kind)
    if predicate.field is SourceAnchorField.FREQUENCY_MODE and isinstance(value, BasicWaveFacet):
        return _observed_value(value.frequency_mode)
    if predicate.field is SourceAnchorField.OUTPUT_ENABLED and isinstance(value, OutputFacet):
        return _observed_value(value.enabled)
    if predicate.field is SourceAnchorField.HARMONICS_ENABLED and isinstance(value, HarmonicFacet):
        return _observed_value(value.enabled)
    if predicate.field is SourceAnchorField.MODULATION_ENABLED and isinstance(value, ModulationFacet):
        return _observed_value(value.enabled)
    if predicate.field is SourceAnchorField.SWEEP_ENABLED and isinstance(value, SweepFacet):
        return _observed_value(value.enabled)
    if predicate.field is SourceAnchorField.BURST_ENABLED and isinstance(value, BurstFacet):
        return _observed_value(value.enabled)
    if (
        predicate.field is SourceAnchorField.ARBITRARY_PLAYBACK_MODE
        and isinstance(value, ArbitraryFacet)
    ):
        return _observed_value(value.playback_mode)
    if isinstance(value, (SourceRelationState, SourceCouplingState)):
        return _observed_value(value.enabled)
    return None


def _observed_value(observed: Observed[object]) -> object | None:
    return observed.value if observed.availability is Availability.VALUE else None


def _narrow_runtime_features(
    features: tuple[SourceFeatureCapability, ...],
    identity: SourceRuntimeIdentity,
) -> tuple[SourceFeatureCapability, ...]:
    narrowed = []
    identity_options = set(identity.option_ids)
    for feature in features:
        applicability = feature.applicability
        if applicability.models and identity.model not in applicability.models:
            continue
        if applicability.firmware_ids and identity.firmware_id not in applicability.firmware_ids:
            continue
        if applicability.option_ids and not set(applicability.option_ids) <= identity_options:
            continue
        narrowed.append(feature)
    return tuple(narrowed)


def _build_consistency(
    *,
    session_epoch: str,
    before: dict[SourceFieldRef, Observed[object]],
    after: dict[SourceFieldRef, Observed[object]],
    device_revision_token_before: str | None,
    device_revision_token_after: str | None,
) -> SourceSnapshotConsistency:
    anchor_fields = tuple(sorted(before, key=_field_ref_key))
    before_payload = _anchor_payload(anchor_fields, before)
    before_digest = source_v2_digest(before_payload)
    public_token_before = (
        None
        if device_revision_token_before is None
        else source_v2_digest(device_revision_token_before)
    )
    public_token_after = (
        None
        if device_revision_token_after is None
        else source_v2_digest(device_revision_token_after)
    )
    if set(before) != set(after) or any(
        observed.availability is not Availability.VALUE for observed in after.values()
    ):
        return SourceSnapshotConsistency(
            state=SnapshotConsistencyState.UNPROVEN,
            session_epoch=session_epoch,
            anchor_fields=anchor_fields,
            anchor_digest_before=before_digest,
            anchor_digest_after=None,
            device_revision_token_before=public_token_before,
            device_revision_token_after=public_token_after,
            reason_code=SourceReasonCode.CONSISTENCY_UNPROVEN,
        )
    after_digest = source_v2_digest(_anchor_payload(anchor_fields, after))
    if (device_revision_token_before is None) != (device_revision_token_after is None):
        return SourceSnapshotConsistency(
            state=SnapshotConsistencyState.UNPROVEN,
            session_epoch=session_epoch,
            anchor_fields=anchor_fields,
            anchor_digest_before=before_digest,
            anchor_digest_after=after_digest,
            device_revision_token_before=public_token_before,
            device_revision_token_after=public_token_after,
            reason_code=SourceReasonCode.CONSISTENCY_UNPROVEN,
        )
    tokens_match = not (
        device_revision_token_before is not None
        and device_revision_token_after is not None
        and device_revision_token_before != device_revision_token_after
    )
    if before_digest == after_digest and tokens_match:
        state = SnapshotConsistencyState.CONSISTENT
        reason = None
    else:
        state = SnapshotConsistencyState.DRIFTED
        reason = SourceReasonCode.CONSISTENCY_DRIFTED
    return SourceSnapshotConsistency(
        state=state,
        session_epoch=session_epoch,
        anchor_fields=anchor_fields,
        anchor_digest_before=before_digest,
        anchor_digest_after=after_digest,
        device_revision_token_before=public_token_before,
        device_revision_token_after=public_token_after,
        reason_code=reason,
    )


def _field_ref_key(value: SourceFieldRef) -> tuple[object, ...]:
    target = value.target
    return (
        value.field.value,
        target.scope.value,
        target.channel or 0,
        target.channels,
        target.input_id or "",
    )


def _anchor_payload(
    anchor_fields: tuple[SourceFieldRef, ...],
    values: dict[SourceFieldRef, Observed[object]],
) -> tuple[tuple[SourceFieldRef, object], ...]:
    payload = []
    for field in anchor_fields:
        observed = values[field]
        if observed.availability is not Availability.VALUE:
            raise SourceSnapshotContractError("source snapshot anchor was not observed")
        payload.append((field, observed.value))
    return tuple(payload)


def _channel_state(
    channel: int,
    values: dict[SourceFieldRef, Observed[object]],
    features: tuple[SourceFeatureCapability, ...],
) -> SourceChannelStateV2:
    target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
    sync = _field_value(
        values,
        SourceFieldRef(SourceFieldId.SYNC, target),
        features,
        SourceFeature.SYNC,
    )
    if sync.availability is Availability.VALUE:
        state = sync.value
        assert isinstance(state, SourceSyncState)
        profile = next(
            (
                feature.profile
                for feature in features
                if feature.feature is SourceFeature.SYNC
                and feature.scope is SourceFacetScope.CHANNEL
                and feature.channels == (channel,)
                and isinstance(feature.profile, SourceSyncCapabilityProfile)
            ),
            None,
        )
        if profile is None:
            raise SourceSnapshotContractError("source sync observation has no runtime profile")
        if state.source_channel.availability is Availability.VALUE and (
            not profile.source_channel_readable
            or state.source_channel.value not in profile.source_channels
        ):
            raise SourceSnapshotContractError(
                "source sync observation references an undeclared source channel"
            )
    noise_overlay = _field_value(
        values,
        SourceFieldRef(SourceFieldId.NOISE_OVERLAY, target),
        features,
        SourceFeature.NOISE_OVERLAY,
    )
    if noise_overlay.availability is Availability.VALUE:
        state = noise_overlay.value
        assert isinstance(state, NoiseOverlayFacet)
        profile = next(
            (
                feature.profile
                for feature in features
                if feature.feature is SourceFeature.NOISE_OVERLAY
                and feature.scope is SourceFacetScope.CHANNEL
                and feature.channels == (channel,)
                and isinstance(feature.profile, SourceNoiseOverlayCapabilityProfile)
            ),
            None,
        )
        if profile is None:
            raise SourceSnapshotContractError(
                "source noise overlay observation has no runtime profile"
            )
        if state.enabled.availability is Availability.VALUE and not profile.enabled_readable:
            raise SourceSnapshotContractError(
                "source noise overlay observation reports unreadable enabled state"
            )
        if state.scales.availability is Availability.VALUE and tuple(
            scale.kind for scale in state.scales.value
        ) != profile.scale_kinds:
            raise SourceSnapshotContractError(
                "source noise overlay observation scale kinds do not match runtime profile"
            )
    return SourceChannelStateV2(
        channel=channel,
        basic=_field_value(
            values,
            SourceFieldRef(SourceFieldId.BASIC, target),
            features,
            SourceFeature.BASIC,
        ),
        output=_field_value(
            values,
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            features,
            SourceFeature.OUTPUT,
        ),
        harmonics=_field_value(
            values,
            SourceFieldRef(SourceFieldId.HARMONICS, target),
            features,
            SourceFeature.HARMONICS,
        ),
        modulation=_field_value(
            values,
            SourceFieldRef(SourceFieldId.MODULATION, target),
            features,
            SourceFeature.MODULATION,
        ),
        sweep=_field_value(
            values,
            SourceFieldRef(SourceFieldId.SWEEP, target),
            features,
            SourceFeature.SWEEP,
        ),
        burst=_field_value(
            values,
            SourceFieldRef(SourceFieldId.BURST, target),
            features,
            SourceFeature.BURST,
        ),
        pulse=_field_value(
            values,
            SourceFieldRef(SourceFieldId.PULSE, target),
            features,
            SourceFeature.PULSE,
        ),
        arbitrary=_field_value(
            values,
            SourceFieldRef(SourceFieldId.ARBITRARY_SELECTION, target),
            features,
            SourceFeature.ARBITRARY,
        ),
        sync=sync,
        noise_overlay=noise_overlay,
    )


def _system_state(
    extensions: SourceDescriptorExtensions,
    values: dict[SourceFieldRef, Observed[object]],
    features: tuple[SourceFeatureCapability, ...],
) -> SourceSystemStateV2:
    counters = []
    for input_id in extensions.topology.input_ids:
        target = SourceScopeRef(SourceFacetScope.INPUT, input_id=input_id)
        observed = _field_value(
            values,
            SourceFieldRef(SourceFieldId.COUNTER, target),
            features,
            SourceFeature.COUNTER,
        )
        if observed.availability is Availability.VALUE:
            counters.append(observed.value)
        else:
            counters.append(_missing_counter_input_state(input_id, observed))
    instrument = SourceScopeRef(SourceFacetScope.INSTRUMENT)
    return SourceSystemStateV2(
        counters=tuple(counters),
        reference_clock=_field_value(
            values,
            SourceFieldRef(SourceFieldId.REFERENCE_CLOCK, instrument),
            features,
            SourceFeature.REFERENCE_CLOCK,
        ),
        cascade=_field_value(
            values,
            SourceFieldRef(SourceFieldId.CASCADE, instrument),
            features,
            SourceFeature.CASCADE,
        ),
    )


def _cross_channel_state(
    extensions: SourceDescriptorExtensions,
    values: dict[SourceFieldRef, Observed[object]],
    features: tuple[SourceFeatureCapability, ...],
) -> SourceCrossChannelStateV2:
    relations: list[SourceRelationState | SourceCouplingState] = []
    relation_fields = {
        SourceFeature.COMBINE: SourceFieldId.COMBINE,
        SourceFeature.TRACKING: SourceFieldId.TRACKING,
        SourceFeature.COUPLING: SourceFieldId.COUPLING,
        SourceFeature.COPY: SourceFieldId.COPY,
        SourceFeature.PHASE_RELATION: SourceFieldId.PHASE_RELATION,
    }
    for feature in features:
        field_id = relation_fields.get(feature.feature)
        if field_id is None or feature.scope is not SourceFacetScope.CHANNEL_SET:
            continue
        target = SourceScopeRef(SourceFacetScope.CHANNEL_SET, channels=feature.channels)
        observed = _field_value(
            values,
            SourceFieldRef(field_id, target),
            features,
            feature.feature,
        )
        if observed.availability is Availability.VALUE:
            relations.append(observed.value)
        elif feature.feature is SourceFeature.COUPLING:
            profile = feature.profile
            if not isinstance(profile, SourceCouplingCapabilityProfile):
                raise SourceSnapshotContractError(
                    "source coupling feature has an invalid runtime profile"
                )
            relations.append(
                SourceCouplingState(
                    feature=SourceFeature.COUPLING,
                    channels=feature.channels,
                    enabled=observed,
                    reference_channel=observed,
                    dimensions=tuple(
                        SourceCouplingDimensionState(
                            dimension=dimension,
                            enabled=observed,
                            parameter=observed,
                        )
                        for dimension in profile.dimensions
                    ),
                )
            )
        else:
            relations.append(
                SourceRelationState(
                    feature=feature.feature,
                    channels=feature.channels,
                    enabled=observed,
                )
            )
    relations.sort(key=lambda item: (item.feature.value, item.channels))
    instrument = SourceScopeRef(SourceFacetScope.INSTRUMENT)
    graph_ref = SourceFieldRef(SourceFieldId.RELATION_GRAPH, instrument)
    graph = values.get(graph_ref)
    if graph is None:
        graph = Observed.missing(
            Availability.NOT_QUERIED,
            SourceReasonCode.NOT_REQUESTED,
        )
    shared_power = _field_value(
        values,
        SourceFieldRef(SourceFieldId.SHARED_POWER, instrument),
        features,
        SourceFeature.SHARED_POWER,
    )
    return SourceCrossChannelStateV2(
        relations=tuple(relations),
        relation_graph=graph,
        shared_power=shared_power,
    )


def _missing_counter_input_state(
    input_id: str,
    observed: Observed[object],
) -> SourceCounterInputState:
    missing = Observed(
        availability=observed.availability,
        reason_code=observed.reason_code,
        evidence_refs=observed.evidence_refs,
    )
    return SourceCounterInputState(
        input_id=input_id,
        enabled=missing,
        measurements=missing,
        coupling=missing,
        impedance_ohm=missing,
        attenuation=missing,
        gate_time_s=missing,
        trigger_level_v=missing,
        statistics_enabled=missing,
    )


def _field_value(
    values: dict[SourceFieldRef, Observed[object]],
    field_ref: SourceFieldRef,
    features: tuple[SourceFeatureCapability, ...],
    feature_kind: SourceFeature,
) -> Observed[object]:
    matching = tuple(
        feature
        for feature in features
        if feature.feature is feature_kind
        and feature.scope is field_ref.target.scope
        and (
            field_ref.target.scope not in {
                SourceFacetScope.CHANNEL,
                SourceFacetScope.CHANNEL_SET,
            }
            or feature.channels
            == (
                (field_ref.target.channel,)
                if field_ref.target.scope is SourceFacetScope.CHANNEL
                else field_ref.target.channels
            )
        )
    )
    if not matching or all(feature.support.value == "unsupported" for feature in matching):
        return Observed.missing(
            Availability.UNSUPPORTED,
            SourceReasonCode.DESCRIPTOR_UNSUPPORTED,
        )
    if all(feature.support.value == "unknown" for feature in matching):
        return Observed.missing(
            Availability.UNKNOWN,
            SourceReasonCode.SUPPORT_UNKNOWN,
        )
    observed = values.get(field_ref)
    if observed is not None:
        return observed
    return Observed.missing(
        Availability.NOT_QUERIED,
        SourceReasonCode.NOT_REQUESTED,
    )


__all__ = [
    "SOURCE_SNAPSHOT_OPERATION_TIMEOUT_MS",
    "SourceSnapshotContext",
    "SourceSnapshotContractError",
    "build_source_snapshot",
    "build_source_snapshot_plan",
    "new_source_snapshot_context",
]
