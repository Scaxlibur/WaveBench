from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TwoLevelWaveform:
    low_level_v: float
    high_level_v: float
    center_v: float


def estimate_two_level_waveform(voltages_v: np.ndarray) -> TwoLevelWaveform | None:
    if voltages_v.size < 8:
        return None
    v_min = float(np.min(voltages_v))
    v_max = float(np.max(voltages_v))
    span = v_max - v_min
    if span <= 0:
        return None
    center = (v_max + v_min) / 2.0
    low_mask = voltages_v <= center - 0.25 * span
    high_mask = voltages_v >= center + 0.25 * span
    low_fraction = float(np.mean(low_mask))
    high_fraction = float(np.mean(high_mask))
    if low_fraction < 0.05 or high_fraction < 0.05:
        return None
    if low_fraction + high_fraction < 0.8:
        return None
    low_level = float(np.median(voltages_v[low_mask]))
    high_level = float(np.median(voltages_v[high_mask]))
    if not high_level > low_level:
        return None
    return TwoLevelWaveform(low_level_v=low_level, high_level_v=high_level, center_v=(low_level + high_level) / 2.0)


def estimate_duty_cycle(voltages_v: np.ndarray, levels: TwoLevelWaveform | None) -> float | None:
    if levels is None or voltages_v.size == 0:
        return None
    return float(np.mean(voltages_v >= levels.center_v))


def interpolate_crossing_time(t0: float, v0: float, t1: float, v1: float, threshold: float) -> float:
    if v1 == v0:
        return t0
    fraction = (threshold - v0) / (v1 - v0)
    return float(t0 + fraction * (t1 - t0))


def estimate_transition_time(
    times_s: np.ndarray, voltages_v: np.ndarray, levels: TwoLevelWaveform | None, *, rising: bool
) -> float | None:
    if levels is None or times_s.size != voltages_v.size or times_s.size < 3:
        return None
    span = levels.high_level_v - levels.low_level_v
    if span <= 0:
        return None
    start_threshold = levels.low_level_v + 0.1 * span if rising else levels.low_level_v + 0.9 * span
    end_threshold = levels.low_level_v + 0.9 * span if rising else levels.low_level_v + 0.1 * span
    durations: list[float] = []
    i = 1
    while i < times_s.size:
        prev_v = float(voltages_v[i - 1])
        v = float(voltages_v[i])
        crosses_start = (prev_v < start_threshold <= v) if rising else (prev_v > start_threshold >= v)
        if not crosses_start:
            i += 1
            continue
        t_start = interpolate_crossing_time(float(times_s[i - 1]), prev_v, float(times_s[i]), v, start_threshold)
        j = i
        found = False
        while j < times_s.size:
            prev2_v = float(voltages_v[j - 1])
            v2 = float(voltages_v[j])
            crosses_end = (prev2_v < end_threshold <= v2) if rising else (prev2_v > end_threshold >= v2)
            if crosses_end:
                t_end = interpolate_crossing_time(float(times_s[j - 1]), prev2_v, float(times_s[j]), v2, end_threshold)
                if t_end >= t_start:
                    durations.append(float(t_end - t_start))
                found = True
                break
            if rising and v2 < start_threshold:
                break
            if (not rising) and v2 > start_threshold:
                break
            j += 1
        i = j + 1 if found else i + 1
    if not durations:
        return None
    return float(np.median(np.asarray(durations, dtype=np.float64)))


@dataclass(frozen=True)
class WaveformQuality:
    voltage_min_v: float
    voltage_max_v: float
    voltage_mean_v: float
    voltage_rms_v: float
    voltage_vpp_v: float
    frequency_estimate_hz: float | None
    frequency_method: str
    estimated_cycles: float | None
    duty_cycle: float | None
    rise_time_s: float | None
    fall_time_s: float | None
    expected_frequency_hz: float | None
    frequency_error_ratio: float | None
    frequency_in_tolerance: bool | None
    points_per_cycle: float | None
    quality_warnings: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "voltage_min_v": self.voltage_min_v,
            "voltage_max_v": self.voltage_max_v,
            "voltage_mean_v": self.voltage_mean_v,
            "voltage_rms_v": self.voltage_rms_v,
            "voltage_vpp_v": self.voltage_vpp_v,
            "frequency_estimate_hz": self.frequency_estimate_hz,
            "frequency_method": self.frequency_method,
            "estimated_cycles": self.estimated_cycles,
            "duty_cycle": self.duty_cycle,
            "rise_time_s": self.rise_time_s,
            "fall_time_s": self.fall_time_s,
            "expected_frequency_hz": self.expected_frequency_hz,
            "frequency_error_ratio": self.frequency_error_ratio,
            "frequency_in_tolerance": self.frequency_in_tolerance,
            "points_per_cycle": self.points_per_cycle,
            "quality_warnings": self.quality_warnings,
        }


def estimate_frequency_hysteresis(times_s: np.ndarray, voltages_v: np.ndarray) -> float | None:
    if times_s.size != voltages_v.size or times_s.size < 3:
        return None
    v_min = float(np.min(voltages_v))
    v_max = float(np.max(voltages_v))
    span = v_max - v_min
    if span <= 0:
        return None

    center = (v_max + v_min) / 2.0
    hysteresis = span * 0.10
    low = center - hysteresis
    armed = bool(voltages_v[0] < low)
    crossings: list[float] = []
    prev_v = float(voltages_v[0])
    prev_t = float(times_s[0])

    for time_s, voltage_v in zip(times_s[1:], voltages_v[1:]):
        v = float(voltage_v)
        t = float(time_s)
        if not armed:
            if v < low:
                armed = True
        elif prev_v < center <= v:
            fraction = (center - prev_v) / (v - prev_v) if v != prev_v else 0.0
            crossings.append(prev_t + fraction * (t - prev_t))
            armed = False
        prev_v = v
        prev_t = t

    if len(crossings) < 2:
        return None
    periods = np.diff(np.asarray(crossings, dtype=np.float64))
    periods = periods[periods > 0]
    if periods.size == 0:
        return None
    return float(1.0 / np.median(periods))


def estimate_frequency_fft(times_s: np.ndarray, voltages_v: np.ndarray, max_points: int = 262_144) -> float | None:
    if times_s.size != voltages_v.size or times_s.size < 4:
        return None
    step = max(1, int(np.ceil(times_s.size / max_points)))
    sampled_times = times_s[::step]
    sampled_voltages = voltages_v[::step]
    if sampled_times.size < 4:
        return None
    dt = float(np.median(np.diff(sampled_times)))
    if dt <= 0:
        return None
    centered = sampled_voltages - np.mean(sampled_voltages)
    if float(np.max(np.abs(centered))) <= 1e-12:
        return None
    spectrum = np.fft.rfft(centered)
    magnitudes = np.abs(spectrum)
    if magnitudes.size <= 1:
        return None
    peak_magnitude = float(np.max(magnitudes[1:]))
    if peak_magnitude <= 1e-12:
        return None
    peak_index = int(np.argmax(magnitudes[1:]) + 1)
    frequencies = np.fft.rfftfreq(sampled_times.size, dt)
    frequency = float(frequencies[peak_index])
    return frequency if frequency > 0 else None


def estimate_cycles_in_window(times_s: np.ndarray, frequency_hz: float | None) -> float | None:
    if frequency_hz is None or times_s.size < 2:
        return None
    dt = float(np.median(np.diff(times_s)))
    if dt <= 0:
        return None
    duration = float(times_s[-1] - times_s[0] + dt)
    if duration <= 0:
        return None
    return float(frequency_hz * duration)


def frequency_error_ratio(frequency_hz: float | None, expected_frequency_hz: float | None) -> float | None:
    if frequency_hz is None or expected_frequency_hz is None or expected_frequency_hz <= 0:
        return None
    return float(abs(frequency_hz - expected_frequency_hz) / expected_frequency_hz)


def quality_warnings(
    *,
    estimated_cycles: float | None,
    frequency_hz: float | None,
    frequency_error: float | None,
    expected_frequency_hz: float | None,
    tolerance_ratio: float,
    sample_count: int,
    voltage_vpp_v: float,
    min_signal_vpp: float = 0.02,
) -> list[str]:
    warnings: list[str] = []
    frequency_required = expected_frequency_hz is not None
    dynamic_signal = frequency_required or frequency_hz is not None
    if frequency_hz is None and frequency_required:
        warnings.append("frequency_unavailable: waveform frequency could not be estimated")
    if estimated_cycles is not None and estimated_cycles < 2.0:
        warnings.append("low_cycle_count: waveform window contains fewer than 2 estimated cycles; frequency estimate may be unreliable")
    if estimated_cycles is not None and estimated_cycles > 0:
        points_per_cycle = sample_count / estimated_cycles
        if points_per_cycle < 20.0:
            warnings.append("low_points_per_cycle: waveform has fewer than 20 samples per estimated cycle; duty and edge metrics may be unreliable")
    if dynamic_signal and voltage_vpp_v < min_signal_vpp:
        warnings.append(
            "low_signal_amplitude: waveform Vpp is below "
            f"{min_signal_vpp * 1e3:g} mV; check channel scale, probe, or signal connection"
        )
    if frequency_error is not None and frequency_error > tolerance_ratio:
        warnings.append("frequency_mismatch: estimated frequency differs from expected frequency")
    return warnings


def summarize_waveform(
    times_s: np.ndarray,
    voltages_v: np.ndarray,
    *,
    expected_frequency_hz: float | None = None,
    frequency_tolerance_ratio: float = 0.05,
    min_signal_vpp: float = 0.02,
) -> WaveformQuality:
    frequency = estimate_frequency_hysteresis(times_s, voltages_v)
    method = "hysteresis_rising_crossing"
    if frequency is None:
        frequency = estimate_frequency_fft(times_s, voltages_v)
        method = "fft_peak"
    estimated_cycles = estimate_cycles_in_window(times_s, frequency)
    levels = estimate_two_level_waveform(voltages_v)
    duty_cycle = estimate_duty_cycle(voltages_v, levels)
    rise_time_s = estimate_transition_time(times_s, voltages_v, levels, rising=True)
    fall_time_s = estimate_transition_time(times_s, voltages_v, levels, rising=False)
    freq_error = frequency_error_ratio(frequency, expected_frequency_hz)
    voltage_min_v = float(np.min(voltages_v))
    voltage_max_v = float(np.max(voltages_v))
    voltage_vpp_v = voltage_max_v - voltage_min_v
    points_per_cycle = None
    if estimated_cycles is not None and estimated_cycles > 0:
        points_per_cycle = float(voltages_v.size / estimated_cycles)
    return WaveformQuality(
        voltage_min_v=voltage_min_v,
        voltage_max_v=voltage_max_v,
        voltage_mean_v=float(np.mean(voltages_v)),
        voltage_rms_v=float(np.sqrt(np.mean(np.square(voltages_v)))),
        voltage_vpp_v=voltage_vpp_v,
        frequency_estimate_hz=frequency,
        frequency_method=method,
        estimated_cycles=estimated_cycles,
        duty_cycle=duty_cycle,
        rise_time_s=rise_time_s,
        fall_time_s=fall_time_s,
        expected_frequency_hz=expected_frequency_hz,
        frequency_error_ratio=freq_error,
        frequency_in_tolerance=None if freq_error is None else freq_error <= frequency_tolerance_ratio,
        points_per_cycle=points_per_cycle,
        quality_warnings=quality_warnings(
            estimated_cycles=estimated_cycles,
            frequency_hz=frequency,
            frequency_error=freq_error,
            expected_frequency_hz=expected_frequency_hz,
            tolerance_ratio=frequency_tolerance_ratio,
            sample_count=int(voltages_v.size),
            voltage_vpp_v=voltage_vpp_v,
            min_signal_vpp=min_signal_vpp,
        ),
    )
