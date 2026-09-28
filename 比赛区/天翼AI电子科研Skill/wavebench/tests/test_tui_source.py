from __future__ import annotations

import time
import unittest
from pathlib import Path

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    DmmConfig,
    OutputConfig,
    PowerConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dg4202 import SourceStatus
from wavebench.tui.source import FakeSourcePanelAdapter, build_source_panel_state
from wavebench.tui.state import SOURCE_TABLE_COLUMNS, source_config_status, source_state_from_status

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
            settle_ms_after_set=0,
            settle_ms_after_output=0,
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
            settle_ms_before_read=0,
        ),
    )


def make_status(
    *,
    output: str = "ON",
    function: str = "SIN",
    frequency_hz: float | None = 1000.0,
    amplitude: float | None = 2.0,
    amplitude_unit: str | None = "VPP",
    offset_v: float | None = 0.1,
) -> SourceStatus:
    return SourceStatus(
        channel=2,
        output=output,
        function=function,
        frequency_hz=frequency_hz,
        amplitude=amplitude,
        amplitude_unit=amplitude_unit,
        offset_v=offset_v,
        phase_deg=0.0,
        frequency_mode="FIX",
        sweep_enabled="OFF",
        apply_raw="SIN,1000,2.0,0.1",
    )


class TuiSourceTests(unittest.TestCase):
    def test_source_config_status_is_bilingual(self):
        status = source_config_status(make_config())
        self.assertIn("驱动 / Driver: dg4202", status)
        self.assertIn("资源 / Resource: TCPIP::source::INSTR", status)
        self.assertIn("默认通道 / Default CH: 2", status)

    def test_source_table_labels_are_bilingual(self):
        self.assertIn("输出 / Output", SOURCE_TABLE_COLUMNS)
        self.assertIn("频率 / Freq Hz", SOURCE_TABLE_COLUMNS)

    def test_source_state_formats_basic_fields(self):
        state = source_state_from_status(
            config=make_config(),
            instrument_id="RIGOL,DG4202,SN,FW",
            status=make_status(),
            log_lines=["Q :SOUR2:FREQ?"],
        )
        self.assertEqual(state.connection_status, "已连接 / Connected")
        self.assertEqual(state.output, "开 / ON")
        self.assertEqual(state.function, "SIN")
        self.assertEqual(state.frequency_hz, "1000")
        self.assertEqual(state.amplitude_vpp, "2")
        self.assertEqual(state.offset_v, "0.1")
        self.assertEqual(state.log_lines, ("Q :SOUR2:FREQ?",))

    def test_source_state_marks_non_vpp_amplitude(self):
        state = build_source_panel_state(
            config=make_config(),
            instrument_id="RIGOL,DG4202,SN,FW",
            status=make_status(amplitude=0.707, amplitude_unit="VRMS"),
        )
        self.assertIn("非VPP / not VPP", state.amplitude_vpp)

    def test_fake_source_adapter_supports_common_operations(self):
        adapter = FakeSourcePanelAdapter()
        refreshed = adapter.refresh()
        self.assertEqual(refreshed.function, "SIN")
        freq_state = adapter.set_frequency(2000.0)
        self.assertEqual(freq_state.frequency_hz, "2000")
        vpp_state = adapter.set_amplitude_vpp(3.3)
        self.assertEqual(vpp_state.amplitude_vpp, "3.3")
        func_state = adapter.set_function("triangle")
        self.assertEqual(func_state.function, "RAMP")
        out_state = adapter.set_output(True)
        self.assertEqual(out_state.output, "开 / ON")
        self.assertTrue(any("fake source" in line for line in out_state.log_lines))

    @unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
    def test_textual_app_builds_with_fake_source(self):
        app = tui_app.build_app(fake=True, refresh_interval_s=10.0)
        self.assertEqual(type(app.source_adapter).__name__, "FakeSourcePanelAdapter")


@unittest.skipIf(tui_app._TEXTUAL_IMPORT_ERROR is not None, "Textual extra is not installed")
class TuiSourceBusyBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_quit_closes_source_adapter(self):
        class ClosableSourceAdapter(FakeSourcePanelAdapter):
            def __init__(self):
                super().__init__()
                self.closed = False

            def close(self):
                self.closed = True

        source = ClosableSourceAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=tui_app.FakeDmmPanelAdapter(),
            source_adapter=source,
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            await app.action_quit()

        self.assertTrue(source.closed)

    async def test_auto_refresh_does_not_poll_source(self):
        class CountingSourceAdapter(FakeSourcePanelAdapter):
            def __init__(self):
                super().__init__()
                self.refresh_calls = 0

            def refresh(self):  # type: ignore[override]
                self.refresh_calls += 1
                return super().refresh()

        source = CountingSourceAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=tui_app.FakeDmmPanelAdapter(),
            source_adapter=source,
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            self.assertEqual(source.refresh_calls, 1)
            app.action_auto_refresh()
            app.action_auto_refresh()
            await pilot.pause(0.25)
            self.assertEqual(source.refresh_calls, 1)

    async def test_source_write_controls_wait_until_initial_state_loads(self):
        class FlakyInitialSourceAdapter(FakeSourcePanelAdapter):
            def __init__(self):
                super().__init__()
                self.refresh_calls = 0

            def refresh(self):  # type: ignore[override]
                self.refresh_calls += 1
                if self.refresh_calls == 1:
                    raise RuntimeError("transient source startup error")
                return super().refresh()

        source = FlakyInitialSourceAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=tui_app.FakeDmmPanelAdapter(),
            source_adapter=source,
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            self.assertIsNone(app._last_source_state)
            self.assertTrue(app.query_one("#source-set-freq", Button).disabled)
            app.action_auto_refresh()
            await pilot.pause(0.25)
            self.assertIsNotNone(app._last_source_state)
            self.assertFalse(app.query_one("#source-set-freq", Button).disabled)

    async def test_source_write_reentry_is_blocked(self):
        class SlowSourceAdapter(FakeSourcePanelAdapter):
            def __init__(self):
                super().__init__()
                self.frequency_calls: list[float] = []

            def set_frequency(self, value_hz: float):  # type: ignore[override]
                self.frequency_calls.append(value_hz)
                time.sleep(0.2)
                return super().set_frequency(value_hz)

        source = SlowSourceAdapter()
        app = tui_app.WaveBenchTuiApp(
            power_adapter=tui_app.FakePowerPanelAdapter(),
            dmm_adapter=tui_app.FakeDmmPanelAdapter(),
            source_adapter=source,
            refresh_interval_s=60.0,
        )
        async with app.run_test() as pilot:
            await pilot.pause(0.25)
            app.query_one("#source-frequency").value = "2500"
            app._set_source_frequency()
            app._set_source_frequency()
            await pilot.pause(0.6)
            self.assertEqual(source.frequency_calls, [2500.0])
            self.assertFalse(app.query_one("#source-set-freq", Button).disabled)


if __name__ == "__main__":
    unittest.main()
