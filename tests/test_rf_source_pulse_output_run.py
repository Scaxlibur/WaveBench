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
    RfModulationState,
    RfObserved,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseMode,
    RfPulseOutputDirection,
    RfPulseOutputRequest,
    RfPulseOutputResult,
    RfPulseOutputSnapshot,
    RfPulsePolarity,
    RfPulseSource,
    RfPulseState,
    RfSourceSnapshot,
    RfSweepState,
    rf_source_pulse_output_operation_artifact,
)
from wavebench.logging import CommandLogger
from wavebench.services.execution_intent import build_execution_intent
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService


def _config(directory: str, *, access: str = "read_write") -> WaveBenchConfig:
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
            driver="example.rf.pulse-output",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _plan(directory: str, *, kind: str = "rf_source.pulse_output_enable"):
    path = Path(directory) / "plan.toml"
    path.write_text(
        "[[steps]]\n"
        f'kind = "{kind}"\n'
        'port_id = "rf_out"\n'
        'interface_id = "pulse_in_out"\n',
        encoding="utf-8",
    )
    return load_run_plan(path)


def _rf_snapshot() -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-50.0),
                output_enabled=RfObserved.value_of(False),
                modulation=RfObserved.value_of(RfModulationState.DISABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def _pulse_output_snapshot(*, enabled: bool) -> RfPulseOutputSnapshot:
    return RfPulseOutputSnapshot(
        port_id="rf_out",
        interface_id="pulse_in_out",
        direction=RfPulseOutputDirection.OUTPUT,
        enabled=enabled,
        low_level_v=0.0,
        high_level_v=3.3,
        output_impedance_ohm=600.0,
        source=RfPulseSource.INTERNAL,
        mode=RfPulseMode.SINGLE,
        period_s=1e-3,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
        pulse_state=RfPulseState.DISABLED,
    )


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.pulse-output",
        kind="rf_source",
        capabilities=capabilities,
    )


def test_rf_source_pulse_output_plan_is_typed_and_rejects_invalid_interface_ids() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        assert plan.steps[0].fields == {
            "port_id": "rf_out",
            "interface_id": "pulse_in_out",
        }
        intent = build_execution_intent(plan, _config(directory))
        assert intent.operations[0]["operation"] == "rf_source.pulse_output_enable"
        assert intent.operations[0]["effect"] == "write"
        assert intent.operations[0]["parameters"] == plan.steps[0].fields

        invalid = Path(directory) / "invalid.toml"
        invalid.write_text(
            "[[steps]]\n"
            'kind = "rf_source.pulse_output_enable"\n'
            'port_id = "rf_out"\n'
            'interface_id = "PULSE IN/OUT"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="interface_id"):
            load_run_plan(invalid)


def test_rf_source_pulse_output_run_preflights_capabilities_before_opening_sessions() -> None:
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
            with pytest.raises(ConfigError, match="rf_source.pulse_output"):
                service.run(_plan(directory))

    open_services.assert_not_called()


def test_rf_source_pulse_output_run_dispatches_a_typed_artifact() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        request = RfPulseOutputRequest(
            port_id="rf_out",
            interface_id="pulse_in_out",
            enabled=True,
        )
        result = RfPulseOutputResult(
            port_id="rf_out",
            interface_id="pulse_in_out",
            enabled=True,
            write_completed=True,
        )
        artifact = rf_source_pulse_output_operation_artifact(
            request,
            result,
            preflight_snapshot=_rf_snapshot(),
            preflight_pulse_output_snapshot=_pulse_output_snapshot(enabled=False),
            postcondition_snapshot=_rf_snapshot(),
            postcondition_pulse_output_snapshot=_pulse_output_snapshot(enabled=True),
        )
        rf_service = SimpleNamespace(
            set_pulse_output_with_artifact=Mock(return_value=(result, artifact)),
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
            "rf_source.pulse_output",
        )
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=descriptor,
        ):
            run_result = OfflineRfRunService(
                config=_config(directory),
                logger=CommandLogger(),
            ).run(plan)

        rf_service.set_pulse_output_with_artifact.assert_called_once_with(request)
        assert run_result.steps[0].artifact == {"rf_source_operation": artifact}
        run_data = json.loads(run_result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["rf_source_operations"] == [artifact]
