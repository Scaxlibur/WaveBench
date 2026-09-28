from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavebench import cli
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
    Availability,
    Observed,
    OutputFacet,
    SourceCombineConfigureRequest,
    SourceConstraintApplicability,
    SourceCouplingCapabilityProfile,
    SourceCouplingConfigureRequest,
    SourceCouplingDimension,
    SourceCouplingDimensionState,
    SourceCouplingParameterKind,
    SourceCouplingState,
    SourceCrossChannelCapabilityProfile,
    SourceCrossChannelConfigureResult,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourcePhaseRelationConfigureRequest,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRelationEdge,
    SourceRelationGraph,
    SourceRelationOutputState,
    SourceRelationState,
    SourceRuntimeIdentity,
    SourceSignalPathKind,
    SourceTypedObservation,
    SourceTrackingConfigureRequest,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-cross-channel-v2"

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del command, replay
        return "ok"

    def write(self, command: str) -> None:
        del command

    def close(self) -> None:
        pass


class _CrossChannelWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        affected_output_on: bool = False,
        independent_output_on: bool = False,
        postcondition_mismatch: bool = False,
        linked_channel: bool = False,
        feature: SourceFeature = SourceFeature.COMBINE,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.outputs = {
            1: False,
            2: affected_output_on,
            3: independent_output_on,
        }
        self.postcondition_mismatch = postcondition_mismatch
        self.linked_channel = linked_channel
        self.feature = feature
        self.relation_enabled = False
        self.combine_requests: list[SourceCombineConfigureRequest] = []
        self.relation_requests: list[object] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_coupling_calls = 0

    def close(self) -> None:
        self.transport.close()

    def execute_source_query_plan_v2(self, plan) -> SourceQueryExecutionRecord:
        records = []
        for index, item in enumerate(plan.items):
            if index == 0:
                self.transport.query("SOURCE:STATE?")
            observations = []
            for field in item.fields:
                if field.field is SourceFieldId.IDENTITY:
                    value = SourceRuntimeIdentity(
                        manufacturer="Example",
                        model="EX1",
                        firmware_id="1.0",
                    )
                elif field.field is SourceFieldId.BASIC:
                    value = basic_facet()
                elif field.field is SourceFieldId.OUTPUT:
                    assert field.target.channel is not None
                    value = self._output(field.target.channel)
                elif field.field is _relation_field(self.feature):
                    value = self._relation(self.relation_enabled)
                elif field.field is SourceFieldId.RELATION_GRAPH:
                    value = self._graph()
                else:  # pragma: no cover - descriptor is intentionally narrow.
                    raise AssertionError(field)
                observations.append(SourceTypedObservation(field, value))
            records.append(
                SourceProtocolQueryRecord(
                    item_id=item.item_id,
                    effect=item.effect,
                    outcome=SourceQueryItemOutcome.OBSERVED,
                    query_count=(1 if index == 0 else 0),
                    observations=tuple(observations),
                )
            )
        return SourceQueryExecutionRecord(
            contract_version=SOURCE_CONTRACT_VERSION,
            plan_id=plan.plan_id,
            items=tuple(records),
            query_count=1,
        )

    def configure_source_combine_v2(
        self,
        request: SourceCombineConfigureRequest,
    ) -> SourceCrossChannelConfigureResult:
        return self._configure_relation(request)

    def configure_source_coupling_v2(
        self,
        request: SourceCouplingConfigureRequest,
    ) -> SourceCrossChannelConfigureResult:
        return self._configure_relation(request)

    def configure_source_tracking_v2(
        self,
        request: SourceTrackingConfigureRequest,
    ) -> SourceCrossChannelConfigureResult:
        return self._configure_relation(request)

    def configure_source_phase_relation_v2(
        self,
        request: SourcePhaseRelationConfigureRequest,
    ) -> SourceCrossChannelConfigureResult:
        return self._configure_relation(request)

    def _configure_relation(self, request: object) -> SourceCrossChannelConfigureResult:
        channels = getattr(request, "channels")
        enabled = getattr(request, "enabled")
        self.transport.write("SOURCE:COMBINE")
        self.relation_requests.append(request)
        if isinstance(request, SourceCombineConfigureRequest):
            self.combine_requests.append(request)
        self.relation_enabled = enabled
        return SourceCrossChannelConfigureResult(
            feature=self.feature,
            channels=channels,
            enabled=enabled,
            relation=self._relation(enabled),
            outputs=(
                SourceRelationOutputState(channel=1, enabled=False),
                SourceRelationOutputState(channel=2, enabled=False),
                *(
                    (SourceRelationOutputState(channel=3, enabled=False),)
                    if self.linked_channel
                    else ()
                ),
            ),
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.outputs[request.channel] = request.enabled
        self.postcondition_mismatch = False
        return SourceOutputResult(channel=request.channel, enabled=request.enabled)

    def configure_coupling(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_coupling_calls += 1
        raise AssertionError("dual-contract V1 coupling must not reach the driver")

    def errors(self) -> list[str]:
        return []

    def assert_no_errors(self) -> None:
        return None

    def _output(self, channel: int) -> OutputFacet:
        enabled = self.outputs[channel]
        if self.postcondition_mismatch and self.relation_requests and channel == 2:
            enabled = True
        return OutputFacet(
            enabled=Observed.value_of(enabled),
            display_load=Observed.missing(
                Availability.NOT_QUERIED,
                # No graph edge declares DISPLAY_LOAD as affected in this fixture.
                # Keep the base output facet otherwise realistic.
                SourceReasonCode.NOT_REQUESTED,
            ),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _relation(self, enabled: bool) -> SourceRelationState | SourceCouplingState:
        if self.feature is SourceFeature.COUPLING:
            not_queried = Observed.missing(
                Availability.NOT_QUERIED,
                SourceReasonCode.NOT_REQUESTED,
            )
            return SourceCouplingState(
                feature=SourceFeature.COUPLING,
                channels=(1, 2),
                enabled=Observed.value_of(enabled),
                reference_channel=Observed.value_of(1),
                dimensions=tuple(
                    SourceCouplingDimensionState(
                        dimension=dimension,
                        enabled=Observed.value_of(enabled),
                        parameter=not_queried,
                    )
                    for dimension in SourceCouplingDimension
                ),
            )
        return SourceRelationState(
            feature=self.feature,
            channels=(1, 2),
            enabled=Observed.value_of(enabled),
        )

    def _graph(self) -> SourceRelationGraph:
        edges = [
            SourceRelationEdge(
                relation_id=f"{self.feature.value}_1_2",
                feature=self.feature,
                sources=(1,),
                targets=(2,),
                signal_path=SourceSignalPathKind.CONFIG_TRACKING,
                affected_fields=(SourceFieldId.OUTPUT,),
            )
        ]
        if self.linked_channel:
            edges.append(
                SourceRelationEdge(
                    relation_id="tracking_2_3",
                    feature=SourceFeature.TRACKING,
                    sources=(2,),
                    targets=(3,),
                    signal_path=SourceSignalPathKind.CONFIG_TRACKING,
                    affected_fields=(SourceFieldId.OUTPUT,),
                )
            )
        return SourceRelationGraph((1, 2, 3), tuple(edges))


def _relation_field(feature: SourceFeature) -> SourceFieldId:
    return {
        SourceFeature.COMBINE: SourceFieldId.COMBINE,
        SourceFeature.COUPLING: SourceFieldId.COUPLING,
        SourceFeature.TRACKING: SourceFieldId.TRACKING,
        SourceFeature.PHASE_RELATION: SourceFieldId.PHASE_RELATION,
    }[feature]


def _capability(feature: SourceFeature) -> str:
    return {
        SourceFeature.COMBINE: "source.combine_configure_v2",
        SourceFeature.COUPLING: "source.coupling_configure_v2",
        SourceFeature.TRACKING: "source.tracking_configure_v2",
        SourceFeature.PHASE_RELATION: "source.phase_relation_configure_v2",
    }[feature]


def _extensions(
    *,
    feature: SourceFeature = SourceFeature.COMBINE,
    configuration_readable: bool = True,
):
    base = source_extensions()
    basic, output = base.features
    profile = (
        SourceCouplingCapabilityProfile(
            dimensions=tuple(SourceCouplingDimension),
            parameter_kinds=(
                SourceCouplingParameterKind.AMPLITUDE_DEVIATION_VPP,
                SourceCouplingParameterKind.FREQUENCY_DEVIATION_HZ,
                SourceCouplingParameterKind.PHASE_DEVIATION_DEG,
            ),
            supported_channel_sets=((1, 2),),
            global_state_readable=True,
            reference_channel_readable=True,
            relation_graph_readable=True,
            configuration_readable=configuration_readable,
        )
        if feature is SourceFeature.COUPLING
        else SourceCrossChannelCapabilityProfile(
            relation_kinds=(feature,),
            supported_channel_sets=((1, 2),),
            relation_graph_readable=True,
            shared_power_constraint_readable=False,
            configuration_readable=configuration_readable,
        )
    )
    relation = SourceFeatureCapability(
        feature=feature,
        support=SupportState.SUPPORTED,
        directions=(SourceFeatureDirection.CONFIGURE, SourceFeatureDirection.READ),
        scope=SourceFacetScope.CHANNEL_SET,
        channels=(1, 2),
        applicability=SourceConstraintApplicability(),
        profile=profile,
    )
    relation_graph = SourceFeatureCapability(
        feature=feature,
        support=SupportState.SUPPORTED,
        directions=(SourceFeatureDirection.READ,),
        scope=SourceFacetScope.INSTRUMENT,
        channels=(),
        applicability=SourceConstraintApplicability(),
        profile=profile,
    )
    relation_query = SourceFacetQueryContract(
        feature=feature,
        scope=SourceFacetScope.CHANNEL_SET,
        fields=(_relation_field(feature),),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    graph_query = SourceFacetQueryContract(
        feature=feature,
        scope=SourceFacetScope.INSTRUMENT,
        fields=(SourceFieldId.RELATION_GRAPH,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        topology=replace(base.topology, channels=(1, 2, 3)),
        features=tuple(
            sorted(
                (
                    replace(basic, channels=(1,)),
                    replace(basic, channels=(2,)),
                    replace(basic, channels=(3,)),
                    relation,
                    relation_graph,
                    replace(
                        output,
                        channels=(1,),
                        directions=(
                            SourceFeatureDirection.DISABLE,
                            SourceFeatureDirection.ENABLE,
                            SourceFeatureDirection.READ,
                        ),
                    ),
                    replace(
                        output,
                        channels=(2,),
                        directions=(
                            SourceFeatureDirection.DISABLE,
                            SourceFeatureDirection.ENABLE,
                            SourceFeatureDirection.READ,
                        ),
                    ),
                    replace(
                        output,
                        channels=(3,),
                        directions=(
                            SourceFeatureDirection.DISABLE,
                            SourceFeatureDirection.ENABLE,
                            SourceFeatureDirection.READ,
                        ),
                    ),
                ),
                key=lambda item: (item.feature.value, item.scope.value, item.channels),
            )
        ),
        query_contract=replace(
            base.query_contract,
            facets=tuple(
                sorted(
                    (
                        base.query_contract.facets[0],
                        base.query_contract.facets[1],
                        relation_query,
                        graph_query,
                        base.query_contract.facets[2],
                    ),
                    key=lambda item: (
                        item.feature.value,
                        item.scope.value,
                        tuple(field.value for field in item.fields),
                    ),
                )
            ),
            max_queries=20,
        ),
    )


def _config() -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DMAX"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("wavebench.toml"),
        source=SourceConfig(
            "example.source-v2",
            "TCPIP::source::INSTR",
            1,
            False,
            True,
            0,
        ),
        safety_limits=SafetyLimitsConfig(),
    )


def _service(
    *,
    affected_output_on: bool = False,
    independent_output_on: bool = False,
    postcondition_mismatch: bool = False,
    linked_channel: bool = False,
    configuration_readable: bool = True,
    feature: SourceFeature = SourceFeature.COMBINE,
    dual_contract: bool = False,
) -> tuple[SourceService, _CrossChannelWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-cross-channel-v2")
    driver = _CrossChannelWriteDriver(
        session_state=session_state,
        affected_output_on=affected_output_on,
        independent_output_on=independent_output_on,
        postcondition_mismatch=postcondition_mismatch,
        linked_channel=linked_channel,
        feature=feature,
    )
    capabilities = [
        "source.snapshot_v2",
        _capability(feature),
        "source.output_v2",
    ]
    if dual_contract:
        capabilities.extend(("source.coupling_configure", "source.errors"))
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=_extensions(
                feature=feature,
                configuration_readable=configuration_readable,
            ),
        ),
        capabilities=tuple(capabilities),
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


def test_combine_configure_v2_writes_once_and_keeps_declared_outputs_off() -> None:
    service, driver = _service()
    request = SourceCombineConfigureRequest(channels=(1, 2), enabled=True)

    result, artifact = service.configure_combine_v2(request, correlation_id="combine-write")

    assert result.relation.enabled.value is True
    assert tuple(item.channel for item in result.outputs) == (1, 2)
    assert driver.combine_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.combine_configure_v2"
    assert artifact["preflight"]["affected_channels"] == [1, 2]
    assert artifact["mutation"]["status"] == "written"
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "affected_outputs_expected": "off",
    }


@pytest.mark.parametrize(
    ("feature", "request_type", "service_method"),
    (
        (
            SourceFeature.COUPLING,
            SourceCouplingConfigureRequest,
            "configure_coupling_v2",
        ),
        (
            SourceFeature.TRACKING,
            SourceTrackingConfigureRequest,
            "configure_tracking_v2",
        ),
        (
            SourceFeature.PHASE_RELATION,
            SourcePhaseRelationConfigureRequest,
            "configure_phase_relation_v2",
        ),
    ),
)
def test_other_cross_channel_routes_use_their_own_capability_and_request_type(
    feature: SourceFeature,
    request_type: type[object],
    service_method: str,
) -> None:
    service, driver = _service(feature=feature)
    request = request_type(channels=(1, 2), enabled=True)  # type: ignore[operator]

    result, artifact = getattr(service, service_method)(request)

    assert result.feature is feature
    assert driver.relation_requests == [request]
    assert artifact["capability_decision"]["capability"] == _capability(feature)


def test_combine_configure_v2_does_not_close_an_independent_output() -> None:
    service, driver = _service(independent_output_on=True)

    result, artifact = service.configure_combine_v2(
        SourceCombineConfigureRequest(channels=(1, 2), enabled=True)
    )

    assert result.enabled is True
    assert driver.outputs[3] is True
    assert driver.output_requests == []
    assert artifact["preflight"]["affected_channels"] == [1, 2]


def test_combine_configure_v2_requires_every_affected_output_off() -> None:
    service, driver = _service(affected_output_on=True)

    with pytest.raises(ConfigError, match="every affected output OFF"):
        service.configure_combine_v2(SourceCombineConfigureRequest(channels=(1, 2), enabled=True))

    assert driver.combine_requests == []
    assert driver.transport.counters.write_requests == 0


def test_combine_configure_v2_expands_only_connected_relation_ports() -> None:
    service, driver = _service(independent_output_on=True, linked_channel=True)

    with pytest.raises(ConfigError, match="every affected output OFF"):
        service.configure_combine_v2(SourceCombineConfigureRequest(channels=(1, 2), enabled=True))

    assert driver.combine_requests == []
    assert driver.transport.counters.write_requests == 0


def test_combine_configure_v2_postcondition_failure_recovers_every_affected_output() -> None:
    service, driver = _service(postcondition_mismatch=True, linked_channel=True)
    request = SourceCombineConfigureRequest(channels=(1, 2), enabled=True)

    with pytest.raises(ConfigError, match="affected output ON") as raised:
        service.configure_combine_v2(request)

    artifact = raised.value.source_operation_artifact
    assert driver.combine_requests == [request]
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=False),
        SourceOutputRequest(channel=2, enabled=False),
        SourceOutputRequest(channel=3, enabled=False),
    ]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "channels": [1, 2, 3],
        "session_health": "uncertain",
    }


def test_combine_configure_v2_noops_when_already_at_target() -> None:
    service, driver = _service()
    driver.relation_enabled = True

    result, artifact = service.configure_combine_v2(
        SourceCombineConfigureRequest(channels=(1, 2), enabled=True)
    )

    assert result.enabled is True
    assert driver.combine_requests == []
    assert driver.transport.counters.write_requests == 0
    assert artifact["mutation"]["status"] == "already_at_target"


def test_combine_descriptor_requires_configuration_readback() -> None:
    with pytest.raises(ConfigError, match="readable declared combine relation state"):
        _service(configuration_readable=False)


def test_v1_coupling_rejects_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service(feature=SourceFeature.COUPLING, dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_coupling(object())  # type: ignore[arg-type]

    assert driver.v1_coupling_calls == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_v1_restore_rejects_before_io_for_a_cross_channel_v2_driver() -> None:
    service, driver = _service()

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


@pytest.mark.parametrize(
    ("command", "request_type", "method_name", "operation"),
    (
        (
            "combine-configure-v2",
            SourceCombineConfigureRequest,
            "configure_combine_v2",
            "source.combine_configure_v2",
        ),
        (
            "coupling-configure-v2",
            SourceCouplingConfigureRequest,
            "configure_coupling_v2",
            "source.coupling_configure_v2",
        ),
        (
            "tracking-configure-v2",
            SourceTrackingConfigureRequest,
            "configure_tracking_v2",
            "source.tracking_configure_v2",
        ),
        (
            "phase-relation-configure-v2",
            SourcePhaseRelationConfigureRequest,
            "configure_phase_relation_v2",
            "source.phase_relation_configure_v2",
        ),
    ),
)
def test_cross_channel_cli_uses_typed_request_and_operation_artifact(
    command: str,
    request_type: type[object],
    method_name: str,
    operation: str,
    capsys,
) -> None:
    calls: list[tuple[str, object]] = []

    class _Service:
        def configure_combine_v2(self, request):
            calls.append(("configure_combine_v2", request))
            return object(), {"operation": "source.combine_configure_v2"}

        def configure_coupling_v2(self, request):
            calls.append(("configure_coupling_v2", request))
            return object(), {"operation": "source.coupling_configure_v2"}

        def configure_tracking_v2(self, request):
            calls.append(("configure_tracking_v2", request))
            return object(), {"operation": "source.tracking_configure_v2"}

        def configure_phase_relation_v2(self, request):
            calls.append(("configure_phase_relation_v2", request))
            return object(), {"operation": "source.phase_relation_configure_v2"}

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        code = cli.main(
            [
                "--json",
                "source",
                command,
                "--channel",
                "1",
                "--channel",
                "2",
                "on",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["result"] == {"operation": operation}
    assert len(calls) == 1
    assert calls[0][0] == method_name
    assert isinstance(calls[0][1], request_type)
    assert calls[0][1].channels == (1, 2)
    assert calls[0][1].enabled is True


def test_cross_channel_cli_rejects_unsorted_channels_before_service_method(capsys) -> None:
    calls: list[object] = []

    class _Service:
        def configure_combine_v2(self, request):
            calls.append(request)
            raise AssertionError("invalid request must not reach SourceService")

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        code = cli.main(
            [
                "--json",
                "source",
                "combine-configure-v2",
                "--channel",
                "2",
                "--channel",
                "1",
                "on",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["message"].endswith("must be sorted and unique")
    assert calls == []


@pytest.mark.parametrize(
    "kind",
    (
        "source.combine_configure_v2",
        "source.coupling_configure_v2",
        "source.tracking_configure_v2",
        "source.phase_relation_configure_v2",
    ),
)
def test_cross_channel_run_plan_steps_use_sorted_channel_sets(tmp_path, kind: str) -> None:
    plan_path = tmp_path / f"{kind}.toml"
    plan_path.write_text(
        f'[[steps]]\nkind = "{kind}"\nchannels = [1, 2]\nenabled = true\n',
        encoding="utf-8",
    )

    step = load_run_plan(plan_path).steps[0]

    assert step.fields == {"channels": (1, 2), "enabled": True}

    plan_path.write_text(
        f'[[steps]]\nkind = "{kind}"\nchannels = [2, 1]\nenabled = true\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="channels must be sorted and unique"):
        load_run_plan(plan_path)


@pytest.mark.parametrize(
    ("kind", "capability"),
    (
        ("source.combine_configure_v2", "source.combine_configure_v2"),
        ("source.coupling_configure_v2", "source.coupling_configure_v2"),
        ("source.tracking_configure_v2", "source.tracking_configure_v2"),
        (
            "source.phase_relation_configure_v2",
            "source.phase_relation_configure_v2",
        ),
    ),
)
def test_cross_channel_run_check_requires_declared_capability(
    tmp_path,
    kind: str,
    capability: str,
) -> None:
    plan_path = tmp_path / "relation.toml"
    plan_path.write_text(
        f'[[steps]]\nkind = "{kind}"\nchannels = [1, 2]\nenabled = true\n',
        encoding="utf-8",
    )
    service = RunService(config=_config(), logger=CommandLogger())
    descriptor = SimpleNamespace(
        driver_id="minimal.source-v2",
        capabilities=("source.snapshot_v2",),
    )

    with patch(
        "wavebench.services.run_service.resolve_instrument_descriptor",
        return_value=descriptor,
    ), pytest.raises(ConfigError, match=capability):
        service.check(load_run_plan(plan_path))


@pytest.mark.parametrize(
    ("kind", "method_name", "request_type", "operation"),
    (
        (
            "source.combine_configure_v2",
            "configure_combine_v2",
            SourceCombineConfigureRequest,
            "source.combine_configure_v2",
        ),
        (
            "source.coupling_configure_v2",
            "configure_coupling_v2",
            SourceCouplingConfigureRequest,
            "source.coupling_configure_v2",
        ),
        (
            "source.tracking_configure_v2",
            "configure_tracking_v2",
            SourceTrackingConfigureRequest,
            "source.tracking_configure_v2",
        ),
        (
            "source.phase_relation_configure_v2",
            "configure_phase_relation_v2",
            SourcePhaseRelationConfigureRequest,
            "source.phase_relation_configure_v2",
        ),
    ),
)
def test_cross_channel_run_step_constructs_the_typed_request(
    tmp_path,
    kind: str,
    method_name: str,
    request_type: type[object],
    operation: str,
) -> None:
    plan_path = tmp_path / f"{kind}.toml"
    plan_path.write_text(
        f'[[steps]]\nkind = "{kind}"\nchannels = [1, 2]\nenabled = true\n',
        encoding="utf-8",
    )
    plan = load_run_plan(plan_path)
    source = SimpleNamespace()
    artifact = {"operation": operation}
    calls: list[object] = []

    def configure(request):
        calls.append(request)
        return object(), artifact

    setattr(source, method_name, configure)
    record = RunService(config=_config(), logger=CommandLogger())._run_step(
        plan,
        plan.steps[0],
        run_dir=tmp_path,
        services=RunInstrumentServices(source=source),
    )

    assert record.artifact == {"source_operation": artifact}
    assert len(calls) == 1
    assert isinstance(calls[0], request_type)
    assert calls[0].channels == (1, 2)
    assert calls[0].enabled is True
