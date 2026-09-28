from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Sequence

import numpy as np

from wavebench.errors import DataError
from wavebench.data.analysis_control import checkpoint


ANALYSIS_TIME_METRICS = frozenset({
    "voltage_min_v",
    "voltage_max_v",
    "voltage_mean_v",
    "voltage_rms_v",
    "voltage_vpp_v",
})
ANALYSIS_FREQUENCY_METRICS = frozenset({
    "peak_frequency_hz",
    "peak_amplitude_v",
    "noise_floor_v",
    "thd_ratio",
    *(
        f"harmonic_{order}_{field}"
        for order in range(2, 6)
        for field in ("frequency_hz", "amplitude_v")
    ),
})
ANALYSIS_FIR_RESPONSES = frozenset({"lowpass", "highpass", "bandpass", "bandstop"})
ANALYSIS_FIR_MODES = frozenset({"causal", "zero_phase"})
ANALYSIS_IIR_DESIGNS = frozenset({
    "butterworth",
    "chebyshev1",
    "chebyshev2",
    "elliptic",
})
ANALYSIS_IIR_MAX_ORDER = 12
ANALYSIS_IIR_MAX_RIPPLE_DB = 20.0
ANALYSIS_IIR_MAX_ATTENUATION_DB = 200.0
SIGNIFICANT_PEAK_V = 1e-12
FILTER_BLOCK_SAMPLES = 4096


@dataclass(frozen=True)
class TimeSignal:
    time_s: np.ndarray
    voltage_v: np.ndarray
    coherent_gain: float = 1.0
    window_name: str | None = None

    def as_array(self) -> np.ndarray:
        return np.column_stack((self.time_s, self.voltage_v))


@dataclass(frozen=True)
class FrequencySignal:
    frequency_hz: np.ndarray
    spectrum_v: np.ndarray
    samples: int
    sample_interval_s: float
    coherent_gain: float
    window_name: str | None

    @property
    def amplitude_v(self) -> np.ndarray:
        return np.abs(self.spectrum_v)

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s

    @property
    def resolution_hz(self) -> float:
        if self.frequency_hz.size < 2:
            return 0.0
        return float(self.frequency_hz[1] - self.frequency_hz[0])

    def as_array(self) -> np.ndarray:
        return np.column_stack(
            (
                self.frequency_hz,
                self.spectrum_v.real,
                self.spectrum_v.imag,
                self.amplitude_v,
            )
        )


@dataclass(frozen=True)
class FirFilterResult:
    signal: TimeSignal
    taps: np.ndarray
    sample_interval_s: float
    scipy_version: str

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s


@dataclass(frozen=True)
class PsdSignal:
    frequency_hz: np.ndarray
    psd_v2_per_hz: np.ndarray
    sample_interval_s: float
    samples: int
    parameters: dict[str, Any]
    segment_count: int
    discarded_tail_samples: int
    window_power_gain: float
    window_sha256: str
    scipy_version: str

    def as_array(self) -> np.ndarray:
        return np.column_stack((self.frequency_hz, self.psd_v2_per_hz))


def normalize_psd_parameters(
    *, method: str, window: str, nperseg: int, noverlap: int, nfft: int,
    detrend: str, average: str,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for name, value, allowed in (
        ("method", method, {"welch"}),
        ("window", window, {"hann", "hamming", "blackman"}),
        ("detrend", detrend, {"none", "constant", "linear"}),
        ("average", average, {"mean", "median"}),
    ):
        if not isinstance(value, str) or value.strip().lower() not in allowed:
            raise DataError(f"analysis PSD {name} must be one of {', '.join(sorted(allowed))}")
        parameters[name] = value.strip().lower()
    for name, value in (("nperseg", nperseg), ("noverlap", noverlap), ("nfft", nfft)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise DataError(f"analysis PSD {name} must be an integer")
        parameters[name] = value
    if nperseg < 4:
        raise DataError("analysis PSD nperseg must be >= 4")
    if not 0 <= noverlap < nperseg:
        raise DataError("analysis PSD noverlap must satisfy 0 <= noverlap < nperseg")
    if nfft < nperseg:
        raise DataError("analysis PSD nfft must be >= nperseg")
    return parameters


def welch_psd(
    signal: TimeSignal, *, method: str, window: str, nperseg: int,
    noverlap: int, nfft: int, detrend: str, average: str,
) -> PsdSignal:
    parameters = normalize_psd_parameters(
        method=method, window=window, nperseg=nperseg, noverlap=noverlap,
        nfft=nfft, detrend=detrend, average=average,
    )
    if signal.window_name is not None or signal.coherent_gain != 1.0:
        raise DataError("analysis PSD must not follow a whole-signal window")
    samples = int(signal.voltage_v.size)
    if samples < nperseg:
        raise DataError("analysis PSD requires at least nperseg samples; segment length is not reduced")
    sample_interval = _uniform_sample_interval(signal, "analysis PSD")
    sample_rate = 1.0 / sample_interval
    if not np.isfinite(sample_rate):
        raise DataError("analysis PSD requires a finite sample rate")
    try:
        from scipy import __version__ as scipy_version
        from scipy import signal as scipy_signal
    except ImportError as exc:
        raise DataError("analysis PSD requires SciPy; install WaveBench with `.[analysis]`") from exc
    weights = scipy_signal.get_window(parameters["window"], nperseg, fftbins=True)
    try:
        kwargs = dict(fs=sample_rate, window=weights, nperseg=nperseg, nfft=nfft,
                      detrend=False if parameters["detrend"] == "none" else parameters["detrend"],
                      scaling="density", return_onesided=True, axis=-1)
        if parameters["average"] == "mean":
            density = np.zeros(nfft // 2 + 1, dtype=np.float64)
            segments = 0
            for start in range(0, samples - nperseg + 1, nperseg - noverlap):
                checkpoint()
                frequencies, segment_density = scipy_signal.welch(
                    signal.voltage_v[start:start + nperseg], noverlap=0, average="mean", **kwargs,
                )
                density += segment_density
                segments += 1
            density /= segments
        else:
            frequencies, density = scipy_signal.welch(
                signal.voltage_v, noverlap=noverlap, average="median", **kwargs,
            )
    except (ValueError, FloatingPointError, OverflowError) as exc:
        raise DataError(f"analysis PSD failed: {exc}") from exc
    if (
        frequencies.shape != (nfft // 2 + 1,) or density.shape != frequencies.shape
        or not np.all(np.isfinite(frequencies)) or not np.all(np.isfinite(density))
        or np.any(density < 0)
    ):
        raise DataError("analysis PSD must produce finite nonnegative one-sided density")
    hop = nperseg - noverlap
    return PsdSignal(
        frequency_hz=frequencies, psd_v2_per_hz=density,
        sample_interval_s=sample_interval, samples=samples, parameters=parameters,
        segment_count=1 + (samples - nperseg) // hop,
        discarded_tail_samples=(samples - nperseg) % hop,
        window_power_gain=float(np.mean(weights ** 2)),
        window_sha256=sha256(np.asarray(weights, dtype="<f8").tobytes()).hexdigest(),
        scipy_version=scipy_version,
    )


@dataclass(frozen=True)
class IirFilterResult:
    signal: TimeSignal
    sos: np.ndarray
    sample_interval_s: float
    scipy_version: str
    max_pole_magnitude: float
    zero_phase_padlen: int

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s


def validate_waveform(data: Any) -> TimeSignal:
    array = np.asarray(data)
    if array.ndim != 2 or array.shape[1:] != (2,) or array.shape[0] < 1:
        raise DataError("analysis pipeline input must be a non-empty Nx2 waveform array")
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(
        array.dtype, np.complexfloating
    ):
        raise DataError("analysis pipeline input must contain real numeric values")
    result = np.empty(array.shape, dtype=np.float64)
    for start in range(0, len(array), 4096):
        checkpoint()
        block = np.asarray(array[start:start + 4096], dtype=np.float64)
        if not np.all(np.isfinite(block)):
            raise DataError("analysis pipeline input must contain only finite values")
        if (not np.all(np.diff(block[:, 0]) > 0)
                or start and block[0, 0] <= result[start - 1, 0]):
            raise DataError("analysis pipeline time axis must be strictly increasing")
        result[start:start + len(block)] = block
    return TimeSignal(time_s=result[:, 0], voltage_v=result[:, 1])


def remove_dc(signal: TimeSignal) -> TimeSignal:
    voltage = signal.voltage_v - float(np.mean(signal.voltage_v))
    return _replace_voltage(signal, voltage)


def detrend_linear(signal: TimeSignal) -> TimeSignal:
    if signal.time_s.size < 2:
        raise DataError("linear detrend requires at least two samples")
    centered_time = signal.time_s - float(np.mean(signal.time_s))
    centered_voltage = signal.voltage_v - float(np.mean(signal.voltage_v))
    denominator = float(np.dot(centered_time, centered_time))
    if not np.isfinite(denominator) or denominator <= 0:
        raise DataError("linear detrend requires a usable time axis")
    slope = float(np.dot(centered_time, centered_voltage) / denominator)
    trend = float(np.mean(signal.voltage_v)) + slope * centered_time
    voltage = signal.voltage_v - trend
    return _replace_voltage(signal, voltage)


def window_signal(signal: TimeSignal, name: str) -> TimeSignal:
    windows = {
        "hann": np.hanning,
        "hamming": np.hamming,
        "blackman": np.blackman,
    }
    factory = windows.get(name)
    if factory is None:
        raise DataError("analysis window must be one of hann, hamming, blackman")
    window = factory(signal.voltage_v.size)
    gain = float(np.mean(window))
    coherent_gain = signal.coherent_gain * gain
    if not np.isfinite(coherent_gain) or coherent_gain <= 0:
        raise DataError("analysis window has an invalid coherent gain")
    voltage = signal.voltage_v * window
    result = _replace_voltage(signal, voltage)
    return TimeSignal(
        time_s=result.time_s,
        voltage_v=result.voltage_v,
        coherent_gain=coherent_gain,
        window_name=name,
    )


def filter_fir(
    signal: TimeSignal,
    *,
    response: str,
    cutoff_hz: float | Sequence[float],
    numtaps: int,
    mode: str,
) -> FirFilterResult:
    if response not in ANALYSIS_FIR_RESPONSES:
        raise DataError("analysis FIR response must be lowpass, highpass, bandpass, or bandstop")
    if mode not in ANALYSIS_FIR_MODES:
        raise DataError("analysis FIR mode must be causal or zero_phase")
    if isinstance(numtaps, bool) or not isinstance(numtaps, int) or numtaps < 3 or numtaps % 2 == 0:
        raise DataError("analysis FIR numtaps must be an odd integer >= 3")

    cutoff = _filter_cutoff(response, cutoff_hz, family="FIR")
    sample_interval = _uniform_sample_interval(signal, "analysis FIR filter")
    sample_rate = 1.0 / sample_interval
    nyquist = sample_rate / 2.0
    cutoff_values = [cutoff] if isinstance(cutoff, float) else cutoff
    if any(value >= nyquist for value in cutoff_values):
        raise DataError(
            f"analysis FIR cutoff_hz must be below Nyquist frequency {nyquist:.17g} Hz"
        )

    if mode == "zero_phase":
        minimum_samples = 3 * numtaps + 1
        if signal.voltage_v.size < minimum_samples:
            raise DataError(
                "analysis zero-phase FIR requires at least "
                f"{minimum_samples} samples for numtaps={numtaps}"
            )

    try:
        from scipy import __version__ as scipy_version
        from scipy import signal as scipy_signal
    except ImportError as exc:  # pragma: no cover - RunService checks this before execution
        raise DataError(
            "analysis FIR filter requires SciPy; install WaveBench with `.[analysis]`"
        ) from exc

    try:
        taps = np.asarray(
            scipy_signal.firwin(
                numtaps,
                cutoff,
                window="hamming",
                pass_zero=response,
                scale=True,
                fs=sample_rate,
            ),
            dtype=np.float64,
        )
        if mode == "causal":
            voltage = np.empty_like(signal.voltage_v)
            state = np.zeros(numtaps - 1)
            for start in range(0, voltage.size, FILTER_BLOCK_SAMPLES):
                checkpoint()
                block, state = scipy_signal.lfilter(
                    taps, [1.0], signal.voltage_v[start:start + FILTER_BLOCK_SAMPLES], zi=state,
                )
                voltage[start:start + len(block)] = block
        else:
            voltage = scipy_signal.filtfilt(
                taps,
                [1.0],
                signal.voltage_v,
                axis=-1,
                padtype="odd",
                padlen=3 * numtaps,
                method="pad",
            )
    except ValueError as exc:
        raise DataError(f"analysis FIR filter failed: {exc}") from exc

    return FirFilterResult(
        signal=_replace_voltage(signal, np.asarray(voltage, dtype=np.float64)),
        taps=taps,
        sample_interval_s=sample_interval,
        scipy_version=scipy_version,
    )


def filter_iir(
    signal: TimeSignal,
    *,
    design: str,
    response: str,
    cutoff_hz: float | Sequence[float],
    order: int,
    mode: str,
    ripple_db: float | None = None,
    attenuation_db: float | None = None,
) -> IirFilterResult:
    if design not in ANALYSIS_IIR_DESIGNS:
        raise DataError(
            "analysis IIR design must be butterworth, chebyshev1, chebyshev2, or elliptic"
        )
    if response not in ANALYSIS_FIR_RESPONSES:
        raise DataError("analysis IIR response must be lowpass, highpass, bandpass, or bandstop")
    if mode not in ANALYSIS_FIR_MODES:
        raise DataError("analysis IIR mode must be causal or zero_phase")
    if (
        isinstance(order, bool)
        or not isinstance(order, int)
        or not 1 <= order <= ANALYSIS_IIR_MAX_ORDER
    ):
        raise DataError(f"analysis IIR order must be an integer from 1 to {ANALYSIS_IIR_MAX_ORDER}")
    ripple, attenuation = _iir_design_parameters(
        design,
        ripple_db=ripple_db,
        attenuation_db=attenuation_db,
    )

    cutoff = _filter_cutoff(response, cutoff_hz, family="IIR")
    sample_interval = _uniform_sample_interval(signal, "analysis IIR filter")
    sample_rate = 1.0 / sample_interval
    nyquist = sample_rate / 2.0
    cutoff_values = [cutoff] if isinstance(cutoff, float) else cutoff
    if any(value >= nyquist for value in cutoff_values):
        raise DataError(
            f"analysis IIR cutoff_hz must be below Nyquist frequency {nyquist:.17g} Hz"
        )

    try:
        from scipy import __version__ as scipy_version
        from scipy import signal as scipy_signal
    except ImportError as exc:  # pragma: no cover - RunService checks this before execution
        raise DataError(
            "analysis IIR filter requires SciPy; install WaveBench with `.[analysis]`"
        ) from exc

    design_kwargs = {
        "btype": response,
        "output": "sos",
        "fs": sample_rate,
    }
    try:
        if design == "butterworth":
            raw_sos = scipy_signal.butter(order, cutoff, **design_kwargs)
        elif design == "chebyshev1":
            raw_sos = scipy_signal.cheby1(order, ripple, cutoff, **design_kwargs)
        elif design == "chebyshev2":
            raw_sos = scipy_signal.cheby2(order, attenuation, cutoff, **design_kwargs)
        else:
            raw_sos = scipy_signal.ellip(order, ripple, attenuation, cutoff, **design_kwargs)
        sos = _validated_sos(raw_sos)
        _, poles, _ = scipy_signal.sos2zpk(sos)
    except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError) as exc:
        raise DataError(f"analysis IIR filter design failed: {exc}") from exc

    if not np.all(np.isfinite(poles)):
        raise DataError("analysis IIR filter design produced non-finite poles")
    max_pole_magnitude = float(np.max(np.abs(poles)))
    if not np.isfinite(max_pole_magnitude) or max_pole_magnitude >= 1.0:
        raise DataError("analysis IIR filter design is not stable")

    padlen = _sos_zero_phase_padlen(sos)
    if mode == "zero_phase" and signal.voltage_v.size <= padlen:
        raise DataError(
            "analysis zero-phase IIR requires at least "
            f"{padlen + 1} samples for {sos.shape[0]} SOS sections"
        )

    try:
        if mode == "causal":
            voltage = np.empty_like(signal.voltage_v)
            state = np.zeros((len(sos), 2))
            for start in range(0, voltage.size, FILTER_BLOCK_SAMPLES):
                checkpoint()
                block, state = scipy_signal.sosfilt(
                    sos, signal.voltage_v[start:start + FILTER_BLOCK_SAMPLES], zi=state,
                )
                voltage[start:start + len(block)] = block
        else:
            voltage = scipy_signal.sosfiltfilt(
                sos,
                signal.voltage_v,
                axis=-1,
                padtype="odd",
                padlen=padlen,
            )
    except (FloatingPointError, OverflowError, ValueError) as exc:
        raise DataError(f"analysis IIR filter execution failed: {exc}") from exc

    return IirFilterResult(
        signal=_replace_voltage(signal, np.asarray(voltage, dtype=np.float64)),
        sos=sos,
        sample_interval_s=sample_interval,
        scipy_version=scipy_version,
        max_pole_magnitude=max_pole_magnitude,
        zero_phase_padlen=padlen,
    )


def fft_signal(signal: TimeSignal) -> FrequencySignal:
    samples = int(signal.voltage_v.size)
    if samples < 4:
        raise DataError("analysis FFT requires at least four samples")
    sample_interval = _uniform_sample_interval(signal, "analysis FFT")
    if not np.isfinite(signal.coherent_gain) or signal.coherent_gain <= 0:
        raise DataError("analysis FFT requires a positive coherent gain")

    spectrum = np.fft.rfft(signal.voltage_v) / (samples * signal.coherent_gain)
    if samples % 2 == 0:
        spectrum[1:-1] *= 2.0
    else:
        spectrum[1:] *= 2.0
    frequencies = np.fft.rfftfreq(samples, d=sample_interval)
    if not np.all(np.isfinite(spectrum)):
        raise DataError("analysis FFT produced non-finite values")
    return FrequencySignal(
        frequency_hz=frequencies,
        spectrum_v=spectrum,
        samples=samples,
        sample_interval_s=sample_interval,
        coherent_gain=signal.coherent_gain,
        window_name=signal.window_name,
    )


def measure_time(signal: TimeSignal, metrics: Iterable[str]) -> dict[str, float]:
    selected = _selected_metrics(metrics, ANALYSIS_TIME_METRICS, "time")
    voltage = signal.voltage_v
    minimum = float(np.min(voltage))
    maximum = float(np.max(voltage))
    scale = float(np.max(np.abs(voltage)))
    rms = 0.0 if scale == 0 else float(scale * np.sqrt(np.mean((voltage / scale) ** 2)))
    values = {
        "voltage_min_v": minimum,
        "voltage_max_v": maximum,
        "voltage_mean_v": float(np.mean(voltage)),
        "voltage_rms_v": rms,
        "voltage_vpp_v": maximum - minimum,
    }
    return {metric: _finite_metric(values[metric], metric) for metric in selected}


def measure_frequency(
    signal: FrequencySignal, metrics: Iterable[str]
) -> tuple[dict[str, float | None], list[str]]:
    selected = _selected_metrics(metrics, ANALYSIS_FREQUENCY_METRICS, "frequency")
    amplitudes = signal.amplitude_v
    non_dc = amplitudes[1:]
    peak_index = int(np.argmax(non_dc) + 1)
    peak_amplitude = float(amplitudes[peak_index])
    significant = peak_amplitude > SIGNIFICANT_PEAK_V
    warnings: list[str] = []

    values: dict[str, float | None] = {}
    if significant:
        peak_frequency = float(signal.frequency_hz[peak_index])
        values["peak_frequency_hz"] = peak_frequency
        values["peak_amplitude_v"] = peak_amplitude
    else:
        peak_frequency = None
        values["peak_frequency_hz"] = None
        values["peak_amplitude_v"] = None
        warnings.append("no_significant_non_dc_peak")

    noise_bins = np.delete(non_dc, peak_index - 1) if significant else non_dc
    if noise_bins.size:
        values["noise_floor_v"] = float(np.median(noise_bins))
    else:
        values["noise_floor_v"] = None
        warnings.append("noise_floor_unavailable")

    requested_orders = {
        order
        for order in range(2, 6)
        if any(metric.startswith(f"harmonic_{order}_") for metric in selected)
    }
    if "thd_ratio" in selected:
        requested_orders.update(range(2, 6))

    harmonic_amplitudes: list[float] = []
    for order in sorted(requested_orders):
        frequency_key = f"harmonic_{order}_frequency_hz"
        amplitude_key = f"harmonic_{order}_amplitude_v"
        if peak_frequency is None:
            values[frequency_key] = None
            values[amplitude_key] = None
            continue
        target = peak_frequency * order
        if target > float(signal.frequency_hz[-1]):
            values[frequency_key] = None
            values[amplitude_key] = None
            warnings.append(f"harmonic_{order}_out_of_band")
            continue
        index = int(np.argmin(np.abs(signal.frequency_hz - target)))
        harmonic_amplitude = float(amplitudes[index])
        values[frequency_key] = float(signal.frequency_hz[index])
        values[amplitude_key] = harmonic_amplitude
        harmonic_amplitudes.append(harmonic_amplitude)

    if "thd_ratio" in selected:
        values["thd_ratio"] = (
            None
            if not significant
            else float(np.sqrt(np.sum(np.square(harmonic_amplitudes))) / peak_amplitude)
        )

    return {
        metric: None if values[metric] is None else _finite_metric(values[metric], metric)
        for metric in selected
    }, warnings


def _replace_voltage(signal: TimeSignal, voltage: np.ndarray) -> TimeSignal:
    if not np.all(np.isfinite(voltage)):
        raise DataError("analysis operation produced non-finite values")
    return TimeSignal(
        time_s=signal.time_s.copy(),
        voltage_v=np.asarray(voltage, dtype=np.float64),
        coherent_gain=signal.coherent_gain,
        window_name=signal.window_name,
    )


def _filter_cutoff(
    response: str,
    cutoff_hz: float | Sequence[float],
    *,
    family: str,
) -> float | list[float]:
    if response in {"lowpass", "highpass"}:
        if isinstance(cutoff_hz, Sequence) and not isinstance(cutoff_hz, (str, bytes)):
            raise DataError(f"analysis {family} {response} cutoff_hz must be a positive number")
        return _positive_finite(cutoff_hz, f"analysis {family} cutoff_hz")

    if (
        not isinstance(cutoff_hz, Sequence)
        or isinstance(cutoff_hz, (str, bytes))
        or len(cutoff_hz) != 2
    ):
        raise DataError(f"analysis {family} {response} cutoff_hz must contain two frequencies")
    values = [_positive_finite(value, f"analysis {family} cutoff_hz") for value in cutoff_hz]
    if values[1] <= values[0]:
        raise DataError(f"analysis {family} cutoff_hz must be strictly increasing")
    return values


def _iir_design_parameters(
    design: str,
    *,
    ripple_db: float | None,
    attenuation_db: float | None,
) -> tuple[float | None, float | None]:
    needs_ripple = design in {"chebyshev1", "elliptic"}
    needs_attenuation = design in {"chebyshev2", "elliptic"}
    if needs_ripple != (ripple_db is not None):
        requirement = "requires" if needs_ripple else "does not accept"
        raise DataError(f"analysis IIR {design} {requirement} ripple_db")
    if needs_attenuation != (attenuation_db is not None):
        requirement = "requires" if needs_attenuation else "does not accept"
        raise DataError(f"analysis IIR {design} {requirement} attenuation_db")

    ripple = (
        _bounded_positive_finite(
            ripple_db,
            "analysis IIR ripple_db",
            maximum=ANALYSIS_IIR_MAX_RIPPLE_DB,
        )
        if ripple_db is not None
        else None
    )
    attenuation = (
        _bounded_positive_finite(
            attenuation_db,
            "analysis IIR attenuation_db",
            maximum=ANALYSIS_IIR_MAX_ATTENUATION_DB,
        )
        if attenuation_db is not None
        else None
    )
    if design == "elliptic" and ripple is not None and attenuation is not None:
        if ripple >= attenuation:
            raise DataError("analysis IIR elliptic ripple_db must be less than attenuation_db")
    return ripple, attenuation


def _bounded_positive_finite(value: Any, name: str, *, maximum: float) -> float:
    result = _positive_finite(value, name)
    if result > maximum:
        raise DataError(f"{name} must be <= {maximum:g}")
    return result


def _validated_sos(value: Any) -> np.ndarray:
    try:
        sos = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise DataError("analysis IIR filter design must produce numeric SOS coefficients") from exc
    if sos.ndim != 2 or sos.shape[0] < 1 or sos.shape[1] != 6:
        raise DataError("analysis IIR filter design must produce an Nx6 SOS array")
    if not np.all(np.isfinite(sos)):
        raise DataError("analysis IIR filter design produced non-finite SOS coefficients")
    if not np.array_equal(sos[:, 3], np.ones(sos.shape[0])):
        raise DataError("analysis IIR filter SOS denominators must have a0 = 1")
    return sos


def _sos_zero_phase_padlen(sos: np.ndarray) -> int:
    zeros_at_origin = int(np.count_nonzero(sos[:, 2] == 0.0))
    poles_at_origin = int(np.count_nonzero(sos[:, 5] == 0.0))
    return 3 * (2 * sos.shape[0] + 1 - min(zeros_at_origin, poles_at_origin))


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise DataError(f"{name} must be a positive finite number")
    result = float(value)
    if not np.isfinite(result) or result <= 0:
        raise DataError(f"{name} must be a positive finite number")
    return result


def _uniform_sample_interval(signal: TimeSignal, operation: str) -> float:
    if signal.time_s.size < 2:
        raise DataError(f"{operation} requires at least two samples")
    intervals = np.diff(signal.time_s)
    sample_interval = float(np.median(intervals))
    if (
        not np.isfinite(sample_interval)
        or sample_interval <= 0
        or not np.allclose(intervals, sample_interval, rtol=1e-6, atol=0.0)
    ):
        raise DataError(f"{operation} requires uniformly sampled data")
    return sample_interval


def _selected_metrics(
    metrics: Iterable[str], allowed: frozenset[str], domain: str
) -> list[str]:
    selected = list(metrics)
    unsupported = [metric for metric in selected if metric not in allowed]
    if unsupported:
        raise DataError(f"unsupported {domain}-domain metric: {unsupported[0]}")
    if len(set(selected)) != len(selected):
        raise DataError(f"duplicate {domain}-domain metric")
    return selected


def _finite_metric(value: float, name: str) -> float:
    result = float(value)
    if not np.isfinite(result):
        raise DataError(f"analysis metric {name} is not finite")
    return result
