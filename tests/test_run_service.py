import csv
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
import json
import unittest

import numpy as np

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    DmmConfig,
    OutputConfig,
    PowerConfig,
    QualityConfig,
    SafetyLimitsConfig,
    SourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dp800 import PowerStatus
from wavebench.errors import ConfigError, SessionHealthError, TransportIOError
from wavebench.logging import CommandLogger
from wavebench.services.run_plan import load_run_plan
from wavebench.services.run_service import RunInstrumentServices, RunService
from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


def make_config(
    tmp: str,
    quality: QualityConfig | None = None,
    safety_limits: SafetyLimitsConfig | None = None,
) -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig(
            backend="lan",
            resource="TCPIP::scope::INSTR",
            timeout_ms=1000,
            opc_timeout_ms=1000,
        ),
        scope=ScopeConfig(
            driver="rtm2032",
            model_hint=None,
            default_channel=1,
            reset_before_run=False,
            check_errors=True,
        ),
        autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
        waveform=WaveformConfig(format="real", byte_order="lsbf", points="DMAX"),
        output=OutputConfig(
            directory=Path(tmp) / "data" / "raw",
            package_naming="timestamp_label",
            save_csv=True,
            save_npy=True,
            save_json=True,
            save_commands_log=True,
            save_screenshot=False,
        ),
        source_path=Path(tmp) / "wavebench.toml",
        source=SourceConfig(
            driver="dg4202",
            resource="TCPIP::source::INSTR",
            default_channel=2,
            check_errors=True,
            ensure_fix_mode_on_set_frequency=True,
            settle_ms_after_set_frequency=0,
        ),
        power=PowerConfig(
            driver="dp800",
            resource="TCPIP::power::INSTR",
            default_channel=1,
            check_errors=True,
            settle_ms_after_set=2000,
            settle_ms_after_output=1000,
        ),
        dmm=DmmConfig(
            driver="dm3058",
            resource="TCPIP::dmm::INSTR",
            backend="lan",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_ms=1000,
        ),
        quality=quality or QualityConfig(),
        safety_limits=safety_limits or SafetyLimitsConfig(),
    )


def write_plan(tmp: str, content: str) -> Path:
    path = Path(tmp) / "plan.toml"
    path.write_text(content, encoding="utf-8")
    return path


def fake_capture(
    tmp: str,
    name: str,
    warnings: list[str] | None = None,
    *,
    frequency_hz: float | None = 1000.0,
    voltage_vpp_v: float = 5.0,
    voltage_mean_v: float = 0.0,
    duty_cycle: float | None = None,
    frequency_error_ratio: float | None = 0.0,
):
    package = Path(tmp) / name
    package.mkdir()
    metadata = package / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    summary = {
        "quality_warnings": warnings or [],
        "frequency_estimate_hz": frequency_hz,
        "estimated_cycles": 10.0,
        "points_per_cycle": 1000.0,
        "voltage_vpp_v": voltage_vpp_v,
        "voltage_mean_v": voltage_mean_v,
        "duty_cycle": duty_cycle,
        "frequency_error_ratio": frequency_error_ratio,
    }
    waveform_path = package / "ch1.npy"
    sample_rate = 100_000.0
    times = np.arange(4096) / sample_rate
    volts = np.sin(2 * np.pi * 1000.0 * times) + 0.1 * np.sin(2 * np.pi * 2000.0 * times)
    np.save(waveform_path, np.column_stack((times, volts)))
    waveform = SimpleNamespace(summary=lambda **kwargs: summary)
    return SimpleNamespace(package_dir=package, metadata_path=metadata, waveform=waveform, npy_path=waveform_path)


def fake_frequency_response_capture(
    tmp: str,
    name: str,
    *,
    frequency_hz: float,
    gain: float = 2.0,
    phase_deg: float = -45.0,
    warnings: list[str] | None = None,
):
    package = Path(tmp) / name
    package.mkdir()
    metadata = package / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    times = 0.013 + np.arange(4096, dtype=float) / 100_000.0

    def waveform(amplitude: float, phase: float):
        values = amplitude * np.sin(2.0 * np.pi * frequency_hz * times + np.radians(phase))
        summary = {
            "quality_warnings": warnings or [],
            "frequency_estimate_hz": frequency_hz,
            "voltage_vpp_v": amplitude * 2.0,
            "frequency_error_ratio": 0.0,
        }
        return SimpleNamespace(
            times_s=times,
            voltages_v=values,
            summary=lambda **kwargs: summary,
        )

    return SimpleNamespace(
        package_dir=package,
        metadata_path=metadata,
        waveforms={1: waveform(1.0, 0.0), 2: waveform(gain, phase_deg)},
    )

def ok_power_status() -> PowerStatus:
    return PowerStatus(
        channel=1,
        output="ON",
        mode="CV",
        rating="P6V",
        set_voltage_v=5.0,
        set_current_a=0.1,
        measured_voltage_v=5.0,
        measured_current_a=0.01,
        measured_power_w=0.05,
    )


class RunServiceTests(unittest.TestCase):
    def test_session_close_failure_is_recorded_after_run_artifacts_are_written(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "sleep"
duration_s = 0.001
""",
                )
            )

            class CloseReportingRunService(RunService):
                @contextmanager
                def _run_instrument_services(self, plan):
                    yield RunInstrumentServices(
                        close_errors=[
                            {
                                "operation": "session.close.scope",
                                "type": "RuntimeError",
                                "error": {
                                    "schema": "wavebench.error.v1",
                                    "code": "unexpected_error",
                                    "type": "RuntimeError",
                                    "message": "close failed",
                                    "exit_code": 1,
                                    "operation": "session.close.scope",
                                },
                            }
                        ]
                    )

                def _run_safety_guards(self, plan, *, services=None):
                    return None

            result = CloseReportingRunService(
                config=make_config(tmp),
                logger=CommandLogger(),
            ).run(plan)
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))

            self.assertEqual(run_data["status"], "failed")
            self.assertEqual(run_data["error"]["code"], "session_close_failed")
            self.assertEqual(
                run_data["provenance"]["session_lifecycle"]["close_errors"][0]["operation"],
                "session.close.scope",
            )

    def test_transport_failure_continue_is_recorded_and_latched_session_blocks_next_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "sleep"
duration_s = 0.001
on_failure = "continue"

[[steps]]
kind = "sleep"
duration_s = 0.001
on_failure = "continue"
""",
                )
            )

            class OfflineRunService(RunService):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.calls = 0

                @contextmanager
                def _run_instrument_services(self, plan):
                    yield RunInstrumentServices()

                def _run_safety_guards(self, plan, *, services=None):
                    return None

                def _run_step(self, plan, step, **kwargs):
                    self.calls += 1
                    if self.calls == 1:
                        raise TransportIOError(
                            "offline read failed",
                            operation="query",
                            phase=TransportPhase.READING,
                            replay_policy=ReplayPolicy.NO_REPLAY,
                            command_transmission=CommandTransmission.SENT,
                            response_progress=ResponseProgress.NONE,
                            synchronization=Synchronization.UNPROVEN,
                            attempts=1,
                        )
                    raise SessionHealthError(
                        "ignored",
                        health="poisoned",
                        io_kind="query",
                        epoch_id="offline-epoch",
                    )

            result = OfflineRunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            self.assertEqual(result.steps[0].status, "failed")
            self.assertEqual(result.steps[1].status, "failed")
            self.assertEqual(result.steps[0].artifact["error"]["code"], "transport_io_error")
            self.assertEqual(result.steps[1].artifact["error"]["code"], "session_health_error")
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
            self.assertEqual(run_data["status"], "failed")

    def test_session_failure_cannot_continue_past_safety_gate(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "sleep"
duration_s = 0.001
on_failure = "continue"

[[steps]]
kind = "sleep"
duration_s = 0.001
on_failure = "continue"
""",
                )
            )
            plan.steps[0].fields["safety_gate"] = {
                "enabled": True,
                "source_channels": [1],
            }

            class OfflineRunService(RunService):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.step_calls = 0

                @contextmanager
                def _run_instrument_services(self, plan):
                    yield RunInstrumentServices()

                def _run_safety_guards(self, plan, *, services=None):
                    return None

                def _run_step(self, plan, step, **kwargs):
                    self.step_calls += 1
                    raise TransportIOError(
                        "offline read failed",
                        operation="query",
                        phase=TransportPhase.READING,
                        replay_policy=ReplayPolicy.NO_REPLAY,
                        command_transmission=CommandTransmission.SENT,
                        response_progress=ResponseProgress.NONE,
                        synchronization=Synchronization.UNPROVEN,
                        attempts=1,
                    )

                def _apply_safety_gate(self, step, gate, *, services=None):
                    return {"status": "failed", "errors": [{"code": "offline"}]}

            service = OfflineRunService(config=make_config(tmp), logger=CommandLogger())
            result = service.run(plan)
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))

            self.assertEqual(service.step_calls, 1)
            self.assertEqual(len(result.steps), 1)
            self.assertEqual(run_data["error"]["code"], "safety_gate_failed")

    def test_check_rejects_missing_capability_before_opening_session(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
screenshot = true
""",
                )
            )
            descriptor = SimpleNamespace(
                driver_id="minimal.scope",
                capabilities=("scope.idn", "scope.capture_waveform", "scope.errors"),
            )
            service = RunService(config=make_config(tmp), logger=CommandLogger())

            with patch(
                "wavebench.services.run_service.resolve_instrument_descriptor",
                return_value=descriptor,
            ), patch.object(service, "_run_instrument_services") as open_services:
                with self.assertRaisesRegex(ConfigError, "scope.screenshot"):
                    service.run(plan)

            open_services.assert_not_called()

    def test_check_requires_protection_capability_for_power_output_on(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "power.output"
state = "on"
""",
                )
            )
            descriptor = SimpleNamespace(
                driver_id="minimal.power",
                capabilities=("power.output", "power.status"),
            )
            service = RunService(config=make_config(tmp), logger=CommandLogger())

            with patch(
                "wavebench.services.run_service.resolve_instrument_descriptor",
                return_value=descriptor,
            ):
                with self.assertRaisesRegex(ConfigError, "power.protection"):
                    service.check(plan)

    def test_verify_queries_only_instruments_referenced_by_plan(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[restore]
source_state = true

[[steps]]
kind = "power.status"

[[steps]]
kind = "scope.capture"
""",
                )
            )
            with patch("wavebench.services.run_service.PowerService") as power_cls, patch(
                "wavebench.services.run_service.ScopeService"
            ) as scope_cls, patch("wavebench.services.run_service.SourceService") as source_cls:
                power_cls.return_value.idn.return_value = "POWER"
                scope_cls.return_value.idn.return_value = "SCOPE"
                source_cls.return_value.idn.return_value = "SOURCE"

                records = RunService(config=make_config(tmp), logger=CommandLogger()).verify(plan)

                self.assertEqual([record.instrument for record in records], ["scope", "source", "power"])
                self.assertEqual([record.idn for record in records], ["SCOPE", "SOURCE", "POWER"])
                scope_cls.return_value.idn.assert_called_once_with()
                source_cls.return_value.idn.assert_called_once_with()
                power_cls.return_value.idn.assert_called_once_with()



    def test_runs_dmm_read_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "dmm.read"
function = "acv"
""",
                )
            )
            with patch("wavebench.services.run_service.DmmService") as dmm_cls:
                dmm_cls.return_value.read.return_value = SimpleNamespace(
                    function="acv", value=0.3535, unit="V", raw="3.535000E-01",
                    as_dict=lambda : {"function": "acv", "value": 0.3535, "unit": "V", "raw": "3.535000E-01"}
                )
                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)
                dmm_cls.return_value.read.assert_called_once_with(function="acv")
                self.assertEqual(result.steps[0].artifact["dmm_reading"]["function"], "acv")


    def test_dmm_read_expect_marks_failed_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "dmm.read"
function = "acv"

[steps.expect]
value = { min = 0.34, max = 0.36 }
"""))
            with patch("wavebench.services.run_service.DmmService") as dmm_cls:
                dmm_cls.return_value.read.return_value = SimpleNamespace(
                    function="acv", value=0.5, unit="V", raw="5.000000E-01",
                    as_dict=lambda: {"function": "acv", "value": 0.5, "unit": "V", "raw": "5.000000E-01"},
                )
                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)
                self.assertEqual(result.steps[0].status, "failed")
                self.assertEqual(result.steps[0].artifact["expect"]["status"], "failed")
                self.assertIn("above max", result.steps[0].artifact["expect"]["checks"]["value"]["reasons"][0])

    def test_verify_includes_dmm_when_plan_uses_it(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "dmm.read"
"""))
            with patch("wavebench.services.run_service.DmmService") as dmm_cls:
                dmm_cls.return_value.idn.return_value = "DMM"
                records = RunService(config=make_config(tmp), logger=CommandLogger()).verify(plan)
                self.assertEqual([record.instrument for record in records], ["dmm"])
                self.assertEqual(records[0].idn, "DMM")

    def test_runs_minimal_power_scope_plan_and_writes_run_files(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[experiment]
name = "plain_voltage_capture"
label = "plain_voltage_capture"

[[steps]]
kind = "power.status"
channel = 1

[[steps]]
kind = "power.set"
channel = 1
voltage_v = 3.3
current_limit_a = 0.1

[[steps]]
kind = "scope.capture"
channel = 2
label = "v3v3"
points = "def"
time_range_s = 0.01
save_csv = false

[[steps]]
kind = "sleep"
duration_s = 0.01
""",
                )
            )
            capture = fake_capture(tmp, "capture")

            with patch("wavebench.services.run_service.PowerService") as power_cls, patch(
                "wavebench.services.run_service.ScopeService"
            ) as scope_cls, patch("wavebench.services.run_service.time.sleep") as sleep:
                power = power_cls.return_value
                power.status.return_value = ok_power_status()
                power.set_voltage_current_limit.return_value = ok_power_status()
                scope_cls.return_value.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                power.status.assert_called_once_with(channel=1)
                power.set_voltage_current_limit.assert_called_once_with(
                    channel=1,
                    voltage_v=3.3,
                    current_limit_a=0.1,
                )
                scope_cls.return_value.capture_waveform.assert_called_once_with(
                    channel=2,
                    label="v3v3",
                )
                sleep.assert_called_once_with(0.01)

            self.assertTrue(result.run_json_path.exists())
            self.assertTrue(result.summary_csv_path.exists())
            self.assertTrue((result.run_dir / "plan.toml").exists())
            run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
            self.assertEqual(run_data["status"], "ok")
            self.assertEqual(len(run_data["steps"]), 4)
            self.assertIn("data", str(result.run_dir))
            self.assertIn("runs", str(result.run_dir))


    def test_reuses_power_session_across_run_steps(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "power.status"
channel = 1

[[steps]]
kind = "power.set"
channel = 1
voltage_v = 3.3
current_limit_a = 0.1
""",
                )
            )
            with patch("wavebench.services.run_service.PowerService") as power_cls:
                power = power_cls.return_value
                session = SimpleNamespace(close=Mock())
                power.open_session.return_value = session
                power.status.return_value = ok_power_status()
                power.set_voltage_current_limit.return_value = ok_power_status()

                RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                power.open_session.assert_called_once_with()
                power.status.assert_called_once_with(channel=1)
                power.set_voltage_current_limit.assert_called_once_with(
                    channel=1,
                    voltage_v=3.3,
                    current_limit_a=0.1,
                )
                session.close.assert_called_once_with()

    def test_closes_power_session_after_step_failure(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "power.set"
channel = 1
voltage_v = 3.3
current_limit_a = 0.1
""",
                )
            )
            with patch("wavebench.services.run_service.PowerService") as power_cls:
                power = power_cls.return_value
                session = SimpleNamespace(close=Mock())
                power.open_session.return_value = session
                power.set_voltage_current_limit.side_effect = ConfigError("boom")

                with self.assertRaisesRegex(ConfigError, "boom"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                session.close.assert_called_once_with()

    def test_opens_all_required_sessions_before_first_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "power.status"
channel = 1

[[steps]]
kind = "dmm.read"
function = "dcv"
""",
                )
            )
            with patch("wavebench.services.run_service.PowerService") as power_cls, patch(
                "wavebench.services.run_service.DmmService"
            ) as dmm_cls:
                power = power_cls.return_value
                power_session = SimpleNamespace(close=Mock())
                power.open_session.return_value = power_session
                dmm_cls.return_value.open_session.side_effect = ConfigError("dmm offline")

                with self.assertRaisesRegex(ConfigError, "dmm offline"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                power.status.assert_not_called()
                power_session.close.assert_called_once_with()


    def test_runs_power_output_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "power.output"
channel = 1
state = "off"
""",
                )
            )
            with patch("wavebench.services.run_service.PowerService") as power_cls:
                power = power_cls.return_value
                power.set_output.return_value = ok_power_status()

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                power.set_output.assert_called_once_with(channel=1, enabled=False)
                self.assertEqual(result.steps[0].artifact["power_status"]["output"], "ON")


    def test_scope_capture_quality_gate_records_warnings_without_recovery(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "weak"
quality_gate = true
""",
                )
            )
            capture = fake_capture(tmp, "weak", ["low_signal_amplitude: waveform Vpp is below 20 mV"])
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope = scope_cls.return_value
                scope.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope.capture_waveform.assert_called_once_with(channel=1, label="weak")
                scope.autoscale.assert_not_called()
                self.assertEqual(result.steps[0].artifact["quality_gate"]["status"], "warning")
                self.assertIn("low_signal_amplitude", result.steps[0].artifact["quality_gate"]["warnings"][0])

    def test_scope_capture_auto_recovers_until_warning_is_clear(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "weak"
quality_gate = true
auto_recover = true
""",
                )
            )
            first = fake_capture(tmp, "weak", ["low_points_per_cycle: too sparse"])
            second = fake_capture(tmp, "weak_retry", [])
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope = scope_cls.return_value
                scope.capture_waveform.side_effect = [first, second]

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(scope.capture_waveform.call_count, 2)
                scope.capture_waveform.assert_any_call(channel=1, label="weak")
                scope.capture_waveform.assert_any_call(channel=1, label="weak_auto_retry1")
                scope.autoscale.assert_called_once_with()
                artifact = result.steps[0].artifact
                self.assertEqual(artifact["quality"]["status"], "ok")
                self.assertEqual(artifact["quality_recovery"]["max_auto_recover_attempts"], 2)
                self.assertIn("low_points_per_cycle", artifact["quality_recovery"]["attempts"][0]["quality"]["warnings"][0])

    def test_scope_capture_uses_configured_recovery_attempts_and_accepts_consistency(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "sparse"
quality_gate = true
auto_recover = true
""",
                )
            )
            warning = ["low_points_per_cycle: too sparse"]
            first = fake_capture(tmp, "sparse", warning, frequency_hz=1000.0, voltage_vpp_v=5.00, voltage_mean_v=0.01, duty_cycle=0.50)
            second = fake_capture(tmp, "sparse_retry1", warning, frequency_hz=1001.0, voltage_vpp_v=5.02, voltage_mean_v=0.02, duty_cycle=0.51)
            third = fake_capture(tmp, "sparse_retry2", warning, frequency_hz=1000.5, voltage_vpp_v=5.01, voltage_mean_v=0.015, duty_cycle=0.505)
            quality = QualityConfig(auto_recover_attempts=3, consistency_required_captures=3)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope = scope_cls.return_value
                scope.capture_waveform.side_effect = [first, second, third]

                result = RunService(config=make_config(tmp, quality), logger=CommandLogger()).run(plan)

                self.assertEqual(scope.capture_waveform.call_count, 3)
                scope.capture_waveform.assert_any_call(channel=1, label="sparse_auto_retry1")
                scope.capture_waveform.assert_any_call(channel=1, label="sparse_auto_retry2")
                self.assertEqual(scope.autoscale.call_count, 2)
                artifact = result.steps[0].artifact
                self.assertEqual(artifact["quality"]["status"], "ok_by_consistency")
                self.assertTrue(artifact["quality"]["trusted_by_consistency"])
                self.assertEqual(artifact["quality_recovery"]["consistency"]["status"], "consistent")


    def test_scope_capture_expect_passes_and_is_written_to_summary(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "pwm"

[steps.expect]
duty_cycle = { min = 0.49, max = 0.51 }
frequency_error_ratio = { max = 0.02 }
voltage_vpp_v = { min = 3.0, max = 3.6 }
""",
                )
            )
            capture = fake_capture(
                tmp,
                "pwm",
                [],
                voltage_vpp_v=3.3,
                duty_cycle=0.5,
                frequency_error_ratio=0.01,
            )
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope_cls.return_value.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(result.steps[0].status, "ok")
                self.assertEqual(result.steps[0].artifact["expect"]["status"], "ok")
                run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
                self.assertEqual(run_data["status"], "ok")
                summary = result.summary_csv_path.read_text(encoding="utf-8")
                self.assertIn("expect_status", summary)
                self.assertIn(",ok,", summary)

    def test_scope_capture_expect_failure_marks_run_failed_without_exception(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "pwm"

[steps.expect]
duty_cycle = { min = 0.73, max = 0.77 }
frequency_error_ratio = { max = 0.02 }
""",
                )
            )
            capture = fake_capture(tmp, "pwm", [], duty_cycle=0.5, frequency_error_ratio=0.03)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope_cls.return_value.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(result.steps[0].status, "failed")
                expect = result.steps[0].artifact["expect"]
                self.assertEqual(expect["status"], "failed")
                self.assertIn("duty_cycle", expect["failures"][0])
                run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
                self.assertEqual(run_data["status"], "failed")

    def test_scope_capture_expect_fails_when_metric_is_unavailable(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "dc"

[steps.expect]
duty_cycle = { min = 0.49, max = 0.51 }
""",
                )
            )
            capture = fake_capture(tmp, "dc", [], duty_cycle=None)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope_cls.return_value.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(result.steps[0].status, "failed")
                self.assertEqual(result.steps[0].artifact["expect"]["checks"]["duty_cycle"]["reason"], "unavailable")


    def test_scope_capture_fft_expect_passes_and_is_written_to_summary(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "fft"

[steps.expect_fft]
peak_frequency_hz = { min = 990.0, max = 1010.0 }
peak_amplitude_v = { min = 0.8, max = 1.2 }
harmonic_2_amplitude_v = { min = 0.05, max = 0.2 }
thd_ratio = { max = 0.2 }
""",
                )
            )
            capture = fake_capture(tmp, "fft", [])
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope_cls.return_value.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(result.steps[0].status, "ok")
                artifact = result.steps[0].artifact
                self.assertEqual(artifact["fft"]["status"], "ok")
                self.assertEqual(artifact["expect_fft"]["status"], "ok")
                self.assertIn("harmonic_2_amplitude_v", artifact["expect_fft"]["checks"])
                summary = result.summary_csv_path.read_text(encoding="utf-8")
                self.assertIn("expect_fft_status", summary)
                self.assertIn("expect_fft_failures", summary)

    def test_scope_capture_fft_expect_failure_marks_run_failed(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "fft"

[steps.expect_fft]
peak_frequency_hz = { min = 2000.0, max = 3000.0 }
""",
                )
            )
            capture = fake_capture(tmp, "fft", [])
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope_cls.return_value.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(result.steps[0].status, "failed")
                self.assertEqual(result.steps[0].artifact["expect_fft"]["status"], "failed")
                self.assertIn("peak_frequency_hz", result.steps[0].artifact["expect_fft"]["failures"][0])

    def test_runs_scope_auto_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.auto"
""",
                )
            )
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope_cls.return_value.autoscale.assert_called_once_with()
                self.assertEqual(len(result.steps), 1)
                self.assertEqual(result.steps[0].artifact, {"autoscale": "completed"})

    def test_scope_capture_screenshot_overrides_output_config(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "with_screen"
screenshot = true
save_csv = false
""",
                )
            )
            capture = fake_capture(tmp, "with_screen")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope_cls.return_value.capture_waveform.return_value = capture

                RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                capture_config = scope_cls.call_args.kwargs["config"]
                self.assertTrue(capture_config.output.save_screenshot)
                self.assertFalse(capture_config.output.save_csv)
                scope_cls.return_value.capture_waveform.assert_called_once_with(channel=1, label="with_screen")

    def test_scope_capture_can_autoscale_before_capture(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
label = "after_auto"
autoscale_before_capture = true
autoscale_settle_s = 0
""",
                )
            )
            capture = fake_capture(tmp, "after_auto")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope = scope_cls.return_value
                scope.capture_waveform.return_value = capture

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope.autoscale.assert_called_once_with()
                scope.capture_waveform.assert_called_once_with(channel=1, label="after_auto")
                self.assertEqual(
                    result.steps[0].artifact["autoscale_before_capture"],
                    {"status": "completed", "settle_s": 0.0},
                )

    def test_allows_safety_guard_on_configured_ch1_when_coupling_is_safe(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[safety]
require_scope_coupling_not = ["DC"]
scope_guard_channel = 1

[[steps]]
kind = "power.status"
channel = 1
""",
                )
            )
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.PowerService"
            ) as power_cls:
                scope_cls.return_value.channel_coupling.return_value = "DCL"
                power_cls.return_value.status.return_value = ok_power_status()

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope_cls.return_value.channel_coupling.assert_called_once_with(1)
                power_cls.return_value.status.assert_called_once_with(channel=1)
                self.assertEqual(len(result.steps), 1)

    def test_scope_capture_requires_high_impedance_by_default(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "scope.capture"
channel = 2
""",
                )
            )
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope = scope_cls.return_value
                scope.require_high_impedance.side_effect = ConfigError("scope CH2 coupling is DC")

                with self.assertRaisesRegex(ConfigError, "CH2"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope.require_high_impedance.assert_called_once_with(2, allow_50ohm=False)
                scope.capture_waveform.assert_not_called()

    def test_scope_capture_passes_explicit_50ohm_opt_in_from_plan(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[safety]
allow_50ohm = true

[[steps]]
kind = "scope.capture"
channel = 2
""",
                )
            )
            capture = fake_capture(tmp, "capture")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls:
                scope = scope_cls.return_value
                scope.capture_waveform.return_value = capture

                RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope.require_high_impedance.assert_called_once_with(2, allow_50ohm=True)
                scope.capture_waveform.assert_called_once()

    def test_rejects_safety_guard_on_configured_ch2_when_coupling_is_blocked(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[safety]
require_scope_coupling_not = ["DC"]
scope_guard_channel = 2

[[steps]]
kind = "power.status"
channel = 1
""",
                )
            )
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.PowerService"
            ) as power_cls:
                power_session = SimpleNamespace(close=Mock())
                power_cls.return_value.open_session.return_value = power_session
                scope_cls.return_value.channel_coupling.return_value = "DC"

                with self.assertRaisesRegex(ConfigError, "scope CH2 coupling is DC"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                scope_cls.return_value.channel_coupling.assert_called_once_with(2)
                power_cls.return_value.status.assert_not_called()
                power_session.close.assert_called_once_with()


    def test_runs_source_arbitrary_load_step(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "source.arb_load"
channel = 1
file = "waveform.npy"
frequency_hz = 1000
amplitude_vpp = 1.0
offset_v = 0.1
sample_rate_hz = 100000
max_points = 1024
byte_order = "little"
output_on = true
""",
                )
            )
            (Path(tmp) / "waveform.npy").write_bytes(b"test-waveform")
            fake_status = SimpleNamespace(as_dict=lambda: {"channel": 1, "function": "USER"})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                source.upload_arbitrary_waveform.return_value = fake_status

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                source.upload_arbitrary_waveform.assert_called_once_with(
                    channel=1,
                    file_path=str(Path(tmp) / "waveform.npy"),
                    playback_frequency_hz=1000.0,
                    amplitude_vpp=1.0,
                    offset_v=0.1,
                    sample_rate_hz=100000.0,
                    max_points=1024,
                    byte_order="little",
                    output_on=True,
                )
                self.assertEqual(result.steps[0].artifact["source_status"]["function"], "USER")

    def test_runs_source_steps(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[[steps]]
kind = "source.status"
channel = 2

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"

[[steps]]
kind = "source.set_freq"
channel = 2
frequency_hz = 1000

[[steps]]
kind = "source.set_vpp"
channel = 2
value_vpp = 3.3

[[steps]]
kind = "source.set_duty"
channel = 2
duty_percent = 25

[[steps]]
kind = "source.output"
channel = 2
state = "on"
""",
                )
            )
            fake_status = SimpleNamespace(as_dict=lambda: {"channel": 2})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                source.status.return_value = fake_status
                source.set_function.return_value = fake_status
                source.set_frequency.return_value = fake_status
                source.set_amplitude_vpp.return_value = fake_status
                source.set_square_duty_cycle.return_value = fake_status
                source.set_output.return_value = fake_status

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                source.status.assert_called_once_with(channel=2)
                source.set_function.assert_called_once_with(channel=2, function="SQU")
                source.set_frequency.assert_called_once_with(channel=2, value_hz=1000.0)
                source.set_amplitude_vpp.assert_called_once_with(channel=2, value_vpp=3.3)
                source.set_square_duty_cycle.assert_called_once_with(channel=2, duty_percent=25.0)
                source.set_output.assert_called_once_with(channel=2, enabled=True)
                self.assertEqual(len(result.steps), 6)

    def test_restores_source_state_after_success_when_enabled(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[restore]
source_state = true
source_channel = 2

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"
""",
                )
            )
            fake_state = SimpleNamespace(channel=2, as_dict=lambda: {"channel": 2, "function": "SIN", "square_duty_cycle_percent": 50.0})
            fake_status = SimpleNamespace(as_dict=lambda: {"channel": 2})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                source.snapshot_restorable_state.return_value = fake_state
                source.set_function.return_value = fake_status

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                source.snapshot_restorable_state.assert_called_once_with(channel=2)
                source.set_function.assert_called_once_with(channel=2, function="SQU")
                source.restore_restorable_state.assert_called_once_with(fake_state)
                run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
                self.assertEqual(run_data["restore"]["status"], "ok")
                self.assertEqual(run_data["restore"]["source_state_scope"], "basic")
                self.assertEqual(run_data["restore"]["snapshot"]["square_duty_cycle_percent"], 50.0)

    def test_source_snapshot_step_and_restore_share_run_session(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[restore]
source_state = true
source_channel = 2

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"
""",
                )
            )
            fake_state = SimpleNamespace(channel=2, as_dict=lambda: {"channel": 2, "function": "SIN"})
            fake_status = SimpleNamespace(as_dict=lambda: {"channel": 2})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                session = SimpleNamespace(close=Mock())
                source.open_session.return_value = session
                source.snapshot_restorable_state.return_value = fake_state
                source.set_function.return_value = fake_status

                RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                source.open_session.assert_called_once_with()
                source.snapshot_restorable_state.assert_called_once_with(channel=2)
                source.set_function.assert_called_once_with(channel=2, function="SQU")
                source.restore_restorable_state.assert_called_once_with(fake_state)
                session.close.assert_called_once_with()

    def test_restores_source_state_after_step_failure_when_enabled(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[restore]
source_state = true
source_channel = 2

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"
""",
                )
            )
            fake_state = SimpleNamespace(channel=2, as_dict=lambda: {"channel": 2, "function": "SIN", "square_duty_cycle_percent": 50.0})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                source.snapshot_restorable_state.return_value = fake_state
                source.set_function.side_effect = ConfigError("boom")

                with self.assertRaisesRegex(ConfigError, "boom"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                source.restore_restorable_state.assert_called_once_with(fake_state)


    def test_restores_multiple_source_channels_after_success_when_enabled(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[restore]
source_state = true
source_channels = [1, 2]

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"
""",
                )
            )
            fake_state_1 = SimpleNamespace(channel=1, as_dict=lambda: {"channel": 1, "function": "SIN"})
            fake_state_2 = SimpleNamespace(channel=2, as_dict=lambda: {"channel": 2, "function": "SIN", "square_duty_cycle_percent": 50.0})
            fake_status = SimpleNamespace(as_dict=lambda: {"channel": 2})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                source.snapshot_restorable_state.side_effect = [fake_state_1, fake_state_2]
                source.set_function.return_value = fake_status

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(source.snapshot_restorable_state.call_args_list, [call(channel=1), call(channel=2)])
                self.assertEqual(source.restore_restorable_state.call_args_list, [call(fake_state_1), call(fake_state_2)])
                run_data = json.loads(result.run_json_path.read_text(encoding="utf-8"))
                self.assertEqual(run_data["restore"]["status"], "ok")
                self.assertEqual(run_data["restore"]["source_channels"], [1, 2])
                self.assertEqual(len(run_data["restore"]["snapshots"]), 2)
                self.assertNotIn("source_channel", run_data["restore"])

    def test_restores_multiple_source_channels_after_step_failure_when_enabled(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(
                write_plan(
                    tmp,
                    """
[restore]
source_state = true
source_channels = [1, 2]

[[steps]]
kind = "source.set_func"
channel = 2
function = "SQU"
""",
                )
            )
            fake_state_1 = SimpleNamespace(channel=1, as_dict=lambda: {"channel": 1, "function": "SIN"})
            fake_state_2 = SimpleNamespace(channel=2, as_dict=lambda: {"channel": 2, "function": "SIN"})
            with patch("wavebench.services.run_service.SourceService") as source_cls:
                source = source_cls.return_value
                source.snapshot_restorable_state.side_effect = [fake_state_1, fake_state_2]
                source.set_function.side_effect = ConfigError("boom")

                with self.assertRaisesRegex(ConfigError, "boom"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

                self.assertEqual(source.restore_restorable_state.call_args_list, [call(fake_state_1), call(fake_state_2)])

    def test_frequency_response_captures_two_channels_and_persists_evidence(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
target_cycles = 6
min_signal_vpp = 0.005
settle_s = 0
"""))
            config = make_config(tmp)
            config = replace(config, output=replace(config.output, save_json=False))
            first = fake_frequency_response_capture(tmp, "response_100", frequency_hz=100.0)
            second = fake_frequency_response_capture(tmp, "response_1000", frequency_hz=1000.0)
            status = SimpleNamespace(output="ON")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                scope = scope_cls.return_value
                source = source_cls.return_value
                source.status.return_value = status
                source.set_frequency.return_value = status
                scope.capture_waveforms.side_effect = [first, second]

                result = RunService(config=config, logger=CommandLogger()).run(plan)

            self.assertEqual(
                source.set_frequency.call_args_list,
                [call(channel=1, value_hz=100.0), call(channel=1, value_hz=1000.0)],
            )
            self.assertEqual(
                scope.capture_waveforms.call_args_list,
                [
                    call(channels=[1, 2], label="frequency_response_00_000_100hz"),
                    call(channels=[1, 2], label="frequency_response_00_001_1000hz"),
                ],
            )
            self.assertEqual(scope.require_high_impedance.call_args_list, [call(1, allow_50ohm=False), call(2, allow_50ohm=False)])
            response = result.steps[0].artifact["frequency_response"]
            self.assertEqual(response["status"], "ok")
            self.assertEqual(len(response["captures"]), 2)
            self.assertTrue((result.run_dir / "frequency_response.csv").exists())
            self.assertTrue(
                all(
                    call.kwargs["config"].output.save_npy and call.kwargs["config"].output.save_json
                    for call in scope_cls.call_args_list
                    if call.kwargs["config"].waveform.expected_frequency_hz is not None
                )
            )
            self.assertTrue(
                all(
                    call.kwargs["config"].waveform.min_signal_vpp == 0.005
                    for call in scope_cls.call_args_list
                    if call.kwargs["config"].waveform.expected_frequency_hz is not None
                )
            )

    def test_frequency_response_retries_warning_after_autoscale_and_uses_clean_retry(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [1000, 2000]
settle_s = 0
"""))
            status = SimpleNamespace(output="ON")
            initial = fake_frequency_response_capture(tmp, "first", frequency_hz=1000, warnings=["frequency_mismatch"])
            retry = fake_frequency_response_capture(tmp, "retry", frequency_hz=1000)
            second = fake_frequency_response_capture(tmp, "second", frequency_hz=2000)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = status
                source.set_frequency.return_value = status
                scope = scope_cls.return_value
                scope.capture_waveforms.side_effect = [initial, retry, second]

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            rows = list(csv.DictReader((result.run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertEqual(scope.autoscale.call_count, 1)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["status"], "ok")
            self.assertEqual(rows[0]["quality_retry_count"], "1")
            self.assertEqual(rows[0]["initial_warnings"], "reference: frequency_mismatch | response: frequency_mismatch")
            self.assertEqual(rows[0]["initial_capture_package"], str(initial.package_dir))

    def test_frequency_response_marks_point_failed_when_warning_retry_is_exhausted(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [1000, 2000]
settle_s = 0
"""))
            status = SimpleNamespace(output="ON")
            first = fake_frequency_response_capture(tmp, "first", frequency_hz=1000, warnings=["frequency_mismatch"])
            retry = fake_frequency_response_capture(tmp, "retry", frequency_hz=1000, warnings=["frequency_mismatch"])
            second = fake_frequency_response_capture(tmp, "second", frequency_hz=2000)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = status
                source.set_frequency.return_value = status
                scope = scope_cls.return_value
                scope.capture_waveforms.side_effect = [first, retry, second]

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            rows = list(csv.DictReader((result.run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertEqual(scope.autoscale.call_count, 1)
            self.assertEqual(rows[0]["status"], "failed")
            self.assertEqual(rows[0]["quality_retry_count"], "1")
            self.assertIn("quality_retry_exhausted", rows[0]["error"])
            self.assertEqual(rows[1]["status"], "ok")

    def test_frequency_response_multiple_vpp_slices_autoscale_and_write_calibration(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000, 5000, 10000]
amplitudes_vpp = [0.05, 0.1]
settle_s = 0

[steps.calibration]
target_mode = "unity_gain"
max_slope_db_per_octave = 20
"""))
            config = make_config(tmp)
            status = SimpleNamespace(output="ON")
            captures = [
                fake_frequency_response_capture(tmp, f"response_{index}", frequency_hz=frequency)
                for index, frequency in enumerate([100, 1000, 5000, 10000] * 2)
            ]
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = status
                source.set_amplitude_vpp.return_value = status
                source.set_frequency.return_value = status
                scope = scope_cls.return_value
                scope.capture_waveforms.side_effect = captures

                result = RunService(config=config, logger=CommandLogger()).run(plan)

            self.assertEqual(
                source.set_amplitude_vpp.call_args_list,
                [call(channel=1, value_vpp=0.05), call(channel=1, value_vpp=0.1)],
            )
            self.assertEqual(scope.autoscale.call_count, 2)
            rows = list(csv.DictReader((result.run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertEqual(len(rows), 8)
            self.assertEqual([row["requested_vpp"] for row in rows[:4]], ["0.05"] * 4)
            self.assertEqual([row["amplitude_index"] for row in rows[4:]], ["1"] * 4)
            self.assertTrue((result.run_dir / "frequency_response_calibration.csv").exists())
            self.assertTrue((result.run_dir / "frequency_response_calibration.json").exists())
            self.assertTrue((result.run_dir / "frequency_response_calibration_fixed.csv").exists())
            self.assertTrue((result.run_dir / "frequency_response_calibration_q.coe").exists())
            self.assertTrue((result.run_dir / "frequency_response_calibration_q.mem").exists())
            response = result.steps[0].artifact["frequency_response"]
            self.assertTrue(response["calibration_csv"])

    def test_frequency_response_vpp_slices_respect_source_safety_limit(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
amplitudes_vpp = [0.05, 0.1]
"""))
            limits = SafetyLimitsConfig(max_source_vpp=0.075)
            with self.assertRaisesRegex(ConfigError, "max_source_vpp"):
                RunService(config=make_config(tmp, safety_limits=limits), logger=CommandLogger()).check(plan)

    def test_frequency_response_refuses_to_set_frequency_when_source_is_off(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
settle_s = 0
"""))
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = SimpleNamespace(output="OFF")

                with self.assertRaisesRegex(ConfigError, "output to be ON"):
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            source.set_frequency.assert_not_called()
            run_dirs = list((Path(tmp) / "data" / "runs").iterdir())
            run_data = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_data["steps"][0]["kind"], "sweep.frequency_response")
            self.assertEqual(run_data["steps"][0]["status"], "failed")
            self.assertTrue((run_dirs[0] / "frequency_response.csv").exists())
            scope_cls.return_value.capture_waveforms.assert_not_called()

    def test_frequency_response_continues_after_an_auditable_capture_failure(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000, 10000]
settle_s = 0
"""))
            status = SimpleNamespace(output="ON")
            first = fake_frequency_response_capture(tmp, "response_100", frequency_hz=100.0)
            third = fake_frequency_response_capture(tmp, "response_10000", frequency_hz=10000.0)
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = status
                source.set_frequency.return_value = status
                scope_cls.return_value.capture_waveforms.side_effect = [
                    first,
                    ConfigError("scope capture failed"),
                    third,
                ]

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            self.assertEqual(source.set_frequency.call_count, 3)
            self.assertEqual(result.steps[0].status, "failed")
            rows = list(csv.DictReader((result.run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertEqual([row["status"] for row in rows], ["ok", "failed", "ok"])
            self.assertIn("scope capture failed", rows[1]["error"])

    def test_frequency_response_source_failure_keeps_csv_step_record_and_restore(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[restore]
source_state = true
source_channel = 1

[[steps]]
kind = "sweep.frequency_response"
source_channel = 1
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000, 10000]
settle_s = 0
"""))
            first = fake_frequency_response_capture(tmp, "response_100", frequency_hz=100.0)
            status = SimpleNamespace(output="ON")
            state = SimpleNamespace(channel=1, as_dict=lambda: {"channel": 1, "function": "SIN"})
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                scope = scope_cls.return_value
                source = source_cls.return_value
                source.status.return_value = status
                source.set_frequency.side_effect = [status, ConfigError("set failed")]
                source.snapshot_restorable_state.return_value = state
                scope.capture_waveforms.return_value = first

                with self.assertRaisesRegex(ConfigError, "set failed") as raised:
                    RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(source.set_frequency.call_count, 2)
            source.restore_restorable_state.assert_called_once_with(state)
            run_dir = next((Path(tmp) / "data" / "runs").iterdir())
            rows = list(csv.DictReader((run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertEqual([row["status"] for row in rows], ["ok", "failed"])
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_data["status"], "failed")
            self.assertEqual(run_data["steps"][0]["kind"], "sweep.frequency_response")
            self.assertEqual(run_data["steps"][0]["status"], "failed")
            self.assertEqual(run_data["error"]["schema"], "wavebench.error.v1")
            self.assertEqual(run_data["error"]["code"], "config_error")
            self.assertEqual(run_data["error"]["exit_code"], 2)
            self.assertEqual(run_data["error"]["type"], "ConfigError")
            self.assertEqual(run_data["error"]["message"], "set failed")
            self.assertEqual(run_data["restore"]["status"], "ok")
            step_record = json.loads(
                (run_dir / "steps" / "00_sweep_frequency_response.json").read_text(encoding="utf-8")
            )
            self.assertEqual(step_record["status"], "failed")
            self.assertEqual(step_record["artifact"]["frequency_response"]["failed_point_count"], 1)
            summary_rows = list(csv.DictReader((run_dir / "summary.csv").open(encoding="utf-8")))
            self.assertEqual(
                summary_rows,
                [
                    {
                        "index": "0",
                        "kind": "sweep.frequency_response",
                        "status": "failed",
                        "package": "",
                        "metadata": "",
                        "quality_status": "",
                        "quality_warnings": "",
                        "recovered": "",
                        "expect_status": "",
                        "expect_failures": "",
                        "expect_fft_status": "",
                        "expect_fft_failures": "",
                    }
                ],
            )

    def test_multiple_frequency_responses_write_independent_directories_and_manifest(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
label = "input_path"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
settle_s = 0

[[steps]]
kind = "sweep.frequency_response"
label = "output_path"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
settle_s = 0
"""))
            captures = [
                fake_frequency_response_capture(tmp, f"multi_{index}", frequency_hz=frequency)
                for index, frequency in enumerate([100, 1000, 100, 1000])
            ]
            status = SimpleNamespace(output="ON")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source_cls.return_value.status.return_value = status
                source_cls.return_value.set_frequency.return_value = status
                scope_cls.return_value.capture_waveforms.side_effect = captures

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            manifest = json.loads((result.run_dir / "frequency_responses.json").read_text(encoding="utf-8"))
            self.assertEqual([item["label"] for item in manifest["responses"]], ["input_path", "output_path"])
            self.assertTrue((result.run_dir / "frequency_response" / "00_input_path" / "frequency_response.csv").exists())
            self.assertTrue((result.run_dir / "frequency_response" / "01_output_path" / "frequency_response.csv").exists())

    def test_adaptive_frequency_response_adds_midpoint_for_all_vpp_slices(self):
        with TemporaryDirectory() as tmp:
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
frequencies_hz = [100, 1000]
amplitudes_vpp = [0.05, 0.1]
settle_s = 0

[steps.adaptive]
gain_threshold_db = 0.5
phase_threshold_deg = 10
max_levels = 1
max_frequency_points = 3
"""))
            captures = [
                fake_frequency_response_capture(tmp, f"adaptive_{index}", frequency_hz=frequency, gain=gain)
                for index, (frequency, gain) in enumerate(
                    [(100, 1), (1000, 2), (100, 1), (1000, 2), (10**2.5, 1.5), (10**2.5, 1.5)]
                )
            ]
            status = SimpleNamespace(output="ON")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = status
                source.set_amplitude_vpp.return_value = status
                source.set_frequency.return_value = status
                scope_cls.return_value.capture_waveforms.side_effect = captures

                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            rows = list(csv.DictReader((result.run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertEqual(len(rows), 6)
            self.assertEqual(sum(row["adaptive_level"] == "1" for row in rows), 2)
            self.assertEqual({row["requested_vpp"] for row in rows if row["adaptive_level"] == "1"}, {"0.05", "0.1"})
            response = result.steps[0].artifact["frequency_response"]
            self.assertEqual(response["adaptive"]["final_frequency_count"], 3)

    def test_frequency_response_applies_referenced_software_baseline_without_rewriting_raw_columns(self):
        with TemporaryDirectory() as tmp:
            baseline_dir = Path(tmp) / "baseline"
            baseline_dir.mkdir()
            (baseline_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            (baseline_dir / "frequency_response.csv").write_text(
                "requested_vpp,requested_frequency_hz,gain_db,phase_unwrapped_deg,status\n"
                "0.1,100,1,10,ok\n0.1,1000,1,10,ok\n",
                encoding="utf-8",
            )
            plan = load_run_plan(write_plan(tmp, """
[[steps]]
kind = "sweep.frequency_response"
reference_channel = 1
response_channel = 2
amplitudes_vpp = [0.1]
frequencies_hz = [100, 1000]
settle_s = 0

[steps.baseline]
run_dir = "baseline"
mode = "complex_transfer"
"""))
            status = SimpleNamespace(output="ON")
            with patch("wavebench.services.run_service.ScopeService") as scope_cls, patch(
                "wavebench.services.run_service.SourceService"
            ) as source_cls:
                source = source_cls.return_value
                source.status.return_value = status
                source.set_amplitude_vpp.return_value = status
                source.set_frequency.return_value = status
                scope_cls.return_value.capture_waveforms.side_effect = [
                    fake_frequency_response_capture(tmp, "baseline_dut_100", frequency_hz=100, gain=2, phase_deg=0),
                    fake_frequency_response_capture(tmp, "baseline_dut_1000", frequency_hz=1000, gain=2, phase_deg=0),
                ]
                result = RunService(config=make_config(tmp), logger=CommandLogger()).run(plan)

            rows = list(csv.DictReader((result.run_dir / "frequency_response.csv").open(encoding="utf-8")))
            self.assertTrue(all(row["gain_db"] for row in rows))
            self.assertTrue(all(row["gain_db_corrected"] for row in rows))
            self.assertTrue((result.run_dir / "frequency_response_baseline.json").exists())


if __name__ == "__main__":
    unittest.main()
