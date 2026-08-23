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
from wavebench.instruments.models import SourcePulseConfiguration
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Availability,
    Observed,
    OutputFacet,
    PulseFacet,
    SourceConstraintApplicability,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourcePulseCapabilityProfile,
    SourcePulseConfigureRequest,
    SourcePulseConfigureResult,
    SourcePulseHoldBasis,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceTypedObservation,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-pulse-v2"

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


def _pulse(
    *,
    width_s: float = 1.0e-6,
    delay_s: float = 0.0,
    leading_transition_s: float = 1.0e-8,
    trailing_transition_s: float = 1.0e-8,
) -> PulseFacet:
    return PulseFacet(
        hold_basis=Observed.value_of(SourcePulseHoldBasis.WIDTH),
        width_s=Observed.value_of(width_s),
        duty_cycle_percent=_missing(),
        delay_s=Observed.value_of(delay_s),
        leading_transition_s=Observed.value_of(leading_transition_s),
        trailing_transition_s=Observed.value_of(trailing_transition_s),
    )


class _PulseWriteDriver:
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
        self.pulse = _pulse()
        self.pulse_requests: list[SourcePulseConfigureRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_pulse_calls = 0

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
                elif field.field is SourceFieldId.PULSE:
                    value = self._readback_pulse()
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

    def configure_source_pulse_v2(
        self,
        request: SourcePulseConfigureRequest,
    ) -> SourcePulseConfigureResult:
        self.transport.write("SOURCE:PULSE:CONFIGURE")
        self.pulse_requests.append(request)
        self.pulse = _pulse(
            width_s=request.width_s,
            delay_s=request.delay_s,
            leading_transition_s=request.leading_transition_s,
            trailing_transition_s=request.trailing_transition_s,
        )
        return SourcePulseConfigureResult(
            channel=request.channel,
            pulse=self.pulse,
            output_enabled=False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            raise AssertionError("the Pulse fixture only uses recovery OFF")
        return SourceOutputResult(channel=request.channel, enabled=False)

    def configure_pulse(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_pulse_calls += 1
        raise AssertionError("dual-contract V1 Pulse route must not reach the V1 driver")

    def _output(self) -> OutputFacet:
        return OutputFacet(
            enabled=Observed.value_of(self.output_enabled),
            display_load=_missing(),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _readback_pulse(self) -> PulseFacet:
        if not self.postcondition_mismatch or not self.pulse_requests:
            return self.pulse
        request = self.pulse_requests[-1]
        return _pulse(
            width_s=request.width_s * 2.0,
            delay_s=request.delay_s,
            leading_transition_s=request.leading_transition_s,
            trailing_transition_s=request.trailing_transition_s,
        )


def _extensions():
    base = source_extensions()
    basic, output = base.features
    pulse = SourceFeatureCapability(
        feature=SourceFeature.PULSE,
        support=SupportState.SUPPORTED,
        directions=(SourceFeatureDirection.CONFIGURE, SourceFeatureDirection.READ),
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourcePulseCapabilityProfile(
            hold_modes=(SourcePulseHoldBasis.WIDTH,),
            delay_readable=True,
            transitions_readable=True,
            width_configuration_readable=True,
        ),
    )
    pulse_query = SourceFacetQueryContract(
        feature=SourceFeature.PULSE,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.PULSE,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        features=(
            basic,
            replace(
                output,
                directions=(
                    SourceFeatureDirection.DISABLE,
                    SourceFeatureDirection.ENABLE,
                    SourceFeatureDirection.READ,
                ),
            ),
            pulse,
        ),
        query_contract=replace(
            base.query_contract,
            facets=(
                base.query_contract.facets[0],
                base.query_contract.facets[1],
                base.query_contract.facets[2],
                pulse_query,
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
) -> tuple[SourceService, _PulseWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-pulse-v2")
    driver = _PulseWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
    )
    capabilities = [
        "source.snapshot_v2",
        "source.pulse_configure_v2",
        "source.output_v2",
    ]
    if dual_contract:
        capabilities.append("source.pulse_configure")
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


def _request() -> SourcePulseConfigureRequest:
    return SourcePulseConfigureRequest(
        channel=1,
        width_s=1.0e-6,
        delay_s=0.0,
        leading_transition_s=1.0e-8,
        trailing_transition_s=1.0e-8,
    )


def test_pulse_configure_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _service()
    request = _request()

    result, artifact = service.configure_pulse_v2(request, correlation_id="pulse-write")

    assert result.pulse.width_s.value == 1.0e-6
    assert driver.pulse_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.pulse_configure_v2"
    assert artifact["request"] == {
        "type": "SourcePulseConfigureRequest",
        "channel": 1,
        "width_s": 1.0e-6,
        "delay_s": 0.0,
        "leading_transition_s": 1.0e-8,
        "trailing_transition_s": 1.0e-8,
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
    assert "fake-source-pulse-v2" not in repr(artifact)


def test_pulse_configure_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.configure_pulse_v2(_request())

    assert driver.pulse_requests == []
    assert driver.transport.counters.write_requests == 0


def test_pulse_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="width readback does not match") as raised:
        service.configure_pulse_v2(_request())

    artifact = raised.value.source_operation_artifact
    assert driver.pulse_requests == [_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_v1_pulse_route_rejects_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_pulse(
            SourcePulseConfiguration(
                hold="DUTY",
                duty_cycle_percent=50.0,
                delay_s=0.0,
                leading_transition_s=1.0e-8,
                trailing_transition_s=1.0e-8,
            ),
            channel=1,
        )

    assert driver.v1_pulse_calls == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_v1_restore_rejects_before_io_for_a_pulse_v2_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0
