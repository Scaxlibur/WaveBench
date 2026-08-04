from __future__ import annotations

import unittest

import numpy as np

from wavebench.errors import ConfigError
from wavebench.services.frequency_response_calibration import (
    FrequencyResponseCalibrationConfig,
    build_frequency_response_calibration,
    normalize_frequency_response_calibration,
)


def _rows(*, amplitudes=(0.05, 0.1, 0.2), frequencies=(1e3, 2e3, 4e3, 8e3, 16e3)):
    rows = []
    for amplitude in amplitudes:
        for frequency in frequencies:
            gain_db = 6.0 - 2.0 * np.log10(frequency / 1e3) + 5.0 * amplitude
            rows.append(
                {
                    "status": "ok",
                    "requested_frequency_hz": frequency,
                    "requested_vpp": amplitude,
                    "gain_db": gain_db,
                }
            )
    return rows


class FrequencyResponseCalibrationTests(unittest.TestCase):
    def test_builds_two_dimensional_lut_and_chebyshev_formulas(self):
        document, lut = build_frequency_response_calibration(
            _rows(), FrequencyResponseCalibrationConfig(max_slope_db_per_octave=20.0)
        )

        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["grid"]["requested_vpp"], [0.05, 0.1, 0.2])
        self.assertEqual(len(lut), 15)
        self.assertEqual(len(document["chebyshev"]), 3)
        self.assertAlmostEqual(document["validation"]["amplitude_holdout_rmse_db"] or 0.0, 0.0)
        self.assertTrue(all(row["correction_linear"] > 0 for row in lut))

    def test_unity_target_and_limit_flags_are_auditable(self):
        document, lut = build_frequency_response_calibration(
            _rows(),
            FrequencyResponseCalibrationConfig(
                target_mode="unity_gain",
                correction_min_db=-12.0,
                correction_max_db=12.0,
                max_slope_db_per_octave=0.01,
            ),
        )

        self.assertEqual(document["target_gain_db"], 0.0)
        self.assertGreater(document["limit_counts"]["slope_limited"], 0)
        self.assertTrue(any(row["slope_limited"] for row in lut))

    def test_requires_two_requested_vpp_slices(self):
        with self.assertRaisesRegex(ConfigError, "at least two"):
            build_frequency_response_calibration(
                _rows(amplitudes=(0.1,)), FrequencyResponseCalibrationConfig()
            )

    def test_normalizes_explicit_target_and_rejects_incomplete_target(self):
        config = normalize_frequency_response_calibration(
            {"target_mode": "explicit_gain_db", "target_gain_db": -1.5}
        )
        self.assertEqual(config.target_gain_db, -1.5)
        with self.assertRaisesRegex(ConfigError, "target_gain_db is required"):
            normalize_frequency_response_calibration({"target_mode": "explicit_gain_db"})
