from __future__ import annotations

import unittest

from wavebench.services.frequency_response import FrequencyResponsePoint
from wavebench.services.frequency_response_adaptive import select_adaptive_frequency_refinement


def _point(index: int, frequency_hz: float, gain_db: float, phase_deg: float, *, amplitude_index: int = 0):
    return FrequencyResponsePoint(
        index=index,
        requested_frequency_hz=frequency_hz,
        reference_frequency_hz=frequency_hz,
        response_frequency_hz=frequency_hz,
        reference_amplitude_peak_v=1.0,
        response_amplitude_peak_v=1.0,
        reference_vpp_v=2.0,
        response_vpp_v=2.0,
        gain_linear=1.0,
        gain_db=gain_db,
        phase_wrapped_deg=phase_deg,
        phase_unwrapped_deg=phase_deg,
        status="ok",
        amplitude_index=amplitude_index,
    )


class FrequencyResponseAdaptiveTests(unittest.TestCase):
    def test_gain_or_phase_change_refines_every_amplitude_slice_as_a_rectangle(self):
        points = [
            _point(0, 100.0, 0.0, 0.0, amplitude_index=0),
            _point(1, 1000.0, 1.0, 0.0, amplitude_index=0),
            _point(2, 100.0, 0.0, 0.0, amplitude_index=1),
            _point(3, 1000.0, 0.0, 20.0, amplitude_index=1),
        ]
        result = select_adaptive_frequency_refinement(
            points,
            spacing="log",
            level=1,
            config={"enabled": True, "max_levels": 2, "max_frequency_points": 10},
            existing_frequencies_hz={100.0, 1000.0},
        )

        self.assertEqual(len(result.frequencies), 1)
        self.assertAlmostEqual(result.frequencies[0].frequency_hz, 10**2.5)
        self.assertEqual(result.frequencies[0].level, 1)

    def test_linear_midpoint_and_budget_are_auditable(self):
        points = [
            _point(0, 100.0, 0.0, 0.0),
            _point(1, 200.0, 1.0, 0.0),
            _point(2, 300.0, 2.0, 0.0),
        ]
        result = select_adaptive_frequency_refinement(
            points,
            spacing="linear",
            level=1,
            config={"enabled": True, "max_frequency_points": 4},
            existing_frequencies_hz={100.0, 200.0, 300.0},
        )

        self.assertEqual([item.frequency_hz for item in result.frequencies], [150.0])
        self.assertTrue(result.budget_limited)
