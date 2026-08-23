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
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceFeatureDirection,
    SourceFieldId,
    SourceOutputRequest,
    SourceOutputResult,
    SourceProtocolQueryRecord,
    SourceQueryExecutionRecord,
    SourceQueryItemOutcome,
    SourceReasonCode,
    SourceRuntimeIdentity,
    SourceTypedObservation,
)
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
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
    resource = "fake-source-output-v2"

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


class _OutputDriver:
    def __init__(
        self,
        *,
        session_state: InstrumentSessionState,
        enabled: dict[int, bool] | None = None,
        final_vpp_available: bool = True,
        ignore_enable: bool = False,
        raise_after_output_write: bool = False,
    ) -> None:
        self.transport = GuardedAuditedTransport(
            _TextTransport(),
            session_state=session_state,
        )
        self.enabled = dict(enabled or {1: False, 2: False})
        self.final_vpp_available = final_vpp_available
        self.ignore_enable = ignore_enable
        self.raise_after_output_write = raise_after_output_write
        self.output_requests: list[SourceOutputRequest] = []
        self.v1_output_calls = 0
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.transport.close()

    def execute_source_query_plan_v2(self, plan) -> SourceQueryExecutionRecord:
        self.transport.query("SOURCE:STATE?")
        records = []
        for index, item in enumerate(plan.items):
            observations = []
            for field in item.fields:
                if field.field is SourceFieldId.IDENTITY:
                    value = SourceRuntimeIdentity(
                        manufacturer="Example",
                        model="EX1",
                        firmware_id="1.0",
                    )
                elif field.field is SourceFieldId.BASIC:
                    assert field.target.channel is not None
                    value = self._basic(field.target.channel)
                elif field.field is SourceFieldId.OUTPUT:
                    assert field.target.channel is not None
                    value = output_facet(enabled=self.enabled[field.target.channel])
                else:  # pragma: no cover - the fixture descriptor only needs these fields.
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

    def set_source_output_v2(self, request: SourceOutputRequest) -> SourceOutputResult:
        self.transport.write(f"SOURCE:OUTPUT {request.channel} {'ON' if request.enabled else 'OFF'}")
        self.output_requests.append(request)
        if self.raise_after_output_write:
            raise ConfigError("fake output write failed after transmission")
        if not request.enabled or not self.ignore_enable:
            self.enabled[request.channel] = request.enabled
        if not request.enabled:
            return SourceOutputResult(channel=request.channel, enabled=False)
        return SourceOutputResult(
            channel=request.channel,
            enabled=True,
            final_amplitude=SourceAmplitude(1.0, SourceAmplitudeUnit.VPP),
            final_offset_v=0.0,
        )

    def set_output(self, *args, **kwargs):
        del args, kwargs
        self.v1_output_calls += 1
        raise AssertionError("M5-C must not fall back to the V1 output route")

    def _basic(self, channel: int):
        del channel
        if self.final_vpp_available:
            return basic_facet()
        return replace(
            basic_facet(),
            amplitude=Observed.value_of(SourceAmplitude(1.0, SourceAmplitudeUnit.VRMS)),
            offset_v=Observed.missing(
                Availability.NOT_QUERIED,
                SourceReasonCode.NOT_REQUESTED,
            ),
        )


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


def _extensions(*, final_vpp_available: bool):
    base = source_extensions()
    basic, output = base.features
    if not final_vpp_available:
        basic = replace(
            basic,
            profile=replace(
                basic.profile,
                amplitude_units=(SourceAmplitudeUnit.VRMS,),
                offset_readable=False,
            ),
        )
    second_basic = replace(basic, channels=(2,))
    output = replace(
        output,
        directions=(
            SourceFeatureDirection.DISABLE,
            SourceFeatureDirection.ENABLE,
            SourceFeatureDirection.READ,
        ),
    )
    second_output = replace(output, channels=(2,))
    return replace(
        base,
        topology=replace(base.topology, channels=(1, 2)),
        features=(basic, second_basic, output, second_output),
        query_contract=replace(base.query_contract, max_queries=10),
    )


def _service(
    *,
    enabled: dict[int, bool] | None = None,
    final_vpp_available: bool = True,
    ignore_enable: bool = False,
    raise_after_output_write: bool = False,
    limits: SafetyLimitsConfig = SafetyLimitsConfig(),
) -> tuple[SourceService, _OutputDriver]:
    session_state = InstrumentSessionState(epoch_id="source-output-v2")
    driver = _OutputDriver(
        session_state=session_state,
        enabled=enabled,
        final_vpp_available=final_vpp_available,
        ignore_enable=ignore_enable,
        raise_after_output_write=raise_after_output_write,
    )
    descriptor = replace(
        source_descriptor(driver=driver, extensions=_extensions(final_vpp_available=final_vpp_available)),
        capabilities=("source.snapshot_v2", "source.output_v2"),
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


def test_output_v2_enables_and_disables_one_port_with_independent_readback() -> None:
    service, driver = _service()

    enabled = service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=True))
    disabled = service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=False))

    assert enabled.result.enabled is True
    assert enabled.artifact["operation"] == "source.output_enable_v2"
    assert enabled.artifact["final_state"]["output_expected"] == "on"
    assert disabled.result.enabled is False
    assert disabled.artifact["operation"] == "source.output_disable_v2"
    assert disabled.artifact["final_state"]["output_expected"] == "off"
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=True),
        SourceOutputRequest(channel=1, enabled=False),
    ]
    assert driver.transport.counters.write_completed == 2
    assert "fake-source-output-v2" not in repr(enabled.artifact)


def test_output_v2_allows_second_independent_port_to_turn_on() -> None:
    service, driver = _service()

    first = service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=True))
    second = service._set_output_v2_transaction(SourceOutputRequest(channel=2, enabled=True))

    assert first.result.enabled is True
    assert second.result.enabled is True
    assert driver.enabled == {1: True, 2: True}
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=True),
        SourceOutputRequest(channel=2, enabled=True),
    ]
    assert all(request.enabled for request in driver.output_requests)


def test_output_disable_v2_does_not_require_final_vpp_or_offset() -> None:
    service, driver = _service(
        enabled={1: True, 2: False},
        final_vpp_available=False,
    )

    transaction = service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=False))

    assert transaction.result == SourceOutputResult(channel=1, enabled=False)
    assert driver.enabled[1] is False
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]


def test_output_enable_v2_rejects_missing_final_vpp_or_offset_before_write() -> None:
    service, driver = _service(final_vpp_available=False)

    with pytest.raises(ConfigError, match="final Vpp amplitude"):
        service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=True))

    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0


def test_output_enable_v2_applies_configured_vpp_limit_before_write() -> None:
    service, driver = _service(limits=SafetyLimitsConfig(max_source_vpp=0.5))

    with pytest.raises(ConfigError, match="max_source_vpp"):
        service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=True))

    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0


def test_output_enable_v2_postcondition_mismatch_runs_one_off_recovery() -> None:
    service, driver = _service(ignore_enable=True)

    with pytest.raises(ConfigError, match="postcondition reports output OFF") as raised:
        service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=True))

    artifact = raised.value.source_operation_artifact
    assert driver.output_requests == [
        SourceOutputRequest(channel=1, enabled=True),
        SourceOutputRequest(channel=1, enabled=False),
    ]
    assert artifact["recovery"] == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert driver.v1_output_calls == 0
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_output_disable_v2_never_retries_an_unknown_off_result() -> None:
    service, driver = _service(
        enabled={1: True, 2: False},
        raise_after_output_write=True,
    )

    with pytest.raises(ConfigError, match="failed after transmission") as raised:
        service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=False))

    artifact = raised.value.source_operation_artifact
    assert driver.output_requests == [SourceOutputRequest(channel=1, enabled=False)]
    assert artifact["recovery"] == {
        "status": "not_attempted",
        "reason": "off_result_unknown_not_retried",
    }
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.POISONED


def test_output_v2_idempotent_requests_send_no_write() -> None:
    service, driver = _service(enabled={1: True, 2: False})

    already_on = service._set_output_v2_transaction(SourceOutputRequest(channel=1, enabled=True))
    already_off = service._set_output_v2_transaction(SourceOutputRequest(channel=2, enabled=False))

    assert already_on.artifact["mutation"]["status"] == "already_at_target"
    assert already_off.artifact["mutation"]["status"] == "already_at_target"
    assert driver.output_requests == []
    assert driver.transport.counters.write_requests == 0
