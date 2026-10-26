from __future__ import annotations

import math
from itertools import combinations
from typing import Any

import numpy as np

from wavebench.instruments.models import WaveformData


def analyze_waveform_relationships(
    waveforms: dict[int, WaveformData],
    *,
    same_acquisition: bool = False,
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
    same_acquisition: bool = False,
    max_correlation_points: int = 4096,
    max_intersections: int = 64,
) -> dict[str, Any]:
    """Summarize waveforms; timing requires an explicit shared-acquisition assertion.

    This helper does not validate capture provenance. Real capture timing analysis
    must use the validated synchronization contract in ``pair_analysis``.
    """
    if not isinstance(same_acquisition, bool):
        raise ValueError("same_acquisition must be a boolean")
    left_summary = left.summary()
    right_summary = right.summary()
    warnings: list[str] = []
    if same_acquisition:
        common = _common_time_axis(left, right, max_points=max_correlation_points)
        correlation = _correlation_payload(common, warnings=warnings)
        intersections = _intersection_payload(
            common,
            warnings=warnings,
            max_intersections=max_intersections,
        )
        common_time = {**common["metadata"], "same_acquisition": True}
    else:
        # 跨 acquisition 的两个通道没有共同时间基准：相关性、交点、相位、延迟都不成立，
        # 只看同步无关的频率比和幅度/均值关系。
        warnings.append("not_same_acquisition_timing_relationships_skipped")
        correlation = _skipped_analysis("not_same_acquisition")
        intersections = _skipped_analysis("not_same_acquisition")
        common_time = {
            "overlap": None,
            "x_start_s": None,
            "x_stop_s": None,
            "duration_s": None,
            "samples": 0,
            "same_acquisition": False,
        }
    left_frequency = _trusted_frequency(left_summary, warnings=warnings, label=f"CH{left.channel}")
    right_frequency = _trusted_frequency(right_summary, warnings=warnings, label=f"CH{right.channel}")
    frequency_ratio = None
    phase_degrees = None
    if left_frequency is not None and right_frequency is not None:
        lower = min(left_frequency, right_frequency)
        upper = max(left_frequency, right_frequency)
        if lower > 0:
            frequency_ratio = float(upper / lower)
        if not same_acquisition:
            pass
        elif (
            abs(left_frequency - right_frequency) / max(left_frequency, right_frequency) <= 0.01
            and common_time.get("overlap") is True
        ):
            # 约定：phase_degrees_at_left_frequency 表示 right 相对 left 的相位滞后，取值 [0, 360)。
            # 用基波拟合相位差而不是相关峰 lag：后者对截断窗口和幅度不对称有系统偏差，
            # 且直接反相（right = -left）会被绝对值最大化吃掉 180°。
            phase_degrees = _fundamental_phase_degrees(common, frequency_hz=left_frequency)
        elif frequency_ratio is not None and abs(frequency_ratio - 1.0) > 0.01:
            warnings.append("phase_not_meaningful_for_different_frequencies")
    return {
        "channels": [left.channel, right.channel],
        "left_channel": left.channel,
        "right_channel": right.channel,
        "common_time": common_time,
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


def _skipped_analysis(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


def _fundamental_phase_degrees(common: dict[str, Any], *, frequency_hz: float) -> float | None:
    """在 common_time 上拟合基波，返回 right 相对 left 的相位滞后（度，[0, 360)）。"""
    times = common["time_s"]
    left = np.asarray(common["left_v"], dtype=np.float64)
    right = np.asarray(common["right_v"], dtype=np.float64)
    if frequency_hz <= 0 or times.size < 8:
        return None
    phase_left = _single_bin_phase(times, left, frequency_hz)
    phase_right = _single_bin_phase(times, right, frequency_hz)
    if phase_left is None or phase_right is None:
        return None
    return float((-math.degrees(_wrap_angle(phase_right - phase_left))) % 360.0)


def _single_bin_phase(times: np.ndarray, values: np.ndarray, frequency_hz: float) -> float | None:
    # 非整数周期窗口内常数、cos、sin 不正交，必须联合拟合以消除 DC 泄漏。
    angle = 2.0 * math.pi * frequency_hz * (times - times[0])
    basis = np.column_stack((np.ones_like(angle), np.cos(angle), np.sin(angle)))
    coefficients, _, rank, _ = np.linalg.lstsq(basis, values, rcond=None)
    _, cosine, sine = coefficients
    tolerance = np.finfo(np.float64).eps * max(float(np.max(np.abs(values))), 1.0) * 8
    if rank < 3 or math.hypot(cosine, sine) <= tolerance:
        return None
    return math.atan2(-sine, cosine)


def _wrap_angle(angle: float) -> float:
    wrapped = (angle + math.pi) % (2.0 * math.pi)
    return wrapped - math.pi


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
