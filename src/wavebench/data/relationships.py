from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np

from wavebench.instruments.models import WaveformData


def analyze_waveform_relationships(
    waveforms: dict[int, WaveformData],
    *,
    same_acquisition: bool = True,
    max_correlation_points: int = 4096,
    max_intersections: int = 64,
) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for left_channel, right_channel in combinations(sorted(waveforms), 2):
        relationships.append(
            analyze_waveform_pair(
                waveforms[left_channel],
                waveforms[right_channel],
                same_acquisition=same_acquisition,
                max_correlation_points=max_correlation_points,
                max_intersections=max_intersections,
            )
        )
    return relationships


def analyze_waveform_pair(
    left: WaveformData,
    right: WaveformData,
    *,
    same_acquisition: bool = True,
    max_correlation_points: int = 4096,
    max_intersections: int = 64,
) -> dict[str, Any]:
    left_summary = left.summary()
    right_summary = right.summary()
    warnings: list[str] = []
    common = _common_time_axis(left, right, max_points=max_correlation_points)
    correlation = _correlation_payload(common, warnings=warnings)
    intersections = _intersection_payload(
        common,
        warnings=warnings,
        max_intersections=max_intersections,
    )
    if not same_acquisition:
        warnings.append("not_same_acquisition_timing_relationships_are_advisory")
    left_frequency = _trusted_frequency(left_summary, warnings=warnings, label=f"CH{left.channel}")
    right_frequency = _trusted_frequency(right_summary, warnings=warnings, label=f"CH{right.channel}")
    frequency_ratio = None
    phase_degrees = None
    if left_frequency is not None and right_frequency is not None:
        lower = min(left_frequency, right_frequency)
        upper = max(left_frequency, right_frequency)
        if lower > 0:
            frequency_ratio = float(upper / lower)
        if (
            same_acquisition
            and
            correlation.get("lag_at_max_correlation_s") is not None
            and abs(left_frequency - right_frequency) / max(left_frequency, right_frequency) <= 0.01
        ):
            phase_degrees = float(
                (correlation["lag_at_max_correlation_s"] * left_frequency * 360.0) % 360.0
            )
        elif frequency_ratio is not None and abs(frequency_ratio - 1.0) > 0.01:
            warnings.append("phase_not_meaningful_for_different_frequencies")
    return {
        "channels": [left.channel, right.channel],
        "left_channel": left.channel,
        "right_channel": right.channel,
        "common_time": {**common["metadata"], "same_acquisition": same_acquisition},
        "frequency": {
            "left_hz": left_frequency,
            "right_hz": right_frequency,
            "ratio_high_over_low": frequency_ratio,
        },
        "voltage": {
            "left_vpp_v": left_summary["voltage_vpp_v"],
            "right_vpp_v": right_summary["voltage_vpp_v"],
            "vpp_ratio_right_over_left": _safe_ratio(
                right_summary["voltage_vpp_v"],
                left_summary["voltage_vpp_v"],
            ),
            "mean_delta_right_minus_left_v": float(
                right_summary["voltage_mean_v"] - left_summary["voltage_mean_v"]
            ),
            "rms_ratio_right_over_left": _safe_ratio(
                right_summary["voltage_rms_v"],
                left_summary["voltage_rms_v"],
            ),
        },
        "correlation": correlation,
        "intersections": intersections,
        "phase_degrees_at_left_frequency": phase_degrees,
        "warnings": warnings,
    }


def _common_time_axis(
    left: WaveformData,
    right: WaveformData,
    *,
    max_points: int,
) -> dict[str, Any]:
    left_times = left.times_s
    right_times = right.times_s
    start = max(float(left_times[0]), float(right_times[0]))
    stop = min(float(left_times[-1]), float(right_times[-1]))
    if stop <= start:
        return {
            "time_s": np.array([], dtype=np.float64),
            "left_v": np.array([], dtype=np.float64),
            "right_v": np.array([], dtype=np.float64),
            "metadata": {
                "overlap": False,
                "x_start_s": start,
                "x_stop_s": stop,
                "duration_s": 0.0,
                "samples": 0,
            },
        }
    left_dt = left.header.x_increment
    right_dt = right.header.x_increment
    dt = max(value for value in (left_dt, right_dt) if value > 0)
    count = int(np.floor((stop - start) / dt)) + 1
    count = max(2, min(count, max_points))
    common_times = np.linspace(start, stop, count, dtype=np.float64)
    return {
        "time_s": common_times,
        "left_v": np.interp(common_times, left_times, left.voltages_v),
        "right_v": np.interp(common_times, right_times, right.voltages_v),
        "metadata": {
            "overlap": True,
            "x_start_s": start,
            "x_stop_s": stop,
            "duration_s": float(stop - start),
            "samples": count,
        },
    }


def _correlation_payload(common: dict[str, Any], *, warnings: list[str]) -> dict[str, Any]:
    times = common["time_s"]
    if times.size < 4:
        warnings.append("insufficient_common_time_overlap")
        return {
            "normalized_pearson": None,
            "max_cross_correlation": None,
            "max_abs_cross_correlation": None,
            "lag_at_max_correlation_s": None,
        }
    left = _normalize(common["left_v"])
    right = _normalize(common["right_v"])
    if left is None or right is None:
        warnings.append("correlation_unavailable_for_flat_signal")
        return {
            "normalized_pearson": None,
            "max_cross_correlation": None,
            "max_abs_cross_correlation": None,
            "lag_at_max_correlation_s": None,
        }
    pearson = float(np.mean(left * right))
    correlation = np.correlate(right, left, mode="full") / left.size
    index = int(np.argmax(np.abs(correlation)))
    lag_samples = index - (left.size - 1)
    dt = float(np.median(np.diff(times)))
    return {
        "normalized_pearson": pearson,
        "max_cross_correlation": float(correlation[index]),
        "max_abs_cross_correlation": float(abs(correlation[index])),
        "lag_at_max_correlation_s": float(lag_samples * dt),
    }


def _intersection_payload(
    common: dict[str, Any],
    *,
    warnings: list[str],
    max_intersections: int,
) -> dict[str, Any]:
    times = common["time_s"]
    left = common["left_v"]
    right = common["right_v"]
    if times.size < 2:
        return {
            "mode": "none",
            "count": 0,
            "returned": 0,
            "truncated": False,
            "points": [],
        }
    diff = left - right
    tolerance = max(float(np.max(np.abs(diff))) * 1e-9, 1e-12)
    if bool(np.all(np.abs(diff) <= tolerance)):
        warnings.append("waveforms_coincident_intersections_unbounded")
        return {
            "mode": "coincident",
            "count": None,
            "returned": 0,
            "truncated": False,
            "points": [],
        }
    points: list[dict[str, float | str]] = []
    count = 0
    last_time: float | None = None
    for index in range(diff.size - 1):
        d0 = float(diff[index])
        d1 = float(diff[index + 1])
        t0 = float(times[index])
        t1 = float(times[index + 1])
        if abs(d0) <= tolerance:
            alpha = 0.0
        elif d0 * d1 < 0.0:
            alpha = -d0 / (d1 - d0)
        else:
            continue
        crossing_time = t0 + alpha * (t1 - t0)
        if last_time is not None and abs(crossing_time - last_time) <= max(abs(t1 - t0) * 0.5, 1e-15):
            continue
        left_value = float(left[index] + alpha * (left[index + 1] - left[index]))
        right_value = float(right[index] + alpha * (right[index + 1] - right[index]))
        left_slope = _segment_slope(left, times, index)
        right_slope = _segment_slope(right, times, index)
        delta_slope = left_slope - right_slope
        count += 1
        last_time = crossing_time
        if len(points) < max_intersections:
            points.append(
                {
                    "time_s": float(crossing_time),
                    "voltage_v": float((left_value + right_value) / 2.0),
                    "left_slope_v_per_s": float(left_slope),
                    "right_slope_v_per_s": float(right_slope),
                    "delta_slope_v_per_s": float(delta_slope),
                    "direction": (
                        "left_minus_right_rising"
                        if delta_slope > 0
                        else "left_minus_right_falling"
                        if delta_slope < 0
                        else "tangent_or_flat"
                    ),
                }
            )
    truncated = count > len(points)
    if truncated:
        warnings.append("intersections_truncated")
    return {
        "mode": "finite",
        "count": count,
        "returned": len(points),
        "truncated": truncated,
        "points": points,
    }


def _segment_slope(values: np.ndarray, times: np.ndarray, index: int) -> float:
    dt = float(times[index + 1] - times[index])
    if abs(dt) <= 1e-18:
        return 0.0
    return float((values[index + 1] - values[index]) / dt)


def _normalize(values: np.ndarray) -> np.ndarray | None:
    centered = values.astype(np.float64) - float(np.mean(values))
    rms = float(np.sqrt(np.mean(np.square(centered))))
    if rms <= 1e-12:
        return None
    return centered / rms


def _trusted_frequency(summary: dict[str, object], *, warnings: list[str], label: str) -> float | None:
    frequency = summary.get("frequency_estimate_hz")
    if not isinstance(frequency, (int, float)) or frequency <= 0:
        warnings.append(f"{label}_frequency_unavailable")
        return None
    quality_warnings = summary.get("quality_warnings", [])
    if any(str(item).startswith("low_cycle_count") for item in quality_warnings):
        warnings.append(f"{label}_frequency_low_confidence")
        return None
    return float(frequency)


def _safe_ratio(numerator: object, denominator: object) -> float | None:
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if abs(float(denominator)) <= 1e-18:
        return None
    return float(numerator) / float(denominator)
