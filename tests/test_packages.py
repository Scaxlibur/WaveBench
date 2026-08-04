import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wavebench.data.packages import load_capture_package, load_run_package
from wavebench.errors import ConfigError


class PackageReaderTests(unittest.TestCase):
    def test_load_single_channel_capture_package(self):
        with TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "metadata.json").write_text(
                json.dumps(
                    {
                        "instrument": {"resource": "TCPIP::example::INSTR"},
                        "operation": {"command": "scope capture", "channel": 1},
                        "waveform": {
                            "header": {"points": 1000},
                            "summary": {
                                "channel": 1,
                                "samples": 1000,
                                "voltage_vpp_v": 5.0,
                                "frequency_estimate_hz": 1000.0,
                            },
                        },
                        "files": {"npy": str(package / "ch1.npy")},
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_capture_package(package)

            self.assertEqual(loaded.operation["command"], "scope capture")
            self.assertEqual(len(loaded.channels), 1)
            self.assertEqual(loaded.channels[0].channel, 1)
            self.assertEqual(loaded.channels[0].summary["samples"], 1000)
            self.assertIn("npy", loaded.channels[0].files)

    def test_load_multi_channel_capture_package(self):
        with TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / "metadata.json").write_text(
                json.dumps(
                    {
                        "operation": {"command": "scope capture", "channels": [1, 2]},
                        "channels": {
                            "2": {"header": {"points": 20}, "summary": {"channel": 2, "samples": 20}},
                            "1": {"header": {"points": 10}, "summary": {"channel": 1, "samples": 10}},
                        },
                        "files": {
                            "1": {"npy": str(package / "ch1.npy")},
                            "2": {"npy": str(package / "ch2.npy")},
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_capture_package(package)

            self.assertEqual([channel.channel for channel in loaded.channels], [1, 2])
            self.assertEqual(loaded.channels[1].files["npy"], str(package / "ch2.npy"))

    def test_load_capture_package_requires_metadata(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                load_capture_package(tmp)

    def test_load_run_package_reads_run_json_and_summary(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [{"index": 1, "kind": "scope.capture", "status": "ok"}],
                    }
                ),
                encoding="utf-8",
            )
            (run / "summary.csv").write_text("index,kind,status\n1,scope.capture,ok\n", encoding="utf-8")

            loaded = load_run_package(run)

            self.assertEqual(loaded.status, "ok")
            self.assertEqual(len(loaded.steps), 1)
            self.assertEqual(loaded.summary_rows[0]["kind"], "scope.capture")

    def test_load_run_package_reads_frequency_response_and_tolerates_bad_fit_json(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "run.json").write_text(json.dumps({"status": "failed", "steps": []}), encoding="utf-8")
            (run / "frequency_response.csv").write_text(
                "index,requested_frequency_hz,gain_linear,status\n0,100,2,ok\n",
                encoding="utf-8",
            )
            (run / "frequency_response_fit.json").write_text("not json", encoding="utf-8")

            loaded = load_run_package(run)

            self.assertEqual(loaded.frequency_response_rows[0]["gain_linear"], "2")
            self.assertIsNotNone(loaded.frequency_response_csv_path)
            self.assertIsNotNone(loaded.frequency_response_fit_path)
            self.assertIsNone(loaded.frequency_response_fit)
            self.assertIn("not valid JSON", loaded.frequency_response_fit_error or "")

    def test_load_run_package_reads_frequency_response_calibration_artifacts(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            (run / "frequency_response_calibration.csv").write_text(
                "frequency_hz,requested_vpp,correction_db\n100,0.1,-1\n",
                encoding="utf-8",
            )
            (run / "frequency_response_calibration.json").write_text(
                json.dumps({"schema_version": 1, "target_gain_db": 0}), encoding="utf-8"
            )

            loaded = load_run_package(run)

            self.assertEqual(loaded.frequency_response_calibration_rows[0]["requested_vpp"], "0.1")
            self.assertEqual(loaded.frequency_response_calibration["target_gain_db"], 0)


if __name__ == "__main__":
    unittest.main()
