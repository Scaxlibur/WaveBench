from __future__ import annotations

from dataclasses import replace
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
from wavebench.instruments.models import SourcePmModulationConfiguration
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Availability,
    ModulationFacet,
    Observed,
    OutputFacet,
    SourceConstraintApplicability,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceModulationCapabilityProfile,
    SourceModulationKind,
    SourceModulationParameter,
    SourceModulationParameterKind,
    SourceModulationSource,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourcePmModulationConfigureRequest,
    SourcePmModulationConfigureResult,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceTypedObservation,
    SourceWaveformKind,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-pm-modulation-v2"

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del command, replay
        return "ok"

    def write(self, command: str) -> None:
        del command

    def close(self) -> None:
        pass


def _missing() -> Observed[object]:
    return Observed.missing(Availability.NOT_QUERIED, SourceReasonCode.NOT_REQUESTED)


def _modulation(
    *,
    enabled: bool,
    phase_deviation_deg: float = 0.0,
    internal_frequency_hz: float = 25.0,
) -> ModulationFacet:
    return ModulationFacet(
        enabled=Observed.value_of(enabled),
        kind=Observed.value_of(SourceModulationKind.PM),
        source=Observed.value_of(SourceModulationSource.INTERNAL),
        parameters=Observed.value_of(
            (
                SourceModulationParameter(
                    SourceModulationParameterKind.PHASE_DEVIATION_DEG,
                    phase_deviation_deg,
                ),
            )
        ),
        internal_frequency_hz=Observed.value_of(internal_frequency_hz),
        internal_waveform_kind=Observed.value_of(SourceWaveformKind.SINE),
    )


class _PmModulationWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        output_enabled: bool = False,
        postcondition_mismatch: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.output_enabled = output_enabled
        self.postcondition_mismatch = postcondition_mismatch
        self.modulation = _modulation(enabled=False)
        self.modulation_requests: list[SourcePmModulationConfigureRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_pm_calls = 0

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
                elif field.field is SourceFieldId.OUTPUT:
                    value = self._output()
                elif field.field is SourceFieldId.BASIC:
                    value = basic_facet()
                elif field.field is SourceFieldId.MODULATION:
                    value = self._readback_modulation()
                else:  # pragma: no cover - this descriptor only declares these fields.
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
            device_revision_token_before="revision-1",
            device_revision_token_after="revision-1",
        )

    def configure_source_pm_modulation_v2(
        self,
        request: SourcePmModulationConfigureRequest,
    ) -> SourcePmModulationConfigureResult:
        self.transport.write("SOURCE:PM:CONFIGURE")
        self.modulation_requests.append(request)
        self.modulation = _modulation(
            enabled=True,
            phase_deviation_deg=request.phase_deviation_deg,
            internal_frequency_hz=request.internal_frequency_hz,
        )
        return SourcePmModulationConfigureResult(
            channel=request.channel,
            modulation=self.modulation,
            output_enabled=False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            raise AssertionError("the PM modulation fixture only uses recovery OFF")
        return SourceOutputResult(channel=request.channel, enabled=False)

    def configure_pm_modulation(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_pm_calls += 1
        raise AssertionError("dual-contract V1 PM route must not reach the V1 driver")

    def _output(self) -> OutputFacet:
        return OutputFacet(
            enabled=Observed.value_of(self.output_enabled),
            display_load=_missing(),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _readback_modulation(self) -> ModulationFacet:
        if not self.postcondition_mismatch or not self.modulation_requests:
            return self.modulation
        request = self.modulation_requests[-1]
        return _modulation(
            enabled=True,
            phase_deviation_deg=request.phase_deviation_deg + 1.0,
            internal_frequency_hz=request.internal_frequency_hz,
        )


def _extensions():
    base = source_extensions()
    basic, output = base.features
    modulation = SourceFeatureCapability(
        feature=SourceFeature.MODULATION,
        support=SupportState.SUPPORTED,
        directions=(SourceFeatureDirection.CONFIGURE, SourceFeatureDirection.READ),
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourceModulationCapabilityProfile(
            kinds=(SourceModulationKind.PM,),
            sources=(SourceModulationSource.INTERNAL,),
            parameter_kinds=(SourceModulationParameterKind.PHASE_DEVIATION_DEG,),
            inactive_readable=False,
            configuration_readable=True,
        ),
    )
    modulation_query = SourceFacetQueryContract(
        feature=SourceFeature.MODULATION,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.MODULATION,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        features=(
            basic,
            modulation,
            replace(
                output,
                directions=(
                    SourceFeatureDirection.DISABLE,
                    SourceFeatureDirection.ENABLE,
                    SourceFeatureDirection.READ,
                ),
            ),
        ),
        query_contract=replace(
            base.query_contract,
            facets=(
                base.query_contract.facets[0],
                base.query_contract.facets[1],
                modulation_query,
                base.query_contract.facets[2],
            ),
            max_queries=7,
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
    output_enabled: bool = False,
    postcondition_mismatch: bool = False,
    dual_contract: bool = False,
) -> tuple[SourceService, _PmModulationWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-pm-modulation-v2")
    driver = _PmModulationWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
    )
    capabilities = [
        "source.snapshot_v2",
        "source.modulation_pm_configure_v2",
        "source.output_v2",
    ]
    if dual_contract:
        capabilities.append("source.modulation_pm_configure")
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_extensions()),
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


def _request(
    *,
    phase_deviation_deg: float = 90.0,
    internal_frequency_hz: float = 25.0,
) -> SourcePmModulationConfigureRequest:
    return SourcePmModulationConfigureRequest(
        channel=1,
        phase_deviation_deg=phase_deviation_deg,
        internal_frequency_hz=internal_frequency_hz,
    )


def test_pm_modulation_configure_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _service()
    request = _request()

    result, artifact = service.configure_pm_modulation_v2(
        request,
        correlation_id="pm-modulation-write",
    )

    assert result.modulation.parameters.value == (
        SourceModulationParameter(
            SourceModulationParameterKind.PHASE_DEVIATION_DEG,
            90.0,
        ),
    )
    assert driver.modulation_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.modulation_pm_configure_v2"
    assert artifact["request"] == {
        "type": "SourcePmModulationConfigureRequest",
        "channel": 1,
        "phase_deviation_deg": 90.0,
        "internal_frequency_hz": 25.0,
    }
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
    }
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]
    assert "fake-source-pm-modulation-v2" not in repr(artifact)


def test_pm_modulation_configure_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.configure_pm_modulation_v2(_request())

    assert driver.modulation_requests == []
    assert driver.transport.counters.write_requests == 0


def test_pm_modulation_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="phase deviation readback does not match") as raised:
        service.configure_pm_modulation_v2(_request())

    artifact = raised.value.source_operation_artifact
    assert driver.modulation_requests == [_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_v1_pm_route_rejects_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_pm_modulation(
            SourcePmModulationConfiguration(True, 90.0, 25.0, "SINE"),
            channel=1,
        )

    assert driver.v1_pm_calls == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_v1_restore_rejects_before_io_for_a_pm_modulation_v2_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0
