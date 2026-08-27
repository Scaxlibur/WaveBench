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
    RfModulatedOutputRequest,
    RfModulatedOutputResult,
    RfModulationKind,
    RfModulationRequest,
    RfModulationResult,
    RfModulationSnapshot,
    RfModulationSource,
    RfModulationState,
    RfModulationWaveform,
    RfObserved,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceSnapshot,
    RfSweepState,
    rf_source_modulated_output_operation_artifact,
)
from wavebench.logging import CommandLogger
from wavebench.services.execution_intent import build_execution_intent
from wavebench.services.run_plan import STEP_SCHEMAS, load_run_plan
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
            driver="example.rf.modulated-output",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _plan(directory: str):
    path = Path(directory) / "plan.toml"
    path.write_text(
        "[[steps]]\n"
        'kind = "rf_source.modulated_output_enable"\n'
        'port_id = "rf_out"\n'
        'modulation_kind = "am"\n'
        "depth_percent = 50\n"
        "internal_frequency_hz = 1000\n",
        encoding="utf-8",
    )
    return load_run_plan(path)


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.modulated-output",
        kind="rf_source",
        capabilities=capabilities,
    )


def _snapshot(*, output_enabled: bool) -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-50.0),
                output_enabled=RfObserved.value_of(output_enabled),
                modulation=RfObserved.value_of(RfModulationState.ENABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def _modulation_snapshot() -> RfModulationSnapshot:
    return RfModulationSnapshot(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        source=RfModulationSource.INTERNAL,
        waveform=RfModulationWaveform.SINE,
        internal_frequency_hz=1_000.0,
        depth_percent=50.0,
        enabled_modes=(RfModulationKind.AM,),
        global_enabled=True,
    )


def _request() -> RfModulatedOutputRequest:
    return RfModulatedOutputRequest(
        modulation=RfModulationRequest(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
        )
    )


def _result() -> RfModulatedOutputResult:
    return RfModulatedOutputResult(
        modulation=RfModulationResult(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
        ),
        write_completed=True,
    )


def test_modulated_output_run_schema_and_intent_are_explicit_writes() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        intent = build_execution_intent(plan, _config(directory))

    assert "rf_source.modulated_output_enable" in STEP_SCHEMAS
    assert intent.operations[0]["operation"] == "rf_source.modulated_output_enable"
    assert intent.operations[0]["effect"] == "write"
    assert intent.operations[0]["parameters"] == {
        "depth_percent": 50.0,
        "internal_frequency_hz": 1_000.0,
        "modulation_kind": "am",
        "port_id": "rf_out",
    }


def test_modulated_output_run_step_requires_capability_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.output",
                "rf_source.modulation_configure",
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="rf_source.modulated_output_enable"):
                service.run(_plan(directory))

    open_services.assert_not_called()


def test_modulated_output_run_step_rejects_read_only_access_before_opening_a_session() -> None:
    with TemporaryDirectory() as directory:
        service = RunService(config=_config(directory, access="read_only"), logger=CommandLogger())
        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.output",
                "rf_source.modulation_configure",
                "rf_source.modulated_output_enable",
            ),
        ), patch.object(service, "_run_instrument_services") as open_services:
            with pytest.raises(ConfigError, match="access policy 'read_only'"):
                service.run(_plan(directory))

    open_services.assert_not_called()


def test_modulated_output_run_step_dispatches_exact_profile_and_artifact() -> None:
    with TemporaryDirectory() as directory:
        plan = _plan(directory)
        config = _config(directory)
        request = _request()
        result = _result()
        artifact = rf_source_modulated_output_operation_artifact(
            request,
            result,
            preflight_snapshot=_snapshot(output_enabled=False),
            preflight_modulation_snapshot=_modulation_snapshot(),
            postcondition_snapshot=_snapshot(output_enabled=True),
            postcondition_modulation_snapshot=_modulation_snapshot(),
        )
        rf_service = SimpleNamespace(
            enable_modulated_output_with_artifact=Mock(return_value=(result, artifact)),
            audit_snapshot=lambda: None,
        )

        class OfflineRfRunService(RunService):
            @contextmanager
            def _run_instrument_services(self, run_plan):
                del run_plan
                yield RunInstrumentServices(rf_source=rf_service)

            def _run_safety_guards(self, run_plan, *, services=None):
                del run_plan, services

        with patch(
            "wavebench.services.run_service.resolve_instrument_descriptor",
            return_value=_descriptor(
                "rf_source.idn",
                "rf_source.snapshot",
                "rf_source.output",
                "rf_source.modulation_configure",
                "rf_source.modulated_output_enable",
            ),
        ):
            run_result = OfflineRfRunService(config=config, logger=CommandLogger()).run(plan)

        rf_service.enable_modulated_output_with_artifact.assert_called_once_with(request)
        assert run_result.steps[0].artifact == {"rf_source_operation": artifact}
        run_data = json.loads(run_result.run_json_path.read_text(encoding="utf-8"))
        assert run_data["rf_source_operations"] == [artifact]
