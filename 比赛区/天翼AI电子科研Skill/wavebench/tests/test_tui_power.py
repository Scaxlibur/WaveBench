from __future__ import annotations

import importlib
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wavebench.cli import build_parser
from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    PowerConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dp800 import PowerMeasurement, PowerProtectionStatus, PowerStatus
from wavebench.errors import WaveBenchError
from wavebench.logging import CommandLogger
from wavebench.tui.dmm import FakeDmmPanelAdapter
from wavebench.tui.power import FakePowerPanelAdapter, PowerServicePanelAdapter, build_power_panel_state
from wavebench.tui.source import FakeSourcePanelAdapter
from wavebench.tui.state import POWER_TABLE_COLUMNS, channel_state_from_status, format_output_state

from wavebench.tui import app as tui_app

if tui_app._TEXTUAL_IMPORT_ERROR is None:
    from textual.widgets import Button, Input


def make_config() -> WaveBenchConfig:
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
        waveform=WaveformConfig(format="real", byte_order="lsbf", points="DEF"),
        output=OutputConfig(
            directory=Path("data/raw"),
            package_naming="timestamp_label",
            save_csv=True,
            save_npy=True,
            save_json=True,
            save_commands_log=True,
            save_screenshot=False,
        ),
        source_path=Path("wavebench.toml"),
        power=PowerConfig(
            driver="dp800",
            resource="TCPIP::power::INSTR",
            default_channel=1,
            check_errors=True,
            settle_ms_after_set=0,
            settle_ms_after_output=0,
        ),
    )


def make_status(
    *,
    channel: int = 1,
    output: str = "OFF",
    voltage: float = 3.3,
    current: float = 0.2,
    measured_voltage: float = 0.0,
    measured_current: float = 0.0,
    measured_power: float = 0.0,
) -> PowerStatus:
    return PowerStatus(
        channel=channel,
        output=output,
        mode="CV",
        rating="30V/3A",
        set_voltage_v=voltage,
        set_current_a=current,
        measured_voltage_v=measured_voltage,
        measured_current_a=measured_current,
        measured_power_w=measured_power,
    )


def make_protection(
    *,
    channel: int = 1,
    ovp_enabled: str = "ON",
    ovp_threshold: float = 6.0,
    ovp_tripped: str = "NO",
    ocp_enabled: str = "ON",
    ocp_threshold: float = 0.5,
    ocp_tripped: str = "NO",
) -> PowerProtectionStatus:
    return PowerProtectionStatus(
        channel=channel,
        ovp_enabled=ovp_enabled,
        ovp_threshold_v=ovp_threshold,
        ovp_tripped=ovp_tripped,
        ocp_enabled=ocp_enabled,
        ocp_threshold_a=ocp_threshold,
        ocp_tripped=ocp_tripped,
    )


class FakePowerService:
    def __init__(self) -> None:
        self.config = make_config()
        self.logger = CommandLogger()
        self.statuses = {1: make_status(channel=1, voltage=3.3, current=0.2)}
        self.protections = {1: make_protection(channel=1)}
        self.measurements = {
            1: PowerMeasurement(
                channel=1,
                measured_voltage_v=3.31,
                measured_current_a=0.01,
                measured_power_w=0.0331,
            )
        }
        self.status_calls: list[int] = []
        self.protection_calls: list[int] = []
        self.protection_set_calls: list[tuple[int, float | None, bool | None, float | None, bool | None]] = []
        self.measurement_calls: list[int] = []
        self.fail_measurement = False

    def idn(self) -> str:
        return "RIGOL,DP832A,SN,FW"

    def status(self, channel: int | None = None) -> PowerStatus:
        channel = 1 if channel is None else channel
        self.status_calls.append(channel)
        return self.statuses[channel]

    def measurement(self, channel: int | None = None) -> PowerMeasurement:
        channel = 1 if channel is None else channel
        self.measurement_calls.append(channel)
        if self.fail_measurement:
            raise RuntimeError("LAN timeout")
        return self.measurements[channel]

    def protection_status(self, channel: int | None = None) -> PowerProtectionStatus:
        channel = 1 if channel is None else channel
        self.protection_calls.append(channel)
        return self.protections[channel]

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
    ) -> PowerProtectionStatus:
        self.protection_set_calls.append(
            (channel, ovp_threshold_v, ovp_enabled, ocp_threshold_a, ocp_enabled)
        )
        old = self.protections[channel]
        self.protections[channel] = PowerProtectionStatus(
            channel=channel,
            ovp_enabled=old.ovp_enabled if ovp_enabled is None else ("ON" if ovp_enabled else "OFF"),
            ovp_threshold_v=old.ovp_threshold_v if ovp_threshold_v is None else ovp_threshold_v,
            ovp_tripped=old.ovp_tripped,
            ocp_enabled=old.ocp_enabled if ocp_enabled is None else ("ON" if ocp_enabled else "OFF"),
            ocp_threshold_a=old.ocp_threshold_a if ocp_threshold_a is None else ocp_threshold_a,
            ocp_tripped=old.ocp_tripped,
        )
        return self.protections[channel]

    def set_output(self, channel: int, enabled: bool) -> PowerStatus:
        self.statuses[channel] = make_status(
            channel=channel,
            output="ON" if enabled else "OFF",
            voltage=self.statuses[channel].set_voltage_v or 0.0,
            current=self.statuses[channel].set_current_a or 0.0,
        )
        return self.statuses[channel]

    def set_voltage_current_limit(self, channel: int, voltage_v: float, current_limit_a: float) -> PowerStatus:
        self.statuses[channel] = make_status(
            channel=channel,
            output=self.statuses[channel].output,
            voltage=voltage_v,
            current=current_limit_a,
        )
        return self.statuses[channel]


class TuiPowerTests(unittest.TestCase):
    def test_tui_package_import_does_not_require_textual(self):
        module = importlib.import_module("wavebench.tui")
        self.assertEqual(module.__all__, ["run"])

    def test_cli_default_refresh_interval_is_conservative(self):
        args = build_parser().parse_args(["tui", "--fake"])
        self.assertEqual(args.refresh_interval, 5.0)
        self.assertEqual(args.log_file, "data/tui/wavebench-tui.log")

    def test_cli_accepts_tui_fake_without_launching(self):
        args = build_parser().parse_args(
            ["tui", "--fake", "--refresh-interval", "0.5", "--log-file", "tmp/tui.log"]
        )
        self.assertEqual(args.domain, "tui")
        self.assertTrue(args.fake)
        self.assertEqual(args.refresh_interval, 0.5)
        self.assertEqual(args.log_file, "tmp/tui.log")

    def test_channel_state_formats_bilingual_output(self):
        status = PowerStatus(
            channel=1,
            output="ON",
            mode="CV",
            rating="30V/3A",
            set_voltage_v=5.0,
            set_current_a=0.2,
            measured_voltage_v=5.01,
            measured_current_a=0.01,
            measured_power_w=0.0501,
        )
        state = channel_state_from_status(status)
        self.assertEqual(format_output_state("0"), "关 / OFF")
        self.assertEqual(state.output, "开 / ON")
        self.assertEqual(state.set_voltage, "5 V")
        self.assertEqual(state.measured_power, "0.0501 W")

    def test_power_table_uses_compact_columns(self):
        self.assertEqual(
            POWER_TABLE_COLUMNS,
            ("通道 / CH", "行 / Row", "项目 A / Item A", "项目 B / Item B"),
        )

    @unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
    def test_power_state_cell_highlights_output_on_and_cc_mode(self):
        cell = tui_app._power_state_cell(1, "开 / ON", "CC")
        plain = cell.plain
        self.assertIn("开 / ON", plain)
        self.assertIn("CC", plain)
        styles = [span.style for span in cell.spans]
        self.assertTrue(any("green" in str(style) for style in styles))
        self.assertTrue(any("blink" in str(style) for style in styles))

    @unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
    def test_persistent_tui_log_trims_to_recent_lines(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tui.log"
            for index in range(6):
                tui_app._append_limited_log_line(path, f"line {index}", max_lines=5, keep_lines=2)

            self.assertEqual(path.read_text(encoding="utf-8").splitlines(), ["line 4", "line 5"])

    def test_channel_state_formats_protection_status(self):
        state = channel_state_from_status(
            make_status(channel=1),
            make_protection(channel=1, ovp_tripped="YES", ocp_enabled="OFF"),
        )
        self.assertEqual(state.ovp_enabled, "启用 / ON")
        self.assertEqual(state.ovp_threshold, "6 V")
        self.assertEqual(state.ovp_tripped, "已触发 / YES")
        self.assertEqual(state.ocp_enabled, "禁用 / OFF")
        self.assertEqual(state.ocp_threshold, "0.5 A")

    def test_build_power_panel_state_includes_config_and_log(self):
        status = PowerStatus(
            channel=1,
            output="OFF",
            mode="CV",
            rating="30V/3A",
            set_voltage_v=3.3,
            set_current_a=0.1,
            measured_voltage_v=0.0,
            measured_current_a=0.0,
            measured_power_w=0.0,
        )
        state = build_power_panel_state(
            config=make_config(),
            instrument_id="RIGOL,DP832A,SN,FW",
            statuses=[status],
            protections=[make_protection(channel=1)],
            log_lines=["Q :APPL? CH1"],
        )
        self.assertIn("驱动 / Driver: dp800", state.config_status)
        self.assertIn("仪器 / Instrument: RIGOL,DP832A", state.instrument_status)
        self.assertEqual(state.channels[0].output, "关 / OFF")
        self.assertEqual(state.channels[0].ovp_enabled, "启用 / ON")
        self.assertEqual(state.log_lines, ("Q :APPL? CH1",))

    def test_fake_adapter_updates_snapshot_without_instruments(self):
        adapter = FakePowerPanelAdapter()
        state = adapter.set_voltage_current_limit(channel=1, voltage_v=3.3, current_limit_a=0.2)
        self.assertEqual(state.channels[0].set_voltage, "3.3 V")
        state = adapter.set_output(channel=1, enabled=True)
        self.assertEqual(state.channels[0].output, "开 / ON")
        self.assertTrue(any("Output -> ON" in line for line in state.log_lines))
        state = adapter.refresh_protection()
        self.assertEqual(state.channels[0].ovp_threshold, "6 V")
        self.assertTrue(any("Protection refresh" in line for line in state.log_lines))

    def test_measurement_refresh_updates_mode_and_protection(self):
        service = FakePowerService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))
        adapter.refresh()
        service.statuses[1] = make_status(channel=1, output="ON", voltage=3.3, current=0.2)
        service.statuses[1] = PowerStatus(
            channel=1,
            output="ON",
            mode="CC",
            rating="30V/3A",
            set_voltage_v=3.3,
            set_current_a=0.2,
            measured_voltage_v=0.0,
            measured_current_a=0.0,
            measured_power_w=0.0,
        )
        service.protections[1] = make_protection(channel=1, ovp_tripped="YES", ocp_threshold=0.25)
        state = adapter.refresh_measurements()
        self.assertEqual(service.status_calls, [1, 1])
        self.assertEqual(service.protection_calls, [1, 1])
        self.assertEqual(service.measurement_calls, [1])
        self.assertEqual(state.channels[0].output, "开 / ON")
        self.assertEqual(state.channels[0].mode, "CC")
        self.assertEqual(state.channels[0].set_voltage, "3.3 V")
        self.assertEqual(state.channels[0].ovp_tripped, "已触发 / YES")
        self.assertEqual(state.channels[0].ocp_threshold, "0.25 A")
        self.assertEqual(state.channels[0].measured_voltage, "3.31 V")

    def test_measurement_refresh_failure_forces_next_full_refresh(self):
        service = FakePowerService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))
        adapter.refresh()
        service.fail_measurement = True
        with self.assertRaisesRegex(WaveBenchError, "实时状态刷新失败.*Live status refresh failed"):
            adapter.refresh_measurements()
        service.fail_measurement = False
        adapter.refresh_measurements()
        self.assertEqual(service.status_calls, [1, 1, 1])

    def test_manual_protection_refresh_does_not_read_measurements(self):
        service = FakePowerService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))
        adapter.refresh()
        service.measurement_calls.clear()
        service.protection_calls.clear()
        state = adapter.refresh_protection()
        self.assertEqual(service.protection_calls, [1])
        self.assertEqual(service.measurement_calls, [])
        self.assertEqual(state.channels[0].ocp_threshold, "0.5 A")

    def test_set_updates_cache_then_refreshes_measurements(self):
        service = FakePowerService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))
        adapter.refresh()
        state = adapter.set_voltage_current_limit(channel=1, voltage_v=4.2, current_limit_a=0.3)
        self.assertEqual(service.status_calls, [1, 1])
        self.assertEqual(service.measurement_calls, [1])
        self.assertEqual(service.protection_calls, [1, 1])
        self.assertEqual(state.channels[0].set_voltage, "4.2 V")
        self.assertEqual(state.channels[0].set_current, "0.3 A")
        self.assertEqual(state.channels[0].measured_power, "0.0331 W")

    def test_set_protection_uses_service_api_then_full_and_measurement_refresh(self):
        service = FakePowerService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))
        adapter.refresh()
        service.status_calls.clear()
        service.protection_calls.clear()
        service.measurement_calls.clear()
        state = adapter.set_protection(
            channel=1,
            ovp_threshold_v=5.5,
            ovp_enabled=True,
            ocp_threshold_a=0.4,
            ocp_enabled=None,
        )
        self.assertEqual(service.protection_set_calls, [(1, 5.5, True, 0.4, None)])
        self.assertEqual(service.status_calls, [1, 1])
        self.assertEqual(service.protection_calls, [1, 1])
        self.assertEqual(service.measurement_calls, [1])
        self.assertEqual(state.channels[0].ovp_threshold, "5.5 V")
        self.assertEqual(state.channels[0].ocp_threshold, "0.4 A")
        self.assertIn("保护设定完成 / Protection set complete", "\n".join(state.log_lines))

    def test_service_adapter_reuses_persistent_power_session(self):
        class FakePersistentPower:
            def __init__(self):
                self.events: list[str] = []
                self.closed = False

            def idn(self):
                self.events.append("idn")
                return "RIGOL,DP832A,SN,FW"

            def get_status(self, channel: int):
                self.events.append(f"status:{channel}")
                return make_status(channel=channel, measured_voltage=3.31, measured_current=0.01)

            def get_protection_status(self, channel: int):
                self.events.append(f"protection:{channel}")
                return make_protection(channel=channel)

            def set_voltage_current_limit(
                self,
                channel: int,
                voltage_v: float,
                current_limit_a: float,
                *,
                check_errors: bool = True,
                settle_ms_after_set: int = 0,
            ):
                self.events.append(f"set:{channel}:{voltage_v}:{current_limit_a}")
                return make_status(channel=channel, voltage=voltage_v, current=current_limit_a)

            def close(self):
                self.closed = True

        class PersistentService:
            def __init__(self):
                self.config = make_config()
                self.logger = CommandLogger()
                self.sessions: list[FakePersistentPower] = []

            def open_session(self):
                session = FakePersistentPower()
                self.sessions.append(session)
                return session

            def _power_config(self):
                return self.config.power

            def _check_power_limits(self, *, voltage_v, current_limit_a):
                return None

        service = PersistentService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))  # type: ignore[arg-type]
        adapter.refresh()
        adapter.set_voltage_current_limit(channel=1, voltage_v=4.2, current_limit_a=0.3)

        self.assertEqual(len(service.sessions), 1)
        self.assertEqual(
            service.sessions[0].events,
            [
                "idn",
                "status:1",
                "protection:1",
                "set:1:4.2:0.3",
                "status:1",
                "protection:1",
            ],
        )
        adapter.close()
        self.assertTrue(service.sessions[0].closed)

    def test_service_adapter_reconnects_after_persistent_power_session_error(self):
        class FlakyPersistentPower:
            def __init__(self, fail_status: bool):
                self.fail_status = fail_status
                self.closed = False

            def idn(self):
                return "RIGOL,DP832A,SN,FW"

            def get_status(self, channel: int):
                if self.fail_status:
                    raise OSError("bad power session")
                return make_status(channel=channel)

            def get_protection_status(self, channel: int):
                return make_protection(channel=channel)

            def close(self):
                self.closed = True

        class PersistentService:
            def __init__(self):
                self.config = make_config()
                self.logger = CommandLogger()
                self.sessions = [FlakyPersistentPower(True), FlakyPersistentPower(False)]

            def open_session(self):
                return self.sessions.pop(0)

        service = PersistentService()
        adapter = PowerServicePanelAdapter(service=service, channels=(1,))  # type: ignore[arg-type]
        with self.assertRaisesRegex(OSError, "bad power session"):
            adapter.refresh()
        self.assertIsNone(adapter._power_session)

        state = adapter.refresh()
        self.assertEqual(state.channels[0].channel, 1)

    @unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
    def test_textual_app_builds_against_fake_adapter(self):
        app = tui_app.build_app(fake=True, refresh_interval_s=10.0)
        self.assertEqual(type(app).__name__, "WaveBenchTuiApp")
        self.assertTrue(hasattr(app, "action_auto_refresh"))


@unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
class TuiPowerBusyBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_tui_persists_debug_log(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "tui.log"
            app = tui_app.build_app(fake=True, refresh_interval_s=60.0, log_path=log_path)
            async with app.run_test() as pilot:
                await pilot.pause(0.25)
                app._log("测试日志 / Test log")
                await pilot.pause(0.05)
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("TUI session started / TUI 会话开始", text)
            self.assertIn("测试日志 / Test log", text)

    async def test_quit_stops_refresh_timer_and_cancels_workers(self):
        app = tui_app.build_app(fake=True, refresh_interval_s=60.0)
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            self.assertIsNotNone(app._refresh_timer)
            await app.action_quit()
            self.assertTrue(app._shutting_down)

    async def test_layout_keeps_user_input_controls_in_scroll_area(self):
        app = tui_app.build_app(fake=True, refresh_interval_s=60.0)
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            self.assertIsNotNone(app.query_one("#main-scroll"))
            for selector in (
                "#set-channel",
                "#set-voltage",
                "#set-current",
                "#source-function",
                "#source-frequency",
                "#source-vpp",
            ):
                self.assertIsInstance(app.query_one(selector), Input)
            for selector in (
                "#set-limits",
                "#dmm-read",
                "#dmm-func-dcv",
                "#dmm-func-acv",
                "#dmm-func-res",
                "#source-apply-func",
                "#source-set-freq",
                "#source-set-vpp",
            ):
                self.assertIsInstance(app.query_one(selector), Button)

    async def test_measurement_refresh_does_not_disable_write_controls(self):
        class SlowReadAdapter(FakePowerPanelAdapter):
            def refresh_measurements(self):  # type: ignore[override]
                time.sleep(0.2)
                return super().refresh_measurements()

        app = tui_app.WaveBenchTuiApp(
            power_adapter=SlowReadAdapter(),
            dmm_adapter=FakeDmmPanelAdapter(),
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            app.action_auto_refresh()
            await pilot.pause(0.02)
            self.assertTrue(app.query_one("#refresh", Button).disabled)
            self.assertTrue(app.query_one("#protection-refresh", Button).disabled)
            self.assertFalse(app.query_one("#toggle-1", Button).disabled)
            self.assertFalse(app.query_one("#set-limits", Button).disabled)
            self.assertFalse(app.query_one("#set-protection", Button).disabled)

    async def test_measurement_refresh_coalesces_pending_reads(self):
        class SlowReadAdapter(FakePowerPanelAdapter):
            def __init__(self):
                super().__init__()
                self.measurement_refresh_calls = 0

            def refresh_measurements(self):  # type: ignore[override]
                self.measurement_refresh_calls += 1
                time.sleep(0.2)
                return super().refresh_measurements()

        power = SlowReadAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=power,
            dmm_adapter=FakeDmmPanelAdapter(),
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            app.action_auto_refresh()
            app.action_auto_refresh()
            app.action_auto_refresh()
            await pilot.pause(0.6)
            self.assertEqual(power.measurement_refresh_calls, 2)

    async def test_toggle_write_reentry_is_blocked_for_same_channel(self):
        class SlowWriteAdapter(FakePowerPanelAdapter):
            def __init__(self):
                super().__init__()
                self.output_calls: list[tuple[int, bool]] = []

            def set_output(self, channel: int, enabled: bool):  # type: ignore[override]
                self.output_calls.append((channel, enabled))
                time.sleep(0.2)
                return super().set_output(channel, enabled)

        power = SlowWriteAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=power,
            dmm_adapter=FakeDmmPanelAdapter(),
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            app._toggle_output(1)
            app._toggle_output(1)
            await pilot.pause(0.5)
            self.assertEqual(power.output_calls, [(1, True)])
            self.assertFalse(app.query_one("#toggle-1", Button).disabled)

    async def test_write_request_during_read_refresh_is_not_rejected(self):
        class SlowReadWriteAdapter(FakePowerPanelAdapter):
            def __init__(self):
                super().__init__()
                self.output_calls: list[tuple[int, bool]] = []

            def refresh_measurements(self):  # type: ignore[override]
                time.sleep(0.2)
                return super().refresh_measurements()

            def set_output(self, channel: int, enabled: bool):  # type: ignore[override]
                self.output_calls.append((channel, enabled))
                return super().set_output(channel, enabled)

        power = SlowReadWriteAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=power,
            dmm_adapter=FakeDmmPanelAdapter(),
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            app.action_auto_refresh()
            await pilot.pause(0.02)
            await pilot.click("#toggle-1")
            await pilot.pause(0.5)
            self.assertEqual(power.output_calls, [(1, True)])


if __name__ == "__main__":
    unittest.main()
