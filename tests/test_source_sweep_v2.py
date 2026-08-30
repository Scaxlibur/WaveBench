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
from wavebench.instruments.source_extension_capabilities import validate_source_descriptor
from wavebench.instruments.source_extensions import (
    SOURCE_CONTRACT_VERSION,
    Availability,
    Observed,
    OutputFacet,
    SourceConstraintApplicability,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceFireRequest,
    SourceFireResult,
    SourceFrequencyMode,
    SourceOutputPolarity,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceSweepCapabilityProfile,
    SourceSweepConfigureRequest,
    SourceSweepConfigureResult,
    SourceSweepMarker,
    SourceSweepSpacing,
    SourceTriggerOutput,
    SourceTriggerSlope,
    SourceTriggerSource,
    SourceTriggerState,
    SourceTypedObservation,
    SweepFacet,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import basic_facet, source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-sweep-v2"

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


def _sweep(
    *,
    enabled: bool = False,
    start_hz: float = 100.0,
    stop_hz: float = 1_000.0,
    spacing: SourceSweepSpacing = SourceSweepSpacing.LINEAR,
    steps: int = 101,
    sweep_time_s: float = 1.0,
    trigger_source: SourceTriggerSource = SourceTriggerSource.INTERNAL,
) -> SweepFacet:
    return SweepFacet(
        enabled=Observed.value_of(enabled),
        start_hz=Observed.value_of(start_hz),
        stop_hz=Observed.value_of(stop_hz),
        spacing=Observed.value_of(spacing),
        steps=Observed.value_of(steps),
        sweep_time_s=Observed.value_of(sweep_time_s),
        start_hold_s=Observed.value_of(0.0),
        stop_hold_s=Observed.value_of(0.0),
        return_time_s=Observed.value_of(0.0),
        trigger=Observed.value_of(
            SourceTriggerState(
                source=Observed.value_of(trigger_source),
                slope=Observed.value_of(SourceTriggerSlope.POSITIVE),
                output=Observed.value_of(SourceTriggerOutput.OFF),
            )
        ),
        marker=Observed.value_of(
            SourceSweepMarker(
                enabled=Observed.value_of(False),
                frequency_hz=Observed.missing(
                    Availability.NOT_APPLICABLE,
                    SourceReasonCode.INACTIVE_BY_ANCHOR,
                ),
            )
        ),
    )


class _SweepWriteDriver:
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
        self.basic = basic_facet()
        self.sweep = _sweep()
        self.sweep_requests: list[SourceSweepConfigureRequest] = []
        self.fire_requests: list[SourceFireRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_sweep_configure_calls = 0
        self.v1_sweep_trigger_calls = 0

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
                    value = self._readback_basic()
                elif field.field is SourceFieldId.SWEEP:
                    value = self._readback_sweep()
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

    def configure_source_sweep_v2(
        self,
        request: SourceSweepConfigureRequest,
    ) -> SourceSweepConfigureResult:
        self.transport.write("SOURCE:SWEEP:CONFIGURE")
        self.sweep_requests.append(request)
        self.basic = replace(
            self.basic,
            frequency_mode=Observed.value_of(SourceFrequencyMode.SWEEP),
        )
        self.sweep = _sweep(
            enabled=True,
            start_hz=request.start_hz,
            stop_hz=request.stop_hz,
            spacing=request.spacing,
            steps=request.steps,
            sweep_time_s=request.sweep_time_s,
            trigger_source=request.trigger_source,
        )
        return SourceSweepConfigureResult(
            channel=request.channel,
            basic=self.basic,
            sweep=self.sweep,
            output_enabled=False,
        )

    def fire_source_sweep_v2(self, request: SourceFireRequest) -> SourceFireResult:
        self.transport.write("SOURCE:SWEEP:FIRE")
        self.fire_requests.append(request)
        if self.raise_after_fire:
            raise ConfigError("fake Sweep fire failed after write")
        return SourceFireResult(channel=request.channel)

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            return SourceOutputResult(
                channel=request.channel,
                enabled=True,
                final_amplitude=self.basic.amplitude.value,
                final_offset_v=self.basic.offset_v.value,
            )
        return SourceOutputResult(channel=request.channel, enabled=False)

    def configure_sweep(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_sweep_configure_calls += 1
        raise AssertionError("dual-contract V1 Sweep route must not reach the V1 driver")

    def trigger_sweep(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.v1_sweep_trigger_calls += 1
        raise AssertionError("dual-contract V1 Sweep trigger must not reach the V1 driver")

    def _output(self) -> OutputFacet:
        return OutputFacet(
            enabled=Observed.value_of(self.output_enabled),
            display_load=_missing(),
            polarity=Observed.value_of(SourceOutputPolarity.NORMAL),
        )

    def _readback_basic(self):
        if not self.postcondition_mismatch or not self.sweep_requests:
            return self.basic
        return replace(
            self.basic,
            frequency_mode=Observed.value_of(SourceFrequencyMode.FIXED),
        )

    def _readback_sweep(self) -> SweepFacet:
        if self.post_fire_mismatch and self.fire_requests:
            return replace(self.sweep, sweep_time_s=Observed.value_of(2.0))
        if not self.postcondition_mismatch or not self.sweep_requests:
            return self.sweep
        return replace(self.sweep, sweep_time_s=Observed.value_of(2.0))


def _extensions(
    *,
    spacing_modes: tuple[SourceSweepSpacing, ...] = (
        SourceSweepSpacing.LINEAR,
        SourceSweepSpacing.LOGARITHMIC,
        SourceSweepSpacing.STEP,
    ),
    include_fire: bool = False,
):
    base = source_extensions()
    basic, output = base.features
    basic = replace(
        basic,
        profile=replace(
            basic.profile,
            frequency_modes=(
                SourceFrequencyMode.FIXED,
                SourceFrequencyMode.SWEEP,
            ),
        ),
    )
    sweep = SourceFeatureCapability(
        feature=SourceFeature.SWEEP,
        support=SupportState.SUPPORTED,
        directions=(
            SourceFeatureDirection.CONFIGURE,
            *((SourceFeatureDirection.FIRE,) if include_fire else ()),
            SourceFeatureDirection.READ,
        ),
        scope=SourceFacetScope.CHANNEL,
        channels=(1,),
        applicability=SourceConstraintApplicability(),
        profile=SourceSweepCapabilityProfile(
            spacing_modes=spacing_modes,
            trigger_sources=(
                SourceTriggerSource.INTERNAL,
                *((SourceTriggerSource.MANUAL,) if include_fire else ()),
            ),
            timing_readable=True,
            marker_readable=True,
            configuration_readable=True,
        ),
    )
    sweep_query = SourceFacetQueryContract(
        feature=SourceFeature.SWEEP,
        scope=SourceFacetScope.CHANNEL,
        fields=(SourceFieldId.SWEEP,),
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
            sweep,
        ),
        query_contract=replace(
            base.query_contract,
            facets=(
                base.query_contract.facets[0],
                base.query_contract.facets[1],
                base.query_contract.facets[2],
                sweep_query,
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
    spacing_modes: tuple[SourceSweepSpacing, ...] = (
        SourceSweepSpacing.LINEAR,
        SourceSweepSpacing.LOGARITHMIC,
        SourceSweepSpacing.STEP,
    ),
) -> tuple[SourceService, _SweepWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-sweep-v2")
    driver = _SweepWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
        post_fire_mismatch=post_fire_mismatch,
        raise_after_fire=raise_after_fire,
    )
    capabilities = [
        "source.snapshot_v2",
        "source.sweep_configure_v2",
        "source.output_v2",
    ]
    if include_fire:
        capabilities.append("source.sweep_fire_v2")
    if dual_contract:
        capabilities.extend(("source.sweep_configure", "source.sweep_trigger"))
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=_extensions(
                spacing_modes=spacing_modes,
                include_fire=include_fire,
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


def _request(
    *,
    spacing: SourceSweepSpacing = SourceSweepSpacing.LINEAR,
    trigger_source: SourceTriggerSource = SourceTriggerSource.INTERNAL,
) -> SourceSweepConfigureRequest:
    return SourceSweepConfigureRequest(
        channel=1,
        start_hz=100.0,
        stop_hz=1_000.0,
        spacing=spacing,
        steps=101,
        sweep_time_s=1.0,
        trigger_source=trigger_source,
    )


def test_sweep_fire_capability_requires_manual_configuration_readback() -> None:
    session_state = InstrumentSessionState(epoch_id="source-sweep-fire-profile")
    driver = _SweepWriteDriver(session_state=session_state)
    extensions = _extensions(include_fire=True)
    basic, output, sweep = extensions.features
    descriptor = replace(
        source_descriptor(
            driver=driver,
            extensions=replace(
                extensions,
                features=(
                    basic,
                    output,
                    replace(
                        sweep,
                        profile=replace(
                            sweep.profile,
                            trigger_sources=(SourceTriggerSource.INTERNAL,),
                        ),
                    ),
                ),
            ),
        ),
        capabilities=(
            "source.snapshot_v2",
            "source.sweep_configure_v2",
            "source.sweep_fire_v2",
            "source.output_v2",
        ),
    )

    with pytest.raises(ConfigError, match="readable manual sweep"):
        validate_source_descriptor(descriptor)


def test_sweep_configure_v2_writes_once_and_keeps_output_off() -> None:
    service, driver = _service()
    request = _request()

    result, artifact = service.configure_sweep_v2(
        request,
        correlation_id="sweep-write",
    )

    assert result.basic.frequency_mode.value is SourceFrequencyMode.SWEEP
    assert result.sweep.spacing.value is SourceSweepSpacing.LINEAR
    assert driver.sweep_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.sweep_configure_v2"
    assert artifact["request"] == {
        "type": "SourceSweepConfigureRequest",
        "channel": 1,
        "start_hz": 100.0,
        "stop_hz": 1_000.0,
        "spacing": "linear",
        "steps": 101,
        "sweep_time_s": 1.0,
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
    assert "fake-source-sweep-v2" not in repr(artifact)


def test_sweep_configure_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.configure_sweep_v2(_request())

    assert driver.sweep_requests == []
    assert driver.transport.counters.write_requests == 0


def test_sweep_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_mismatch=True)
    request = _request()

    with pytest.raises(ConfigError, match="sweep frequency mode") as raised:
        service.configure_sweep_v2(request)

    artifact = raised.value.source_operation_artifact
    assert driver.sweep_requests == [request]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_sweep_configure_v2_rejects_unsupported_spacing_before_write() -> None:
    service, driver = _service(spacing_modes=(SourceSweepSpacing.LINEAR,))

    with pytest.raises(ConfigError, match="spacing is not supported"):
        service.configure_sweep_v2(_request(spacing=SourceSweepSpacing.LOGARITHMIC))

    assert driver.sweep_requests == []
    assert driver.transport.counters.write_requests == 0


def test_v1_sweep_routes_reject_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_sweep(object())  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.trigger_sweep(channel=1)

    assert driver.v1_sweep_configure_calls == 0
    assert driver.v1_sweep_trigger_calls == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_v1_restore_rejects_before_io_for_a_sweep_v2_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_sweep_fire_v2_reuses_configuring_session_and_keeps_output_on() -> None:
    service, driver = _service(include_fire=True)
    configured, configure_artifact = service.configure_sweep_v2(
        _request(trigger_source=SourceTriggerSource.MANUAL)
    )
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    result, artifact = service.fire_sweep_v2(
        SourceFireRequest(channel=1),
        correlation_id="sweep-fire",
    )

    assert result == SourceFireResult(channel=1)
    assert configured.sweep.trigger.value.source.value is SourceTriggerSource.MANUAL
    assert configure_artifact["request"]["trigger_source"] == "manual"
    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.output_enabled is True
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=True)]
    assert artifact["operation"] == "source.sweep_fire_v2"
    assert artifact["persistent_session_verified"] is True
    assert artifact["postcondition"]["emission_verified"] is False
    assert artifact["postcondition"]["external_measurement_required"] is True
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "on",
    }


def test_sweep_fire_v2_requires_same_session_configuration_before_io() -> None:
    service, driver = _service(include_fire=True)

    with pytest.raises(ConfigError, match="configuration from the same session"):
        service.fire_sweep_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.transport.counters.query_calls == 0
    assert driver.transport.counters.write_requests == 0


def test_sweep_fire_v2_requires_output_on_before_fire_write() -> None:
    service, driver = _service(include_fire=True)
    service.configure_sweep_v2(_request(trigger_source=SourceTriggerSource.MANUAL))

    with pytest.raises(ConfigError, match="target output ON"):
        service.fire_sweep_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.output_requests == []


def test_sweep_fire_v2_rejects_internal_trigger_configuration() -> None:
    service, driver = _service(include_fire=True)
    service.configure_sweep_v2(_request())
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    with pytest.raises(ConfigError, match="manual trigger source"):
        service.fire_sweep_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == []
    assert driver.output_enabled is True


def test_sweep_fire_v2_failure_is_not_retried_and_recovers_off() -> None:
    service, driver = _service(include_fire=True, raise_after_fire=True)
    service.configure_sweep_v2(_request(trigger_source=SourceTriggerSource.MANUAL))
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    with pytest.raises(ConfigError, match="failed after write") as raised:
        service.fire_sweep_v2(SourceFireRequest(channel=1))

    artifact = raised.value.source_operation_artifact
    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=True),
        SourceOutputRequest(channel=1, enabled=False),
    ]
    assert driver.output_enabled is False
    assert artifact["recovery"]["status"] == "off_verified"
    assert artifact["final_state"]["output_expected"] == "off"


def test_sweep_fire_v2_postcondition_mismatch_recovers_off() -> None:
    service, driver = _service(include_fire=True, post_fire_mismatch=True)
    service.configure_sweep_v2(_request(trigger_source=SourceTriggerSource.MANUAL))
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    with pytest.raises(ConfigError, match="same-session receipt"):
        service.fire_sweep_v2(SourceFireRequest(channel=1))

    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.output_requests[-1] == SourceOutputRequest(channel=1, enabled=False)
    assert driver.output_enabled is False


def test_v1_sweep_trigger_maps_to_fire_v2_when_declared() -> None:
    service, driver = _service(include_fire=True, dual_contract=True)
    service.configure_sweep_v2(_request(trigger_source=SourceTriggerSource.MANUAL))
    service.set_output_v2(SourceOutputRequest(channel=1, enabled=True))

    service.trigger_sweep(channel=1)

    assert driver.fire_requests == [SourceFireRequest(channel=1)]
    assert driver.v1_sweep_trigger_calls == 0
