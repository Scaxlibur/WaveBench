from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wavebench import cli
from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.logging import CommandLogger
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.source_service import SourceService
from wavebench.services.source_snapshot_v2 import SourceSnapshotContractError
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import SourceStatus
from wavebench.instruments.registry import InstrumentRegistry
from wavebench.instruments import InstrumentDescriptor
from wavebench.instruments.source_extensions import (
    SOURCE_OPERATION_ARTIFACT_SCHEMA,
    SOURCE_SNAPSHOT_SCHEMA,
    SnapshotConsistencyState,
    SourceQueryExecutionRecord,
    SourceCrossChannelCapabilityProfile,
    SourceFacetScope,
    SourceFeature,
    SourceFeatureCapability,
    SourceHarmonicPreset,
    SourceTopologyContract,
    SupportState,
    source_snapshot_v2_operation_artifact,
)
from wavebench.transport.session import InstrumentSessionState
from wavebench.transport.session import SessionHealth
from wavebench.errors import ConfigError, TransportIOError
from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)

from tests.source_v2_fixtures import (
    SourceV2FakeDriver,
    source_descriptor,
    source_extensions_with_harmonics,
)
from wavebench.instruments.source_extensions import SourceConstraintApplicability


def make_config() -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1000, 1000),
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
    )


def make_service(
    driver: SourceV2FakeDriver,
    *,
    session_health: SessionHealth = SessionHealth.HEALTHY,
) -> SourceService:
    return SourceService(
        config=make_config(),
        logger=CommandLogger(),
        session=driver,  # type: ignore[arg-type]
        descriptor=source_descriptor(driver=driver),
        session_state=InstrumentSessionState(
            epoch_id="epoch-source-v2",
            health=session_health,
        ),
    )


@pytest.mark.parametrize(("combined", "query_count"), [(True, 1), (False, 6)])
def test_snapshot_v2_accepts_combined_and_scalar_protocol_plans(
    combined: bool,
    query_count: int,
) -> None:
    driver = SourceV2FakeDriver(combined=combined)
    snapshot = make_service(driver).snapshot_v2(correlation_id="test-correlation")

    assert snapshot.consistency.state is SnapshotConsistencyState.CONSISTENT
    assert snapshot.query_count == query_count
    assert snapshot.runtime_profile.identity.model == "EX1"
    assert snapshot.channels[0].basic.value.frequency_hz.value == 1000.0
    assert snapshot.channels[0].output.value.enabled.value is False
    assert snapshot.correlation_id == "test-correlation"
    assert len(driver.plans) == 1
    assert driver.plans[0].allowed_effects[0].value == "pure_read"


def test_snapshot_v2_runtime_identity_can_only_narrow_descriptor_features() -> None:
    extensions = source_extensions_with_harmonics()
    narrowed_output = replace(
        extensions.features[-1],
        applicability=SourceConstraintApplicability(firmware_ids=("2.0",)),
    )
    extensions = replace(
        extensions,
        features=(*extensions.features[:-1], narrowed_output),
    )
    driver = SourceV2FakeDriver(combined=True)
    service = make_service(driver)
    service.descriptor = source_descriptor(driver=driver, extensions=extensions)

    snapshot = service.snapshot_v2()
    assert all(
        feature.feature.value != "output" for feature in snapshot.runtime_profile.features
    )
    assert snapshot.channels[0].output.availability.value == "unsupported"


def test_snapshot_v2_preserves_declared_input_and_relation_placeholders() -> None:
    extensions = source_extensions_with_harmonics()
    relation = SourceFeatureCapability(
        feature=SourceFeature.COMBINE,
        support=SupportState.UNSUPPORTED,
        directions=(),
        scope=SourceFacetScope.CHANNEL_SET,
        channels=(1, 2),
        applicability=SourceConstraintApplicability(),
        profile=SourceCrossChannelCapabilityProfile(
            relation_kinds=(SourceFeature.COMBINE,),
            supported_channel_sets=((1, 2),),
            relation_graph_readable=False,
            shared_power_constraint_readable=False,
        ),
    )
    second_basic = replace(extensions.features[0], channels=(2,))
    second_output = replace(extensions.features[-1], channels=(2,))
    extensions = replace(
        extensions,
        topology=SourceTopologyContract((1, 2), ("counter",)),
        features=(
            extensions.features[0],
            second_basic,
            relation,
            extensions.features[1],
            extensions.features[-1],
            second_output,
        ),
        query_contract=replace(extensions.query_contract, max_queries=11),
    )
    driver = SourceV2FakeDriver(combined=True)
    service = make_service(driver)
    service.descriptor = source_descriptor(driver=driver, extensions=extensions)

    snapshot = service.snapshot_v2()

    counter = snapshot.system.value.counters[0]
    assert counter.input_id == "counter"
    assert counter.enabled.availability.value == "unsupported"
    relation_state = snapshot.cross_channel.value.relations[0]
    assert relation_state.feature is SourceFeature.COMBINE
    assert relation_state.enabled.availability.value == "unsupported"


def test_snapshot_v2_reports_anchor_drift_without_authorizing_writes() -> None:
    snapshot = make_service(SourceV2FakeDriver(combined=False, drift=True)).snapshot_v2()

    assert snapshot.consistency.state is SnapshotConsistencyState.DRIFTED
    assert snapshot.consistency.reason_code.value == "consistency_drifted"


def test_snapshot_v2_core_derives_inactive_and_unavailable_facets() -> None:
    extensions = source_extensions_with_harmonics()
    inactive_driver = SourceV2FakeDriver(combined=True)
    inactive_service = make_service(inactive_driver)
    inactive_service.descriptor = source_descriptor(
        driver=inactive_driver,
        extensions=extensions,
    )
    inactive = inactive_service.snapshot_v2()
    assert inactive.channels[0].harmonics.availability.value == "not_applicable"
    assert inactive.channels[0].harmonics.reason_code.value == "inactive_by_anchor"

    unavailable_driver = SourceV2FakeDriver(
        combined=False,
        harmonic_unavailable=True,
    )
    unavailable_service = make_service(unavailable_driver)
    unavailable_service.descriptor = source_descriptor(
        driver=unavailable_driver,
        extensions=extensions,
    )
    unavailable = unavailable_service.snapshot_v2()
    assert unavailable.channels[0].harmonics.availability.value == "unavailable"
    assert unavailable.channels[0].harmonics.reason_code.value == "response_missing_field"


def test_snapshot_v2_rejects_skipped_facet_when_activation_anchor_is_unknown() -> None:
    driver = SourceV2FakeDriver(combined=True, anchor_unknown=True)
    service = make_service(driver)
    service.descriptor = source_descriptor(
        driver=driver,
        extensions=source_extensions_with_harmonics(),
    )

    with pytest.raises(SourceSnapshotContractError, match="without proven activation"):
        service.snapshot_v2()


def test_snapshot_v2_rejects_query_count_overrun() -> None:
    class BadDriver(SourceV2FakeDriver):
        def execute_source_query_plan_v2(self, plan):
            result = super().execute_source_query_plan_v2(plan)
            records = list(result.items)
            records[0] = replace(records[0], query_count=2)
            return SourceQueryExecutionRecord(
                contract_version=result.contract_version,
                plan_id=result.plan_id,
                items=tuple(records),
                query_count=result.query_count + 1,
                device_revision_token_before=result.device_revision_token_before,
                device_revision_token_after=result.device_revision_token_after,
            )

    with pytest.raises(SourceSnapshotContractError, match="item contract"):
        make_service(BadDriver(combined=True)).snapshot_v2()


def test_snapshot_v2_rejects_invalid_execution_record_type() -> None:
    class BadDriver(SourceV2FakeDriver):
        def execute_source_query_plan_v2(self, plan):
            return object()

    with pytest.raises(SourceSnapshotContractError, match="invalid query execution"):
        make_service(BadDriver(combined=True)).snapshot_v2()


def test_snapshot_v2_rejects_unhealthy_session_before_driver_call() -> None:
    driver = SourceV2FakeDriver(combined=True)
    service = make_service(driver, session_health=SessionHealth.UNCERTAIN)

    with pytest.raises(SourceSnapshotContractError, match="healthy"):
        service.snapshot_v2()
    assert driver.plans == []


def test_new_core_keeps_a_v1_entry_point_usable_and_rejects_v2_before_driver_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LegacyDriver:
        def __init__(self) -> None:
            self.frequency_calls = 0
            self.error_checks = 0
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def errors(self, limit: int = 8) -> list[str]:
            del limit
            return []

        def assert_no_errors(self) -> None:
            self.error_checks += 1

        def set_frequency(
            self,
            channel: int,
            value_hz: float,
            *,
            ensure_fix_mode: bool = True,
            check_errors: bool = True,
        ) -> SourceStatus:
            del ensure_fix_mode, check_errors
            self.frequency_calls += 1
            return SourceStatus(
                channel=channel,
                output="OFF",
                function="SIN",
                frequency_hz=value_hz,
                amplitude=1.0,
                amplitude_unit="VPP",
                offset_v=0.0,
                phase_deg=0.0,
                frequency_mode="FIX",
                sweep_enabled="OFF",
                apply_raw="SIN,1000,1,0",
            )

    class EntryPoint:
        group = "wavebench.instruments"
        dist = None

        def __init__(self, descriptor: InstrumentDescriptor) -> None:
            self.name = descriptor.driver_id
            self._descriptor = descriptor
            self.load_count = 0

        def load(self):
            self.load_count += 1
            return lambda: self._descriptor

    driver = LegacyDriver()
    factory_calls = 0

    def factory(context):
        nonlocal factory_calls
        del context
        factory_calls += 1
        return driver

    descriptor = InstrumentDescriptor(
        driver_id="example.legacy-source",
        kind="source",
        display_name="Example Legacy Source",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=("source.errors", "source.set_frequency"),
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=factory,
    )
    entry_point = EntryPoint(descriptor)
    resolved = InstrumentRegistry(external_entry_points=(entry_point,)).resolve(
        "example.legacy-source",
        expected_kind="source",
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda reference, expected_kind: resolved,
    )
    opened = open_instrument_driver(
        driver_reference="example.legacy-source",
        expected_kind="source",
        resource="TCPIP::legacy-source::INSTR",
        configured_backend="lan",
        timeout_ms=1000,
        opc_timeout_ms=1000,
        read_retry_attempts=0,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )
    service = SourceService(
        config=make_config(),
        logger=CommandLogger(),
        session=opened.driver,  # type: ignore[arg-type]
        descriptor=opened.descriptor,
    )

    status = service.set_frequency(channel=1, value_hz=1234.0)
    calls_before_v2 = (factory_calls, driver.frequency_calls, driver.error_checks)
    with pytest.raises(ConfigError, match="missing capabilities: source.snapshot_v2"):
        service.snapshot_v2()

    assert status.frequency_hz == 1234.0
    assert entry_point.load_count == 1
    assert calls_before_v2 == (1, 1, 1)
    assert (factory_calls, driver.frequency_calls, driver.error_checks) == calls_before_v2


def test_snapshot_v2_rejects_session_health_change_after_query() -> None:
    class DegradingDriver(SourceV2FakeDriver):
        session_state = None

        def execute_source_query_plan_v2(self, plan):
            result = super().execute_source_query_plan_v2(plan)
            self.session_state.degrade(SessionHealth.UNCERTAIN, reason="test_query_uncertain")
            return result

    driver = DegradingDriver(combined=True)
    service = make_service(driver)
    driver.session_state = service.session_state

    with pytest.raises(SourceSnapshotContractError, match="health changed"):
        service.snapshot_v2()


def test_snapshot_v2_marks_asymmetric_device_revision_token_unproven() -> None:
    class PartialTokenDriver(SourceV2FakeDriver):
        def execute_source_query_plan_v2(self, plan):
            result = super().execute_source_query_plan_v2(plan)
            return replace(result, device_revision_token_after=None)

    snapshot = make_service(PartialTokenDriver(combined=True)).snapshot_v2()
    assert snapshot.consistency.state is SnapshotConsistencyState.UNPROVEN
    assert snapshot.consistency.reason_code.value == "consistency_unproven"


def test_snapshot_v2_does_not_flatten_transport_failures() -> None:
    expected = TransportIOError(
        "redacted",
        operation="source.snapshot_v2",
        phase=TransportPhase.BEFORE_SEND,
        replay_policy=ReplayPolicy.NO_REPLAY,
        command_transmission=CommandTransmission.NOT_SENT,
        response_progress=ResponseProgress.NONE,
        synchronization=Synchronization.PROVEN,
        attempts=0,
        reason_code="query_rejected",
    )

    class RaisingDriver(SourceV2FakeDriver):
        def execute_source_query_plan_v2(self, plan):
            raise expected

    with pytest.raises(TransportIOError) as raised:
        make_service(RaisingDriver(combined=True)).snapshot_v2()
    assert raised.value is expected


def test_snapshot_v2_enforces_absolute_deadline(monkeypatch) -> None:
    ticks = iter((100.0, 103.0))
    monkeypatch.setattr(
        "wavebench.services.source_snapshot_v2.time.monotonic",
        lambda: next(ticks),
    )

    with pytest.raises(SourceSnapshotContractError, match="deadline"):
        make_service(SourceV2FakeDriver(combined=True)).snapshot_v2()


def test_snapshot_v2_artifact_is_typed_and_excludes_protocol_records() -> None:
    snapshot = make_service(SourceV2FakeDriver(combined=True)).snapshot_v2()
    artifact = source_snapshot_v2_operation_artifact(snapshot)

    assert artifact["schema"] == SOURCE_OPERATION_ARTIFACT_SCHEMA
    assert artifact["snapshot"]["schema"] == SOURCE_SNAPSHOT_SCHEMA
    assert artifact["snapshot"]["channels"][0]["basic"]["availability"] == "value"
    assert "items" not in artifact["query"]
    serialized = json.dumps(artifact)
    assert "TCPIP" not in serialized
    assert "revision-1" not in serialized


def test_snapshot_v2_operation_spec_is_read_only_and_bounded() -> None:
    spec = require_operation_spec("source.snapshot_v2")

    assert spec.required_capabilities == ("source.snapshot_v2",)
    assert spec.effect == "stateful_read"
    assert spec.mutates is False
    assert spec.lease_mode == "exclusive"
    assert spec.operation_timeout_ms == 5000
    assert spec.changed_fields == ()


def test_snapshot_v2_cli_emits_operation_artifact(capsys) -> None:
    service = make_service(SourceV2FakeDriver(combined=True))
    with patch("wavebench.cli._load_source_service", return_value=service):
        exit_code = cli.main(
            ["--json", "source", "snapshot-v2", "--config", "unused.toml"]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema"] == "wavebench.cli.result.v1"
    assert payload["result"]["schema"] == SOURCE_OPERATION_ARTIFACT_SCHEMA
    assert payload["result"]["snapshot"]["schema"] == SOURCE_SNAPSHOT_SCHEMA


def test_source_v2_write_cli_emits_existing_operation_artifacts(capsys) -> None:
    basic_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.basic_configure_v2",
    }
    output_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.output_enable_v2",
    }
    harmonic_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.harmonics_configure_v2",
    }
    modulation_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.modulation_configure_v2",
    }
    pulse_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.pulse_configure_v2",
    }
    pm_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.modulation_pm_configure_v2",
    }
    fm_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.modulation_fm_configure_v2",
    }
    pwm_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.modulation_pwm_configure_v2",
    }
    sweep_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.sweep_configure_v2",
    }
    burst_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.burst_configure_v2",
    }

    class _Service:
        def configure_basic_v2(self, request):
            assert request.channel == 1
            assert request.patch.frequency_hz.value == 2_000.0
            return object(), basic_artifact

        def set_output_v2(self, request):
            assert request.channel == 1
            assert request.enabled is True
            return object(), output_artifact

        def configure_harmonics_v2(self, request):
            assert request.channel == 1
            assert request.order == 8
            assert request.preset is SourceHarmonicPreset.ODD
            return object(), harmonic_artifact

        def configure_modulation_v2(self, request):
            assert request.channel == 1
            assert request.depth_percent == 80.0
            assert request.internal_frequency_hz == 25.0
            return object(), modulation_artifact

        def configure_pulse_v2(self, request):
            assert request.channel == 1
            assert request.width_s == 1.0e-6
            assert request.delay_s == 0.0
            assert request.leading_transition_s == 1.0e-8
            assert request.trailing_transition_s == 1.0e-8
            return object(), pulse_artifact

        def configure_pm_modulation_v2(self, request):
            assert request.channel == 1
            assert request.phase_deviation_deg == 90.0
            assert request.internal_frequency_hz == 25.0
            return object(), pm_artifact

        def configure_fm_modulation_v2(self, request):
            assert request.channel == 1
            assert request.frequency_deviation_hz == 12_500.0
            assert request.internal_frequency_hz == 25.0
            return object(), fm_artifact

        def configure_pwm_modulation_v2(self, request):
            assert request.channel == 1
            assert request.internal_frequency_hz == 25.0
            assert request.duty_deviation_percent == 25.0
            assert request.width_deviation_s is None
            return object(), pwm_artifact

        def configure_sweep_v2(self, request):
            assert request.channel == 1
            assert request.start_hz == 100.0
            assert request.stop_hz == 1_000.0
            assert request.spacing.value == "linear"
            assert request.steps == 101
            assert request.sweep_time_s == 1.0
            return object(), sweep_artifact

        def configure_burst_v2(self, request):
            assert request.channel == 1
            assert request.cycles == 12
            assert request.phase_deg == 30.0
            assert request.internal_period_s == 0.25
            assert request.delay_s == 0.5
            return object(), burst_artifact

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        basic_code = cli.main(
            [
                "--json",
                "source",
                "basic-configure-v2",
                "--channel",
                "1",
                "--frequency-hz",
                "2000",
                "--config",
                "unused.toml",
            ]
        )
        basic_payload = json.loads(capsys.readouterr().out)
        output_code = cli.main(
            [
                "--json",
                "source",
                "output-v2",
                "--channel",
                "1",
                "on",
                "--config",
                "unused.toml",
            ]
        )
        output_payload = json.loads(capsys.readouterr().out)
        harmonic_code = cli.main(
            [
                "--json",
                "source",
                "harmonics-configure-v2",
                "--channel",
                "1",
                "--order",
                "8",
                "--preset",
                "odd",
                "--config",
                "unused.toml",
            ]
        )
        harmonic_payload = json.loads(capsys.readouterr().out)
        modulation_code = cli.main(
            [
                "--json",
                "source",
                "modulation-configure-v2",
                "--channel",
                "1",
                "--depth-percent",
                "80",
                "--internal-frequency-hz",
                "25",
                "--config",
                "unused.toml",
            ]
        )
        modulation_payload = json.loads(capsys.readouterr().out)
        pulse_code = cli.main(
            [
                "--json",
                "source",
                "pulse-configure-v2",
                "--channel",
                "1",
                "--width-s",
                "1e-6",
                "--delay-s",
                "0",
                "--leading-transition-s",
                "1e-8",
                "--trailing-transition-s",
                "1e-8",
                "--config",
                "unused.toml",
            ]
        )
        pulse_payload = json.loads(capsys.readouterr().out)
        pm_code = cli.main(
            [
                "--json",
                "source",
                "pm-modulation-configure-v2",
                "--channel",
                "1",
                "--phase-deviation-deg",
                "90",
                "--internal-frequency-hz",
                "25",
                "--config",
                "unused.toml",
            ]
        )
        pm_payload = json.loads(capsys.readouterr().out)
        fm_code = cli.main(
            [
                "--json",
                "source",
                "fm-modulation-configure-v2",
                "--channel",
                "1",
                "--frequency-deviation-hz",
                "12500",
                "--internal-frequency-hz",
                "25",
                "--config",
                "unused.toml",
            ]
        )
        fm_payload = json.loads(capsys.readouterr().out)
        pwm_code = cli.main(
            [
                "--json",
                "source",
                "pwm-modulation-configure-v2",
                "--channel",
                "1",
                "--internal-frequency-hz",
                "25",
                "--duty-deviation-percent",
                "25",
                "--config",
                "unused.toml",
            ]
        )
        pwm_payload = json.loads(capsys.readouterr().out)
        sweep_code = cli.main(
            [
                "--json",
                "source",
                "sweep-configure-v2",
                "--channel",
                "1",
                "--start-hz",
                "100",
                "--stop-hz",
                "1000",
                "--spacing",
                "linear",
                "--steps",
                "101",
                "--sweep-time-s",
                "1",
                "--config",
                "unused.toml",
            ]
        )
        sweep_payload = json.loads(capsys.readouterr().out)
        burst_code = cli.main(
            [
                "--json",
                "source",
                "burst-configure-v2",
                "--channel",
                "1",
                "--cycles",
                "12",
                "--phase-deg",
                "30",
                "--internal-period-s",
                "0.25",
                "--delay-s",
                "0.5",
                "--config",
                "unused.toml",
            ]
        )
        burst_payload = json.loads(capsys.readouterr().out)

    assert basic_code == 0
    assert basic_payload["result"] == basic_artifact
    assert output_code == 0
    assert output_payload["result"] == output_artifact
    assert harmonic_code == 0
    assert harmonic_payload["result"] == harmonic_artifact
    assert modulation_code == 0
    assert modulation_payload["result"] == modulation_artifact
    assert pulse_code == 0
    assert pulse_payload["result"] == pulse_artifact
    assert pm_code == 0
    assert pm_payload["result"] == pm_artifact
    assert fm_code == 0
    assert fm_payload["result"] == fm_artifact
    assert pwm_code == 0
    assert pwm_payload["result"] == pwm_artifact
    assert sweep_code == 0
    assert sweep_payload["result"] == sweep_artifact
    assert burst_code == 0
    assert burst_payload["result"] == burst_artifact


def test_source_v2_write_cli_keeps_failure_operation_artifact(capsys) -> None:
    artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.output_enable_v2",
        "recovery": {"status": "off_verified"},
    }

    class _Service:
        def set_output_v2(self, request):
            del request
            error = ConfigError("write failed")
            error.source_operation_artifact = artifact
            raise error

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "output-v2",
                "--channel",
                "1",
                "on",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert payload["source_operation_artifact"] == artifact


def test_source_v2_modulation_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_modulation_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "modulation-configure-v2",
                "--channel",
                "1",
                "--depth-percent",
                "100.1",
                "--internal-frequency-hz",
                "25",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "depth_percent" in payload["message"]


def test_source_v2_pulse_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_pulse_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "pulse-configure-v2",
                "--channel",
                "1",
                "--width-s",
                "3e-9",
                "--delay-s",
                "0",
                "--leading-transition-s",
                "1e-9",
                "--trailing-transition-s",
                "1e-9",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "width_s" in payload["message"]


def test_source_v2_pm_modulation_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_pm_modulation_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "pm-modulation-configure-v2",
                "--channel",
                "1",
                "--phase-deviation-deg",
                "360.1",
                "--internal-frequency-hz",
                "25",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "phase_deviation_deg" in payload["message"]


def test_source_v2_burst_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_burst_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "burst-configure-v2",
                "--channel",
                "1",
                "--cycles",
                "500001",
                "--phase-deg",
                "30",
                "--internal-period-s",
                "0.25",
                "--delay-s",
                "0.5",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "cycles" in payload["message"]


def test_source_v2_fm_modulation_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_fm_modulation_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "fm-modulation-configure-v2",
                "--channel",
                "1",
                "--frequency-deviation-hz",
                "0",
                "--internal-frequency-hz",
                "25",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "frequency_deviation_hz" in payload["message"]


def test_source_v2_pwm_modulation_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_pwm_modulation_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "pwm-modulation-configure-v2",
                "--channel",
                "1",
                "--internal-frequency-hz",
                "25",
                "--duty-deviation-percent",
                "50.1",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "duty_deviation_percent" in payload["message"]


def test_source_v2_sweep_cli_rejects_invalid_request_before_driver_call(capsys) -> None:
    class _Service:
        def configure_sweep_v2(self, request):
            raise AssertionError(request)

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        exit_code = cli.main(
            [
                "--json",
                "source",
                "sweep-configure-v2",
                "--channel",
                "1",
                "--start-hz",
                "1000",
                "--stop-hz",
                "100",
                "--spacing",
                "linear",
                "--steps",
                "101",
                "--sweep-time-s",
                "1",
                "--config",
                "unused.toml",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["schema"] == "wavebench.error.v1"
    assert "start_hz" in payload["message"]


def test_source_v2_arbitrary_cli_emits_payload_free_operation_artifacts(tmp_path, capsys) -> None:
    payload_file = tmp_path / "payload.bin"
    payload_file.write_bytes(b"abc")
    storage_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.arbitrary_storage_v2",
        "request": {"payload_sha256": "sha256:" + "a" * 64},
    }
    selection_artifact = {
        "schema": SOURCE_OPERATION_ARTIFACT_SCHEMA,
        "operation": "source.arbitrary_select_v2",
    }

    class _Service:
        def mutate_arbitrary_storage_v2(self, request, *, payload):
            assert request.channel == 1
            assert request.slot_id == "slot_a"
            assert request.write_mode.value == "create_only"
            assert request.payload_size_bytes == 3
            assert payload == b"abc"
            return object(), storage_artifact

        def select_arbitrary_v2(self, request):
            assert request.channel == 1
            assert request.slot_id == "slot_a"
            assert request.playback_mode.value == "dds"
            assert request.playback_frequency_hz == 1_000.0
            assert request.sample_rate_hz is None
            return object(), selection_artifact

    with patch("wavebench.cli._load_source_service", return_value=_Service()):
        storage_code = cli.main(
            [
                "--json",
                "source",
                "arbitrary-storage-v2",
                "--channel",
                "1",
                "--slot-id",
                "slot_a",
                "--payload-file",
                str(payload_file),
                "--write-mode",
                "create-only",
                "--config",
                "unused.toml",
            ]
        )
        storage_payload = json.loads(capsys.readouterr().out)
        selection_code = cli.main(
            [
                "--json",
                "source",
                "arbitrary-select-v2",
                "--channel",
                "1",
                "--slot-id",
                "slot_a",
                "--playback-mode",
                "dds",
                "--playback-frequency-hz",
                "1000",
                "--config",
                "unused.toml",
            ]
        )
        selection_payload = json.loads(capsys.readouterr().out)

    assert storage_code == 0
    assert storage_payload["result"] == storage_artifact
    assert selection_code == 0
    assert selection_payload["result"] == selection_artifact
    assert "abc" not in json.dumps(storage_payload, ensure_ascii=False)


def test_source_v2_arbitrary_cli_rejects_invalid_request_before_loading_service(tmp_path, capsys) -> None:
    payload_file = tmp_path / "payload.bin"
    payload_file.write_bytes(b"abc")

    with patch("wavebench.cli._load_source_service") as load_service:
        exit_code = cli.main(
            [
                "--json",
                "source",
                "arbitrary-storage-v2",
                "--channel",
                "1",
                "--slot-id",
                "slot_a",
                "--payload-file",
                str(payload_file),
                "--write-mode",
                "replace-if-digest-matches",
                "--config",
                "unused.toml",
            ]
        )

    error = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert error["schema"] == "wavebench.error.v1"
    assert "expected_previous_sha256" in error["message"]
    load_service.assert_not_called()
