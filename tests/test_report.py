import base64
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np

from wavebench.data.packages import load_run_package
from wavebench.errors import ConfigError
from wavebench.report.html import _response_svg, render_run_report_html, write_run_report_html, write_run_report_pdf


class RunReportTests(unittest.TestCase):
    def test_response_svg_uses_a_separate_two_column_legend_area(self):
        svg = _response_svg(
            [[(100.0, 1.0), (1000.0, 2.0)]],
            title="Fit comparison",
            y_label="Linear gain",
            actual_label="Measured",
            series=[
                ("Frequency piecewise linear interpolation", "#7c3aed", [(100.0, 1.0), (1000.0, 2.0)]),
                ("Degree-3 polynomial in log frequency", "#dc2626", [(100.0, 1.2), (1000.0, 1.8)]),
                ("PCHIP shape-preserving cubic interpolation", "#0891b2", [(100.0, 1.1), (1000.0, 1.9)]),
            ],
        )

        positions = re.findall(
            r'<g class="legend-item" data-label="[^"]+"><line x1="([0-9.]+)" y1="([0-9.]+)"',
            svg,
        )
        self.assertEqual(len(positions), 4)
        self.assertEqual(len(set(positions)), 4)
        self.assertEqual({position[0] for position in positions}, {"58.00", "359.00"})
        self.assertEqual(len({position[1] for position in positions}), 2)
        self.assertIn(">Measured</text>", svg)
        self.assertIn("…</text>", svg)
        self.assertNotIn("Frequency piecewise linear interpolation</text>", svg)

    def test_run_report_embeds_capture_screenshot_relative_to_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "cap1"
            capture.mkdir(parents=True)
            (capture / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            np.save(capture / "ch1.npy", np.array([[0.0, 0.0], [0.5e-3, 1.0], [1.0e-3, 0.0]]))
            (capture / "metadata.json").write_text(
                json.dumps(
                    {
                        "operation": {"command": "scope capture", "channel": 1},
                        "waveform": {
                            "summary": {
                                "channel": 1,
                                "samples": 10,
                                "frequency_estimate_hz": 10000.0,
                                "voltage_vpp_v": 0.8,
                                "voltage_rms_v": 0.28,
                                "voltage_mean_v": -0.01,
                                "duty_cycle": 0.5,
                                "rise_time_s": 3.2e-8,
                                "fall_time_s": 3.4e-8,
                                "quality_warnings": [],
                            }
                        },
                        "files": {
                            "npy": "data\\raw\\cap1\\ch1.npy",
                            "screenshot": "data\\raw\\cap1\\screenshot.png",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run_dir = root / "data" / "runs" / "run1"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 3,
                                "kind": "scope.capture",
                                "status": "ok",
                                "artifact": {
                                    "package": "data\\raw\\cap1",
                                    "metadata": "data\\raw\\cap1\\metadata.json",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = write_run_report_html(load_run_package(run_dir))

            html = output.read_text(encoding="utf-8")
            self.assertIn("<h2>摘要 / Summary</h2>", html)
            self.assertIn('<div class="label">状态 / Status</div><div class="value ok">ok</div>', html)
            self.assertIn('<div class="label">采集 / Captures</div><div class="value">1</div>', html)
            self.assertIn('<div class="label">截图 / Screenshots</div><div class="value">1</div>', html)
            self.assertIn('<div class="label">主频率 / Primary frequency</div><div class="value">10000 Hz</div>', html)
            self.assertIn('<div class="label">主峰峰值 / Primary Vpp</div><div class="value">0.8 V</div>', html)
            self.assertIn("<h2>截图 / Screenshots</h2>", html)
            self.assertIn('src="../../raw/cap1/screenshot.png"', html)
            self.assertIn('href="../../raw/cap1/screenshot.png"', html)
            self.assertIn('class="screenshot-thumb"', html)
            self.assertIn("<h2>信号分析 / Signal analysis</h2>", html)
            self.assertIn("10000 Hz", html)
            self.assertIn("0.8 V", html)
            self.assertIn("50%", html)
            self.assertIn("<h2>波形预览 / Waveform previews</h2>", html)
            self.assertIn("Step 3 ch1", html)
            self.assertIn("<polyline", html)
            manifest = json.loads((run_dir / "report-assets" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "wavebench.report_manifest.v1")
            self.assertEqual(manifest["report"], "report.html")
            self.assertEqual(manifest["run_json"], "run.json")
            self.assertEqual(manifest["capture_packages"][0]["package"], "data\\raw\\cap1")
            self.assertEqual(manifest["screenshots"][0]["path"], "../../raw/cap1/screenshot.png")
            self.assertEqual(manifest["waveform_previews"][0]["source_npy"], "../../raw/cap1/ch1.npy")
            self.assertEqual(manifest["warnings"], [])

    def test_run_report_without_screenshot_omits_screenshots_section(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "data" / "runs" / "run1"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps({"status": "ok", "steps": [{"index": 0, "kind": "sleep"}]}),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>摘要 / Summary</h2>", html)
            self.assertIn('<div class="label">步骤 / Steps</div><div class="value">1</div>', html)
            self.assertIn('<div class="label">采集 / Captures</div><div class="value">0</div>', html)
            self.assertNotIn("<h2>截图 / Screenshots</h2>", html)
            self.assertNotIn("<h2>信号分析 / Signal analysis</h2>", html)
            self.assertIn("<th>截图 / Screenshot</th>", html)

    def test_run_report_summary_counts_failed_expectations_and_warnings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "cap1"
            capture.mkdir(parents=True)
            (capture / "metadata.json").write_text(
                json.dumps(
                    {
                        "waveform": {
                            "summary": {
                                "channel": 1,
                                "samples": 20,
                                "frequency_estimate_hz": 1000.0,
                                "voltage_vpp_v": 0.01,
                                "quality_warnings": ["low_signal_amplitude"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            run_dir = root / "data" / "runs" / "run_failed"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "experiment": {"label": "bad_run"},
                        "restore": {"status": "ok"},
                        "steps": [
                            {
                                "index": 0,
                                "kind": "scope.capture",
                                "status": "failed",
                                "artifact": {
                                    "package": "data/raw/cap1",
                                    "metadata": "data/raw/cap1/metadata.json",
                                    "quality": {"warnings": ["low_signal_amplitude"]},
                                    "expect": {
                                        "status": "failed",
                                        "checks": {
                                            "voltage_vpp_v": {
                                                "status": "failed",
                                                "value": 0.01,
                                                "limits": {"min": 0.05},
                                            }
                                        },
                                        "failures": ["voltage_vpp_v: 0.01 below min 0.05"],
                                    },
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn('<div class="label">状态 / Status</div><div class="value failed">failed</div>', html)
            self.assertIn('<div class="label">失败步骤 / Failed steps</div><div class="value failed">1</div>', html)
            self.assertIn('<div class="label">警告 / Warnings</div><div class="value warning">1</div>', html)
            self.assertIn('<div class="label">预期失败 / Expect failed</div><div class="value failed">1</div>', html)
            self.assertIn('<div class="label">恢复 / Restore</div><div class="value">ok</div>', html)

    def test_run_report_lists_multi_channel_signal_analysis(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "dual"
            capture.mkdir(parents=True)
            np.save(capture / "ch1.npy", np.array([[0.0, 0.0], [1e-3, 1.0], [2e-3, 0.0]]))
            np.save(capture / "ch2.npy", np.array([[0.0, 1.65], [1e-3, 3.3], [2e-3, 1.65]]))
            (capture / "metadata.json").write_text(
                json.dumps(
                    {
                        "channels": {
                            "1": {
                                "summary": {
                                    "channel": 1,
                                    "samples": 100,
                                    "frequency_estimate_hz": 1000.0,
                                    "voltage_vpp_v": 1.2,
                                    "voltage_rms_v": 0.4,
                                    "voltage_mean_v": 0.0,
                                    "quality_warnings": ["low_cycles"],
                                }
                            },
                            "2": {
                                "summary": {
                                    "channel": 2,
                                    "samples": 100,
                                    "frequency_estimate_hz": 2000.0,
                                    "voltage_vpp_v": 3.3,
                                    "voltage_rms_v": 1.1,
                                    "voltage_mean_v": 1.65,
                                    "duty_cycle": 0.25,
                                    "quality_warnings": [],
                                }
                            },
                        },
                        "files": {"1": {"npy": "ch1.npy"}, "2": {"npy": "ch2.npy"}},
                    }
                ),
                encoding="utf-8",
            )
            run_dir = root / "data" / "runs" / "run1"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 0,
                                "kind": "scope.capture",
                                "status": "ok",
                                "artifact": {
                                    "package": "data/raw/dual",
                                    "metadata": "data/raw/dual/metadata.json",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>信号分析 / Signal analysis</h2>", html)
            self.assertIn("1000 Hz", html)
            self.assertIn("2000 Hz", html)
            self.assertIn("25%", html)
            self.assertIn("low_cycles", html)
            self.assertIn("Step 0 ch1", html)
            self.assertIn("Step 0 ch2", html)

    def test_run_report_keeps_running_when_waveform_preview_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "bad_waveform"
            capture.mkdir(parents=True)
            (capture / "metadata.json").write_text(
                json.dumps(
                    {
                        "waveform": {"summary": {"channel": 1, "frequency_estimate_hz": 1000.0}},
                        "files": {"npy": "data/raw/bad_waveform/missing.npy"},
                    }
                ),
                encoding="utf-8",
            )
            run_dir = root / "data" / "runs" / "run_bad_waveform"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 0,
                                "kind": "scope.capture",
                                "status": "ok",
                                "artifact": {
                                    "package": "data/raw/bad_waveform",
                                    "metadata": "data/raw/bad_waveform/metadata.json",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = write_run_report_html(load_run_package(run_dir))
            html = output.read_text(encoding="utf-8")

            self.assertIn("<h2>波形预览 / Waveform previews</h2>", html)
            self.assertIn("waveform preview unavailable", html)
            self.assertIn("FileNotFoundError", html)
            manifest = json.loads((run_dir / "report-assets" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["waveform_previews"][0]["exists"], False)
            self.assertIn("waveform npy missing", manifest["warnings"][0])

    def test_run_report_renders_expected_vs_measured_table(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "data" / "runs" / "run_expect"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "steps": [
                            {
                                "index": 7,
                                "kind": "scope.capture",
                                "status": "failed",
                                "artifact": {
                                    "expect": {
                                        "status": "failed",
                                        "checks": {
                                            "frequency_estimate_hz": {
                                                "status": "ok",
                                                "value": 10000.0,
                                                "limits": {"min": 9500.0, "max": 10500.0},
                                            },
                                            "voltage_vpp_v": {
                                                "status": "failed",
                                                "value": 0.01,
                                                "limits": {"min": 0.05},
                                                "reasons": ["below min 0.05"],
                                            },
                                            "duty_cycle": {
                                                "status": "failed",
                                                "reason": "unavailable",
                                                "limits": {"min": 0.49, "max": 0.51},
                                            },
                                            "frequency_error_ratio": {
                                                "status": "failed",
                                                "value": "nan-ish",
                                                "reason": "not_numeric",
                                                "limits": {"max": 0.02},
                                            },
                                        },
                                        "failures": [
                                            "voltage_vpp_v: 0.01 below min 0.05",
                                            "duty_cycle: unavailable",
                                            "frequency_error_ratio: not numeric",
                                        ],
                                    },
                                    "expect_fft": {
                                        "status": "ok",
                                        "checks": {
                                            "peak_frequency_hz": {
                                                "status": "ok",
                                                "value": 1000.0,
                                                "limits": {"min": 990.0, "max": 1010.0},
                                            },
                                            "harmonic_2_amplitude_v": {
                                                "status": "ok",
                                                "value": 0.1,
                                                "limits": {"max": 0.2},
                                            },
                                        },
                                        "failures": [],
                                    },
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>验收摘要 / Acceptance summary</h2>", html)
            self.assertIn("频率 / Frequency", html)
            self.assertIn("峰峰值 / Vpp", html)
            self.assertIn("占空比 / Duty", html)
            self.assertIn("频率误差 / Frequency error", html)
            self.assertIn("<h2>预期 vs 实测 / Expected vs measured</h2>", html)
            self.assertIn("<td>frequency_estimate_hz</td>", html)
            self.assertIn("<td>9500..10500</td>", html)
            self.assertIn("<td>10000</td>", html)
            self.assertIn("<td>&gt;= 0.05</td>", html)
            self.assertIn("<td>below min 0.05</td>", html)
            self.assertIn("<td>unavailable</td>", html)
            self.assertIn("<td>not_numeric</td>", html)
            self.assertIn('<tr class="failed"><td>7</td><td>scope.capture</td><td>voltage_vpp_v</td>', html)
            self.assertIn("FFT 主频 / FFT peak", html)
            self.assertIn("FFT H2 幅度 / FFT H2 amplitude", html)
            self.assertIn("<td>fft.peak_frequency_hz</td>", html)
            self.assertIn("<td>fft.harmonic_2_amplitude_v</td>", html)

    def test_run_report_renders_sweep_summary_table(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "data" / "runs" / "run_sweep"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 6,
                                "kind": "scope.capture",
                                "status": "ok",
                                "fields": {"label": "sweep_100hz"},
                                "artifact": {
                                    "quality": {
                                        "status": "ok",
                                        "frequency_estimate_hz": 100.0,
                                        "voltage_vpp_v": 1.008,
                                    },
                                    "expect": {"status": "ok"},
                                    "expect_fft": {"status": "ok"},
                                    "fft": {
                                        "peak_frequency_hz": 100.0,
                                        "peak_amplitude_v": 0.494,
                                        "thd_ratio": 0.003,
                                    },
                                },
                            },
                            {
                                "index": 9,
                                "kind": "scope.capture",
                                "status": "ok",
                                "fields": {"label": "sweep_1k"},
                                "artifact": {
                                    "quality": {
                                        "status": "ok",
                                        "frequency_estimate_hz": 1000.0,
                                        "voltage_vpp_v": 1.0,
                                    },
                                    "expect": {"status": "ok"},
                                    "expect_fft": {"status": "ok"},
                                    "fft": {
                                        "peak_frequency_hz": 1000.0,
                                        "peak_amplitude_v": 0.5,
                                        "thd_ratio": 0.004,
                                    },
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>扫频摘要 / Sweep summary</h2>", html)
            self.assertIn("<th>FFT 主频 / FFT peak</th>", html)
            self.assertIn('<tr class="ok"><td>6</td><td>sweep_100hz</td><td class="ok">ok</td>', html)
            self.assertIn("<td>100 Hz</td>", html)
            self.assertIn("<td>1.008 V</td>", html)
            self.assertIn("<td>0.3%</td>", html)
            self.assertIn('<tr class="ok"><td>9</td><td>sweep_1k</td><td class="ok">ok</td>', html)
            self.assertIn("<td>1000 Hz</td>", html)

    def test_run_report_renders_dmm_reading_cards(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "data" / "runs" / "run_dmm"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 6,
                                "kind": "dmm.read",
                                "status": "ok",
                                "fields": {"function": "acv"},
                                "artifact": {
                                    "dmm_reading": {
                                        "function": "acv",
                                        "value": 0.3535,
                                        "unit": "V",
                                        "raw": "3.535000E-01",
                                    },
                                    "expect": {
                                        "status": "ok",
                                        "checks": {
                                            "value": {
                                                "status": "ok",
                                                "value": 0.3535,
                                                "limits": {"min": 0.34, "max": 0.37},
                                            }
                                        },
                                        "failures": [],
                                    },
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>DMM 读数 / DMM readings</h2>", html)
            self.assertIn('<article class="card dmm-card">', html)
            self.assertIn("<header><h3>dmm.read</h3><span class=\"badge ok\">ok</span></header>", html)
            self.assertIn('<p class="reading">0.3535<span class="unit">V</span></p>', html)
            self.assertIn("<div><dt>功能 / Function</dt><dd>acv</dd></div>", html)
            self.assertIn("<div><dt>步骤 / Step</dt><dd>6 · dmm.read</dd></div>", html)
            self.assertIn("<div><dt>预期 / Expected</dt><dd>0.34..0.37 V</dd></div>", html)
            self.assertIn('<tr class="ok"><td>6</td><td>dmm.read</td><td>value</td>', html)

    def test_run_report_renders_evidence_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "evidence"
            capture.mkdir(parents=True)
            np.save(capture / "ch1.npy", np.array([[0.0, 0.0], [1e-3, 1.0], [2e-3, 0.0]]))
            (capture / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (capture / "metadata.json").write_text(
                json.dumps(
                    {
                        "operation": {"command": "scope capture", "channel": 1},
                        "waveform": {
                            "summary": {
                                "channel": 1,
                                "samples": 3,
                                "frequency_estimate_hz": 1000.0,
                                "voltage_vpp_v": 1.0,
                                "quality_warnings": [],
                            }
                        },
                        "files": {
                            "npy": "data/raw/evidence/ch1.npy",
                            "screenshot": "data/raw/evidence/screenshot.png",
                        },
                    }
                ),
                encoding="utf-8",
            )
            run_dir = root / "data" / "runs" / "run_evidence"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.csv").write_text(
                "index,kind,status\n0,source.set_freq,ok\n1,scope.capture,failed\n2,dmm.read,ok\n3,sleep,ok\n",
                encoding="utf-8",
            )
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "steps": [
                            {
                                "index": 0,
                                "kind": "source.set_freq",
                                "status": "ok",
                                "fields": {"channel": 1, "frequency_hz": 1000.0},
                                "artifact": {"source_status": {"channel": 1, "frequency_hz": 1000.0}},
                            },
                            {
                                "index": 1,
                                "kind": "scope.capture",
                                "status": "failed",
                                "artifact": {
                                    "package": "data/raw/evidence",
                                    "metadata": "data/raw/evidence/metadata.json",
                                    "expect": {
                                        "status": "failed",
                                        "checks": {
                                            "voltage_vpp_v": {
                                                "status": "failed",
                                                "value": 1.0,
                                                "limits": {"min": 2.0},
                                            }
                                        },
                                        "failures": ["voltage_vpp_v below min"],
                                    },
                                },
                            },
                            {
                                "index": 2,
                                "kind": "dmm.read",
                                "status": "ok",
                                "artifact": {
                                    "dmm_reading": {"function": "dcv", "value": 3.3, "unit": "V"},
                                    "expect": {
                                        "status": "ok",
                                        "checks": {
                                            "value": {
                                                "status": "ok",
                                                "value": 3.3,
                                                "limits": {"min": 3.2, "max": 3.4},
                                            }
                                        },
                                    },
                                },
                            },
                            {
                                "index": 3,
                                "kind": "sleep",
                                "status": "ok",
                                "fields": {"duration_s": 0.25},
                                "artifact": {"duration_s": 0.25},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>实验证据摘要 / Run evidence summary</h2>", html)
            self.assertIn('<section class="evidence-grid">', html)
            self.assertIn('<div class="label">信号源设置步骤 / Source setting steps</div>', html)
            self.assertIn('<div class="label">示波器采集步骤 / Scope capture steps</div>', html)
            self.assertIn('<div class="label">DMM 读数 / DMM readings</div>', html)
            self.assertIn('<div class="label">失败预期项 / Failed expectations</div>', html)
            self.assertIn('<div class="value failed">1</div>', html)
            self.assertIn('<div class="label">run.json</div>', html)
            self.assertIn('<div class="value ok">存在 / present</div>', html)
            self.assertIn('<div class="label">summary.csv</div>', html)
            self.assertIn('<div class="label">采集包 / Capture packages</div>', html)
            self.assertIn('<div class="label">截图 / Screenshots</div>', html)
            self.assertIn('<div class="label">波形预览 / Waveform previews</div>', html)
            self.assertIn("<h2>证据时间线 / Evidence timeline</h2>", html)
            self.assertIn("<th>证据 / Evidence</th>", html)
            self.assertIn("<td>0</td><td>source.set_freq</td><td><span class=\"badge ok\">ok</span></td>", html)
            self.assertIn('<span class="evidence-token">信号源 / Source</span>', html)
            self.assertIn('<span class="evidence-token">通道 / Channel: 1</span>', html)
            self.assertIn('<span class="evidence-token">频率 / Frequency: 1000 Hz</span>', html)
            self.assertIn("<td>1</td><td>scope.capture</td><td><span class=\"badge failed\">failed</span></td>", html)
            self.assertIn('<span class="evidence-token">示波器 / Scope</span>', html)
            self.assertIn('<span class="evidence-token">采集包 / Package: data/raw/evidence</span>', html)
            self.assertIn('<span class="evidence-token">截图 / Screenshot: 存在 / present</span>', html)
            self.assertIn('<span class="evidence-token">预期 / Expect: failed</span>', html)
            self.assertIn("<td>2</td><td>dmm.read</td><td><span class=\"badge ok\">ok</span></td>", html)
            self.assertIn('<span class="evidence-token">DMM</span>', html)
            self.assertIn('<span class="evidence-token">功能 / Function: dcv</span>', html)
            self.assertIn('<span class="evidence-token">读数 / Reading: 3.3 V</span>', html)
            self.assertIn("<td>3</td><td>sleep</td><td><span class=\"badge ok\">ok</span></td>", html)
            self.assertIn('<span class="evidence-token">等待 / Sleep</span>', html)
            self.assertIn('<span class="evidence-token">时长 / Duration: 0.25 s</span>', html)
            self.assertIn("<h2>产物链接 / Artifact links</h2>", html)
            self.assertIn(
                '<td>运行记录 / Run JSON</td><td><code>run.json</code></td>'
                '<td><a class="artifact-link" href="run.json">run.json</a></td>',
                html,
            )
            self.assertIn(
                '<td>摘要 CSV / Summary CSV</td><td><code>summary.csv</code></td>'
                '<td><a class="artifact-link" href="summary.csv">summary.csv</a></td>',
                html,
            )
            self.assertIn(
                '<td>采集包 / Capture package</td><td><code>data/raw/evidence</code></td>'
                '<td><a class="artifact-link" href="../../raw/evidence">../../raw/evidence</a></td>',
                html,
            )
            self.assertIn(
                '<td>截图 / Screenshot</td><td><code>screenshot.png</code></td>'
                '<td><a class="artifact-link" href="../../raw/evidence/screenshot.png">'
                "../../raw/evidence/screenshot.png</a></td>",
                html,
            )
            self.assertIn(
                '<td>波形原始数据 / Waveform raw artifact</td><td><code>ch1 ch1.npy</code></td>'
                '<td><a class="artifact-link" href="../../raw/evidence/ch1.npy">'
                "../../raw/evidence/ch1.npy</a></td>",
                html,
            )

    def test_run_report_renders_evidence_timeline(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "timeline"
            capture.mkdir(parents=True)
            (capture / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (capture / "metadata.json").write_text(
                json.dumps({"files": {"screenshot": "data/raw/timeline/screenshot.png"}}),
                encoding="utf-8",
            )
            run_dir = root / "data" / "runs" / "run_timeline"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 0,
                                "kind": "source.set_freq",
                                "status": "ok",
                                "fields": {"frequency_hz": 1000.0},
                                "artifact": {
                                    "source_status": {
                                        "channel": 1,
                                        "output": "ON",
                                        "function": "SIN",
                                        "frequency_hz": 1000.0,
                                        "amplitude": 1.2,
                                        "amplitude_unit": "VPP",
                                    }
                                },
                            },
                            {
                                "index": 1,
                                "kind": "scope.capture",
                                "status": "failed",
                                "artifact": {
                                    "package": "data/raw/timeline",
                                    "metadata": "data/raw/timeline/metadata.json",
                                    "quality": {"status": "ok", "warnings": []},
                                    "expect": {"status": "failed"},
                                },
                            },
                            {
                                "index": 2,
                                "kind": "dmm.read",
                                "status": "ok",
                                "artifact": {
                                    "dmm_reading": {"function": "dcv", "value": 3.3, "unit": "V"},
                                    "expect": {"status": "ok"},
                                },
                            },
                            {
                                "index": 3,
                                "kind": "sleep",
                                "status": "ok",
                                "artifact": {"duration_s": 0.5},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertIn("<h2>证据时间线 / Evidence timeline</h2>", html)
            self.assertIn("<th>步骤 / Step</th><th>类型 / Kind</th><th>状态 / Status</th><th>证据 / Evidence</th>", html)
            self.assertIn('<td>0</td><td>source.set_freq</td><td><span class="badge ok">ok</span></td>', html)
            self.assertIn('<span class="evidence-token">信号源 / Source</span>', html)
            self.assertIn('<span class="evidence-token">通道 / Channel: 1</span>', html)
            self.assertIn('<span class="evidence-token">频率 / Frequency: 1000 Hz</span>', html)
            self.assertIn('<span class="evidence-token">幅度 / Amplitude: 1.2 VPP</span>', html)
            self.assertIn('<td>1</td><td>scope.capture</td><td><span class="badge failed">failed</span></td>', html)
            self.assertIn('<span class="evidence-token">采集包 / Package: data/raw/timeline</span>', html)
            self.assertIn('<span class="evidence-token">截图 / Screenshot: 存在 / present</span>', html)
            self.assertIn('<span class="evidence-token">预期 / Expect: failed</span>', html)
            self.assertIn('<td>2</td><td>dmm.read</td><td><span class="badge ok">ok</span></td>', html)
            self.assertIn('<span class="evidence-token">DMM</span>', html)
            self.assertIn('<span class="evidence-token">功能 / Function: dcv</span>', html)
            self.assertIn('<span class="evidence-token">读数 / Reading: 3.3 V</span>', html)
            self.assertIn('<span class="evidence-token">预期 / Expect: ok</span>', html)
            self.assertIn('<td>3</td><td>sleep</td><td><span class="badge ok">ok</span></td>', html)
            self.assertIn('<span class="evidence-token">等待 / Sleep</span>', html)
            self.assertIn('<span class="evidence-token">时长 / Duration: 0.5 s</span>', html)

    def test_run_report_omits_expected_vs_measured_without_expect_checks(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "data" / "runs" / "run_no_expect"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps({"status": "ok", "steps": [{"index": 0, "kind": "sleep", "status": "ok"}]}),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), output_dir=run_dir)

            self.assertNotIn("<h2>验收摘要 / Acceptance summary</h2>", html)
            self.assertNotIn("<h2>预期 vs 实测 / Expected vs measured</h2>", html)

    def test_run_report_discovers_frequency_response_capture_screenshots_from_csv(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "data" / "runs" / "response"
            run_dir.mkdir(parents=True)
            captures = []
            for index in range(2):
                capture = root / "data" / "raw" / f"response_{index}"
                capture.mkdir(parents=True)
                (capture / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
                np.save(capture / "ch1.npy", np.array([[0.0, 0.0], [1e-3, 1.0]]))
                np.save(capture / "ch2.npy", np.array([[0.0, 0.0], [1e-3, 2.0]]))
                metadata = capture / "metadata.json"
                metadata.write_text(
                    json.dumps(
                        {
                            "channels": {"1": {"summary": {}}, "2": {"summary": {}}},
                            "files": {
                                "1": {"npy": str(capture / "ch1.npy")},
                                "2": {"npy": str(capture / "ch2.npy")},
                                "screenshot": str(capture / "screenshot.png"),
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                captures.append((capture, metadata))
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [{"index": 0, "kind": "sweep.frequency_response", "status": "ok"}],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "frequency_response.csv").write_text(
                "index,requested_frequency_hz,gain_linear,gain_db,phase_unwrapped_deg,status,capture_package,metadata_path\n"
                f"0,100,2,6.0206,-45,ok,{captures[0][0]},{captures[0][1]}\n"
                f"1,1000,2,6.0206,-45,ok,{captures[1][0]},{captures[1][1]}\n",
                encoding="utf-8",
            )

            output = write_run_report_html(load_run_package(run_dir))
            html = output.read_text(encoding="utf-8")
            manifest = json.loads((run_dir / "report-assets" / "manifest.json").read_text(encoding="utf-8"))

            self.assertIn("<h2>频率响应 / Frequency response</h2>", html)
            self.assertEqual(html.count('<figure class="card screenshot-card">'), 2)
            self.assertIn("frequency response 0", html)
            self.assertEqual(len(manifest["capture_packages"]), 2)
            self.assertEqual(len(manifest["screenshots"]), 2)

    def test_pdf_report_uses_output_directory_as_resource_base(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            html_factory = Mock()
            renderer = html_factory.return_value
            module = types.ModuleType("weasyprint")
            module.HTML = html_factory
            pdf_path = Path(tmp) / "export" / "report.pdf"

            with patch.dict(sys.modules, {"weasyprint": module}):
                result = write_run_report_pdf(load_run_package(run_dir), output_path=pdf_path)

            self.assertEqual(result, pdf_path)
            self.assertEqual(html_factory.call_args.kwargs["base_url"], pdf_path.parent.resolve().as_uri() + "/")
            compact_html = html_factory.call_args.kwargs["string"]
            self.assertIn('<body class="pdf-compact">', compact_html)
            self.assertIn('<section class="summary-grid pdf-summary-grid">', compact_html)
            self.assertIn("width: 24%;", compact_html)
            self.assertIn("page-break-inside: avoid;", compact_html)
            self.assertNotIn("<h2>实验证据摘要 / Run evidence summary</h2>", compact_html)
            renderer.write_pdf.assert_called_once_with(str(pdf_path))

    def test_compact_pdf_html_keeps_summary_and_omits_nonessential_evidence(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(
                json.dumps({"status": "ok", "steps": [{"index": 0, "kind": "sleep", "status": "ok"}]}),
                encoding="utf-8",
            )

            html = render_run_report_html(load_run_package(run_dir), compact=True)

            self.assertIn('<body class="pdf-compact">', html)
            self.assertIn('<section class="summary-grid pdf-summary-grid">', html)
            self.assertIn("PDF 精简为结果摘要、Bode 曲线与拟合", html)
            self.assertIn("width: 24%;", html)
            self.assertIn("page-break-inside: avoid;", html)
            self.assertNotIn("<h2>实验证据摘要 / Run evidence summary</h2>", html)
            self.assertNotIn("<h2>证据时间线 / Evidence timeline</h2>", html)

    def test_pdf_report_turns_renderer_failures_into_config_errors(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            html_factory = Mock()
            html_factory.return_value.write_pdf.side_effect = RuntimeError("renderer unavailable")
            module = types.ModuleType("weasyprint")
            module.HTML = html_factory

            with patch.dict(sys.modules, {"weasyprint": module}):
                with self.assertRaisesRegex(ConfigError, "PDF report export failed"):
                    write_run_report_pdf(load_run_package(run_dir))

    def test_pdf_report_explains_missing_optional_dependency(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")

            with patch.dict(sys.modules, {"weasyprint": None}):
                with self.assertRaisesRegex(ConfigError, "optional PDF dependency"):
                    write_run_report_pdf(load_run_package(run_dir))

    @unittest.skipUnless(importlib.util.find_spec("weasyprint"), "PDF extra is not installed")
    def test_pdf_report_smoke_embeds_a_local_screenshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "data" / "runs" / "run"
            capture = root / "data" / "raw" / "capture"
            run_dir.mkdir(parents=True)
            capture.mkdir(parents=True)
            (capture / "screenshot.png").write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
                    "y4p3VQAAAABJRU5ErkJggg=="
                )
            )
            (capture / "metadata.json").write_text(
                json.dumps({"files": {"screenshot": str(capture / "screenshot.png")}}),
                encoding="utf-8",
            )
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "steps": [
                            {
                                "index": 0,
                                "kind": "scope.capture",
                                "status": "ok",
                                "artifact": {
                                    "package": str(capture),
                                    "metadata": str(capture / "metadata.json"),
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            pdf = write_run_report_pdf(load_run_package(run_dir))

            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            self.assertGreater(pdf.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
