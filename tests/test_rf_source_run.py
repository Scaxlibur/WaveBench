from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    RfSourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import ConfigError
from wavebench.instruments.rf_source_extensions import (
    RfCwRequest,
    RfCwResult,
    RfModulationKind,
    RfModulationRequest,
    RfModulationResult,
    RfModulationSnapshot,
    RfModulationSource,
    RfModulationState,
    RfModulationStateSnapshot,
    RfModulationWaveform,
    RfObserved,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseConfigureRequest,
    RfPulseConfigureResult,
    RfPulseMode,
    RfPulsePolarity,
    RfPulseSnapshot,
    RfPulseSource,
    RfPulseState,
    RfSourceSnapshot,
    RfSweepState,
    rf_source_cw_operation_artifact,
    rf_source_modulation_operation_artifact,
    rf_source_pulse_operation_artifact,
    RfOutputRequest,
    RfOutputResult,
    rf_source_output_operation_artifact,
    rf_source_snapshot_operation_artifact,
)
from wavebench.logging import CommandLogger
from wavebench.services.execution_intent import build_execution_intent
from wavebench.services.run_artifacts import RunStepRecord, write_run_files
from wavebench.services.run_plan import STEP_SCHEMAS, load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService


def _config(directory: str, *, access: str = "read_only") -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(
            Path(directory) / "data" / "raw",
            "timestamp_label",
            True,
            True,
            True,
            True,
            False,
        ),
        source_path=Path(directory) / "wavebench.toml",
        rf_source=RfSourceConfig(
            driver="example.rf1",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _plan(directory: str):
    path = Path(directory) / "plan.toml"
    path.write_text('[[steps]]\nkind = "rf_source.status"\n', encoding="utf-8")
    return load_run_plan(path)


def _cw_plan(directory: str, *, kind: str, field: str, value: float):
    path = Path(directory) / "plan.toml"
    path.write_text(
        f'[[steps]]\nkind = "{kind}"\nport_id = "rf_out"\n{field} = {value}\n',
        encoding="utf-8",
    )
    return load_run_plan(path)


def _output_plan(directory: str, *, kind: str):
    path = Path(directory) / "plan.toml"
    path.write_text(
        f'[[steps]]\nkind = "{kind}"\nport_id = "rf_out"\n',
        encoding="utf-8",
    )
    return load_run_plan(path)


def _modulation_plan(
    directory: str,
    *,
    modulation_kind: str = "am",
    value_field: str = "depth_percent",
    value: float = 50.0,
):
    path = Path(directory) / "plan.toml"
    path.write_text(
        "[[steps]]\n"
        'kind = "rf_source.modulation_configure"\n'
        'port_id = "rf_out"\n'
        f'modulation_kind = "{modulation_kind}"\n'
        f"{value_field} = {value}\n"
        "internal_frequency_hz = 1000\n",
        encoding="utf-8",
    )
    return load_run_plan(path)


def _pulse_plan(directory: str):
    path = Path(directory) / "plan.toml"
    path.write_text(
        "[[steps]]\n"
        'kind = "rf_source.pulse_configure"\n'
        'port_id = "rf_out"\n'
        "period_s = 0.001\n"
        "width_s = 0.0001\n"
        'polarity = "inverted"\n',
        encoding="utf-8",
    )
    return load_run_plan(path)


def _snapshot() -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-30.0),
                output_enabled=RfObserved.value_of(False),
                modulation=RfObserved.value_of(RfModulationState.DISABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf1",
        kind="rf_source",
        capabilities=capabilities,
    )


def test_rf_source_status_schema_and_intent_are_read_only() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        intent = build_execution_intent(plan, _config(directory))

    assert STEP_SCHEMAS["rf_source.status"].required == ()
    assert intent.operations == (
        {
            "step_index": 0,
            "step_kind": "rf_source.status",
            "operation": "rf_source.snapshot",
            "instrument_kind": "rf_source",
            "effect": "stateful_read",
            "lease_mode": "exclusive",
            "changed_fields": [],
            "restore_coverage": "none-read-only",
            "session_purpose": "normal",
            "required_verified_fields": [],
            "verification_fields": [],
            "timeout_source": "connection.timeout_ms",
            "risk_flags": ["state_dependent_query"],
            "parameters": {},
            "policy": {"on_failure": "stop", "safety_gate": {}},
        },
    )


def test_rf_source_status_requires_snapshot_capability_before_session_opens() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor("rf_source.idn"),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="rf_source.snapshot"):
                service.run(_plan(directory))

    open_services.assert_not_called()


def test_rf_source_status_verify_and_run_use_the_isolated_service_and_artifact_namespace() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        descriptor = _descriptor("rf_source.idn", "rf_source.snapshot")
        snapshot = _snapshot()
        service = RunService(config=_config(directory), logger=CommandLogger())

        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ), patch("wavebench.services.run_service.RfSourceService") as rf_service_type:
            rf_service_type.return_value.idn.return_value = "EXAMPLE,RF1,0,1"
            records = service.verify(plan)

        assert [(record.instrument, record.idn) for record in records] == [
            ("rf_source", "EXAMPLE,RF1,0,1"),
        ]

        rf_service = SimpleNamespace(snapshot=Mock(return_value=snapshot), audit_snapshot=lambda: None)

        class OfflineRfRunService(RunService):
            @contextmanager
            def _run_instrument_services(self, run_plan):
                del run_plan
                yield RunInstrumentServices(rf_source=rf_service)

            def _run_safety_guards(self, run_plan, *, services=None):
                del run_plan, services

        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ):
            result = OfflineRfRunService(config=_config(directory), logger=CommandLogger()).run(plan)

        rf_service.snapshot.assert_called_once_with()
        run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
        artifact = rf_source_snapshot_operation_artifact(snapshot)
        assert result.steps[0].artifact == {"rf_source_operation": artifact}
        assert run_data["rf_source_operations"] == [artifact]
        assert "source_operations" not in run_data


def test_rf_source_operation_artifacts_are_validated_in_a_separate_root_namespace() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        artifact = rf_source_snapshot_operation_artifact(_snapshot())
        run_json_path = Path(directory) / "run.json"
        write_run_files(
            plan=plan,
            run_json_path=run_json_path,
            summary_csv_path=Path(directory) / "summary.csv",
            status="ok",
            records=[
                RunStepRecord(
                    index=0,
                    kind="rf_source.status",
                    status="ok",
                    fields={},
                    artifact={"rf_source_operation": artifact},
                )
            ],
            error=None,
            rf_source_operations=[artifact],
        )

        data = json.loads(run_json_path.read_text(encoding="utf-8"))
        assert data["rf_source_operations"] == [artifact]
        assert "source_operations" not in data

        with pytest.raises(ValueError, match="rf_source"):
            write_run_files(
                plan=plan,
                run_json_path=run_json_path,
                summary_csv_path=Path(directory) / "summary.csv",
                status="ok",
                records=[],
                error=None,
                rf_source_operations=[
                    {"schema": "wavebench.rf_source.operation.v1", "operation": "source.status"}
                ],
            )


def test_rf_source_cw_steps_require_capability_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory, access="read_write"), logger=CommandLogger())
        plan = _cw_plan(
            directory,
            kind="rf_source.set_frequency",
            field="frequency_hz",
            value=2_000_000.0,
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor("rf_source.idn", "rf_source.snapshot"),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="rf_source.cw_configure"):
                service.run(plan)

    open_services.assert_not_called()


def test_rf_source_cw_step_rejects_read_only_access_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory), logger=CommandLogger())
        plan = _cw_plan(
            directory,
            kind="rf_source.set_frequency",
            field="frequency_hz",
            value=2_000_000.0,
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.cw_configure",
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="access policy 'read_only'"):
                service.run(plan)

    open_services.assert_not_called()


def test_rf_source_cw_step_has_write_intent_and_separate_artifact_namespace() -> None:
    with TemporaryDirectory() as directory:
        plan = _cw_plan(
            directory,
            kind="rf_source.set_power_dbm",
            field="power_dbm",
            value=-10.0,
        )
        config = _config(directory, access="read_write")
        intent = build_execution_intent(plan, config)
        assert intent.operations[0]["operation"] == "rf_source.set_power_dbm"
        assert intent.operations[0]["effect"] == "write"
        assert intent.operations[0]["parameters"] == {"port_id": "rf_out", "power_dbm": -10.0}

        preflight = _snapshot()
        postcondition = RfSourceSnapshot(
            ports=(
                RfPortSnapshot(
                    port_id="rf_out",
                    frequency_hz=RfObserved.value_of(1_000_000.0),
                    power_dbm=RfObserved.value_of(-10.0),
                    output_enabled=RfObserved.value_of(False),
                    modulation=RfObserved.value_of(RfModulationState.DISABLED),
                    pulse=RfObserved.value_of(RfPulseState.DISABLED),
                    sweep=RfObserved.value_of(RfSweepState.DISABLED),
                ),
            ),
            protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
        )
        result_value = RfCwResult(port_id="rf_out", power_dbm=-10.0)
        artifact = rf_source_cw_operation_artifact(
            request=RfCwRequest(port_id="rf_out", power_dbm=-10.0),
            result=result_value,
            preflight_snapshot=preflight,
            postcondition_snapshot=postcondition,
        )
        rf_service = SimpleNamespace(
            configure_cw_with_artifact=Mock(return_value=(result_value, artifact)),
            audit_snapshot=lambda: None,
        )

        class OfflineRfRunService(RunService):
            @contextmanager
            def _run_instrument_services(self, run_plan):
                del run_plan
                yield RunInstrumentServices(rf_source=rf_service)

            def _run_safety_guards(self, run_plan, *, services=None):
                del run_plan, services

        descriptor = _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.cw_configure",
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ):
            run_result = OfflineRfRunService(config=config, logger=CommandLogger()).run(plan)

        rf_service.configure_cw_with_artifact.assert_called_once()
        assert run_result.steps[0].artifact == {"rf_source_operation": artifact}
        run_data = json.loads(run_result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["rf_source_operations"] == [artifact]


def test_rf_source_modulation_plan_requires_its_matching_value_field() -> None:
    with TemporaryDirectory() as directory:
        plan = _modulation_plan(directory)
        assert plan.steps[0].fields == {
            "port_id": "rf_out",
            "modulation_kind": "am",
            "depth_percent": 50.0,
            "internal_frequency_hz": 1_000.0,
        }

        invalid_path = Path(directory) / "invalid-plan.toml"
        invalid_path.write_text(
            "[[steps]]\n"
            'kind = "rf_source.modulation_configure"\n'
            'port_id = "rf_out"\n'
            'modulation_kind = "am"\n'
            "frequency_deviation_hz = 1000\n"
            "internal_frequency_hz = 1000\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="requires only depth_percent"):
            load_run_plan(invalid_path)


def test_rf_source_modulation_step_requires_capability_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory, access="read_write"), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor("rf_source.idn", "rf_source.snapshot"),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="rf_source.modulation_configure"):
                service.run(_modulation_plan(directory))

    open_services.assert_not_called()


def test_rf_source_modulation_step_rejects_read_only_access_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.modulation_configure",
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="access policy 'read_only'"):
                service.run(_modulation_plan(directory))

    open_services.assert_not_called()


def test_rf_source_modulation_step_has_write_intent_and_separate_artifact_namespace() -> None:
    with TemporaryDirectory() as directory:
        plan = _modulation_plan(directory)
        config = _config(directory, access="read_write")
        intent = build_execution_intent(plan, config)
        assert intent.operations[0]["operation"] == "rf_source.modulation_configure"
        assert intent.operations[0]["effect"] == "write"
        assert intent.operations[0]["parameters"] == {
            "port_id": "rf_out",
            "modulation_kind": "am",
            "depth_percent": 50.0,
            "internal_frequency_hz": 1_000.0,
        }

        request = RfModulationRequest(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            depth_percent=50.0,
            internal_frequency_hz=1_000.0,
        )
        result_value = RfModulationResult(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            depth_percent=50.0,
            internal_frequency_hz=1_000.0,
        )
        preflight_snapshot = _snapshot()
        preflight_modulation_state = RfModulationStateSnapshot(port_id="rf_out")
        postcondition_snapshot = RfSourceSnapshot(
            ports=(
                RfPortSnapshot(
                    port_id="rf_out",
                    frequency_hz=RfObserved.value_of(1_000_000.0),
                    power_dbm=RfObserved.value_of(-30.0),
                    output_enabled=RfObserved.value_of(False),
                    modulation=RfObserved.value_of(RfModulationState.ENABLED),
                    pulse=RfObserved.value_of(RfPulseState.DISABLED),
                    sweep=RfObserved.value_of(RfSweepState.DISABLED),
                ),
            ),
            protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
        )
        postcondition_modulation_snapshot = RfModulationSnapshot(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            source=RfModulationSource.INTERNAL,
            waveform=RfModulationWaveform.SINE,
            depth_percent=50.0,
            internal_frequency_hz=1_000.0,
            enabled_modes=(RfModulationKind.AM,),
            global_enabled=True,
        )
        artifact = rf_source_modulation_operation_artifact(
            request=request,
            result=result_value,
            preflight_snapshot=preflight_snapshot,
            preflight_modulation_state=preflight_modulation_state,
            postcondition_snapshot=postcondition_snapshot,
            postcondition_modulation_snapshot=postcondition_modulation_snapshot,
        )
        rf_service = SimpleNamespace(
            configure_modulation_with_artifact=Mock(return_value=(result_value, artifact)),
            audit_snapshot=lambda: None,
        )

        class OfflineRfRunService(RunService):
            @contextmanager
            def _run_instrument_services(self, run_plan):
                del run_plan
                yield RunInstrumentServices(rf_source=rf_service)

            def _run_safety_guards(self, run_plan, *, services=None):
                del run_plan, services

        descriptor = _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.modulation_configure",
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ):
            run_result = OfflineRfRunService(config=config, logger=CommandLogger()).run(plan)

        rf_service.configure_modulation_with_artifact.assert_called_once_with(request)
        assert run_result.steps[0].artifact == {"rf_source_operation": artifact}
        run_data = json.loads(run_result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["rf_source_operations"] == [artifact]


def test_rf_source_pulse_plan_normalizes_the_disabled_internal_single_subset() -> None:
    with TemporaryDirectory() as directory:
        plan = _pulse_plan(directory)
        assert plan.steps[0].fields == {
            "port_id": "rf_out",
            "period_s": 0.001,
            "width_s": 0.0001,
            "polarity": "inverted",
        }

        invalid_width_path = Path(directory) / "invalid-width.toml"
        invalid_width_path.write_text(
            "[[steps]]\n"
            'kind = "rf_source.pulse_configure"\n'
            'port_id = "rf_out"\n'
            "period_s = 0.001\n"
            "width_s = 0.001\n"
            'polarity = "normal"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="width_s must be less than period_s"):
            load_run_plan(invalid_width_path)

        invalid_polarity_path = Path(directory) / "invalid-polarity.toml"
        invalid_polarity_path.write_text(
            "[[steps]]\n"
            'kind = "rf_source.pulse_configure"\n'
            'port_id = "rf_out"\n'
            "period_s = 0.001\n"
            "width_s = 0.0001\n"
            'polarity = "external"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="polarity must be one of normal, inverted"):
            load_run_plan(invalid_polarity_path)


def test_rf_source_pulse_step_requires_capability_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory, access="read_write"), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor("rf_source.idn", "rf_source.snapshot"),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="rf_source.pulse_configure"):
                service.run(_pulse_plan(directory))

    open_services.assert_not_called()


def test_rf_source_pulse_step_rejects_read_only_access_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.pulse_configure",
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="access policy 'read_only'"):
                service.run(_pulse_plan(directory))

    open_services.assert_not_called()


def test_rf_source_pulse_step_has_write_intent_and_separate_artifact_namespace() -> None:
    with TemporaryDirectory() as directory:
        plan = _pulse_plan(directory)
        config = _config(directory, access="read_write")
        intent = build_execution_intent(plan, config)
        assert intent.operations[0]["operation"] == "rf_source.pulse_configure"
        assert intent.operations[0]["effect"] == "write"
        assert intent.operations[0]["parameters"] == {
            "port_id": "rf_out",
            "period_s": 0.001,
            "width_s": 0.0001,
            "polarity": "inverted",
        }

        request = RfPulseConfigureRequest(
            port_id="rf_out",
            period_s=0.001,
            width_s=0.0001,
            polarity=RfPulsePolarity.INVERTED,
        )
        result_value = RfPulseConfigureResult(
            port_id="rf_out",
            period_s=0.001,
            width_s=0.0001,
            polarity=RfPulsePolarity.INVERTED,
        )
        preflight_snapshot = _snapshot()
        postcondition_snapshot = _snapshot()
        postcondition_pulse_snapshot = RfPulseSnapshot(
            port_id="rf_out",
            source=RfPulseSource.INTERNAL,
            mode=RfPulseMode.SINGLE,
            period_s=0.001,
            width_s=0.0001,
            polarity=RfPulsePolarity.INVERTED,
            state=RfPulseState.DISABLED,
        )
        artifact = rf_source_pulse_operation_artifact(
            request=request,
            result=result_value,
            preflight_snapshot=preflight_snapshot,
            postcondition_snapshot=postcondition_snapshot,
            postcondition_pulse_snapshot=postcondition_pulse_snapshot,
        )
        rf_service = SimpleNamespace(
            configure_pulse_with_artifact=Mock(return_value=(result_value, artifact)),
            audit_snapshot=lambda: None,
        )

        class OfflineRfRunService(RunService):
            @contextmanager
            def _run_instrument_services(self, run_plan):
                del run_plan
                yield RunInstrumentServices(rf_source=rf_service)

            def _run_safety_guards(self, run_plan, *, services=None):
                del run_plan, services

        descriptor = _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.pulse_configure",
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ):
            run_result = OfflineRfRunService(config=config, logger=CommandLogger()).run(plan)

        rf_service.configure_pulse_with_artifact.assert_called_once_with(request)
        assert run_result.steps[0].artifact == {"rf_source_operation": artifact}
        run_data = json.loads(run_result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["rf_source_operations"] == [artifact]


def test_rf_source_output_step_requires_capability_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory, access="read_write"), logger=CommandLogger())
        plan = _output_plan(directory, kind="rf_source.output_enable")
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor("rf_source.idn", "rf_source.snapshot"),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="rf_source.output"):
                service.run(plan)

    open_services.assert_not_called()


def test_rf_source_output_step_rejects_read_only_access_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory), logger=CommandLogger())
        plan = _output_plan(directory, kind="rf_source.output_disable")
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.output",
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="access policy 'read_only'"):
                service.run(plan)

    open_services.assert_not_called()


def test_rf_source_output_step_has_write_intent_and_separate_artifact_namespace() -> None:
    with TemporaryDirectory() as directory:
        plan = _output_plan(directory, kind="rf_source.output_enable")
        config = _config(directory, access="read_write")
        intent = build_execution_intent(plan, config)
        assert intent.operations[0]["operation"] == "rf_source.output_enable"
        assert intent.operations[0]["effect"] == "write"
        assert intent.operations[0]["parameters"] == {"port_id": "rf_out"}

        preflight = _snapshot()
        postcondition = RfSourceSnapshot(
            ports=(
                RfPortSnapshot(
                    port_id="rf_out",
                    frequency_hz=RfObserved.value_of(1_000_000.0),
                    power_dbm=RfObserved.value_of(-30.0),
                    output_enabled=RfObserved.value_of(True),
                    modulation=RfObserved.value_of(RfModulationState.DISABLED),
                    pulse=RfObserved.value_of(RfPulseState.DISABLED),
                    sweep=RfObserved.value_of(RfSweepState.DISABLED),
                ),
            ),
            protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
        )
        request = RfOutputRequest(port_id="rf_out", enabled=True)
        result_value = RfOutputResult(port_id="rf_out", enabled=True, write_completed=True)
        artifact = rf_source_output_operation_artifact(
            request=request,
            result=result_value,
            preflight_snapshot=preflight,
            postcondition_snapshot=postcondition,
        )
        rf_service = SimpleNamespace(
            set_output_with_artifact=Mock(return_value=(result_value, artifact)),
            audit_snapshot=lambda: None,
        )

        class OfflineRfRunService(RunService):
            @contextmanager
            def _run_instrument_services(self, run_plan):
                del run_plan
                yield RunInstrumentServices(rf_source=rf_service)

            def _run_safety_guards(self, run_plan, *, services=None):
                del run_plan, services

        descriptor = _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.output",
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ):
            run_result = OfflineRfRunService(config=config, logger=CommandLogger()).run(plan)

        rf_service.set_output_with_artifact.assert_called_once_with(request)
        assert run_result.steps[0].artifact == {"rf_source_operation": artifact}
        run_data = json.loads(run_result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["rf_source_operations"] == [artifact]
