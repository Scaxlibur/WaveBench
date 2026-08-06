import unittest

import numpy as np

from wavebench.data.quality import estimate_frequency_fft, estimate_frequency_hysteresis, summarize_waveform


def make_pwm_waveform(*, period_s: float, duration_s: float, duty_cycle: float, rise_time_s: float, fall_time_s: float, amplitude_v: float = 2.5):
    dt = 1e-6
    times = np.arange(0.0, duration_s, dt)
    phase = np.mod(times, period_s)
    voltages = np.empty_like(times)
    low = -amplitude_v
    high = amplitude_v
    high_end = duty_cycle * period_s
    fall_end = high_end + fall_time_s
    rise_end = rise_time_s
    for i, t in enumerate(phase):
        if t < rise_end:
            voltages[i] = low + (high - low) * (t / rise_time_s)
        elif t < high_end:
            voltages[i] = high
        elif t < fall_end:
            voltages[i] = high - (high - low) * ((t - high_end) / fall_time_s)
        else:
            voltages[i] = low
    return times, voltages


class QualityTests(unittest.TestCase):
    def test_estimates_sine_frequency_with_hysteresis(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = 2.5 * np.sin(2 * np.pi * 1000.0 * times)
        self.assertAlmostEqual(estimate_frequency_hysteresis(times, voltages), 1000.0, delta=2.0)

    def test_estimates_sine_frequency_with_fft(self):
        times = np.linspace(-0.001, 0.001, 10000, endpoint=False)
        voltages = 2.5 * np.sin(2 * np.pi * 1000.0 * times)
        self.assertAlmostEqual(estimate_frequency_fft(times, voltages), 1000.0, delta=5.0)

    def test_summarizes_voltage_metrics(self):
        times = np.array([0.0, 0.25, 0.5, 0.75])
        voltages = np.array([0.0, 2.0, 0.0, -2.0])
        quality = summarize_waveform(times, voltages)
        self.assertEqual(quality.voltage_vpp_v, 4.0)
        self.assertAlmostEqual(quality.voltage_rms_v, np.sqrt(2.0))

    def test_estimates_duty_cycle_for_square_like_pwm(self):
        times, voltages = make_pwm_waveform(period_s=0.001, duration_s=0.01, duty_cycle=0.25, rise_time_s=20e-6, fall_time_s=20e-6)
        quality = summarize_waveform(times, voltages)
        self.assertIsNotNone(quality.duty_cycle)
        self.assertAlmostEqual(quality.duty_cycle, 0.25, delta=0.03)

    def test_estimates_rise_and_fall_time_for_square_like_wave(self):
        times, voltages = make_pwm_waveform(period_s=0.001, duration_s=0.01, duty_cycle=0.5, rise_time_s=20e-6, fall_time_s=30e-6)
        quality = summarize_waveform(times, voltages)
        self.assertIsNotNone(quality.rise_time_s)
        self.assertIsNotNone(quality.fall_time_s)
        self.assertAlmostEqual(quality.rise_time_s, 16e-6, delta=3e-6)
        self.assertAlmostEqual(quality.fall_time_s, 24e-6, delta=3e-6)

    def test_sine_does_not_report_edge_metrics(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = 2.5 * np.sin(2 * np.pi * 1000.0 * times)
        quality = summarize_waveform(times, voltages)
        self.assertIsNone(quality.duty_cycle)
        self.assertIsNone(quality.rise_time_s)
        self.assertIsNone(quality.fall_time_s)

    def test_warns_when_window_has_too_few_cycles(self):
        times = np.linspace(-0.001, 0.001, 10000, endpoint=False)
        voltages = 2.5 * np.sin(2 * np.pi * 500.0 * times)
        quality = summarize_waveform(times, voltages)
        self.assertAlmostEqual(quality.estimated_cycles, 1.0, delta=0.05)
        self.assertTrue(any("frequency estimate may be unreliable" in warning for warning in quality.quality_warnings))

    def test_warns_when_expected_frequency_mismatches(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = 2.5 * np.sin(2 * np.pi * 5000.0 * times)
        quality = summarize_waveform(times, voltages, expected_frequency_hz=500.0, frequency_tolerance_ratio=0.05)
        self.assertFalse(quality.frequency_in_tolerance)
        self.assertAlmostEqual(quality.frequency_error_ratio, 9.0, delta=0.1)
        self.assertTrue(any("frequency_mismatch" in warning for warning in quality.quality_warnings))

    def test_warns_when_points_per_cycle_is_low(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = 2.5 * np.sin(2 * np.pi * 100000.0 * times)
        quality = summarize_waveform(times, voltages)
        self.assertLess(quality.points_per_cycle, 20.0)
        self.assertTrue(any("low_points_per_cycle" in warning for warning in quality.quality_warnings))

    def test_warns_when_signal_amplitude_is_low(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = 0.001 * np.sin(2 * np.pi * 1000.0 * times)
        quality = summarize_waveform(times, voltages)
        self.assertTrue(any("low_signal_amplitude" in warning for warning in quality.quality_warnings))

    def test_frequency_response_can_use_a_lower_signal_warning_threshold(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = 0.005 * np.sin(2 * np.pi * 1000.0 * times)

        default_quality = summarize_waveform(times, voltages, expected_frequency_hz=1000.0)
        relaxed_quality = summarize_waveform(
            times,
            voltages,
            expected_frequency_hz=1000.0,
            min_signal_vpp=0.005,
        )

        self.assertTrue(any("low_signal_amplitude" in warning for warning in default_quality.quality_warnings))
        self.assertFalse(any("low_signal_amplitude" in warning for warning in relaxed_quality.quality_warnings))

    def test_flat_dc_without_expected_frequency_has_no_frequency_warning(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = np.full_like(times, 5.0)
        quality = summarize_waveform(times, voltages)
        self.assertIsNone(quality.frequency_estimate_hz)
        self.assertFalse(any("frequency_unavailable" in warning for warning in quality.quality_warnings))
        self.assertFalse(any("low_signal_amplitude" in warning for warning in quality.quality_warnings))

    def test_flat_signal_with_expected_frequency_warns_frequency_unavailable(self):
        times = np.linspace(0.0, 0.01, 10000, endpoint=False)
        voltages = np.full_like(times, 5.0)
        quality = summarize_waveform(times, voltages, expected_frequency_hz=1000.0)
        self.assertTrue(any("frequency_unavailable" in warning for warning in quality.quality_warnings))
        self.assertTrue(any("low_signal_amplitude" in warning for warning in quality.quality_warnings))


if __name__ == "__main__":
    unittest.main()
