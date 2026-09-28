import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.data.package import safe_label
from wavebench.drivers.rtm2032 import WaveformData, WaveformHeader
from wavebench.logging import CommandLogger
from wavebench.instruments.registry import build_instrument_registry
from wavebench.services.scope_service import ScopeService


class PackageTests(unittest.TestCase):
    def test_safe_label_keeps_simple_names(self):
        self.assertEqual(safe_label("ch1"), "ch1")

    def test_safe_label_replaces_spaces(self):
        self.assertEqual(safe_label("my capture"), "my_capture")


class ScreenshotScope:
    def idn(self):
        return "FAKE,SCOPE"

    def capture_waveform(self, *, channel, points, check_errors, time_range_s):
        return WaveformData(
            channel=channel,
            header=WaveformHeader(x_start=0.0, x_stop=0.001, points=3, segment=1),
            voltages_v=np.array([0.0, 1.0, 0.0], dtype=np.float64),
        )

    def screenshot_png(self, *, include_menu=False, color_scheme="COL"):
        return b"\x89PNG\r\n\x1a\nfake"

    def close(self):
        pass


class ScreenshotCaptureTests(unittest.TestCase):
    def test_capture_waveform_writes_screenshot_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = WaveBenchConfig(
                connection=ConnectionConfig(backend="lan", resource="TCPIP::fake::INSTR", timeout_ms=10000, opc_timeout_ms=30000),
                scope=ScopeConfig(driver="rtm2032", model_hint=None, default_channel=1, reset_before_run=False, check_errors=True),
                autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
                waveform=WaveformConfig(format="real", byte_order="lsbf", points="DEF"),
                output=OutputConfig(directory=Path(tmp), package_naming="timestamp_label", save_csv=False, save_npy=True, save_json=True, save_commands_log=True, save_screenshot=True),
                source_path=Path(tmp) / "wavebench.toml",
            )
            service = ScopeService(config=config, logger=CommandLogger())
            with patch.object(service, "_open_scope", return_value=ScreenshotScope()):
                result = service.capture_waveform(channel=1, label="with_screen")

            self.assertIsNotNone(result.screenshot_path)
            self.assertTrue(result.screenshot_path.exists())
            self.assertEqual(result.screenshot_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            metadata = json.loads((result.package_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["files"]["screenshot"], str(result.screenshot_path))

    def test_new_screenshot_capability_rejects_legacy_embedding_before_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = WaveBenchConfig(
                connection=ConnectionConfig(
                    backend="lan",
                    resource="TCPIP::fake::INSTR",
                    timeout_ms=10_000,
                    opc_timeout_ms=30_000,
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
                    directory=Path(tmp),
                    package_naming="timestamp_label",
                    save_csv=False,
                    save_npy=False,
                    save_json=True,
                    save_commands_log=False,
                    save_screenshot=True,
                ),
                source_path=Path(tmp) / "wavebench.toml",
            )
            original = build_instrument_registry(include_entry_points=False).resolve("rtm2032")
            descriptor = replace(
                original,
                capabilities=(
                    *(capability for capability in original.capabilities if capability != "scope.screenshot"),
                    "scope.screenshot_v2",
                ),
            )
            service = ScopeService(
                config=config,
                logger=CommandLogger(),
                descriptor=descriptor,
            )

            with patch.object(service, "_open_scope") as open_scope:
                with self.assertRaisesRegex(Exception, "field-closure runtime"):
                    service.capture_waveform(channel=1, label="blocked")

            open_scope.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_legacy_embedding_remains_available_during_dual_capability_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = WaveBenchConfig(
                connection=ConnectionConfig(
                    backend="lan",
                    resource="TCPIP::fake::INSTR",
                    timeout_ms=10_000,
                    opc_timeout_ms=30_000,
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
                ),
                output=OutputConfig(
                    directory=Path(tmp),
                    package_naming="timestamp_label",
                    save_csv=False,
                    save_npy=False,
                    save_json=True,
                    save_commands_log=False,
                    save_screenshot=True,
                ),
                source_path=Path(tmp) / "wavebench.toml",
            )
            original = build_instrument_registry(include_entry_points=False).resolve(
                "rtm2032"
            )
            descriptor = replace(
                original,
                capabilities=(*original.capabilities, "scope.screenshot_v2"),
            )
            service = ScopeService(
                config=config,
                logger=CommandLogger(),
                descriptor=descriptor,
            )

            self.assertEqual(
                service._legacy_capture_screenshot_capability(),
                "scope.screenshot",
            )


if __name__ == "__main__":
    unittest.main()
