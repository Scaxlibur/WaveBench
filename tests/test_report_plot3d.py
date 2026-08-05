import json
from math import log10
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wavebench.data.packages import load_run_package
from wavebench.report.html import render_run_report_html, write_run_report_html
from wavebench.report.plot3d import (
    PLOTLY_ASSET_RELATIVE,
    build_surface_payload,
    plotly_initializer,
    render_surface_card,
    write_plotly_asset,
)


def _surface_rows(*, corrected: bool = True) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for amplitude in (0.5, 1.0):
        for frequency in (10.0, 100.0):
            row = {
                "requested_vpp": str(amplitude),
                "requested_frequency_hz": str(frequency),
                "gain_db": str(-frequency / 100.0 - amplitude),
                "status": "ok",
                "warnings": "",
                "error": "",
                "quality_retry_count": "0",
                "initial_warnings": "",
                "initial_capture_package": "",
                "capture_package": f"data/raw/{amplitude}_{frequency}",
            }
            if corrected:
                # Supplying only linear corrected gain exercises dB/V/V conversion.
                row["gain_linear_corrected"] = str(0.9 - frequency / 1000.0)
            rows.append(row)
    rows[0].update(
        {
            "quality_retry_count": "1",
            "initial_warnings": "reference frequency_mismatch",
            "initial_capture_package": "data/raw/first_warning",
        }
    )
    rows[1].update({"status": "warning", "warnings": "response low_signal_amplitude"})
    rows[-1].update({"status": "failed", "error": "retry still warning"})
    return rows


def _write_legacy_2d_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"status": "ok", "steps": []}), encoding="utf-8"
    )
    header = (
        "requested_vpp,requested_frequency_hz,gain_linear,gain_db,"
        "gain_linear_corrected,gain_db_corrected,status,warnings,error,"
        "quality_retry_count,initial_warnings,initial_capture_package,capture_package"
    )
    rows = [header]
    for amplitude in (0.5, 1.0):
        for frequency in (10, 100):
            rows.append(
                f"{amplitude},{frequency},0.8,-1.938,0.9,-0.915,ok,,,0,,,"
                f"data/raw/{amplitude}_{frequency}"
            )
    (run_dir / "frequency_response.csv").write_text("\n".join(rows), encoding="utf-8")


class FrequencyResponsePlot3DTests(unittest.TestCase):
    def test_surface_payload_supports_four_modes_and_preserves_quality_audit(self):
        payload = build_surface_payload(
            _surface_rows(), plot_id="plot-a", response_label="output"
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(
            set(payload["modes"]),
            {"raw_db", "raw_linear", "corrected_db", "corrected_linear"},
        )
        self.assertEqual(payload["default_mode"], "corrected_db")
        self.assertEqual(payload["x_log10"], [1.0, 2.0])
        self.assertEqual(payload["x_tick_labels"], ["10 Hz", "100 Hz"])
        self.assertAlmostEqual(payload["modes"]["raw_linear"][0][0], 10 ** (-0.6 / 20))
        self.assertAlmostEqual(
            payload["modes"]["corrected_db"][0][0], 20 * log10(0.89)
        )
        self.assertIsNone(payload["modes"]["raw_db"][1][1])
        self.assertIsNone(payload["modes"]["corrected_linear"][1][1])
        recovered = next(
            point for point in payload["points"] if point["quality_retry_count"] == 1
        )
        self.assertEqual(recovered["initial_warnings"], "reference frequency_mismatch")
        self.assertEqual(recovered["initial_capture_package"], "data/raw/first_warning")
        warning = next(point for point in payload["points"] if point["status"] == "warning")
        self.assertEqual(warning["warnings"], "response low_signal_amplitude")

    def test_surface_payload_falls_back_to_raw_and_does_not_invent_a_surface(self):
        payload = build_surface_payload(
            _surface_rows(corrected=False), plot_id="plot-a", response_label="output"
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(set(payload["modes"]), {"raw_db", "raw_linear"})
        self.assertEqual(payload["default_mode"], "raw_db")
        for rows in (
            _surface_rows()[:2],
            [row for row in _surface_rows() if row["requested_frequency_hz"] == "10.0"],
        ):
            with self.subTest(rows=len(rows)):
                self.assertIsNone(
                    build_surface_payload(rows, plot_id="too-small", response_label="small")
                )

    def test_surface_card_has_switches_reset_and_missing_dependency_fallback(self):
        payload = build_surface_payload(
            _surface_rows(), plot_id="plot-a", response_label="output"
        )

        interactive = render_surface_card(payload, plotly_url="report-assets/plotly.min.js")
        fallback = render_surface_card(payload, plotly_url=None)

        self.assertIn('data-role="basis"', interactive)
        self.assertIn('data-role="unit"', interactive)
        self.assertIn('data-role="reset"', interactive)
        self.assertIn('class="response-3d-data"', interactive)
        self.assertIn("first_warning", interactive)
        self.assertIn("WaveBench[report3d]", fallback)
        initializer = plotly_initializer("report-assets/plotly.min.js")
        self.assertIn('connectgaps: false', initializer)
        self.assertIn('name: "Recovered"', initializer)
        self.assertIn('name: "Warning"', initializer)
        self.assertIn("Plotly.relayout", initializer)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_plotly_initializer_is_valid_javascript(self):
        initializer = plotly_initializer("report-assets/plotly.min.js")
        javascript = initializer.removeprefix("<script>").removesuffix("</script>")

        result = subprocess.run(
            ["node", "--check"],
            input=javascript,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plotly_asset_writer_is_lazy_and_writes_the_bundled_runtime(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            with patch.dict(sys.modules, {"plotly": None, "plotly.offline": None}):
                self.assertIsNone(write_plotly_asset(output_dir))

            package = types.ModuleType("plotly")
            package.__path__ = []
            offline = types.ModuleType("plotly.offline")
            offline.get_plotlyjs = lambda: "window.Plotly = {version: 'test'};"
            with patch.dict(sys.modules, {"plotly": package, "plotly.offline": offline}):
                asset = write_plotly_asset(output_dir)

            self.assertEqual(asset, output_dir / PLOTLY_ASSET_RELATIVE)
            assert asset is not None
            self.assertEqual(asset.read_text(encoding="utf-8"), "window.Plotly = {version: 'test'};")

    def test_html_export_writes_shared_asset_manifest_and_relative_link(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "data" / "runs" / "run"
            _write_legacy_2d_run(run_dir)
            output = root / "exports" / "nested" / "report.html"

            def fake_asset_writer(output_dir: Path) -> Path:
                asset = output_dir / PLOTLY_ASSET_RELATIVE
                asset.parent.mkdir(parents=True, exist_ok=True)
                asset.write_text("/* local Plotly */", encoding="utf-8")
                return asset

            with patch(
                "wavebench.report.html.write_plotly_asset", side_effect=fake_asset_writer
            ):
                result = write_run_report_html(load_run_package(run_dir), output_path=output)

            self.assertEqual(result, output)
            html = output.read_text(encoding="utf-8")
            self.assertIn('<script defer src="report-assets/plotly.min.js"></script>', html)
            self.assertIn('id="wavebench-frequency-response-3d-0"', html)
            manifest = json.loads(
                (output.parent / "report-assets" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["interactive_assets"],
                [{"kind": "plotly.js", "path": "report-assets/plotly.min.js", "exists": True}],
            )

    def test_html_export_without_plotly_keeps_static_report_and_enable_hint(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            _write_legacy_2d_run(run_dir)

            with patch("wavebench.report.html.write_plotly_asset", return_value=None):
                output = write_run_report_html(load_run_package(run_dir))

            html = output.read_text(encoding="utf-8")
            self.assertIn("WaveBench[report3d]", html)
            self.assertIn("原始幅频 / Raw magnitude", html)
            self.assertNotIn("plotly.min.js", html)
            manifest = json.loads(
                (run_dir / "report-assets" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["interactive_assets"], [])

    def test_multi_response_uses_unique_dom_ids_and_pdf_compact_omits_plotly(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(
                json.dumps({"status": "ok", "steps": []}), encoding="utf-8"
            )
            entries = []
            for index, label in enumerate(("input", "output")):
                directory = run_dir / "frequency_response" / label
                _write_legacy_2d_run(directory)
                entries.append(
                    {
                        "step_index": index,
                        "label": label,
                        "directory": f"frequency_response/{label}",
                    }
                )
            (run_dir / "frequency_responses.json").write_text(
                json.dumps({"schema_version": 1, "responses": entries}), encoding="utf-8"
            )
            run = load_run_package(run_dir)

            html = render_run_report_html(
                run, output_dir=run_dir, plotly_url="report-assets/plotly.min.js"
            )
            compact = render_run_report_html(
                run,
                output_dir=run_dir,
                compact=True,
                plotly_url="report-assets/plotly.min.js",
            )

            self.assertEqual(html.count('class="response-3d-data"'), 2)
            self.assertIn('id="wavebench-frequency-response-3d-0"', html)
            self.assertIn('id="wavebench-frequency-response-3d-1"', html)
            self.assertNotIn("response-3d-data", compact)
            self.assertNotIn("plotly.min.js", compact)
            self.assertNotIn("window.addEventListener", compact)


if __name__ == "__main__":
    unittest.main()
