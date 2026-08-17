from __future__ import annotations

import unittest
from pathlib import Path
import time
from unittest.mock import patch

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    DmmConfig,
    OutputConfig,
    PowerConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dm3000 import DmmReading
from wavebench.tui.dmm import DmmServicePanelAdapter, FakeDmmPanelAdapter, build_dmm_panel_state
from wavebench.tui.source import FakeSourcePanelAdapter
from wavebench.tui.state import dmm_config_status, dmm_state_from_reading

from wavebench.tui import app as tui_app

if tui_app._TEXTUAL_IMPORT_ERROR is None:
    from textual.widgets import Button


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
        dmm=DmmConfig(
            driver="dm3000",
            resource="/dev/ttyUSB0",
            backend="serial",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_ms=1000,
            settle_ms_before_read=0,
        ),
    )


class TuiDmmTests(unittest.TestCase):
    def test_dmm_config_status_is_bilingual(self):
        status = dmm_config_status(make_config())
        self.assertIn("驱动 / Driver: dm3000", status)
        self.assertIn("后端 / Backend: serial", status)
        self.assertIn("资源 / Resource: /dev/ttyUSB0", status)

    def test_dmm_state_formats_reading_and_log(self):
        reading = DmmReading(function="dcv", value=1.234567, unit="V", raw="+1.234567E+00")
        state = dmm_state_from_reading(
            config=make_config(),
            instrument_id="RIGOL,DM3058,SN,FW",
            reading=reading,
            log_lines=["Q :MEASure:VOLTage:DC?"],
        )
        self.assertEqual(state.connection_status, "已连接 / Connected")
        self.assertEqual(state.function, "dcv")
        self.assertEqual(state.value, "1.23457")
        self.assertEqual(state.unit, "V")
        self.assertEqual(state.raw_reading, "+1.234567E+00")
        self.assertEqual(state.log_lines, ("Q :MEASure:VOLTage:DC?",))

    def test_build_dmm_panel_state_includes_instrument(self):
        state = build_dmm_panel_state(
            config=make_config(),
            instrument_id="RIGOL,DM3058,SN,FW",
            reading=DmmReading(function="res", value=1000.0, unit="ohm", raw="1000"),
        )
        self.assertIn("仪器 / Instrument: RIGOL,DM3058", state.instrument_status)
        self.assertEqual(state.value, "1000")
        self.assertEqual(state.unit, "ohm")

    def test_fake_dmm_adapter_reads_without_instrument(self):
        adapter = FakeDmmPanelAdapter()
        state = adapter.read("ohm")
        self.assertEqual(state.function, "res")
        self.assertEqual(state.value, "1000")
        self.assertEqual(state.unit, "ohm")
        self.assertTrue(any("fake DMM" in line for line in state.log_lines))
        self.assertEqual(adapter.function_status(), "res")

    def test_fake_dmm_adapter_set_function_updates_state_and_readout(self):
        adapter = FakeDmmPanelAdapter()
        state = adapter.set_function("acv")
        self.assertEqual(state.function, "acv")
        self.assertEqual(adapter.function_status(), "acv")
        self.assertTrue(any("Function set fake DMM acv" in line for line in state.log_lines))

    def test_service_adapter_read_uses_cached_function_without_status_query(self):
        class NoStatusService:
            config = make_config()
            logger = type("Logger", (), {"entries": []})()

            def __init__(self):
                self.read_calls: list[str] = []

            def idn(self):
                return "RIGOL,DM3058,SN,FW"

            def function_status(self):
                raise AssertionError("read refresh must not query :FUNCtion?")

            def read(self, function: str = "dcv"):
                self.read_calls.append(function)
                return DmmReading(function=function, value=1.0, unit="V", raw="1.0")

        service = NoStatusService()
        adapter = DmmServicePanelAdapter(service=service)  # type: ignore[arg-type]
        state = adapter.read()
        self.assertEqual(state.function, "dcv")
        self.assertEqual(service.read_calls, ["dcv"])

    def test_service_adapter_repeated_function_click_skips_set_command(self):
        class RecordingService:
            config = make_config()
            logger = type("Logger", (), {"entries": []})()

            def __init__(self):
                self.set_calls: list[str] = []
                self.read_calls: list[str] = []

            def idn(self):
                return "RIGOL,DM3058,SN,FW"

            def function_status(self):
                return "dcv"

            def set_function(self, function: str):
                self.set_calls.append(function)
                return function

            def read(self, function: str = "dcv"):
                self.read_calls.append(function)
                return DmmReading(function=function, value=1.0, unit="V", raw="1.0")

        service = RecordingService()
        adapter = DmmServicePanelAdapter(service=service)  # type: ignore[arg-type]
        state = adapter.set_function("dcv")
        self.assertEqual(state.function, "dcv")
        self.assertEqual(service.set_calls, [])
        self.assertEqual(service.read_calls, ["dcv"])

    def test_service_adapter_waits_after_function_change_before_read(self):
        class RecordingService:
            logger = type("Logger", (), {"entries": []})()

            def __init__(self):
                self.config = make_config()
                object.__setattr__(self.config, "dmm", DmmConfig(
                    driver="dm3000",
                    resource="/dev/ttyUSB0",
                    backend="serial",
                    baudrate=9600,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                    timeout_ms=1000,
                    settle_ms_before_read=0,
                    settle_ms_after_function_change=500,
                ))
                self.events: list[str] = []

            def idn(self):
                return "RIGOL,DM3058,SN,FW"

            def function_status(self):
                return "dcv"

            def set_function(self, function: str):
                self.events.append(f"set:{function}")
                return function

            def read(self, function: str = "dcv"):
                self.events.append(f"read:{function}")
                return DmmReading(function=function, value=1.0, unit="V", raw="1.0")

        service = RecordingService()
        adapter = DmmServicePanelAdapter(service=service)  # type: ignore[arg-type]
        with patch("wavebench.tui.dmm.time.sleep", side_effect=lambda seconds: service.events.append(f"sleep:{seconds}")):
            adapter.set_function("acv")
        self.assertEqual(service.events, ["set:acv", "sleep:0.5", "read:acv"])

    def test_service_adapter_reuses_persistent_dmm_session(self):
        class FakePersistentDmm:
            def __init__(self):
                self.events: list[str] = []
                self.closed = False

            def idn(self):
                self.events.append("idn")
                return "RIGOL,DM3058,SN,FW"

            def apply_function(self, function: str):
                self.events.append(f"apply:{function}")
                return function

            def read(self, function: str = "dcv"):
                self.events.append(f"read:{function}")
                return DmmReading(function=function, value=1.0, unit="V", raw="1.0")

            def close(self):
                self.closed = True

        class PersistentService:
            logger = type("Logger", (), {"entries": []})()

            def __init__(self):
                self.config = make_config()
                self.sessions: list[FakePersistentDmm] = []

            def open_session(self):
                session = FakePersistentDmm()
                self.sessions.append(session)
                return session

        service = PersistentService()
        adapter = DmmServicePanelAdapter(service=service)  # type: ignore[arg-type]
        adapter.read()
        adapter.set_function("acv")

        self.assertEqual(len(service.sessions), 1)
        self.assertEqual(
            service.sessions[0].events,
            ["idn", "read:dcv", "apply:acv", "read:acv"],
        )
        adapter.close()
        self.assertTrue(service.sessions[0].closed)

    def test_service_adapter_reconnects_after_persistent_session_error(self):
        class FlakyPersistentDmm:
            def __init__(self, fail_read: bool):
                self.fail_read = fail_read
                self.closed = False

            def idn(self):
                return "RIGOL,DM3058,SN,FW"

            def read(self, function: str = "dcv"):
                if self.fail_read:
                    raise OSError("bad session")
                return DmmReading(function=function, value=2.0, unit="V", raw="2.0")

            def close(self):
                self.closed = True

        class PersistentService:
            logger = type("Logger", (), {"entries": []})()

            def __init__(self):
                self.config = make_config()
                self.sessions = [FlakyPersistentDmm(True), FlakyPersistentDmm(False)]

            def open_session(self):
                return self.sessions.pop(0)

        service = PersistentService()
        adapter = DmmServicePanelAdapter(service=service)  # type: ignore[arg-type]
        with self.assertRaisesRegex(OSError, "bad session"):
            adapter.read()
        self.assertTrue(adapter._dmm_session is None)

        state = adapter.read()
        self.assertEqual(state.value, "2")

    def test_power_resource_override_preserves_dmm_config(self):
        config = make_config().with_power_resource("TCPIP::new-power::INSTR")
        self.assertIsNotNone(config.dmm)
        self.assertEqual(config.power.resource, "TCPIP::new-power::INSTR")
        self.assertEqual(config.dmm.resource, "/dev/ttyUSB0")

    @unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
    def test_textual_app_builds_with_fake_power_and_dmm(self):
        app = tui_app.build_app(fake=True, refresh_interval_s=10.0)
        self.assertEqual(type(app.power_adapter).__name__, "FakePowerPanelAdapter")
        self.assertEqual(type(app.dmm_adapter).__name__, "FakeDmmPanelAdapter")


@unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
class TuiDmmBusyBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_refresh_disables_function_buttons(self):
        class SlowReadAdapter(FakeDmmPanelAdapter):
            def read(self, function: str | None = None):  # type: ignore[override]
                time.sleep(0.2)
                return super().read(function=function)

        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=SlowReadAdapter(),
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            for _ in range(20):
                if not app._dmm_read_in_flight:
                    break
                await pilot.pause(0.05)
            self.assertFalse(app._dmm_read_in_flight)
            app._read_dmm()
            await pilot.pause(0.02)
            self.assertTrue(app.query_one("#dmm-read", Button).disabled)
            self.assertTrue(app.query_one("#dmm-func-acv", Button).disabled)

    async def test_apply_reentry_is_blocked(self):
        class SlowApplyAdapter(FakeDmmPanelAdapter):
            def __init__(self):
                super().__init__()
                self.apply_calls: list[str] = []

            def set_function(self, function: str):  # type: ignore[override]
                self.apply_calls.append(function)
                time.sleep(0.2)
                return super().set_function(function)

        adapter = SlowApplyAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=adapter,
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            app._set_dmm_function("acv")
            app._set_dmm_function("acv")
            await pilot.pause(0.5)
            self.assertEqual(adapter.apply_calls, ["acv"])
            self.assertFalse(app.query_one("#dmm-func-acv", Button).disabled)

    async def test_function_button_applies_function(self):
        class RecordingAdapter(FakeDmmPanelAdapter):
            def __init__(self):
                super().__init__()
                self.apply_calls: list[str] = []

            def set_function(self, function: str):  # type: ignore[override]
                self.apply_calls.append(function)
                return super().set_function(function)

        adapter = RecordingAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=adapter,
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            self.assertIsInstance(app.query_one("#dmm-func-acv"), Button)
            self.assertEqual(app.query_one("#dmm-func-acv", Button).label.plain, "交流电压 / ACV")
            app._set_dmm_function("acv")
            await pilot.pause(0.25)
            self.assertEqual(adapter.apply_calls, ["acv"])

    async def test_dmm_readout_highlights_function_and_value(self):
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=FakeDmmPanelAdapter(),
            source_adapter=FakeSourcePanelAdapter(),
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            state = FakeDmmPanelAdapter().read("acv")
            app._render_dmm_state(state)
            text = tui_app._dmm_readout_text(state)
            self.assertIn("功能 / Function", text.plain)
            self.assertIn("读数 / Reading", text.plain)
            styles = [str(span.style) for span in text.spans]
            self.assertTrue(any("on #1f3a5f" in style for style in styles))
            self.assertTrue(any("on #9adf9a" in style for style in styles))


if __name__ == "__main__":
    unittest.main()
