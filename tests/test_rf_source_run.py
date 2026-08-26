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
    RfPulseState,
    RfSourceSnapshot,
    RfSweepState,
    rf_source_snapshot_operation_artifact,
)
from wavebench.logging import CommandLogger
from wavebench.services.execution_intent import build_execution_intent
from wavebench.services.run_artifacts import RunStepRecord, write_run_files
from wavebench.services.run_plan import STEP_SCHEMAS, load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService


def _config(directory: str) -> WaveBenchConfig:
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
            access="read_only",
        ),
    )


def _plan(directory: str):
    path = Path(directory) / "plan.toml"
    path.write_text('[[steps]]\nkind = "rf_source.status"\n', encoding="utf-8")
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
