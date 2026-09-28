from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    SafetyLimitsConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import ConfigError
from wavebench.instruments.capabilities import validate_declared_capabilities
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Observed,
    OutputFacet,
    SourceArbitraryWorkspaceCapabilityProfile,
    SourceArbitraryWorkspaceVolatileReplaceRequest,
    SourceArbitraryWorkspaceVolatileReplaceResult,
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
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryContract,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceRuntimeIdentity,
    SourceSafetyProfile,
    SourceTopologyContract,
    SourceTypedObservation,
    SourceWaveformKind,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, missing, source_descriptor


class _TextTransport:
    resource = "fake-source-arbitrary-workspace-v2"

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del command, replay
        return "ok"

    def write(self, command: str) -> None:
        del command

    def write_bytes(self, command: bytes) -> None:
        del command

    def close(self) -> None:
        pass


def _digest(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _output(enabled: bool) -> OutputFacet:
    return OutputFacet(
        enabled=Observed.value_of(enabled),
        display_load=missing(),
        polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
    )


class _WorkspaceDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        outputs: dict[int, bool] | None = None,
        postcondition_mismatch: bool = False,
        write_error: bool = False,
        invalid_recovery_result_channels: tuple[int, ...] = (),
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.outputs = {1: False, 2: False} if outputs is None else dict(outputs)
        self.postcondition_mismatch = postcondition_mismatch
        self.write_error = write_error
        self.invalid_recovery_result_channels = frozenset(invalid_recovery_result_channels)
        self.workspace_requests: list[
            tuple[SourceArbitraryWorkspaceVolatileReplaceRequest, bytes]
        ] = []
        self.output_requests: list[SourceOutputRequest] = []

    def close(self) -> None:
        self.transport.close()

    def execute_source_query_plan_v2(self, plan) -> SourceQueryExecutionRecord:
        records = []
        for item in plan.items:
            if item.phase.value == "anchor_before":
                self.transport.query("SOURCE:STATE?")
            observations = []
            for field in item.fields:
                if field.field is SourceFieldId.IDENTITY:
                    value = SourceRuntimeIdentity(
                        manufacturer="Example",
                        model="EX2",
                        firmware_id="1.0",
                    )
                elif field.field is SourceFieldId.BASIC:
                    value = basic_facet()
                elif field.field is SourceFieldId.OUTPUT:
                    assert field.target.channel is not None
                    enabled = self.outputs[field.target.channel]
                    if (
                        self.postcondition_mismatch
                        and self.workspace_requests
                        and not self.output_requests
                        and field.target.channel == 2
                    ):
                        enabled = True
                    value = _output(enabled)
                else:  # pragma: no cover - the descriptor has no other read fields.
                    raise AssertionError(field)
                observations.append(SourceTypedObservation(field, value))
            records.append(
                SourceProtocolQueryRecord(
                    item_id=item.item_id,
                    effect=item.effect,
                    outcome=SourceQueryItemOutcome.OBSERVED,
                    query_count=(1 if item.phase.value == "anchor_before" else 0),
                    observations=tuple(observations),
                )
            )
        return SourceQueryExecutionRecord(
            contract_version=SOURCE_CONTRACT_VERSION,
            plan_id=plan.plan_id,
            items=tuple(records),
            query_count=sum(record.query_count for record in records),
            device_revision_token_before="revision-1",
            device_revision_token_after="revision-1",
        )

    def replace_source_arbitrary_workspace_volatile_v2(
        self,
        request: SourceArbitraryWorkspaceVolatileReplaceRequest,
        payload: bytes,
    ) -> SourceArbitraryWorkspaceVolatileReplaceResult:
        self.transport.write_bytes(payload)
        self.workspace_requests.append((request, payload))
        if self.write_error:
            raise ConfigError("fake workspace binary write result is unknown")
        return SourceArbitraryWorkspaceVolatileReplaceResult(
            workspace_id="volatile",
            payload_sha256=request.payload_sha256,
            payload_size_bytes=request.payload_size_bytes,
            point_count=request.point_count,
            write_completed=True,
            content_readback_verified=False,
            previous_content_restorable=False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.outputs[request.channel] = request.enabled
        if request.enabled:
            raise AssertionError("the workspace fixture only uses recovery OFF")
        if request.channel in self.invalid_recovery_result_channels:
            return SourceOutputResult(channel=request.channel + 10, enabled=False)
        return SourceOutputResult(channel=request.channel, enabled=False)


def _extensions() -> SourceDescriptorExtensions:
    applicability = SourceConstraintApplicability()
    return SourceDescriptorExtensions(
        contract_version=SOURCE_CONTRACT_VERSION,
        topology=SourceTopologyContract((1, 2)),
        features=(
            SourceFeatureCapability(
                feature=SourceFeature.ARBITRARY_WORKSPACE,
                support=SupportState.SUPPORTED,
                directions=(SourceFeatureDirection.CONFIGURE,),
                scope=SourceFacetScope.INSTRUMENT,
                channels=(),
                applicability=applicability,
                profile=SourceArbitraryWorkspaceCapabilityProfile(
                    workspace_id="volatile",
                    volatile_replace_min_points=2,
                    volatile_replace_max_points=16_384,
                    volatile_replace_max_payload_bytes=32_768,
                ),
            ),
            *(
                SourceFeatureCapability(
                    feature=SourceFeature.BASIC,
                    support=SupportState.SUPPORTED,
                    directions=(SourceFeatureDirection.READ,),
                    scope=SourceFacetScope.CHANNEL,
                    channels=(channel,),
                    applicability=applicability,
                    profile=SourceBasicCapabilityProfile(
                        waveform_kinds=(SourceWaveformKind.SINE,),
                        frequency_modes=(SourceFrequencyMode.FIXED,),
                        amplitude_units=(SourceAmplitudeUnit.VPP,),
                        offset_readable=False,
                        phase_readable=False,
                        square_duty_readable=False,
                    ),
                )
                for channel in (1, 2)
            ),
            *(
                SourceFeatureCapability(
                    feature=SourceFeature.OUTPUT,
                    support=SupportState.SUPPORTED,
                    directions=(
                        SourceFeatureDirection.DISABLE,
                        SourceFeatureDirection.ENABLE,
                        SourceFeatureDirection.READ,
                    ),
                    scope=SourceFacetScope.CHANNEL,
                    channels=(channel,),
                    applicability=applicability,
                    profile=SourceOutputCapabilityProfile(
                        output_readable=True,
                        display_load_readable=False,
                        polarity_readable=True,
                    ),
                )
                for channel in (1, 2)
            ),
        ),
        query_contract=SourceQueryContract(
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
            max_queries=16,
            timeout_ms=2_000,
        ),
        safety_profile=SourceSafetyProfile(),
    )


def _config() -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DMAX"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("wavebench.toml"),
        source=SourceConfig("example.source-v2", "TCPIP::source::INSTR", 1, False, True, 0),
        safety_limits=SafetyLimitsConfig(),
    )


def _service(
    *,
    outputs: dict[int, bool] | None = None,
    postcondition_mismatch: bool = False,
    write_error: bool = False,
    invalid_recovery_result_channels: tuple[int, ...] = (),
) -> tuple[SourceService, _WorkspaceDriver]:
    session_state = InstrumentSessionState(epoch_id="source-arbitrary-workspace-v2")
    driver = _WorkspaceDriver(
        session_state=session_state,
        outputs=outputs,
        postcondition_mismatch=postcondition_mismatch,
        write_error=write_error,
        invalid_recovery_result_channels=invalid_recovery_result_channels,
    )
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_extensions()),
        capabilities=(
            "source.snapshot_v2",
            "source.output_v2",
            "source.arbitrary_workspace_volatile_replace_v2",
        ),
    )
    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, driver)
    return (
        SourceService(
            config=_config(),
            logger=CommandLogger(),
            session=driver,  # type: ignore[arg-type]
            descriptor=descriptor,
            transport=driver.transport,
            session_state=session_state,
        ),
        driver,
    )


def _request(payload: bytes) -> SourceArbitraryWorkspaceVolatileReplaceRequest:
    return SourceArbitraryWorkspaceVolatileReplaceRequest(
        payload_sha256=_digest(payload),
        payload_size_bytes=len(payload),
        point_count=len(payload) // 2,
    )


def test_workspace_result_cannot_claim_previous_content_is_restorable() -> None:
    payload = b"\x00\x00\xff\x3f"

    with pytest.raises(ValueError, match="cannot claim previous content is restorable"):
        SourceArbitraryWorkspaceVolatileReplaceResult(
            workspace_id="volatile",
            payload_sha256=_digest(payload),
            payload_size_bytes=len(payload),
            point_count=len(payload) // 2,
            write_completed=True,
            content_readback_verified=False,
            previous_content_restorable=True,
        )


def test_workspace_capability_requires_off_support_on_every_topology_channel() -> None:
    extensions = replace(
        _extensions(),
        features=tuple(
            replace(feature, directions=(SourceFeatureDirection.READ,))
            if feature.feature is SourceFeature.OUTPUT and feature.channels == (2,)
            else feature
            for feature in _extensions().features
        ),
    )
    session_state = InstrumentSessionState(epoch_id="workspace-missing-off-support")
    driver = _WorkspaceDriver(session_state=session_state)
    descriptor = replace(
        source_descriptor(driver=driver, extensions=extensions),
        capabilities=(
            "source.snapshot_v2",
            "source.output_v2",
            "source.arbitrary_workspace_volatile_replace_v2",
        ),
    )

    with pytest.raises(ConfigError, match="output DISABLE support on every topology channel"):
        validate_source_descriptor(descriptor)


def test_workspace_volatile_replace_writes_once_without_claiming_a_channel() -> None:
    service, driver = _service()
    payload = b"\x00\x00\xff\x3f"
    request = _request(payload)

    result, artifact = service.replace_arbitrary_workspace_volatile_v2(
        request,
        payload=payload,
        correlation_id="arb-workspace",
    )

    assert driver.workspace_requests == [(request, payload)]
    assert driver.output_requests == []
    assert driver.transport.counters.binary_write_completed == 1
    assert result.workspace_id == "volatile"
    assert artifact["operation"] == "source.arbitrary_workspace_volatile_replace_v2"
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "topology_outputs_expected": "off",
        "channel_selection": "unverified",
        "content_readback_verified": False,
        "previous_content": "unrecoverable",
    }
    assert payload.hex() not in repr(artifact)
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]


def test_workspace_volatile_replace_requires_every_output_off_before_binary_write() -> None:
    service, driver = _service(outputs={1: False, 2: True})
    payload = b"\x00\x00\xff\x3f"

    with pytest.raises(ConfigError, match="every topology output OFF"):
        service.replace_arbitrary_workspace_volatile_v2(_request(payload), payload=payload)

    assert driver.workspace_requests == []
    assert driver.transport.counters.binary_write_requests == 0
    assert driver.output_requests == []


@pytest.mark.parametrize(
    ("postcondition_mismatch", "write_error", "message"),
    (
        (True, False, "postcondition reports an output ON"),
        (False, True, "workspace binary write result is unknown"),
    ),
)
def test_workspace_volatile_replace_recovers_every_output_after_main_failure(
    postcondition_mismatch: bool,
    write_error: bool,
    message: str,
) -> None:
    service, driver = _service(
        postcondition_mismatch=postcondition_mismatch,
        write_error=write_error,
    )
    payload = b"\x00\x00\xff\x3f"

    with pytest.raises(ConfigError, match=message) as raised:
        service.replace_arbitrary_workspace_volatile_v2(_request(payload), payload=payload)

    artifact = raised.value.source_operation_artifact
    assert driver.transport.counters.binary_write_requests == 1
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=False),
        SourceOutputRequest(channel=2, enabled=False),
    ]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "channels": [1, 2],
        "session_health": "uncertain",
    }


def test_workspace_recovery_continues_after_an_unproven_off_result() -> None:
    service, driver = _service(
        write_error=True,
        invalid_recovery_result_channels=(1,),
    )
    payload = b"\x00\x00\xff\x3f"

    with pytest.raises(ConfigError, match="workspace binary write result is unknown") as raised:
        service.replace_arbitrary_workspace_volatile_v2(_request(payload), payload=payload)

    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=False),
        SourceOutputRequest(channel=2, enabled=False),
    ]
    assert raised.value.source_operation_artifact["recovery"] == {
        "status": "off_failed",
        "channels": [1, 2],
        "attempted_channels": [1, 2],
        "failed_channels": [1],
        "session_health": "uncertain",
    }
