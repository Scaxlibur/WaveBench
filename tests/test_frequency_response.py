from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

from wavebench.instruments.models import WaveformData, WaveformHeader
from wavebench.services.frequency_response import (
    FrequencyResponsePoint,
    analyze_frequency_response_point,
    build_fit_document,
    unwrap_frequency_response_phase,
    write_fit_document,
    write_frequency_response_csv,
)


HAS_SCIPY = importlib.util.find_spec("scipy") is not None


def _waveform(
    *,
    channel: int,
    start_s: float,
    samples: int,
    sample_rate_hz: float,
    frequency_hz: float,
    amplitude_peak_v: float,
    phase_deg: float,
    offset_v: float,
) -> WaveformData:
    times = start_s + np.arange(samples, dtype=float) / sample_rate_hz
    voltages = offset_v + amplitude_peak_v * np.sin(
        2.0 * np.pi * frequency_hz * times + np.radians(phase_deg)
    )
    return WaveformData(
        channel=channel,
        header=WaveformHeader(
            x_start=float(times[0]),
            x_stop=float(times[-1]),
            points=samples,
            segment=1,
        ),
        voltages_v=voltages,
    )


def _point(index: int, frequency_hz: float, gain: float, phase: float = 0.0) -> FrequencyResponsePoint:
    return FrequencyResponsePoint(
        index=index,
        requested_frequency_hz=frequency_hz,
        reference_frequency_hz=frequency_hz,
        response_frequency_hz=frequency_hz,
        reference_amplitude_peak_v=1.0,
        response_amplitude_peak_v=gain,
        reference_vpp_v=2.0,
        response_vpp_v=2.0 * gain,
        gain_linear=gain,
        gain_db=20.0 * np.log10(gain),
        phase_wrapped_deg=phase,
        phase_unwrapped_deg=None,
        status="ok",
    )


class FrequencyResponseTests(unittest.TestCase):
    def test_measures_gain_and_phase_from_different_time_origins(self):
        frequency_hz = 1_000.0
        reference = _waveform(
            channel=1,
            start_s=0.017,
            samples=4096,
            sample_rate_hz=100_000.0,
            frequency_hz=frequency_hz,
            amplitude_peak_v=1.5,
            phase_deg=25.0,
            offset_v=0.2,
        )
        response = _waveform(
            channel=2,
            start_s=0.0173,
            samples=3500,
            sample_rate_hz=100_000.0,
            frequency_hz=frequency_hz,
            amplitude_peak_v=3.0,
            phase_deg=-65.0,
            offset_v=-0.4,
        )

        point = analyze_frequency_response_point(
            index=0,
            requested_frequency_hz=frequency_hz,
            reference_waveform=reference,
            response_waveform=response,
            frequency_tolerance_ratio=0.05,
            capture_package="capture",
            metadata_path="metadata.json",
        )

        self.assertNotEqual(point.status, "failed")
        self.assertAlmostEqual(point.reference_amplitude_peak_v or 0.0, 1.5, places=8)
        self.assertAlmostEqual(point.response_amplitude_peak_v or 0.0, 3.0, places=8)
        self.assertAlmostEqual(point.gain_linear or 0.0, 2.0, places=8)
        self.assertAlmostEqual(point.gain_db or 0.0, 6.020599913, places=7)
        self.assertAlmostEqual(point.phase_wrapped_deg or 0.0, -90.0, places=7)

    def test_zero_response_is_a_failed_point(self):
        frequency_hz = 1_000.0
        reference = _waveform(
            channel=1,
            start_s=0.0,
            samples=200,
            sample_rate_hz=50_000.0,
            frequency_hz=frequency_hz,
            amplitude_peak_v=1.0,
            phase_deg=0.0,
            offset_v=0.0,
        )
        response = _waveform(
            channel=2,
            start_s=0.0,
            samples=200,
            sample_rate_hz=50_000.0,
            frequency_hz=frequency_hz,
            amplitude_peak_v=0.0,
            phase_deg=0.0,
            offset_v=0.0,
        )

        point = analyze_frequency_response_point(
            index=0,
            requested_frequency_hz=frequency_hz,
            reference_waveform=reference,
            response_waveform=response,
            frequency_tolerance_ratio=0.05,
            capture_package="capture",
            metadata_path="metadata.json",
        )

        self.assertEqual(point.status, "failed")
        self.assertIn("too small", point.error)

    def test_phase_unwrap_does_not_bridge_failed_points(self):
        points = [
            _point(0, 10.0, 1.0, 170.0),
            _point(1, 100.0, 1.0, -170.0),
            FrequencyResponsePoint(
                index=2,
                requested_frequency_hz=1_000.0,
                reference_frequency_hz=None,
                response_frequency_hz=None,
                reference_amplitude_peak_v=None,
                response_amplitude_peak_v=None,
                reference_vpp_v=None,
                response_vpp_v=None,
                gain_linear=None,
                gain_db=None,
                phase_wrapped_deg=None,
                phase_unwrapped_deg=None,
                status="failed",
                error="capture failed",
            ),
            _point(3, 10_000.0, 1.0, -170.0),
            _point(4, 100_000.0, 1.0, 170.0),
        ]

        result = unwrap_frequency_response_phase(points)

        self.assertAlmostEqual(result[0].phase_unwrapped_deg or 0.0, 170.0)
        self.assertAlmostEqual(result[1].phase_unwrapped_deg or 0.0, 190.0)
        self.assertIsNone(result[2].phase_unwrapped_deg)
        self.assertAlmostEqual(result[3].phase_unwrapped_deg or 0.0, -170.0)
        self.assertAlmostEqual(result[4].phase_unwrapped_deg or 0.0, -190.0)

    def test_fit_document_writes_csv_and_json_for_all_candidates(self):
        frequencies = (10.0, 100.0, 1_000.0, 10_000.0)
        points = [_point(index, frequency, 1.0 + 0.5 * np.log10(frequency)) for index, frequency in enumerate(frequencies)]
        methods = ["linear_log", "polynomial"]
        if HAS_SCIPY:
            methods.append("pchip")

        document, values = build_fit_document(
            points,
            {"methods": methods, "polynomial_degree": 2},
        )

        assert document is not None
        self.assertEqual(document["methods"]["linear_log"]["status"], "ok")
        self.assertEqual(document["methods"]["polynomial"]["status"], "ok")
        if HAS_SCIPY:
            self.assertEqual(document["methods"]["pchip"]["status"], "ok")
        self.assertAlmostEqual(values["linear_log"][2][0] or 0.0, 2.5)
        with TemporaryDirectory() as tmp:
            csv_path = write_frequency_response_csv(Path(tmp) / "frequency_response.csv", points, values)
            json_path = write_fit_document(Path(tmp) / "frequency_response_fit.json", document)
            self.assertIn("fit_polynomial_gain_linear", csv_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(json_path)
            if HAS_SCIPY:
                self.assertIn("PCHIP", json_path.read_text(encoding="utf-8"))

    def test_fit_formulas_export_directly_usable_piecewise_parameters(self):
        points = [
            _point(0, 10.0, 1.0),
            _point(1, 100.0, 1.5),
            _point(2, 1000.0, 2.0),
        ]

        document, _values = build_fit_document(
            points,
            {"methods": ["linear_log", "polynomial"], "polynomial_degree": 2},
        )

        assert document is not None
        segment = document["methods"]["linear_log"]["parameters"]["segments"][0]
        self.assertEqual(segment["x_start"], 1.0)
        self.assertEqual(segment["x_stop"], 2.0)
        self.assertAlmostEqual(segment["slope"], 0.5)
        self.assertAlmostEqual(segment["intercept"], 0.5)
        polynomial = document["methods"]["polynomial"]
        self.assertEqual(polynomial["metrics"]["r_squared"], 1.0)
        if HAS_SCIPY:
            pchip_document, _values = build_fit_document(
                points,
                {"methods": ["pchip"], "polynomial_degree": 2},
            )
            assert pchip_document is not None
            pchip_segment = pchip_document["methods"]["pchip"]["parameters"]["segments"][0]
            self.assertEqual(len(pchip_segment["coefficients"]), 4)
            self.assertEqual(pchip_segment["x_start"], 1.0)

    def test_invalid_waveform_time_axis_becomes_an_auditable_failed_point(self):
        frequency_hz = 1_000.0
        reference = _waveform(
            channel=1,
            start_s=0.0,
            samples=100,
            sample_rate_hz=50_000.0,
            frequency_hz=frequency_hz,
            amplitude_peak_v=1.0,
            phase_deg=0.0,
            offset_v=0.0,
        )
        response_times = reference.times_s.copy()
        response_times[20] = response_times[19]
        response = SimpleNamespace(
            times_s=response_times,
            voltages_v=reference.voltages_v.copy(),
            summary=lambda **kwargs: {},
        )

        point = analyze_frequency_response_point(
            index=0,
            requested_frequency_hz=frequency_hz,
            reference_waveform=reference,
            response_waveform=response,
            frequency_tolerance_ratio=0.05,
            capture_package="capture",
            metadata_path="metadata.json",
        )

        self.assertEqual(point.status, "failed")
        self.assertIn("strictly increasing", point.error)


if __name__ == "__main__":
    unittest.main()
