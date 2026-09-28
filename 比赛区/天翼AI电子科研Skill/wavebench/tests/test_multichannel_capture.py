import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from wavebench.config import AutoscaleConfig, ConnectionConfig, OutputConfig, ScopeConfig, WaveBenchConfig, WaveformConfig
from wavebench.drivers.rtm2032 import WaveformData, WaveformHeader
from wavebench.errors import ConfigError, DataError
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService


class FakeScope:
    def __init__(self):
        self.closed = False
        self.events = []

    def idn(self):
        return "FAKE,SCOPE"

    def capture_waveforms(
        self,
        *,
        channels,
        points,
        check_errors,
        time_range_s,
        on_channel_start,
        on_waveform,
    ):
        self.events.extend(["single", "opc"])
        waveforms = {}
        for channel in channels:
            on_channel_start(channel)
            waveform = _waveform(channel)
            waveforms[channel] = waveform
            on_waveform(channel, waveform)
        return waveforms

    def close(self):
        self.closed = True


class MultiChannelCaptureTests(unittest.TestCase):
    def test_missing_multichannel_capability_fails_before_opening_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ScopeService(config=_config(tmp), logger=CommandLogger())
            descriptor = SimpleNamespace(
                driver_id="minimal.scope",
                capabilities=("scope.idn", "scope.capture_waveform", "scope.errors"),
            )

            with patch(
                "wavebench.services.scope_service.resolve_instrument_descriptor",
                return_value=descriptor,
            ), patch.object(service, "_open_scope") as open_scope:
                with self.assertRaisesRegex(ConfigError, "scope.capture_waveforms"):
                    service.capture_waveforms(channels=[1, 2], label="unsupported")

            open_scope.assert_not_called()

    def test_capture_waveforms_writes_per_channel_files_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = WaveBenchConfig(
                connection=ConnectionConfig(backend="lan", resource="TCPIP::fake::INSTR", timeout_ms=10000, opc_timeout_ms=30000),
                scope=ScopeConfig(driver="rtm2032", model_hint=None, default_channel=1, reset_before_run=False, check_errors=True),
                autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
                waveform=WaveformConfig(format="real", byte_order="lsbf", points="DEF", time_range_s=0.01),
                output=OutputConfig(directory=Path(tmp), package_naming="timestamp_label", save_csv=False, save_npy=True, save_json=True, save_commands_log=True, save_screenshot=False),
                source_path=Path(tmp) / "wavebench.toml",
            )
            service = ScopeService(config=config, logger=CommandLogger())
            scope = FakeScope()
            with patch.object(service, "_open_scope", return_value=scope):
                result = service.capture_waveforms(channels=[1, 2], label="dual")

            self.assertTrue((result.package_dir / "ch1.npy").exists())
            self.assertTrue((result.package_dir / "ch2.npy").exists())
            metadata = json.loads((result.package_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["operation"]["channels"], [1, 2])
            self.assertEqual(metadata["operation"]["trigger_mode"], "single_acquisition")
            self.assertEqual(scope.events, ["single", "opc"])
            self.assertIn("1", metadata["channels"])
            self.assertIn("2", metadata["channels"])
            self.assertEqual(metadata["channels"]["1"]["summary"]["channel"], 1)
            self.assertEqual(metadata["channels"]["2"]["summary"]["channel"], 2)
            self.assertIn("npy", metadata["files"]["1"])
            self.assertIn("npy", metadata["files"]["2"])

    def test_partial_failure_preserves_completed_channel_and_screenshot(self):
        class PartiallyFailingScope(FakeScope):
            def capture_waveforms(self, **kwargs):
                kwargs["on_channel_start"](1)
                waveform = _waveform(1)
                kwargs["on_waveform"](1, waveform)
                kwargs["on_channel_start"](2)
                raise DataError("CH2 read failed")

            def screenshot_png(self, **kwargs):
                return b"\x89PNG\r\n\x1a\npartial"

        with tempfile.TemporaryDirectory() as tmp:
            config = _config(tmp, save_csv=True, save_screenshot=True)
            service = ScopeService(config=config, logger=CommandLogger())
            with patch.object(service, "_open_scope", return_value=PartiallyFailingScope()):
                with self.assertRaisesRegex(DataError, "CH2 read failed"):
                    service.capture_waveforms(channels=[1, 2], label="partial")

            [failed_dir] = Path(tmp).glob("*partial_failed")
            self.assertTrue((failed_dir / "ch1.csv").exists())
            self.assertTrue((failed_dir / "ch1.npy").exists())
            self.assertFalse((failed_dir / "ch2.csv").exists())
            self.assertFalse((failed_dir / "ch2.npy").exists())
            self.assertTrue((failed_dir / "screenshot.png").exists())
            metadata = json.loads(
                (failed_dir / "metadata.partial.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["completed_channels"], [1])
            self.assertEqual(metadata["failed_channel"], 2)
            self.assertEqual(metadata["stage"], "read_waveform")
            self.assertEqual(metadata["channels"]["1"]["summary"]["channel"], 1)
            self.assertTrue(metadata["files"]["1"]["csv"].startswith(str(failed_dir)))
            self.assertEqual(metadata["files"]["screenshot"], str(failed_dir / "screenshot.png"))

    def test_channel_file_promotion_rolls_back_when_second_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(tmp, save_csv=True)
            service = ScopeService(config=config, logger=CommandLogger())
            real_replace = os.replace
            replace_count = 0

            def fail_second_replace(source, destination):
                nonlocal replace_count
                replace_count += 1
                if replace_count == 2:
                    raise OSError("simulated promotion failure")
                return real_replace(source, destination)

            with patch.object(service, "_open_scope", return_value=FakeScope()), patch(
                "wavebench.services.scope_service.os.replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaisesRegex(OSError, "simulated promotion failure"):
                    service.capture_waveforms(channels=[1, 2], label="atomic")

            [failed_dir] = Path(tmp).glob("*atomic_failed")
            self.assertFalse((failed_dir / "ch1.csv").exists())
            self.assertFalse((failed_dir / "ch1.npy").exists())
            self.assertEqual(list(failed_dir.glob(".*.tmp")), [])
            metadata = json.loads(
                (failed_dir / "metadata.partial.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["completed_channels"], [])
            self.assertEqual(metadata["failed_channel"], 1)
            self.assertEqual(metadata["stage"], "write_waveform")


def _waveform(channel):
    return WaveformData(
        channel=channel,
        header=WaveformHeader(x_start=0.0, x_stop=0.001, points=3, segment=1),
        voltages_v=np.array([0.0, float(channel), 0.0], dtype=np.float64),
    )


def _config(tmp, *, save_csv=False, save_screenshot=False):
    return WaveBenchConfig(
        connection=ConnectionConfig(
            backend="lan",
            resource="TCPIP::fake::INSTR",
            timeout_ms=10000,
            opc_timeout_ms=30000,
        ),
        scope=ScopeConfig(
            driver="rtm2032",
            model_hint=None,
            default_channel=1,
            reset_before_run=False,
            check_errors=True,
        ),
        autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
        waveform=WaveformConfig(
            format="real",
            byte_order="lsbf",
            points="DEF",
            time_range_s=0.01,
        ),
        output=OutputConfig(
            directory=Path(tmp),
            package_naming="timestamp_label",
            save_csv=save_csv,
            save_npy=True,
            save_json=True,
            save_commands_log=True,
            save_screenshot=save_screenshot,
        ),
        source_path=Path(tmp) / "wavebench.toml",
    )


if __name__ == "__main__":
    unittest.main()
