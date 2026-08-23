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
    Observed,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceBasicConfigureRequest,
    SourceBasicConfigureResult,
    SourceBasicPatch,
    SourceFeatureDirection,
    SourceFieldId,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceRuntimeIdentity,
    SourceTypedObservation,
    PatchAction,
    PatchValue,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.services.source_state import RestorableSourceState
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth

from tests.source_v2_fixtures import (
    basic_facet,
    output_facet,
    source_descriptor,
    source_extensions,
)


class _TextTransport:
    resource = "fake-source-v2"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []

    def record_event(self, direction: str, text: str) -> None:
        del direction, text

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        del replay
        self.queries.append(command)
        return "ok"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        pass


class _BasicWriteDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        combined: bool,
        output_enabled: bool = False,
        postcondition_frequency_hz: float | None = None,
        raise_after_write: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.combined = combined
        self.output_enabled = output_enabled
        self.postcondition_frequency_hz = postcondition_frequency_hz
        self.raise_after_write = raise_after_write
        self.basic = basic_facet()
        self.basic_requests: list[SourceBasicConfigureRequest] = []
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_output_calls = 0
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.transport.close()

    def execute_source_query_plan_v2(self, plan) -> SourceQueryExecutionRecord:
        records = []
        for index, item in enumerate(plan.items):
            if not self.combined or index == 0:
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
                    value = self._readback_basic()
                elif field.field is SourceFieldId.OUTPUT:
                    value = output_facet(enabled=self.output_enabled)
                else:  # pragma: no cover - the fixture descriptor only needs these fields.
                    raise AssertionError(field)
                observations.append(SourceTypedObservation(field, value))
            records.append(
                SourceProtocolQueryRecord(
                    item_id=item.item_id,
                    effect=item.effect,
                    outcome=SourceQueryItemOutcome.OBSERVED,
                    query_count=(1 if not self.combined or index == 0 else 0),
                    observations=tuple(observations),
                )
            )
        return SourceQueryExecutionRecord(
            contract_version=SOURCE_CONTRACT_VERSION,
            plan_id=plan.plan_id,
            items=tuple(records),
            query_count=(1 if self.combined else len(records)),
            device_revision_token_before="revision-1",
            device_revision_token_after="revision-1",
        )

    def configure_source_basic_v2(
        self,
        request: SourceBasicConfigureRequest,
    ) -> SourceBasicConfigureResult:
        self.transport.write("SOURCE:CONFIGURE")
        self.basic_requests.append(request)
        self.basic = self._apply_patch(request)
        if self.raise_after_write:
            raise ConfigError("fake basic configure failed after write")
        return SourceBasicConfigureResult(
            channel=request.channel,
            basic=self.basic,
            output_enabled=False,
        )

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write("SOURCE:OUTPUT OFF")
        self.output_requests.append(request)
        self.output_enabled = request.enabled
        return SourceOutputResult(channel=request.channel, enabled=request.enabled)

    def set_output(self, *args, **kwargs):
        del args, kwargs
        self.v1_output_calls += 1
        raise AssertionError("M5-B recovery must not fall back to the V1 output route")

    def _readback_basic(self):
        if self.postcondition_frequency_hz is None or not self.basic_requests:
            return self.basic
        return replace(
            self.basic,
            frequency_hz=Observed.value_of(self.postcondition_frequency_hz),
        )

    def _apply_patch(self, request: SourceBasicConfigureRequest):
        patch = request.patch
        updates = {}
        if patch.waveform_kind.action is PatchAction.SET:
            updates["waveform_kind"] = Observed.value_of(patch.waveform_kind.value)
        if patch.frequency_hz.action is PatchAction.SET:
            updates["frequency_hz"] = Observed.value_of(patch.frequency_hz.value)
        if patch.amplitude_vpp.action is PatchAction.SET:
            updates["amplitude"] = Observed.value_of(
                SourceAmplitude(patch.amplitude_vpp.value, SourceAmplitudeUnit.VPP)
            )
        if patch.offset_v.action is PatchAction.SET:
            updates["offset_v"] = Observed.value_of(patch.offset_v.value)
        if patch.square_duty_cycle_percent.action is PatchAction.SET:
            updates["square_duty_cycle_percent"] = Observed.value_of(
                patch.square_duty_cycle_percent.value
            )
        return replace(self.basic, **updates)


def _config(*, limits: SafetyLimitsConfig = SafetyLimitsConfig()) -> WaveBenchConfig:
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
            True,
            True,
            0,
        ),
        safety_limits=limits,
    )


def _write_extensions(*, include_output: bool):
    extensions = source_extensions()
    basic, output = extensions.features
    return replace(
        extensions,
        features=(
            replace(
                basic,
                directions=(
                    SourceFeatureDirection.CONFIGURE,
                    SourceFeatureDirection.READ,
                ),
            ),
            replace(
                output,
                directions=(
                    (
                        SourceFeatureDirection.DISABLE,
                        SourceFeatureDirection.ENABLE,
                        SourceFeatureDirection.READ,
                    )
                    if include_output
                    else (SourceFeatureDirection.READ,)
                ),
            ),
        ),
    )


def _service(
    *,
    combined: bool = True,
    include_output: bool = True,
    output_enabled: bool = False,
    postcondition_frequency_hz: float | None = None,
    raise_after_write: bool = False,
    limits: SafetyLimitsConfig = SafetyLimitsConfig(),
) -> tuple[SourceService, _BasicWriteDriver]:
    session_state = InstrumentSessionState(epoch_id="source-basic-v2")
    driver = _BasicWriteDriver(
        session_state=session_state,
        combined=combined,
        output_enabled=output_enabled,
        postcondition_frequency_hz=postcondition_frequency_hz,
        raise_after_write=raise_after_write,
    )
    extensions = _write_extensions(include_output=include_output)
    capabilities = ["source.snapshot_v2", "source.basic_configure_v2"]
    if include_output:
        capabilities.append("source.output_v2")
    descriptor = replace(
        source_descriptor(driver=driver, extensions=extensions),
        capabilities=tuple(capabilities),
    )
    validate_source_descriptor(descriptor)
    validate_declared_capabilities(descriptor, driver)
    return (
        SourceService(
            config=_config(limits=limits),
            logger=CommandLogger(),
            session=driver,  # type: ignore[arg-type]
            descriptor=descriptor,
            transport=driver.transport,
            session_state=session_state,
        ),
        driver,
    )


def _frequency_request(value_hz: float = 2_000.0) -> SourceBasicConfigureRequest:
    return SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(
            frequency_hz=PatchValue(PatchAction.SET, value_hz),
        ),
    )


@pytest.mark.parametrize("combined", (True, False))
def test_basic_configure_v2_public_service_supports_combined_and_scalar_queries(
    combined: bool,
) -> None:
    service, driver = _service(combined=combined)
    request = _frequency_request()

    result, artifact = service.configure_basic_v2(request, correlation_id="basic-write")

    assert result.basic.frequency_hz.value == 2_000.0
    assert driver.basic_requests == [request]
    assert driver.output_requests == []
    assert driver.transport.counters.write_completed == 1
    assert artifact["operation"] == "source.basic_configure_v2"
    assert artifact["request"]["channel"] == 1
    assert artifact["final_state"] == {
        "session_health": "healthy",
        "output_expected": "off",
    }
    assert [item["phase"] for item in artifact["phases"]] == [
        "preflight",
        "main",
        "postcondition",
    ]
    assert "fake-source-v2" not in repr(artifact)
    assert "SOURCE:STATE?" not in repr(artifact)


def test_v1_frequency_route_maps_to_v2_for_a_dual_contract_driver() -> None:
    service, driver = _service()
    assert service.descriptor is not None
    service.descriptor = replace(
        service.descriptor,
        capabilities=(
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
            "source.set_frequency",
        ),
    )

    status = service.set_frequency(channel=1, value_hz=2_000.0)

    assert status.channel == 1
    assert status.output == "OFF"
    assert status.frequency_hz == 2_000.0
    assert driver.basic_requests == [_frequency_request()]
    assert driver.transport.counters.write_completed == 1


@pytest.mark.parametrize(
    ("method", "value", "patch_field", "expected"),
    (
        ("set_frequency", 2_000.0, "frequency_hz", 2_000.0),
        ("set_function", "SIN", "waveform_kind", "sine"),
        ("set_amplitude_vpp", 1.5, "amplitude_vpp", 1.5),
        ("set_square_duty_cycle", 25.0, "square_duty_cycle_percent", 25.0),
    ),
)
def test_all_v1_basic_routes_use_the_v2_transaction_when_declared(
    method: str,
    value: object,
    patch_field: str,
    expected: object,
) -> None:
    service, driver = _service()

    if method == "set_frequency":
        service.set_frequency(channel=1, value_hz=value)
    elif method == "set_function":
        service.set_function(channel=1, function=value)
    elif method == "set_amplitude_vpp":
        service.set_amplitude_vpp(channel=1, value_vpp=value)
    else:
        service.set_square_duty_cycle(channel=1, duty_percent=value)

    assert len(driver.basic_requests) == 1
    patch_value = getattr(driver.basic_requests[0].patch, patch_field)
    assert patch_value.action is PatchAction.SET
    assert getattr(patch_value.value, "value", patch_value.value) == expected
    assert driver.transport.counters.write_completed == 1


def test_v1_restore_route_rejects_before_io_for_a_dual_contract_driver() -> None:
    service, driver = _service()

    with pytest.raises(ConfigError, match="restore_restorable_state cannot run"):
        service.restore_restorable_state(
            RestorableSourceState(
                channel=1,
                output="OFF",
                function="SIN",
                frequency_hz=1_000.0,
                amplitude_vpp=1.0,
                amplitude_unit="VPP",
            )
        )

    assert driver.transport.counters.write_requests == 0


@pytest.mark.parametrize(
    "operation",
    ("upload", "trigger_burst", "trigger_sweep"),
)
def test_overlapping_v1_routes_reject_before_io_for_a_dual_contract_driver(operation: str) -> None:
    service, driver = _service()

    with pytest.raises(ConfigError, match="cannot run for a Source V2 write driver"):
        if operation == "upload":
            service.upload_arbitrary_waveform(
                channel=1,
                file_path="unused.npy",
                playback_frequency_hz=1_000.0,
                amplitude_vpp=1.0,
            )
        elif operation == "trigger_burst":
            service.trigger_burst(channel=1)
        else:
            service.trigger_sweep(channel=1)

    assert driver.transport.counters.write_requests == 0


def test_basic_configure_v2_rejects_target_output_on_before_write() -> None:
    service, driver = _service(output_enabled=True)

    with pytest.raises(ConfigError, match="target output OFF"):
        service._configure_basic_v2_transaction(_frequency_request())

    assert driver.basic_requests == []
    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_basic_configure_v2_rejects_configured_limits_before_write() -> None:
    service, driver = _service(limits=SafetyLimitsConfig(max_source_vpp=2.0))
    request = SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(
            amplitude_vpp=PatchValue(PatchAction.SET, 2.5),
        ),
    )

    with pytest.raises(ConfigError, match="max_source_vpp"):
        service._configure_basic_v2_transaction(request)

    assert driver.basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_configure_v2_rejects_configured_absolute_port_limits_before_write() -> None:
    service, driver = _service(
        limits=SafetyLimitsConfig(
            min_source_port_voltage_v=-1.0,
            max_source_port_voltage_v=1.0,
        )
    )
    request = SourceBasicConfigureRequest(
        channel=1,
        patch=SourceBasicPatch(offset_v=PatchValue(PatchAction.SET, 1.0)),
    )

    with pytest.raises(ConfigError, match="port voltage"):
        service._configure_basic_v2_transaction(request)

    assert driver.basic_requests == []
    assert driver.transport.counters.write_requests == 0


def test_basic_configure_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(postcondition_frequency_hz=2_001.0)

    with pytest.raises(ConfigError, match="frequency_hz readback") as raised:
        service._configure_basic_v2_transaction(_frequency_request())

    artifact = raised.value.source_operation_artifact
    assert driver.basic_requests == [_frequency_request()]
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert driver.transport.counters.write_completed == 2
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert artifact["safe_state_verified"] is True
    assert artifact["final_state"]["session_health"] == "uncertain"
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_basic_configure_v2_never_falls_back_to_v1_output_for_recovery() -> None:
    service, driver = _service(include_output=False, raise_after_write=True)

    with pytest.raises(ConfigError, match="failed after write") as raised:
        service._configure_basic_v2_transaction(_frequency_request())

    artifact = raised.value.source_operation_artifact
    assert driver.basic_requests == [_frequency_request()]
    assert driver.output_requests == []
    assert driver.v1_output_calls == 0
    assert artifact["recovery"] == {
        "status": "not_attempted",
        "reason": "output_capability_unavailable",
    }
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.POISONED
