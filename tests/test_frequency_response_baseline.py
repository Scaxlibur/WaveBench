from __future__ import annotations

import unittest

import numpy as np

from wavebench.errors import ConfigError
from wavebench.services.frequency_response import FrequencyResponsePoint
from wavebench.services.frequency_response_baseline import apply_frequency_response_baseline


def _point(index: int, frequency_hz: float, gain_db: float, phase_deg: float, *, vpp: float | None = 0.1):
    return FrequencyResponsePoint(
        index=index,
        requested_frequency_hz=frequency_hz,
        reference_frequency_hz=frequency_hz,
        response_frequency_hz=frequency_hz,
        reference_amplitude_peak_v=1.0,
        response_amplitude_peak_v=10.0 ** (gain_db / 20.0),
        reference_vpp_v=2.0,
        response_vpp_v=2.0,
        gain_linear=10.0 ** (gain_db / 20.0),
        gain_db=gain_db,
        phase_wrapped_deg=(phase_deg + 180.0) % 360.0 - 180.0,
        phase_unwrapped_deg=phase_deg,
        status="ok",
        requested_vpp=vpp,
    )


class FrequencyResponseBaselineTests(unittest.TestCase):
    def test_complex_transfer_preserves_raw_evidence_and_subtracts_log_interpolated_baseline(self):
        points = [_point(0, 1_000.0, 8.0, 35.0), _point(1, 10_000.0, 10.0, 55.0)]
        baseline = [
            {"status": "ok", "requested_vpp": 0.1, "requested_frequency_hz": 1_000, "gain_db": 2, "phase_unwrapped_deg": 5},
            {"status": "ok", "requested_vpp": 0.1, "requested_frequency_hz": 10_000, "gain_db": 4, "phase_unwrapped_deg": 15},
        ]

        corrected, document = apply_frequency_response_baseline(
            points, baseline, {"run_dir": "baseline", "mode": "complex_transfer"}
        )

        self.assertEqual([point.gain_db for point in corrected], [8.0, 10.0])
        self.assertEqual([point.phase_unwrapped_deg for point in corrected], [35.0, 55.0])
        self.assertEqual([point.gain_db_corrected for point in corrected], [6.0, 6.0])
        self.assertEqual([point.phase_unwrapped_corrected_deg for point in corrected], [30.0, 40.0])
        self.assertAlmostEqual(corrected[0].gain_linear_corrected or 0.0, 10.0 ** (6.0 / 20.0))
        self.assertEqual(document["mode"], "complex_transfer")
        self.assertEqual(document["valid_domain_hz_by_requested_vpp"]["0.1"], [1000.0, 10000.0])

    def test_delay_only_estimates_and_removes_linear_phase_delay(self):
        delay_s = 2e-6
        frequencies = (1_000.0, 10_000.0)
        baseline = [
            {
                "status": "ok",
                "requested_frequency_hz": frequency,
                "gain_db": 0,
                "phase_unwrapped_deg": -360.0 * frequency * delay_s,
            }
            for frequency in frequencies
        ]
        points = [_point(index, frequency, 3.0, -360.0 * frequency * delay_s - 20.0, vpp=None)
                  for index, frequency in enumerate(frequencies)]

        corrected, document = apply_frequency_response_baseline(
            points, baseline, {"run_dir": "baseline", "mode": "delay_only"}
        )

        self.assertTrue(np.allclose(
            [point.phase_unwrapped_corrected_deg for point in corrected], [-20.0, -20.0]
        ))
        self.assertAlmostEqual(document["estimated_delay_s_by_requested_vpp"]["unspecified"], delay_s)

    def test_phase_only_keeps_gain_and_corrects_only_phase(self):
        corrected, _document = apply_frequency_response_baseline(
            [_point(0, 1_000.0, 6.0, 30.0)],
            [
                {"status": "ok", "requested_vpp": 0.1, "requested_frequency_hz": 100, "gain_db": 1, "phase_unwrapped_deg": 10},
                {"status": "ok", "requested_vpp": 0.1, "requested_frequency_hz": 10_000, "gain_db": 1, "phase_unwrapped_deg": 10},
            ],
            {"run_dir": "baseline", "mode": "phase_only"},
        )

        self.assertEqual(corrected[0].gain_db, 6.0)
        self.assertEqual(corrected[0].gain_db_corrected, 6.0)
        self.assertEqual(corrected[0].phase_unwrapped_corrected_deg, 20.0)

    def test_refuses_to_extrapolate_outside_baseline_domain(self):
        with self.assertRaisesRegex(ConfigError, "does not cover"):
            apply_frequency_response_baseline(
                [_point(0, 20_000.0, 1.0, 0.0)],
                [
                    {"status": "ok", "requested_vpp": 0.1, "requested_frequency_hz": 1_000, "gain_db": 0, "phase_unwrapped_deg": 0},
                    {"status": "ok", "requested_vpp": 0.1, "requested_frequency_hz": 10_000, "gain_db": 0, "phase_unwrapped_deg": 0},
                ],
                {"run_dir": "baseline"},
            )
