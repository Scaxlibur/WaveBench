import importlib.util
import unittest
from unittest.mock import patch

import numpy as np

from wavebench.data.signal_pipeline import (
    ANALYSIS_FREQUENCY_METRICS,
    FrequencySignal,
    detrend_linear,
    fft_signal,
    filter_fir,
    filter_iir,
    measure_frequency,
    measure_time,
    remove_dc,
    validate_waveform,
    window_signal,
)
from wavebench.errors import DataError


HAS_SCIPY = importlib.util.find_spec("scipy") is not None


class SignalPipelineTests(unittest.TestCase):
    def waveform(self, samples: int = 1000, sample_rate_hz: float = 10_000.0) -> np.ndarray:
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = (
            1.5 * np.sin(2 * np.pi * 100.0 * time_s)
            + 0.15 * np.sin(2 * np.pi * 200.0 * time_s)
            + 0.075 * np.sin(2 * np.pi * 300.0 * time_s)
        )
        return np.column_stack((time_s, voltage_v))

    def test_waveform_validation_is_strict(self) -> None:
        valid = self.waveform(8)
        self.assertEqual(validate_waveform(valid).as_array().shape, (8, 2))

        invalid = (
            np.empty((0, 2)),
            np.zeros((4, 3)),
            np.array([["0", "1"], ["1", "2"]]),
            np.array([[0.0, 1.0], [1.0, np.nan]]),
            np.array([[0.0, 1.0], [0.0, 2.0]]),
            np.array([[0.0, 1.0], [-1.0, 2.0]]),
        )
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(DataError):
                    validate_waveform(data)

    def test_remove_dc_subtracts_arithmetic_mean(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(4, dtype=float), [1.0, 2.0, 3.0, 8.0]))
        )
        result = remove_dc(signal)
        np.testing.assert_allclose(result.voltage_v, [-2.5, -1.5, -0.5, 4.5])
        self.assertEqual(float(np.mean(result.voltage_v)), 0.0)

    def test_linear_detrend_removes_slope_and_intercept_on_centered_time(self) -> None:
        time_s = np.array([1000.0, 1000.2, 1000.5, 1001.0])
        residual = np.array([0.2, -0.1, -0.1, 0.2])
        residual -= np.mean(residual)
        centered_time = time_s - np.mean(time_s)
        residual -= np.dot(centered_time, residual) / np.dot(centered_time, centered_time) * centered_time
        voltage_v = 4.0 + 2.5 * centered_time + residual

        result = detrend_linear(validate_waveform(np.column_stack((time_s, voltage_v))))

        self.assertAlmostEqual(float(np.mean(result.voltage_v)), 0.0, places=12)
        self.assertAlmostEqual(float(np.dot(centered_time, result.voltage_v)), 0.0, places=12)
        np.testing.assert_allclose(result.voltage_v, residual, atol=1e-12)

    def test_numpy_window_definitions_and_coherent_gain(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(8, dtype=float), np.ones(8)))
        )
        for name, expected in (
            ("hann", np.hanning(8)),
            ("hamming", np.hamming(8)),
            ("blackman", np.blackman(8)),
        ):
            with self.subTest(name=name):
                result = window_signal(signal, name)
                np.testing.assert_array_equal(result.voltage_v, expected)
                self.assertEqual(result.coherent_gain, float(np.mean(expected)))
                self.assertEqual(result.window_name, name)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_causal_fir_matches_firwin_and_preserves_time_axis(self) -> None:
        from scipy.signal import firwin

        samples = 64
        sample_rate_hz = 1000.0
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = np.zeros(samples)
        voltage_v[0] = 1.0
        signal = validate_waveform(np.column_stack((time_s, voltage_v)))

        result = filter_fir(
            signal,
            response="lowpass",
            cutoff_hz=100.0,
            numtaps=11,
            mode="causal",
        )
        expected_taps = firwin(
            11,
            100.0,
            window="hamming",
            pass_zero="lowpass",
            scale=True,
            fs=result.sample_rate_hz,
        )

        np.testing.assert_array_equal(result.signal.time_s, time_s)
        np.testing.assert_allclose(result.taps, expected_taps, rtol=0, atol=0)
        np.testing.assert_allclose(result.signal.voltage_v[:11], expected_taps, atol=1e-15)
        np.testing.assert_allclose(result.signal.voltage_v[11:], 0.0, atol=1e-15)
        self.assertAlmostEqual(result.sample_rate_hz, sample_rate_hz)
        self.assertTrue(result.scipy_version)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_zero_phase_fir_fixes_padding_and_minimum_samples(self) -> None:
        from scipy.signal import filtfilt

        numtaps = 11
        sample_rate_hz = 1000.0
        too_short = self.waveform(3 * numtaps, sample_rate_hz)
        with self.assertRaisesRegex(DataError, "at least 34 samples"):
            filter_fir(
                validate_waveform(too_short),
                response="lowpass",
                cutoff_hz=100.0,
                numtaps=numtaps,
                mode="zero_phase",
            )

        minimum = validate_waveform(self.waveform(3 * numtaps + 1, sample_rate_hz))
        with patch("scipy.signal.filtfilt", wraps=filtfilt) as apply_filter:
            result = filter_fir(
                minimum,
                response="lowpass",
                cutoff_hz=100.0,
                numtaps=numtaps,
                mode="zero_phase",
            )

        self.assertEqual(result.signal.voltage_v.size, 3 * numtaps + 1)
        self.assertTrue(np.all(np.isfinite(result.signal.voltage_v)))
        self.assertEqual(
            apply_filter.call_args.kwargs,
            {
                "axis": -1,
                "padtype": "odd",
                "padlen": 3 * numtaps,
                "method": "pad",
            },
        )

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_zero_phase_fir_squares_the_single_pass_magnitude_response(self) -> None:
        sample_rate_hz = 10_000.0
        samples = 10_000
        frequency_hz = 1000.0
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = np.sin(2 * np.pi * frequency_hz * time_s)
        signal = validate_waveform(np.column_stack((time_s, voltage_v)))

        causal = filter_fir(
            signal,
            response="lowpass",
            cutoff_hz=frequency_hz,
            numtaps=101,
            mode="causal",
        ).signal.voltage_v
        zero_phase = filter_fir(
            signal,
            response="lowpass",
            cutoff_hz=frequency_hz,
            numtaps=101,
            mode="zero_phase",
        ).signal.voltage_v
        interior = slice(2000, 8000)
        basis = np.exp(-2j * np.pi * frequency_hz * time_s[interior])
        causal_amplitude = 2 * abs(np.dot(causal[interior], basis)) / basis.size
        zero_phase_amplitude = 2 * abs(np.dot(zero_phase[interior], basis)) / basis.size

        self.assertAlmostEqual(zero_phase_amplitude, causal_amplitude**2, places=10)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_fir_supports_low_high_bandpass_and_bandstop(self) -> None:
        sample_rate_hz = 10_000.0
        samples = 6000
        frequencies = (500.0, 1500.0, 3000.0)
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = sum(np.sin(2 * np.pi * frequency * time_s) for frequency in frequencies)
        signal = validate_waveform(np.column_stack((time_s, voltage_v)))
        cases = {
            "lowpass": (1000.0, {500.0}, {3000.0}),
            "highpass": (2000.0, {3000.0}, {500.0}),
            "bandpass": ([1000.0, 2000.0], {1500.0}, {500.0, 3000.0}),
            "bandstop": ([1000.0, 2000.0], {500.0, 3000.0}, {1500.0}),
        }

        for response, (cutoff_hz, passed, rejected) in cases.items():
            with self.subTest(response=response):
                filtered = filter_fir(
                    signal,
                    response=response,
                    cutoff_hz=cutoff_hz,
                    numtaps=101,
                    mode="zero_phase",
                ).signal.voltage_v
                interior = slice(500, -500)
                interior_time = time_s[interior]
                amplitudes = {
                    frequency: 2
                    * abs(
                        np.dot(
                            filtered[interior],
                            np.exp(-2j * np.pi * frequency * interior_time),
                        )
                    )
                    / interior_time.size
                    for frequency in frequencies
                }
                for frequency in passed:
                    self.assertGreater(amplitudes[frequency], 0.8)
                for frequency in rejected:
                    self.assertLess(amplitudes[frequency], 0.01)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_fir_rejects_nonuniform_sampling_and_nyquist_cutoff(self) -> None:
        data = self.waveform(1000, 10_000.0)
        data[500:, 0] += 1e-5
        with self.assertRaisesRegex(DataError, "uniformly sampled"):
            filter_fir(
                validate_waveform(data),
                response="lowpass",
                cutoff_hz=1000.0,
                numtaps=31,
                mode="causal",
            )

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_causal_iir_designs_match_direct_sos_execution(self) -> None:
        from scipy import signal as scipy_signal

        sample_rate_hz = 10_000.0
        time_signal = validate_waveform(self.waveform(2000, sample_rate_hz))
        cases = {
            "butterworth": (scipy_signal.butter, {}, (4, 1000.0)),
            "chebyshev1": (
                scipy_signal.cheby1,
                {"ripple_db": 1.0},
                (4, 1.0, 1000.0),
            ),
            "chebyshev2": (
                scipy_signal.cheby2,
                {"attenuation_db": 40.0},
                (4, 40.0, 1000.0),
            ),
            "elliptic": (
                scipy_signal.ellip,
                {"ripple_db": 1.0, "attenuation_db": 40.0},
                (4, 1.0, 40.0, 1000.0),
            ),
        }

        for design, (factory, request_parameters, scipy_args) in cases.items():
            with self.subTest(design=design):
                result = filter_iir(
                    time_signal,
                    design=design,
                    response="lowpass",
                    cutoff_hz=1000.0,
                    order=4,
                    mode="causal",
                    **request_parameters,
                )
                expected_sos = factory(
                    *scipy_args,
                    btype="lowpass",
                    output="sos",
                    fs=result.sample_rate_hz,
                )
                expected_voltage = scipy_signal.sosfilt(
                    expected_sos,
                    time_signal.voltage_v,
                    axis=-1,
                    zi=None,
                )

                np.testing.assert_array_equal(result.signal.time_s, time_signal.time_s)
                np.testing.assert_allclose(result.sos, expected_sos, rtol=0, atol=0)
                np.testing.assert_allclose(
                    result.signal.voltage_v,
                    expected_voltage,
                    rtol=0,
                    atol=0,
                )
                self.assertTrue(0.0 < result.max_pole_magnitude < 1.0)
                self.assertTrue(result.scipy_version)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_zero_phase_iir_fixes_padding_and_minimum_samples(self) -> None:
        from scipy import signal as scipy_signal

        sample_rate_hz = 1000.0
        sos = scipy_signal.butter(
            3,
            100.0,
            btype="lowpass",
            output="sos",
            fs=sample_rate_hz,
        )
        padlen = 3 * (
            2 * len(sos)
            + 1
            - min(np.count_nonzero(sos[:, 2] == 0), np.count_nonzero(sos[:, 5] == 0))
        )
        with self.assertRaisesRegex(DataError, f"at least {padlen + 1} samples"):
            filter_iir(
                validate_waveform(self.waveform(padlen, sample_rate_hz)),
                design="butterworth",
                response="lowpass",
                cutoff_hz=100.0,
                order=3,
                mode="zero_phase",
            )

        minimum = validate_waveform(self.waveform(padlen + 1, sample_rate_hz))
        with patch("scipy.signal.sosfiltfilt", wraps=scipy_signal.sosfiltfilt) as apply_filter:
            result = filter_iir(
                minimum,
                design="butterworth",
                response="lowpass",
                cutoff_hz=100.0,
                order=3,
                mode="zero_phase",
            )

        self.assertEqual(result.zero_phase_padlen, padlen)
        self.assertEqual(result.signal.voltage_v.size, padlen + 1)
        self.assertEqual(
            apply_filter.call_args.kwargs,
            {"axis": -1, "padtype": "odd", "padlen": padlen},
        )

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_zero_phase_iir_squares_the_single_pass_magnitude_response(self) -> None:
        sample_rate_hz = 10_000.0
        samples = 10_000
        frequency_hz = 1000.0
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = np.sin(2 * np.pi * frequency_hz * time_s)
        time_signal = validate_waveform(np.column_stack((time_s, voltage_v)))

        causal = filter_iir(
            time_signal,
            design="butterworth",
            response="lowpass",
            cutoff_hz=frequency_hz,
            order=4,
            mode="causal",
        ).signal.voltage_v
        zero_phase = filter_iir(
            time_signal,
            design="butterworth",
            response="lowpass",
            cutoff_hz=frequency_hz,
            order=4,
            mode="zero_phase",
        ).signal.voltage_v
        interior = slice(2000, 8000)
        basis = np.exp(-2j * np.pi * frequency_hz * time_s[interior])
        causal_amplitude = 2 * abs(np.dot(causal[interior], basis)) / basis.size
        zero_phase_amplitude = 2 * abs(np.dot(zero_phase[interior], basis)) / basis.size

        self.assertAlmostEqual(zero_phase_amplitude, causal_amplitude**2, places=10)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_iir_supports_low_high_bandpass_and_bandstop(self) -> None:
        sample_rate_hz = 10_000.0
        samples = 6000
        frequencies = (500.0, 1500.0, 3000.0)
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = sum(np.sin(2 * np.pi * frequency * time_s) for frequency in frequencies)
        time_signal = validate_waveform(np.column_stack((time_s, voltage_v)))
        cases = {
            "lowpass": (1000.0, {500.0}, {3000.0}),
            "highpass": (2000.0, {3000.0}, {500.0}),
            "bandpass": ([1000.0, 2000.0], {1500.0}, {500.0, 3000.0}),
            "bandstop": ([1000.0, 2000.0], {500.0, 3000.0}, {1500.0}),
        }

        for response, (cutoff_hz, passed, rejected) in cases.items():
            with self.subTest(response=response):
                filtered = filter_iir(
                    time_signal,
                    design="butterworth",
                    response=response,
                    cutoff_hz=cutoff_hz,
                    order=8,
                    mode="zero_phase",
                ).signal.voltage_v
                interior = slice(500, -500)
                interior_time = time_s[interior]
                amplitudes = {
                    frequency: 2
                    * abs(
                        np.dot(
                            filtered[interior],
                            np.exp(-2j * np.pi * frequency * interior_time),
                        )
                    )
                    / interior_time.size
                    for frequency in frequencies
                }
                for frequency in passed:
                    self.assertGreater(amplitudes[frequency], 0.8)
                for frequency in rejected:
                    self.assertLess(amplitudes[frequency], 0.01)

    @unittest.skipUnless(HAS_SCIPY, "SciPy analysis dependency is unavailable")
    def test_iir_rejects_bad_sampling_parameters_and_unstable_sos(self) -> None:
        time_signal = validate_waveform(self.waveform(1000, 10_000.0))
        with self.assertRaisesRegex(DataError, "below Nyquist"):
            filter_iir(
                time_signal,
                design="butterworth",
                response="lowpass",
                cutoff_hz=5000.0,
                order=4,
                mode="causal",
            )

        nonuniform = self.waveform(1000, 10_000.0)
        nonuniform[500:, 0] += 1e-5
        with self.assertRaisesRegex(DataError, "uniformly sampled"):
            filter_iir(
                validate_waveform(nonuniform),
                design="butterworth",
                response="lowpass",
                cutoff_hz=1000.0,
                order=4,
                mode="causal",
            )

        unstable = np.array([[1.0, 0.0, 0.0, 1.0, -2.0, 0.0]])
        with patch("scipy.signal.butter", return_value=unstable):
            with self.assertRaisesRegex(DataError, "not stable"):
                filter_iir(
                    time_signal,
                    design="butterworth",
                    response="lowpass",
                    cutoff_hz=1000.0,
                    order=4,
                    mode="causal",
                )

        invalid_sos = (
            (np.ones((2, 5)), "Nx6 SOS"),
            (np.array([[1.0, 0.0, 0.0, 2.0, 0.0, 0.0]]), "a0 = 1"),
            (np.array([[1.0, np.nan, 0.0, 1.0, 0.0, 0.0]]), "non-finite"),
        )
        for sos, message in invalid_sos:
            with self.subTest(message=message), patch(
                "scipy.signal.butter", return_value=sos
            ):
                with self.assertRaisesRegex(DataError, message):
                    filter_iir(
                        time_signal,
                        design="butterworth",
                        response="lowpass",
                        cutoff_hz=1000.0,
                        order=4,
                        mode="causal",
                    )

    def test_iir_parameters_are_validated_without_scipy(self) -> None:
        time_signal = validate_waveform(self.waveform(100, 10_000.0))
        cases = (
            ({"design": "bessel"}, "design must be"),
            ({"order": 0}, "order must be"),
            ({"order": 13}, "order must be"),
            ({"ripple_db": None}, "requires ripple_db"),
            ({"ripple_db": 21.0}, "must be <= 20"),
            ({"ripple_db": 20.0, "attenuation_db": 20.0}, "less than"),
        )
        for overrides, message in cases:
            request = {
                "design": "chebyshev1",
                "response": "lowpass",
                "cutoff_hz": 1000.0,
                "order": 4,
                "mode": "causal",
                "ripple_db": 1.0,
            }
            request.update(overrides)
            if "attenuation_db" in overrides:
                request["design"] = "elliptic"
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(DataError, message):
                    filter_iir(time_signal, **request)

        with self.assertRaisesRegex(DataError, "below Nyquist"):
            filter_fir(
                validate_waveform(self.waveform(1000, 10_000.0)),
                response="lowpass",
                cutoff_hz=5000.0,
                numtaps=31,
                mode="causal",
            )

    def test_even_fft_preserves_dc_and_nyquist_without_double_scaling(self) -> None:
        samples = 8
        indices = np.arange(samples)
        time_s = indices / samples
        voltage_v = 3.0 + 2.0 * np.cos(2 * np.pi * indices / samples) + 5.0 * (-1.0) ** indices

        result = fft_signal(validate_waveform(np.column_stack((time_s, voltage_v))))

        self.assertAlmostEqual(float(result.amplitude_v[0]), 3.0, places=12)
        self.assertAlmostEqual(float(result.amplitude_v[1]), 2.0, places=12)
        self.assertAlmostEqual(float(result.amplitude_v[-1]), 5.0, places=12)

    def test_odd_fft_doubles_the_last_non_dc_bin(self) -> None:
        samples = 9
        indices = np.arange(samples)
        time_s = indices / samples
        voltage_v = 2.0 * np.cos(2 * np.pi * 4 * indices / samples)

        result = fft_signal(validate_waveform(np.column_stack((time_s, voltage_v))))

        self.assertAlmostEqual(float(result.amplitude_v[4]), 2.0, places=12)

    def test_fft_uses_window_coherent_gain(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(8, dtype=float) / 8.0, np.full(8, 2.5)))
        )
        windowed = window_signal(signal, "hamming")

        result = fft_signal(windowed)

        self.assertAlmostEqual(float(result.amplitude_v[0]), 2.5, places=12)
        self.assertEqual(result.coherent_gain, float(np.mean(np.hamming(8))))

    def test_fft_rejects_short_or_nonuniform_input(self) -> None:
        with self.assertRaisesRegex(DataError, "at least four"):
            fft_signal(validate_waveform(self.waveform(3)))

        data = self.waveform(8)
        data[4:, 0] += 1e-4
        with self.assertRaisesRegex(DataError, "uniformly sampled"):
            fft_signal(validate_waveform(data))

    def test_time_metrics_have_fixed_units_and_peak_semantics(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(4, dtype=float), [-2.0, -1.0, 1.0, 2.0]))
        )
        metrics = measure_time(
            signal,
            [
                "voltage_min_v",
                "voltage_max_v",
                "voltage_mean_v",
                "voltage_rms_v",
                "voltage_vpp_v",
            ],
        )
        self.assertEqual(metrics["voltage_min_v"], -2.0)
        self.assertEqual(metrics["voltage_max_v"], 2.0)
        self.assertEqual(metrics["voltage_mean_v"], 0.0)
        self.assertAlmostEqual(metrics["voltage_rms_v"], np.sqrt(2.5))
        self.assertEqual(metrics["voltage_vpp_v"], 4.0)

    def test_finite_input_cannot_emit_nonfinite_metrics(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(2, dtype=float), [-1e308, 1e308]))
        )

        with self.assertRaisesRegex(DataError, "not finite"):
            measure_time(signal, ["voltage_vpp_v"])

    def test_frequency_metrics_find_peak_harmonics_thd_and_noise_floor(self) -> None:
        spectrum = fft_signal(validate_waveform(self.waveform()))
        metrics, warnings = measure_frequency(spectrum, sorted(ANALYSIS_FREQUENCY_METRICS))

        self.assertEqual(warnings, [])
        self.assertAlmostEqual(metrics["peak_frequency_hz"], 100.0)
        self.assertAlmostEqual(metrics["peak_amplitude_v"], 1.5, places=12)
        self.assertAlmostEqual(metrics["harmonic_2_frequency_hz"], 200.0)
        self.assertAlmostEqual(metrics["harmonic_2_amplitude_v"], 0.15, places=12)
        self.assertAlmostEqual(metrics["harmonic_3_amplitude_v"], 0.075, places=12)
        self.assertAlmostEqual(
            metrics["thd_ratio"],
            np.sqrt(0.15**2 + 0.075**2) / 1.5,
            places=12,
        )
        self.assertLess(metrics["noise_floor_v"], 1e-13)

    def test_harmonics_outside_nyquist_are_null_with_warnings(self) -> None:
        samples = 80
        sample_rate_hz = 8000.0
        time_s = np.arange(samples) / sample_rate_hz
        voltage_v = np.sin(2 * np.pi * 2000.0 * time_s)
        spectrum = fft_signal(validate_waveform(np.column_stack((time_s, voltage_v))))

        metrics, warnings = measure_frequency(
            spectrum,
            [
                "harmonic_2_frequency_hz",
                "harmonic_2_amplitude_v",
                "harmonic_3_frequency_hz",
                "harmonic_3_amplitude_v",
                "thd_ratio",
            ],
        )

        self.assertAlmostEqual(metrics["harmonic_2_frequency_hz"], 4000.0)
        self.assertIsNone(metrics["harmonic_3_frequency_hz"])
        self.assertIsNone(metrics["harmonic_3_amplitude_v"])
        self.assertIn("harmonic_3_out_of_band", warnings)
        self.assertIn("harmonic_5_out_of_band", warnings)

    def test_silent_spectrum_has_no_peak_or_thd(self) -> None:
        time_s = np.arange(8, dtype=float) / 8.0
        spectrum = fft_signal(
            validate_waveform(np.column_stack((time_s, np.full(8, 1e-13))))
        )

        metrics, warnings = measure_frequency(
            spectrum,
            ["peak_frequency_hz", "peak_amplitude_v", "noise_floor_v", "thd_ratio"],
        )

        self.assertIsNone(metrics["peak_frequency_hz"])
        self.assertIsNone(metrics["peak_amplitude_v"])
        self.assertIsNone(metrics["thd_ratio"])
        self.assertEqual(metrics["noise_floor_v"], 0.0)
        self.assertIn("no_significant_non_dc_peak", warnings)

    def test_noise_floor_is_median_after_excluding_dc_and_main_peak(self) -> None:
        spectrum = FrequencySignal(
            frequency_hz=np.arange(5, dtype=float),
            spectrum_v=np.array([100.0, 10.0, 1.0, 3.0, 5.0], dtype=complex),
            samples=8,
            sample_interval_s=0.125,
            coherent_gain=1.0,
            window_name=None,
        )

        metrics, _ = measure_frequency(spectrum, ["noise_floor_v"])

        self.assertEqual(metrics["noise_floor_v"], 3.0)


if __name__ == "__main__":
    unittest.main()
