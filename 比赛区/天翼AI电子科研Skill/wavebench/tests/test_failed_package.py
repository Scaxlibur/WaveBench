import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wavebench.config import AutoscaleConfig, ConnectionConfig, OutputConfig, ScopeConfig, WaveBenchConfig, WaveformConfig
from wavebench.errors import OperationTimeout
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService


class FailingScopeService(ScopeService):
    def _open_scope(self):
        class Scope:
            def idn(self):
                return "fake"
            def capture_waveform(self, channel, points, check_errors, time_range_s=None):
                raise OperationTimeout("simulated trigger timeout")
            def close(self):
                pass
        return Scope()


class FailedPackageTests(unittest.TestCase):
    def test_capture_failure_writes_failed_package(self):
        with TemporaryDirectory() as tmp:
            config = WaveBenchConfig(
                connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
                scope=ScopeConfig("rtm2032", None, 1, False, True),
                autoscale=AutoscaleConfig(True, True),
                waveform=WaveformConfig("real", "lsbf", "dmax"),
                output=OutputConfig(Path(tmp), "timestamp_label", True, True, True, True, False),
                source_path=Path("test.toml"),
            )
            service = FailingScopeService(config=config, logger=CommandLogger())
            with self.assertRaises(OperationTimeout):
                service.capture_waveform(channel=1, label="timeout_case")
            failed = list(Path(tmp).glob("*timeout_case_failed"))
            self.assertEqual(len(failed), 1)
            self.assertTrue((failed[0] / "error.txt").exists())
            self.assertTrue((failed[0] / "metadata.partial.json").exists())


if __name__ == "__main__":
    unittest.main()
