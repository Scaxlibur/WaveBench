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
from wavebench.instruments.models import SourcePwmModulationConfiguration
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
    SourceProtocolQueryRecord,
    SourcePwmModulationConfigureRequest,
    SourcePwmModulationConfigureResult,
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
    resource = "fake-source-pwm-modulation-v2"

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


def _pwm_modulation(
    *,
    parameter: SourceModulationParameter | None = None,
    internal_frequency_hz: float = 25.0,
) -> ModulationFacet:
    if parameter is None:
        parameter = SourceModulationParameter(
            SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
            25.0,
        )
    return ModulationFacet(
        enabled=Observed.value_of(True),
        kind=Observed.value_of(SourceModulationKind.PWM),
        source=Observed.value_of(SourceModulationSource.INTERNAL),
        parameters=Observed.value_of((parameter,)),
        internal_frequency_hz=Observed.value_of(internal_frequency_hz),
        internal_waveform_kind=Observed.value_of(SourceWaveformKind.SINE),
    )


class _PwmModulationWriteDriver:
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
        self.modulation = _pwm_modulation()
        self.modulation_requests: list[SourcePwmModulationConfigureRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_pwm_calls = 0

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

    def configure_source_pwm_modulation_v2(
        self,
        request: SourcePwmModulationConfigureRequest,
    ) -> SourcePwmModulationConfigureResult:
        self.transport.write("SOURCE:MODULATION:PWM:CONFIGURE")
        self.modulation_requests.append(request)
        self.modulation = _pwm_modulation(
            parameter=request.deviation_parameter,
            internal_frequency_hz=request.internal_frequency_hz,
        )
        return SourcePwmModulationConfigureResult(
            channel=request.channel,
            modulation=self.modulation,
            output_enabled=False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        if request.enabled:
            raise AssertionError("the PWM fixture only uses recovery OFF")
        return SourceOutputResult(channel=request.channel, enabled=False)

    def configure_pwm_modulation(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.v1_pwm_calls += 1
        raise AssertionError("dual-contract V1 PWM route must not reach the V1 driver")

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
        parameter = request.deviation_parameter
        mismatch_value = parameter.value * 2.0 if parameter.value else 1.0
        return _pwm_modulation(
            parameter=SourceModulationParameter(parameter.kind, mismatch_value),
            internal_frequency_hz=request.internal_frequency_hz,
        )


def _extensions(
    *,
    parameter_kinds: tuple[SourceModulationParameterKind, ...] = (
        SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
        SourceModulationParameterKind.WIDTH_DEVIATION_S,
    ),
):
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
            kinds=(SourceModulationKind.PWM,),
            sources=(SourceModulationSource.INTERNAL,),
            parameter_kinds=parameter_kinds,
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
    parameter_kinds: tuple[SourceModulationParameterKind, ...] = (
        SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,
        SourceModulationParameterKind.WIDTH_DEVIATION_S,
    ),
) -> tuple[SourceService, _PwmModulationWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-pwm-modulation-v2")
    driver = _PwmModulationWriteDriver(
        session_state=session_state,
        output_enabled=output_enabled,
        postcondition_mismatch=postcondition_mismatch,
    )
    capabilities = [
        "source.snapshot_v2",
        "source.modulation_pwm_configure_v2",
        "source.output_v2",
    ]
    if dual_contract:
        capabilities.append("source.modulation_pwm_configure")
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_extensions(parameter_kinds=parameter_kinds)),
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


def _duty_request() -> SourcePwmModulationConfigureRequest:
    return SourcePwmModulationConfigureRequest(
        channel=1,
        internal_frequency_hz=25.0,
        duty_deviation_percent=25.0,
    )


def _width_request() -> SourcePwmModulationConfigureRequest:
    return SourcePwmModulationConfigureRequest(
        channel=1,
        internal_frequency_hz=25.0,
        width_deviation_s=1.0e-6,
    )


@pytest.mark.parametrize("pwm_request", (_duty_request(), _width_request()))
def test_pwm_modulation_configure_v2_writes_once_and_keeps_output_off(
    pwm_request: SourcePwmModulationConfigureRequest,
) -> None:
    service, driver = _service()

    result, artifact = service.configure_pwm_modulation_v2(
        pwm_request,
        correlation_id="pwm-modulation-write",
    )

    assert result.modulation.parameters.value == (pwm_request.deviation_parameter,)
    assert driver.modulation_requests == [pwm_request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.modulation_pwm_configure_v2"
    assert artifact["request"] == {
        "type": "SourcePwmModulationConfigureRequest",
        "channel": 1,
        "internal_frequency_hz": 25.0,
        "duty_deviation_percent": pwm_request.duty_deviation_percent,
        "width_deviation_s": pwm_request.width_deviation_s,
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
    assert "fake-source-pwm-modulation-v2" not in repr(artifact)


def test_pwm_modulation_configure_v2_requires_output_off_before_driver_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service.configure_pwm_modulation_v2(_duty_request())

    assert driver.modulation_requests == []
    assert driver.transport.counters.write_requests == 0


@pytest.mark.parametrize("pwm_request", (_duty_request(), _width_request()))
def test_pwm_modulation_configure_v2_postcondition_mismatch_runs_one_off_recovery(
    pwm_request: SourcePwmModulationConfigureRequest,
) -> None:
    service, driver = _service(postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="deviation readback does not match") as raised:
        service.configure_pwm_modulation_v2(pwm_request)

    artifact = raised.value.source_operation_artifact
    assert driver.modulation_requests == [pwm_request]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }


def test_pwm_modulation_configure_v2_rejects_an_unsupported_runtime_branch_before_write() -> None:
    service, driver = _service(
        parameter_kinds=(SourceModulationParameterKind.DUTY_DEVIATION_PERCENT,)
    )

    with pytest.raises(ConfigError, match="requested PWM deviation"):
        service.configure_pwm_modulation_v2(_width_request())

    assert driver.modulation_requests == []
    assert driver.transport.counters.write_requests == 0


def test_v1_pwm_route_rejects_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.configure_pwm_modulation(
            SourcePwmModulationConfiguration(True, "WIDTH", 1.0e-6, 25.0, "SINE"),
            channel=1,
        )

    assert driver.v1_pwm_calls == 0
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0


def test_v1_restore_rejects_before_io_for_a_pwm_modulation_v2_driver() -> None:
    service, driver = _service(dual_contract=True)

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        service.restore_restorable_state(object())  # type: ignore[arg-type]

    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 0
