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
    PatchAction,
    PatchValue,
    SourceConstraintApplicability,
    SourceCounterCapabilityProfile,
    SourceCounterConfigurationField,
    SourceCounterConfigurationPatch,
    SourceCounterConfigureRequest,
    SourceCounterConfigureResult,
    SourceCounterEnableRequest,
    SourceCounterEnableResult,
    SourceCounterInputState,
    SourceCounterMeasureRequest,
    SourceCounterMeasurementKind,
    SourceCounterMeasurementV2,
    SourceCounterMeasureResult,
    SourceFacetQueryContract,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceFeatureDirection,
    SourceFieldId,
    SourceInputCoupling,
    SourceProtocolQueryRecord,
    SourceQueryEffect,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceTopologyContract,
    SourceTypedObservation,
    SupportState,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState

from tests.source_v2_fixtures import source_descriptor, source_extensions


class _TextTransport:
    resource = "fake-source-counter-v2"

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


def _counter(
    *,
    enabled: bool = False,
    coupling: SourceInputCoupling = SourceInputCoupling.DC,
    impedance_ohm: float = 1_000_000.0,
    attenuation: int = 1,
    trigger_level_v: float = 0.0,
    statistics_enabled: bool = False,
) -> SourceCounterInputState:
    return SourceCounterInputState(
        input_id="counter",
        enabled=Observed.value_of(enabled),
        measurements=(
            Observed.missing(
                Availability.NOT_APPLICABLE,
                SourceReasonCode.INACTIVE_BY_ANCHOR,
            )
            if not enabled
            else _missing()
        ),
        coupling=Observed.value_of(coupling),
        impedance_ohm=Observed.value_of(impedance_ohm),
        attenuation=Observed.value_of(attenuation),
        gate_time_s=Observed.missing(
            Availability.UNSUPPORTED,
            SourceReasonCode.DESCRIPTOR_UNSUPPORTED,
        ),
        trigger_level_v=Observed.value_of(trigger_level_v),
        statistics_enabled=Observed.value_of(statistics_enabled),
    )


class _CounterDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        enabled: bool = False,
        postcondition_mismatch: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(_TextTransport(), session_state=session_state)
        self.counter = _counter(enabled=enabled)
        self.postcondition_mismatch = postcondition_mismatch
        self.configure_requests: list[SourceCounterConfigureRequest] = []
        self.enable_requests: list[SourceCounterEnableRequest] = []
        self.measure_requests: list[SourceCounterMeasureRequest] = []

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
                    from tests.source_v2_fixtures import basic_facet

                    value = basic_facet()
                elif field.field is SourceFieldId.OUTPUT:
                    from tests.source_v2_fixtures import output_facet

                    value = output_facet()
                else:
                    assert field.field is SourceFieldId.COUNTER
                    value = self._snapshot_counter()
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

    def configure_source_counter_v2(
        self,
        request: SourceCounterConfigureRequest,
    ) -> SourceCounterConfigureResult:
        self.transport.write("SOURCE:COUNTER:CONFIGURE")
        self.configure_requests.append(request)
        values = {
            SourceCounterConfigurationField.COUPLING: ("coupling", request.patch.coupling.value),
            SourceCounterConfigurationField.IMPEDANCE_OHM: (
                "impedance_ohm",
                request.patch.impedance_ohm.value,
            ),
            SourceCounterConfigurationField.ATTENUATION: (
                "attenuation",
                request.patch.attenuation.value,
            ),
            SourceCounterConfigurationField.TRIGGER_LEVEL_V: (
                "trigger_level_v",
                request.patch.trigger_level_v.value,
            ),
            SourceCounterConfigurationField.STATISTICS_ENABLED: (
                "statistics_enabled",
                request.patch.statistics_enabled.value,
            ),
        }
        field = next(
            key
            for key, patch_value in (
                (SourceCounterConfigurationField.COUPLING, request.patch.coupling),
                (SourceCounterConfigurationField.IMPEDANCE_OHM, request.patch.impedance_ohm),
                (SourceCounterConfigurationField.ATTENUATION, request.patch.attenuation),
                (SourceCounterConfigurationField.TRIGGER_LEVEL_V, request.patch.trigger_level_v),
                (SourceCounterConfigurationField.STATISTICS_ENABLED, request.patch.statistics_enabled),
            )
            if patch_value.action is PatchAction.SET
        )
        name, value = values[field]
        self.counter = replace(self.counter, **{name: Observed.value_of(value)})
        return SourceCounterConfigureResult(request.input_id, self.counter)

    def set_source_counter_enabled_v2(
        self,
        request: SourceCounterEnableRequest,
    ) -> SourceCounterEnableResult:
        self.transport.write("SOURCE:COUNTER:ENABLE")
        self.enable_requests.append(request)
        self.counter = replace(self.counter, enabled=Observed.value_of(request.enabled))
        return SourceCounterEnableResult(request.input_id, request.enabled)

    def measure_source_counter_v2(
        self,
        request: SourceCounterMeasureRequest,
    ) -> SourceCounterMeasureResult:
        self.transport.query("SOURCE:COUNTER:MEASURE?")
        self.measure_requests.append(request)
        return SourceCounterMeasureResult(
            request.input_id,
            (
                SourceCounterMeasurementV2(SourceCounterMeasurementKind.DUTY_PERCENT, 40.0),
                SourceCounterMeasurementV2(SourceCounterMeasurementKind.FREQUENCY_HZ, 1_000.0),
            ),
        )

    def _snapshot_counter(self) -> SourceCounterInputState:
        if not self.postcondition_mismatch or not self.configure_requests:
            return self.counter
        return replace(self.counter, coupling=Observed.value_of(SourceInputCoupling.DC))


def _extensions():
    base = source_extensions()
    counter = SourceFeatureCapability(
        feature=SourceFeature.COUNTER,
        support=SupportState.SUPPORTED,
        directions=(
            SourceFeatureDirection.CONFIGURE,
            SourceFeatureDirection.DISABLE,
            SourceFeatureDirection.ENABLE,
            SourceFeatureDirection.READ,
        ),
        scope=SourceFacetScope.INPUT,
        channels=(),
        applicability=SourceConstraintApplicability(),
        profile=SourceCounterCapabilityProfile(
            input_ids=("counter",),
            measurement_kinds=(
                SourceCounterMeasurementKind.DUTY_PERCENT,
                SourceCounterMeasurementKind.FREQUENCY_HZ,
            ),
            configuration_readable=True,
            query_effect=SourceQueryEffect.PURE_READ,
            readable_configuration_fields=(
                SourceCounterConfigurationField.ATTENUATION,
                SourceCounterConfigurationField.COUPLING,
                SourceCounterConfigurationField.IMPEDANCE_OHM,
                SourceCounterConfigurationField.STATISTICS_ENABLED,
                SourceCounterConfigurationField.TRIGGER_LEVEL_V,
            ),
            configurable_fields=(
                SourceCounterConfigurationField.ATTENUATION,
                SourceCounterConfigurationField.COUPLING,
                SourceCounterConfigurationField.IMPEDANCE_OHM,
                SourceCounterConfigurationField.STATISTICS_ENABLED,
                SourceCounterConfigurationField.TRIGGER_LEVEL_V,
            ),
            enabled_configurable=True,
        ),
    )
    counter_query = SourceFacetQueryContract(
        feature=SourceFeature.COUNTER,
        scope=SourceFacetScope.INPUT,
        fields=(SourceFieldId.COUNTER,),
        activation_any=(),
        effect=SourceQueryEffect.PURE_READ,
        max_queries=1,
        required=True,
    )
    return replace(
        base,
        topology=SourceTopologyContract((1,), input_ids=("counter",)),
        features=tuple(
            sorted(
                (*base.features, counter),
                key=lambda item: (item.feature.value, item.scope.value, item.channels),
            )
        ),
        query_contract=replace(
            base.query_contract,
            facets=tuple(
                sorted(
                    (*base.query_contract.facets, counter_query),
                    key=lambda item: (
                        item.feature.value,
                        item.scope.value,
                        tuple(field.value for field in item.fields),
                    ),
                )
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
    enabled: bool = False,
    postcondition_mismatch: bool = False,
) -> tuple[SourceService, _CounterDriver]:
    session_state = InstrumentSessionState(epoch_id="source-counter-v2")
    driver = _CounterDriver(
        session_state=session_state,
        enabled=enabled,
        postcondition_mismatch=postcondition_mismatch,
    )
    descriptor = replace(
        source_descriptor(extensions=_extensions()),
        capabilities=(
            "source.snapshot_v2",
            "source.counter_configure_v2",
            "source.counter_enable_v2",
            "source.counter_measure_v2",
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


def _configure_request() -> SourceCounterConfigureRequest:
    return SourceCounterConfigureRequest(
        "counter",
        SourceCounterConfigurationPatch(
            coupling=PatchValue(PatchAction.SET, SourceInputCoupling.AC)
        ),
    )


def test_counter_configure_v2_writes_once_and_records_no_rollback_boundary() -> None:
    service, driver = _service()

    result, artifact = service.configure_counter_v2(_configure_request())

    assert result.state.coupling.value is SourceInputCoupling.AC
    assert driver.configure_requests == [_configure_request()]
    assert driver.enable_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["mutation"]["status"] == "written"
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "counter_input_id": "counter",
        "automatic_rollback": "not_available",
    }


def test_counter_enable_v2_is_independent_from_configuration() -> None:
    service, driver = _service()
    request = SourceCounterEnableRequest("counter", True)

    result, artifact = service.set_counter_enabled_v2(request)

    assert result.enabled is True
    assert driver.configure_requests == []
    assert driver.enable_requests == [request]
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.counter_enable_v2"


def test_counter_measure_v2_queries_once_after_enabled_preflight_without_writes() -> None:
    service, driver = _service(enabled=True)
    request = SourceCounterMeasureRequest("counter")

    result = service.measure_counter_v2(request)

    assert result.input_id == "counter"
    assert driver.measure_requests == [request]
    assert driver.configure_requests == []
    assert driver.enable_requests == []
    assert driver.transport.counters.write_requests == 0
    assert driver.transport.counters.query_calls == 2


def test_counter_measure_v2_refuses_disabled_counter_before_measurement_query() -> None:
    service, driver = _service(enabled=False)

    with pytest.raises(ConfigError, match="requires Counter enabled"):
        service.measure_counter_v2(SourceCounterMeasureRequest("counter"))

    assert driver.measure_requests == []
    assert driver.transport.counters.write_requests == 0


def test_counter_postcondition_failure_does_not_write_rollback_or_disable() -> None:
    service, driver = _service(postcondition_mismatch=True)

    with pytest.raises(ConfigError, match="readback does not match") as raised:
        service.configure_counter_v2(_configure_request())

    artifact = raised.value.source_operation_artifact
    assert driver.configure_requests == [_configure_request()]
    assert driver.enable_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["recovery"] == {
        "status": "not_attempted",
        "reason": "counter_state_not_rollback_safe",
    }
    assert artifact["final_state"]["session_health"] == "poisoned"
