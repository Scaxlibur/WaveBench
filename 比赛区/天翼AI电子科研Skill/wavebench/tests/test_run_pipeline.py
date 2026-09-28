from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from wavebench.errors import ConfigError
from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_pipeline import (
    ensure_analysis_pipeline_dependencies,
    execute_analysis_pipeline,
)
from wavebench.services.run_plan import RunStep, load_run_plan


HAS_SCIPY = importlib.util.find_spec("scipy") is not None


class AnalysisPipelineArtifactTests(unittest.TestCase):
    def source(
        self,
        root: Path,
        data: np.ndarray,
        *,
        npy_metadata_path: str | None = None,
        status: str = "ok",
    ) -> tuple[RunStep, RunStepRecord, Path]:
        package = root / "raw" / "capture"
        package.mkdir(parents=True)
        npy_path = package / "ch1.npy"
        np.save(npy_path, data)
        metadata = package / "metadata.json"
        metadata.write_text(
            json.dumps({
                "operation": {"channel": 1},
                "files": {"npy": npy_metadata_path or str(npy_path)},
            }),
            encoding="utf-8",
        )
        source_step = RunStep(
            index=0,
            kind="scope.capture",
            fields={"save_npy": True},
            id="capture_main",
        )
        source_record = RunStepRecord(
            index=0,
            kind="scope.capture",
            status=status,
            fields=source_step.fields,
            artifact={"package": str(package), "metadata": str(metadata)},
        )
        return source_step, source_record, npy_path

    def pipeline(
        self,
        operations: list[dict[str, object]],
        *,
        expect: dict[str, dict[str, float]] | None = None,
    ) -> RunStep:
        fields: dict[str, object] = {
            "source": {"step": "capture_main"},
            "operations": operations,
        }
        if expect is not None:
            fields["expect"] = expect
        return RunStep(
            index=1,
            kind="analysis.pipeline",
            fields=fields,
            id="spectrum_main",
        )

    def waveform(self, *, nonuniform: bool = False) -> np.ndarray:
        samples = 1000
        time_s = np.arange(samples, dtype=float) / 10_000.0
        if nonuniform:
            time_s[500:] += 1e-5
        voltage_v = 0.5 + np.sin(2 * np.pi * 100.0 * time_s)
        return np.column_stack((time_s, voltage_v))

    def test_success_writes_versioned_metrics_manifest_and_frequency_exports(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, source_npy = self.source(root, self.waveform())
            source_before = sha256(source_npy.read_bytes()).hexdigest()
            step = self.pipeline(
                [
                    {"op": "measure", "metrics": ["voltage_mean_v"]},
                    {"op": "remove_dc"},
                    {"op": "window", "name": "hann"},
                    {"op": "fft"},
                    {
                        "op": "measure",
                        "metrics": [
                            "peak_frequency_hz",
                            "peak_amplitude_v",
                            "noise_floor_v",
                            "thd_ratio",
                        ],
                    },
                    {"op": "export", "name": "spectrum", "formats": ["npy", "csv"]},
                ],
                expect={"peak_frequency_hz": {"min": 99.0, "max": 101.0}},
            )

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            processing = run_dir / "processing" / "01_spectrum_main"
            manifest = json.loads((processing / "manifest.json").read_text(encoding="utf-8"))
            metrics = json.loads((processing / "metrics.json").read_text(encoding="utf-8"))
            exported_npy = processing / "exports" / "spectrum.npy"
            exported_csv = processing / "exports" / "spectrum.csv"
            self.assertEqual(manifest["schema"], "wavebench.analysis_pipeline.v1")
            self.assertEqual(manifest["status"], "ok")
            self.assertFalse(manifest["partial"])
            self.assertNotIn("filters", manifest)
            self.assertEqual(manifest["source"]["step"], "capture_main")
            self.assertEqual(manifest["source"]["npy_sha256"], source_before)
            self.assertEqual(manifest["window"]["name"], "hann")
            self.assertAlmostEqual(manifest["window"]["coherent_gain"], np.mean(np.hanning(1000)))
            self.assertEqual(metrics["schema"], "wavebench.analysis_metrics.v1")
            self.assertAlmostEqual(metrics["metrics"]["peak_frequency_hz"], 100.0)
            self.assertEqual(artifact["expect"]["status"], "ok")
            self.assertEqual(artifact["analysis_pipeline"]["manifest"], "processing/01_spectrum_main/manifest.json")
            self.assertEqual(np.load(exported_npy).shape, (501, 4))
            self.assertEqual(
                exported_csv.read_text(encoding="utf-8").splitlines()[0],
                "frequency_hz,real_v,imaginary_v,amplitude_v",
            )
            self.assertEqual(sha256(source_npy.read_bytes()).hexdigest(), source_before)
            self.assertFalse(list(processing.rglob("*.tmp")))
            for export in manifest["exports"]:
                export_path = run_dir / export["path"]
                self.assertEqual(export["sha256"], sha256(export_path.read_bytes()).hexdigest())

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_serial_fir_filters_write_metadata_and_time_export(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, source_npy = self.source(root, self.waveform())
            source_before = sha256(source_npy.read_bytes()).hexdigest()
            step = self.pipeline([
                {
                    "op": "filter",
                    "family": "fir",
                    "response": "bandstop",
                    "cutoff_hz": [49.0, 51.0],
                    "numtaps": 31,
                    "mode": "zero_phase",
                },
                {
                    "op": "filter",
                    "family": "fir",
                    "response": "lowpass",
                    "cutoff_hz": 1000.0,
                    "numtaps": 33,
                    "mode": "causal",
                },
                {"op": "export", "name": "filtered", "formats": ["npy", "csv"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            processing = run_dir / "processing" / "01_spectrum_main"
            manifest = json.loads((processing / "manifest.json").read_text(encoding="utf-8"))
            filters = manifest["filters"]
            self.assertEqual(artifact["analysis_pipeline"]["status"], "ok")
            self.assertEqual([item["operation_index"] for item in filters], [0, 1])
            self.assertEqual([item["response"] for item in filters], ["bandstop", "lowpass"])
            self.assertEqual(filters[0]["execution_function"], "scipy.signal.filtfilt")
            self.assertEqual(filters[0]["effective_magnitude_response"], "single_pass_squared")
            self.assertEqual(filters[0]["boundary"], "odd_extension")
            self.assertEqual(filters[0]["padlen"], 93)
            self.assertEqual(filters[1]["execution_function"], "scipy.signal.lfilter")
            self.assertEqual(filters[1]["initial_state"], "zeros")
            self.assertEqual(filters[1]["nominal_single_pass_group_delay_samples"], 16.0)
            self.assertAlmostEqual(filters[0]["sample_rate_hz"], 10_000.0)
            self.assertEqual(len(filters[0]["coefficients_sha256"]), 64)
            self.assertEqual(manifest["stages"][1]["filter"], filters[0])
            self.assertEqual(manifest["stages"][2]["filter"], filters[1])
            self.assertEqual(np.load(processing / "exports" / "filtered.npy").shape, (1000, 2))
            self.assertEqual(
                (processing / "exports" / "filtered.csv")
                .read_text(encoding="utf-8")
                .splitlines()[0],
                "time_s,voltage_v",
            )
            self.assertEqual(sha256(source_npy.read_bytes()).hexdigest(), source_before)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_mixed_fir_iir_filters_write_stable_sos_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, source_npy = self.source(root, self.waveform())
            source_before = sha256(source_npy.read_bytes()).hexdigest()
            step = self.pipeline([
                {
                    "op": "filter",
                    "family": "fir",
                    "response": "lowpass",
                    "cutoff_hz": 2000.0,
                    "numtaps": 31,
                    "mode": "causal",
                },
                {
                    "op": "filter",
                    "family": "iir",
                    "design": "elliptic",
                    "response": "bandstop",
                    "cutoff_hz": [49.0, 51.0],
                    "order": 4,
                    "ripple_db": 1.0,
                    "attenuation_db": 60.0,
                    "mode": "zero_phase",
                },
                {"op": "export", "name": "filtered", "formats": ["npy"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(
                    encoding="utf-8"
                )
            )
            filters = manifest["filters"]
            iir = filters[1]
            self.assertEqual(artifact["analysis_pipeline"]["status"], "ok")
            self.assertEqual([item["family"] for item in filters], ["fir", "iir"])
            self.assertEqual(iir["operation_index"], 1)
            self.assertEqual(iir["design"], "elliptic")
            self.assertEqual(iir["response"], "bandstop")
            self.assertEqual(iir["order"], 4)
            self.assertEqual(iir["digital_filter_order"], 8)
            self.assertEqual(iir["design_function"], "scipy.signal.ellip")
            self.assertEqual(iir["design_output"], "sos")
            self.assertEqual(iir["critical_frequency_semantics"], "single_pass_passband_ripple_edge")
            self.assertEqual(iir["sos_shape"], [iir["sections"], 6])
            self.assertEqual(len(iir["sos_sha256"]), 64)
            self.assertTrue(iir["stable"])
            self.assertLess(iir["max_pole_magnitude"], 1.0)
            self.assertEqual(iir["ripple_db"], 1.0)
            self.assertEqual(iir["attenuation_db"], 60.0)
            self.assertEqual(iir["execution_function"], "scipy.signal.sosfiltfilt")
            self.assertEqual(iir["effective_magnitude_response"], "single_pass_squared")
            self.assertEqual(iir["boundary"], "odd_extension")
            self.assertGreater(iir["padlen"], 0)
            self.assertEqual(manifest["stages"][2]["filter"], iir)
            self.assertEqual(sha256(source_npy.read_bytes()).hexdigest(), source_before)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_completed_iir_metadata_survives_later_filter_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(root, self.waveform())
            step = self.pipeline([
                {
                    "op": "filter",
                    "family": "iir",
                    "design": "butterworth",
                    "response": "lowpass",
                    "cutoff_hz": 1000.0,
                    "order": 4,
                    "mode": "causal",
                },
                {
                    "op": "filter",
                    "family": "iir",
                    "design": "butterworth",
                    "response": "lowpass",
                    "cutoff_hz": 5000.0,
                    "order": 4,
                    "mode": "causal",
                },
                {"op": "export", "name": "filtered", "formats": ["npy"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            self.assertEqual(artifact["analysis_pipeline"]["failed_stage"], "operations[1]")
            self.assertTrue(manifest["partial"])
            self.assertEqual(len(manifest["filters"]), 1)
            self.assertEqual(manifest["filters"][0]["family"], "iir")
            self.assertEqual(manifest["stages"][2]["status"], "failed")
            self.assertIn("below Nyquist", manifest["error"]["message"])

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_completed_filter_metadata_survives_later_nyquist_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(root, self.waveform())
            step = self.pipeline([
                {
                    "op": "filter",
                    "family": "fir",
                    "response": "lowpass",
                    "cutoff_hz": 1000.0,
                    "numtaps": 31,
                    "mode": "causal",
                },
                {
                    "op": "filter",
                    "family": "fir",
                    "response": "lowpass",
                    "cutoff_hz": 5000.0,
                    "numtaps": 31,
                    "mode": "causal",
                },
                {"op": "export", "name": "filtered", "formats": ["npy"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            self.assertEqual(artifact["analysis_pipeline"]["failed_stage"], "operations[1]")
            self.assertTrue(manifest["partial"])
            self.assertEqual(len(manifest["filters"]), 1)
            self.assertEqual(manifest["stages"][2]["status"], "failed")
            self.assertEqual(manifest["stages"][3]["status"], "skipped")
            self.assertIn("below Nyquist", manifest["error"]["message"])

    def test_filter_dependency_check_is_conditional_and_actionable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            no_filter_path = root / "no_filter.toml"
            no_filter_path.write_text(
                """
[[steps]]
id = "capture_main"
kind = "scope.capture"
save_npy = true

[[steps]]
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [{ op = "measure", metrics = ["voltage_mean_v"] }]
""",
                encoding="utf-8",
            )
            fir_path = root / "fir.toml"
            fir_path.write_text(
                """
[[steps]]
id = "capture_main"
kind = "scope.capture"
save_npy = true

[[steps]]
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [
  { op = "filter", family = "fir", response = "lowpass", cutoff_hz = 1000, numtaps = 31, mode = "causal" },
  { op = "export", name = "filtered", formats = ["npy"] },
]
""",
                encoding="utf-8",
            )
            iir_path = root / "iir.toml"
            iir_path.write_text(
                """
[[steps]]
id = "capture_main"
kind = "scope.capture"
save_npy = true

[[steps]]
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [
  { op = "filter", family = "iir", design = "butterworth", response = "lowpass", cutoff_hz = 1000, order = 4, mode = "zero_phase" },
  { op = "export", name = "filtered", formats = ["npy"] },
]
""",
                encoding="utf-8",
            )

            with patch("wavebench.services.run_pipeline.import_module") as load_dependency:
                ensure_analysis_pipeline_dependencies(load_run_plan(no_filter_path))
                load_dependency.assert_not_called()

            with patch(
                "wavebench.services.run_pipeline.import_module",
                side_effect=ImportError("scipy unavailable"),
            ):
                with self.assertRaisesRegex(ConfigError, r"\.\[analysis\]"):
                    ensure_analysis_pipeline_dependencies(load_run_plan(fir_path))

            available = SimpleNamespace(
                butter=lambda: None,
                sos2zpk=lambda: None,
                sosfiltfilt=lambda: None,
            )
            with patch(
                "wavebench.services.run_pipeline.import_module",
                return_value=available,
            ):
                ensure_analysis_pipeline_dependencies(load_run_plan(iir_path))

            del available.sosfiltfilt
            with patch(
                "wavebench.services.run_pipeline.import_module",
                return_value=available,
            ):
                with self.assertRaisesRegex(ConfigError, "sosfiltfilt"):
                    ensure_analysis_pipeline_dependencies(load_run_plan(iir_path))

    def test_source_expectation_failure_still_allows_complete_npy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(root, self.waveform(), status="failed")

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=self.pipeline([
                    {"op": "measure", "metrics": ["voltage_mean_v"]},
                ]),
                source_step=source_step,
                source_record=source_record,
            )

            self.assertEqual(artifact["analysis_pipeline"]["status"], "ok")
            self.assertEqual(artifact["analysis_pipeline"]["source_status"], "failed")

    def test_metadata_path_traversal_is_a_structured_pipeline_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            outside = root / "raw" / "outside.npy"
            outside.parent.mkdir(parents=True)
            np.save(outside, self.waveform())
            outside_before = sha256(outside.read_bytes()).hexdigest()
            source_step, source_record, _ = self.source(
                root,
                self.waveform(),
                npy_metadata_path="../outside.npy",
            )
            step = self.pipeline(
                [{"op": "measure", "metrics": ["voltage_mean_v"]}],
                expect={"voltage_mean_v": {"min": -1.0, "max": 1.0}},
            )

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            self.assertEqual(artifact["analysis_pipeline"]["failed_stage"], "source")
            self.assertIsNone(artifact["metrics"]["voltage_mean_v"])
            self.assertEqual(artifact["expect"]["checks"]["voltage_mean_v"]["reason"], "unavailable")
            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["stages"][0]["status"], "failed")
            self.assertIn("must not contain '..'", manifest["error"]["message"])
            self.assertEqual(sha256(outside.read_bytes()).hexdigest(), outside_before)

    def test_completed_time_export_survives_later_fft_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(
                root, self.waveform(nonuniform=True)
            )
            step = self.pipeline([
                {"op": "export", "name": "time", "formats": ["npy", "csv"]},
                {"op": "fft"},
                {"op": "measure", "metrics": ["peak_frequency_hz"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            processing = run_dir / "processing" / "01_spectrum_main"
            manifest = json.loads((processing / "manifest.json").read_text(encoding="utf-8"))
            metrics = json.loads((processing / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            self.assertTrue(manifest["partial"])
            self.assertEqual(len(manifest["exports"]), 2)
            self.assertTrue((processing / "exports" / "time.npy").is_file())
            self.assertEqual(
                (processing / "exports" / "time.csv")
                .read_text(encoding="utf-8")
                .splitlines()[0],
                "time_s,voltage_v",
            )
            self.assertEqual(np.load(processing / "exports" / "time.npy").shape, (1000, 2))
            self.assertIsNone(metrics["metrics"]["peak_frequency_hz"])
            self.assertEqual(manifest["failed_stage"], "operations[1]")
            self.assertEqual(manifest["stages"][-1]["status"], "skipped")

    def test_completed_export_is_recorded_if_a_later_format_write_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(root, self.waveform())
            step = self.pipeline([
                {"op": "export", "name": "time", "formats": ["npy", "csv"]},
            ])

            with patch(
                "wavebench.services.run_pipeline._atomic_write_csv",
                side_effect=OSError("disk full"),
            ):
                artifact = execute_analysis_pipeline(
                    run_dir=run_dir,
                    step=step,
                    source_step=source_step,
                    source_record=source_record,
                )

            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "failed")
            self.assertTrue(manifest["partial"])
            self.assertEqual([item["format"] for item in manifest["exports"]], ["npy"])

    def test_missing_source_record_still_writes_null_metrics_and_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step = RunStep(
                index=0,
                kind="scope.capture",
                fields={"save_npy": True},
                id="capture_main",
            )
            step = self.pipeline([
                {"op": "measure", "metrics": ["voltage_mean_v"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=None,
            )

            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            metrics_text = (
                run_dir / artifact["analysis_pipeline"]["metrics"]
            ).read_text(encoding="utf-8")
            self.assertIn('"voltage_mean_v": null', metrics_text)
            self.assertNotIn("NaN", metrics_text)


if __name__ == "__main__":
    unittest.main()
