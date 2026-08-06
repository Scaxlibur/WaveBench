from __future__ import annotations

from typing import Any

import numpy as np

from wavebench.instruments.models import WaveformData


def evaluate_waveform_expectation(
    waveform: WaveformData,
    expectation: dict[str, Any],
) -> dict[str, Any]:
    summary = waveform.summary(
        expected_frequency_hz=_optional_positive_float(expectation, "frequency_hz"),
        frequency_tolerance_ratio=float(expectation.get("frequency_tolerance_ratio", 0.05)),
    )
    checks: list[dict[str, Any]] = []
    _check_frequency(summary, expectation, checks)
    _check_vpp(summary, expectation, checks)
    _check_mean(summary, expectation, checks)
    _check_duty(summary, expectation, checks)
    _check_symmetry(waveform, expectation, checks)
    statuses = {check["status"] for check in checks}
    if "fail" in statuses:
        status = "fail"
    elif "warn" in statuses:
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "channel": waveform.channel,
        "label": expectation.get("label"),
        "shape": expectation.get("shape"),
        "checks": checks,
    }


def expectation_summary(results: dict[int, dict[str, Any]]) -> dict[str, Any]:
    statuses = {result["status"] for result in results.values()}
    if "fail" in statuses:
        status = "fail"
    elif "warn" in statuses:
        status = "warn"
    else:
        status = "pass" if results else "skipped"
    return {
        "status": status,
        "channels": {str(channel): result["status"] for channel, result in sorted(results.items())},
    }


def estimate_triangle_symmetry_percent(waveform: WaveformData) -> float | None:
    times = waveform.times_s
    values = np.asarray(waveform.voltages_v, dtype=np.float64)
    if times.size != values.size or values.size < 8:
        return None
    span = float(np.max(values) - np.min(values))
    if span <= 1e-12:
        return None
    centered = values - float(np.mean(values))
    diffs = np.diff(centered)
    if diffs.size < 3:
        return None
    signs = np.sign(diffs)
    for index in range(1, signs.size):
        if signs[index] == 0:
            signs[index] = signs[index - 1]
    maxima = [
        index
        for index in range(1, values.size - 1)
        if signs[index - 1] > 0 and signs[index] < 0
    ]
    minima = [
        index
        for index in range(1, values.size - 1)
        if signs[index - 1] < 0 and signs[index] > 0
    ]
    fractions: list[float] = []
    for left_min, right_min in zip(minima, minima[1:]):
        if right_min <= left_min:
            continue
        peaks = [index for index in maxima if left_min < index < right_min]
        if not peaks:
            continue
        peak = max(peaks, key=lambda index: values[index])
        period = float(times[right_min] - times[left_min])
        if period <= 0:
            continue
        fractions.append(float((times[peak] - times[left_min]) / period * 100.0))
    if not fractions:
        return None
    return float(np.median(np.asarray(fractions, dtype=np.float64)))


def _check_frequency(
    summary: dict[str, Any],
    expectation: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    expected = _optional_positive_float(expectation, "frequency_hz")
    if expected is None:
        return
    actual = summary.get("frequency_estimate_hz")
    tolerance = float(expectation.get("frequency_tolerance_ratio", 0.05))
    low_confidence = any(
        str(item).startswith("low_cycle_count")
        for item in summary.get("quality_warnings", [])
    )
    if not isinstance(actual, (int, float)) or actual <= 0:
        checks.append(_check("frequency_hz", "warn", expected, actual, "frequency unavailable"))
        return
    error_ratio = abs(float(actual) - expected) / expected
    if low_confidence:
        checks.append(
            _check(
                "frequency_hz",
                "warn",
                expected,
                float(actual),
                "frequency low confidence because waveform contains too few cycles",
                error_ratio=error_ratio,
                tolerance_ratio=tolerance,
            )
        )
        return
    checks.append(
        _check(
            "frequency_hz",
            "pass" if error_ratio <= tolerance else "fail",
            expected,
            float(actual),
            "ok" if error_ratio <= tolerance else "frequency out of tolerance",
            error_ratio=error_ratio,
            tolerance_ratio=tolerance,
        )
    )


def _check_vpp(
    summary: dict[str, Any],
    expectation: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    expected = _optional_positive_float(expectation, "vpp_v")
    if expected is None:
        return
    actual = summary.get("voltage_vpp_v")
    tolerance = float(expectation.get("vpp_tolerance_ratio", 0.10))
    if not isinstance(actual, (int, float)):
        checks.append(_check("vpp_v", "warn", expected, actual, "Vpp unavailable"))
        return
    error_ratio = abs(float(actual) - expected) / expected
    checks.append(
        _check(
            "vpp_v",
            "pass" if error_ratio <= tolerance else "fail",
            expected,
            float(actual),
            "ok" if error_ratio <= tolerance else "Vpp out of tolerance",
            error_ratio=error_ratio,
            tolerance_ratio=tolerance,
        )
    )


def _check_mean(
    summary: dict[str, Any],
    expectation: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    expected = _optional_float(expectation, "mean_v")
    if expected is None:
        expected = _optional_float(expectation, "offset_v")
    if expected is None:
        return
    actual = summary.get("voltage_mean_v")
    tolerance = float(expectation.get("mean_tolerance_v", 0.05))
    if not isinstance(actual, (int, float)):
        checks.append(_check("mean_v", "warn", expected, actual, "mean unavailable"))
        return
    error = abs(float(actual) - expected)
    checks.append(
        _check(
            "mean_v",
            "pass" if error <= tolerance else "fail",
            expected,
            float(actual),
            "ok" if error <= tolerance else "mean out of tolerance",
            error_abs=error,
            tolerance_abs=tolerance,
        )
    )


def _check_duty(
    summary: dict[str, Any],
    expectation: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    expected = _optional_float(expectation, "duty_cycle")
    if expected is None and "duty_percent" in expectation:
        expected = _optional_float(expectation, "duty_percent")
        if expected is not None:
            expected /= 100.0
    if expected is None:
        return
    actual = summary.get("duty_cycle")
    tolerance = float(expectation.get("duty_tolerance", 0.05))
    if not isinstance(actual, (int, float)):
        checks.append(_check("duty_cycle", "warn", expected, actual, "duty unavailable"))
        return
    error = abs(float(actual) - expected)
    checks.append(
        _check(
            "duty_cycle",
            "pass" if error <= tolerance else "fail",
            expected,
            float(actual),
            "ok" if error <= tolerance else "duty out of tolerance",
            error_abs=error,
            tolerance_abs=tolerance,
        )
    )


def _check_symmetry(
    waveform: WaveformData,
    expectation: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    expected = _optional_float(expectation, "symmetry_percent")
    if expected is None:
        return
    actual = estimate_triangle_symmetry_percent(waveform)
    tolerance = float(expectation.get("symmetry_tolerance_percent", 5.0))
    if actual is None:
        checks.append(_check("symmetry_percent", "warn", expected, actual, "symmetry unavailable"))
        return
    error = abs(actual - expected)
    checks.append(
        _check(
            "symmetry_percent",
            "pass" if error <= tolerance else "fail",
            expected,
            actual,
            "ok" if error <= tolerance else "symmetry out of tolerance",
            error_abs=error,
            tolerance_abs=tolerance,
        )
    )


def _check(name: str, status: str, expected: Any, actual: Any, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "metric": name,
        "status": status,
        "expected": expected,
        "actual": actual,
        "message": message,
        **extra,
    }


def _optional_float(data: dict[str, Any], name: str) -> float | None:
    if name not in data or data[name] is None:
        return None
    value = data[name]
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_positive_float(data: dict[str, Any], name: str) -> float | None:
    value = _optional_float(data, name)
    if value is None or value <= 0:
        return None
    return value
