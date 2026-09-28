from __future__ import annotations

from typing import Any

import numpy as np


def analyze_fft(waveform: Any, *, max_harmonic_order: int = 5) -> dict[str, Any]:
    data = np.asarray(waveform, dtype=float)
    if data.ndim != 2 or data.shape[1] < 2:
        raise ValueError("expected an Nx2 waveform array")
    finite = np.isfinite(data[:, 0]) & np.isfinite(data[:, 1])
    data = data[finite]
    if data.shape[0] < 4:
        raise ValueError("need at least four finite waveform samples")
    time_s = data[:, 0]
    voltage_v = data[:, 1]
    dt = np.diff(time_s)
    positive_dt = dt[dt > 0]
    if positive_dt.size == 0:
        raise ValueError("waveform time axis must be increasing")
    median_dt = float(np.median(positive_dt))
    if median_dt <= 0:
        raise ValueError("waveform sample interval must be positive")
    warnings: list[str] = []
    if not np.all(dt > 0):
        warnings.append("non_monotonic_time_axis")
    if np.max(np.abs(dt - median_dt)) > median_dt * 0.01:
        warnings.append("non_uniform_sample_interval")
    samples = int(voltage_v.size)
    centered = voltage_v - float(np.mean(voltage_v))
    window = np.hanning(samples)
    coherent_gain = float(np.mean(window))
    if coherent_gain <= 0:
        raise ValueError("invalid FFT window")
    spectrum = np.fft.rfft(centered * window)
    frequencies = np.fft.rfftfreq(samples, d=median_dt)
    amplitudes = np.abs(spectrum) * 2.0 / (samples * coherent_gain)
    if amplitudes.size:
        amplitudes[0] = amplitudes[0] / 2.0
    if amplitudes.size < 2:
        raise ValueError("not enough FFT bins")
    search = amplitudes[1:]
    peak_index = int(np.argmax(search) + 1)
    peak_frequency = float(frequencies[peak_index])
    peak_amplitude = float(amplitudes[peak_index])
    noise_bins = np.delete(amplitudes[1:], max(peak_index - 1, 0))
    noise_floor = float(np.median(noise_bins)) if noise_bins.size else 0.0
    harmonics = fft_harmonics(frequencies, amplitudes, peak_frequency, max_order=max_harmonic_order)
    harmonic_power = sum(item["amplitude_v"] ** 2 for item in harmonics)
    thd = (harmonic_power ** 0.5 / peak_amplitude) if peak_amplitude > 0 and harmonics else None
    result: dict[str, Any] = {
        "window": "hann",
        "samples": samples,
        "sample_rate_hz": 1.0 / median_dt,
        "resolution_hz": float(frequencies[1] - frequencies[0]) if frequencies.size > 1 else 0.0,
        "peak_frequency_hz": peak_frequency,
        "peak_amplitude_v": peak_amplitude,
        "noise_floor_v": noise_floor,
        "harmonics": harmonics,
        "thd_ratio": thd,
        "warnings": warnings,
    }
    for harmonic in harmonics:
        order = int(harmonic["order"])
        result[f"harmonic_{order}_frequency_hz"] = harmonic["frequency_hz"]
        result[f"harmonic_{order}_amplitude_v"] = harmonic["amplitude_v"]
    return result


def fft_harmonics(
    frequencies: Any, amplitudes: Any, fundamental_hz: float, *, max_order: int = 5
) -> list[dict[str, float]]:
    if fundamental_hz <= 0:
        return []
    result: list[dict[str, float]] = []
    max_frequency = float(frequencies[-1])
    if max_order < 2:
        return []
    for order in range(2, max_order + 1):
        target = fundamental_hz * order
        if target > max_frequency:
            break
        index = int(np.argmin(np.abs(frequencies - target)))
        result.append(
            {
                "order": float(order),
                "frequency_hz": float(frequencies[index]),
                "amplitude_v": float(amplitudes[index]),
            }
        )
    return result
