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
from wavebench.instruments.models import SourceBurstConfiguration
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Availability,
    BurstFacet,
    Observed,
    OutputFacet,
    SourceBurstCapabilityProfile,
    SourceBurstConfigureRequest,
    SourceBurstConfigureResult,
    SourceBurstMode,
    SourceConstraintApplicability,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceFireRequest,
    SourceFireResult,
    SourceGatePolarity,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceTriggerOutput,
    SourceTriggerSlope,
    SourceTriggerSource,
    SourceTriggerState,
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
    resource = "fake-source-burst-v2"

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


def _burst(
    *,
    cycles: int = 12,
    phase_deg: float = 30.0,
    internal_period_s: float = 0.25,
    delay_s: float = 0.5,
    trigger_source: SourceTriggerSource = SourceTriggerSource.INTERNAL,
) -> BurstFacet:
    return BurstFacet(
        enabled=Observed.value_of(True),
        mode=Observed.value_of(SourceBurstMode.TRIGGERED),
        cycles=Observed.value_of(cycles),
        phase_deg=Observed.value_of(phase_deg),
        internal_period_s=Observed.value_of(internal_period_s),
        delay_s=Observed.value_of(delay_s),
        gate_polarity=Observed.value_of(SourceGatePolarity.NORMAL),
        trigger=Observed.value_of(
            SourceTriggerState(
                source=Observed.value_of(trigger_source),
                slope=Observed.value_of(SourceTriggerSlope.POSITIVE),
                output=Observed.value_of(SourceTriggerOutput.OFF),
            )
        ),
    )


class _BurstWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        output_enabled: bool = False,
        postcondition_mismatch: bool = False,
        post_fire_mismatch: bool = False,
        raise_after_fire: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.output_enabled = output_enabled
        self.postcondition_mismatch = postcondition_mismatch
        self.post_fire_mismatch = post_fire_mismatch
        self.raise_after_fire = raise_after_fire
        self.burst = _burst()
        self.burst_requests: list[SourceBurstConfigureRequest] = []
        self.fire_requests: list[SourceFireRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_burst_calls = 0
        self.v1_trigger_calls = 0

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
                elif field.field is SourceFieldId.BURST:
                    value = self._readback_burst()
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

    def configure_source_burst_v2(
        self,
        request: SourceBurstConfigureRequest,
    ) -> SourceBurstConfigureResult:
        self.transport.write("SOURCE:BURST:CONFIGURE")
        self.burst_requests.append(request)
        self.burst = _burst(
            cycles=request.cycles,
            phase_deg=request.phase_deg,
            internal_period_s=request.internal_period_s,
            delay_s=request.delay_s,
            trigger_source=request.trigger_source,
        )
        return SourceBurstConfigureResult(
            channel=request.channel,
            burst=self.burst,
            output_enabled=False,
        )

    def fire_source_burst_v2(self, request: SourceFireRequest) -> SourceFireResult:
        self.transport.write("SOURCE:BURST:FIRE")
        self.fire_requests.append(request)
        if self.raise_after_fire:
            raise ConfigError("fake Burst fire failed after write")
        return SourceFireResult(channel=request.channel)

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            basic = basic_facet()
            return SourceOutputResult(
                channel=request.channel,
                enabled=True,
                final_amplitude=basic.amplitude.value,
                final_offset_v=basic.offset_v.value,
            )
        return SourceOutputResult(channel=request.channel, enabled=False)

    def configure_burst(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_burst_calls += 1
        raise AssertionError("dual-contract V1 Burst route must not reach the V1 driver")

    def trigger_burst(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.v1_trigger_calls += 1
        raise AssertionError("dual-contract V1 Burst trigger must not reach the V1 driver")

    def _output(self) -> OutputFacet:
        return OutputFacet(
            enabled=Observed.value_of(self.output_enabled),
            display_load=_missing(),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _readback_burst(self) -> BurstFacet:
        if self.post_fire_mismatch and self.fire_requests:
            return _burst(
                delay_s=self.burst.delay_s.value * 2.0,
                trigger_source=self.burst.trigger.value.source.value,
            )
        if not self.postcondition_mismatch or not self.burst_requests:
            return self.burst
        request = self.burst_requests[-1]
        return _burst(
            cycles=request.cycles,
            phase_deg=request.phase_deg,
            internal_period_s=request.internal_period_s,
            delay_s=request.delay_s * 2.0,
        )


def _extensions(*, include_fire: bool = False):
    base = source_extensions()
    basic, output = base.features
    burst = SourceFeatureCapability(
        feature=SourceFeature.BURST,
        support=SupportState.SUPPORTED,
        directions=(
            SourceFeatureDirection.CONFIGURE,
            *((SourceFeatureDirection.FIRE,) if include_fire else ()),
            SourceFeatureDirection.READ,
        ),
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourceBurstCapabilityProfile(
            modes=(SourceBurstMode.TRIGGERED,),
            trigger_sources=(
                SourceTriggerSource.INTERNAL,
                *((SourceTriggerSource.MANUAL,) if include_fire else ()),
            ),
            timing_readable=True,
            gate_readable=False,
            triggered_internal_configuration_readable=True,
            triggered_manual_configuration_readable=include_fire,
        ),
    )
    burst_query = SourceFacetQueryContract(
        feature=SourceFeature.BURST,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.BURST,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        features=(
            basic,
            burst,
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
                burst_query,
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
    post_fire_mismatch: bool = False,
    raise_after_fire: bool = False,
    dual_contract: bool = False,
    include_fire: bool = False,
) -> tuple[SourceService, _BurstWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-burst-v2")
    driver = _BurstWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
        post_fire_mismatch=post_fire_mismatch,
        raise_after_fire=raise_after_fire,
    )
    capabilities = [
        "source.snapshot_v2",
        "source.burst_configure_v2",
        "source.output_v2",
    ]
    if include_fire:
        capabilities.append("source.burst_fire_v2")
    if dual_contract:
        capabilities.extend(("source.burst_configure", "source.burst_trigger"))
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_extensions(include_fire=include_fire)),
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
    trigger_source: SourceTriggerSource = SourceTriggerSource.INTERNAL,
) -> SourceBurstConfigureRequest:
    return SourceBurstConfigureRequest(
        channel=1,
        cycles=12,
        phase_deg=30.0,
        internal_period_s=0.25,
        delay_s=0.5,
        trigger_source=trigger_source,
    )


def test_burst_fire_capability_requires_manual_configuration_readback() -> None:
    session_state = InstrumentSessionState(epoch_id="source-burst-fire-profile")
    driver = _BurstWriteDriver(session_state=session_state)
    extensions = _extensions(include_fire=True)
    basic, burst, output = extensions.features
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=replace(
                extensions,
                features=(
                    basic,
                    replace(
                        burst,
                        profile=replace(
                            burst.profile,
                            triggered_manual_configuration_readable=False,
                        ),
                    ),
                    output,
                ),
            ),
        ),
        capabilities=(
            "source.snapshot_v2",
            "source.burst_configure_v2",
            "source.burst_fire_v2",
            "source.output_v2",
        ),
    )

    with pytest.raises(ConfigError, match="readable manual triggered burst"):
        validate_source_descriptor(descriptor)


def test_burst_configure_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _service()
    request = _request()

    result, artifact = service.configure_burst_v2(request, correlation_id="burst-write")

    assert result.burst.cycles.value == 12
    assert driver.burst_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.burst_configure_v2"
    assert artifact["request"] == {
        "type": "SourceBurstConfigureRequest",
        "channel": 1,
        "cycles": 12,
        "phase_deg": 30.0,
        "internal_period_s": 0.25,
        "delay_s": 0.5,
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
    assert "fake-source-burst-v2" not in repr(artifact)


def test_burst_configure_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.configure_burst_v2(_request())

    assert driver.burst_requests == []
    assert driver.transport.counters.write_requests == 0


def test_burst_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="delay readback does not match") as raised:
        service.configure_burst_v2(_request())

    artifact = raised.value.source_operation_artifact
    assert driver.burst_requests == [_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_v1_burst_routes_reject_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_burst(
            SourceBurstConfiguration(
                enabled=True,
                mode="TRIGGERED",
                cycles=12,
                phase_deg=30.0,
                internal_period_s=0.25,
                delay_s=0.5,
                gate_polarity="NORMAL",
                trigger_source="INTERNAL",
                trigger_slope="POSITIVE",
                trigger_out="OFF",
            ),
            channel=1,
        )
    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.trigger_burst(channel=1)

    assert driver.v1_burst_calls == 0
    assert driver.v1_trigger_calls == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_v1_restore_rejects_before_io_for_a_burst_v2_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_burst_fire_v2_reuses_configuring_session_and_keeps_output_on() -> None:
    service, driver = _service(include_fire=True)
    configured, configure_artifact = service.configure_burst_v2(
        _request(trigger_source=SourceTriggerSource.MANUAL)
    )
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    result, artifact = service.fire_burst_v2(
        SourceFireRequest(channel=1),
        correlation_id="burst-fire",
    )

    assert result == SourceFireResult(channel=1)
    assert configured.burst.trigger.value.source.value is SourceTriggerSource.MANUAL
    assert configure_artifact["request"]["trigger_source"] == "manual"
    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.output_enabled is True
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=True)]
    assert artifact["operation"] == "source.burst_fire_v2"
    assert artifact["persistent_session_verified"] is True
    assert artifact["postcondition"]["emission_verified"] is False
    assert artifact["postcondition"]["external_measurement_required"] is True
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "on",
    }


def test_burst_fire_v2_requires_same_session_configuration_before_io() -> None:
    service, driver = _service(include_fire=True)

    with pytest.raises(ConfigError, match="configuration from the same session"):
        service.fire_burst_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.transport.counters.query_calls == 0
    assert driver.transport.counters.write_requests == 0


def test_burst_fire_v2_requires_persistent_session_before_io() -> None:
    service, driver = _service(include_fire=True)
    service.session = None

    with pytest.raises(ConfigError, match="persistent source session"):
        service.fire_burst_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.transport.counters.query_calls == 0
    assert driver.transport.counters.write_requests == 0


def test_burst_fire_v2_requires_output_on_before_fire_write() -> None:
    service, driver = _service(include_fire=True)
    service.configure_burst_v2(_request(trigger_source=SourceTriggerSource.MANUAL))

    with pytest.raises(ConfigError, match="target output ON"):
        service.fire_burst_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.output_requests == []


def test_burst_fire_v2_rejects_internal_trigger_configuration() -> None:
    service, driver = _service(include_fire=True)
    service.configure_burst_v2(_request())
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    with pytest.raises(ConfigError, match="manual trigger source"):
        service.fire_burst_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.output_enabled is True


def test_burst_fire_v2_failure_is_not_retried_and_recovers_off() -> None:
    service, driver = _service(include_fire=True, raise_after_fire=True)
    service.configure_burst_v2(_request(trigger_source=SourceTriggerSource.MANUAL))
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    with pytest.raises(ConfigError, match="failed after write") as raised:
        service.fire_burst_v2(SourceFireRequest(channel=1))

    artifact = raised.value.source_operation_artifact
    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=True),
        SourceOutputRequest(channel=1, enabled=False),
    ]
    assert driver.output_enabled is False
    assert artifact["recovery"]["status"] == "off_verified"
    assert artifact["final_state"]["output_expected"] == "off"


def test_burst_fire_v2_postcondition_mismatch_recovers_off() -> None:
    service, driver = _service(include_fire=True, post_fire_mismatch=True)
    service.configure_burst_v2(_request(trigger_source=SourceTriggerSource.MANUAL))
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    with pytest.raises(ConfigError, match="same-session receipt"):
        service.fire_burst_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.output_requests[-1] == SourceOutputRequest(channel=1, enabled=False)
    assert driver.output_enabled is False


def test_v1_burst_trigger_maps_to_fire_v2_when_declared() -> None:
    service, driver = _service(include_fire=True, dual_contract=True)
    service.configure_burst_v2(_request(trigger_source=SourceTriggerSource.MANUAL))
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    service.trigger_burst(channel=1)

    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.v1_trigger_calls == 0
