from __future__ import annotations

import math
from typing import Any

import numpy as np

from wavebench.errors import ConfigError
from wavebench.instruments.models import WaveformData

# expectation 允许的字符串字段
_STRING_FIELDS = ("label", "shape")
# expectation 允许的数值字段：名称 -> (下界, 上界, 下界是否排他)
_NUMBER_FIELDS: dict[str, tuple[float, float, bool]] = {
    "frequency_hz": (0.0, math.inf, True),
    "frequency_tolerance_ratio": (0.0, math.inf, False),
    "vpp_v": (0.0, math.inf, True),
    "vpp_tolerance_ratio": (0.0, math.inf, False),
    "mean_v": (-math.inf, math.inf, False),
    "offset_v": (-math.inf, math.inf, False),
    "mean_tolerance_v": (0.0, math.inf, False),
    "duty_cycle": (0.0, 1.0, False),
    "duty_percent": (0.0, 100.0, False),
    "duty_tolerance": (0.0, math.inf, False),
    "symmetry_percent": (0.0, 100.0, False),
    "symmetry_tolerance_percent": (0.0, math.inf, False),
}
# 同义字段，不允许同时出现，避免“哪个生效”依赖字典顺序
_MUTUALLY_EXCLUSIVE = (("duty_cycle", "duty_percent"), ("mean_v", "offset_v"))
# 三角波对称度估计的默认滞回阈值（占 Vpp 的比例）
_SYMMETRY_HYSTERESIS_RATIO = 0.02


def validate_expectation(raw: dict[str, Any]) -> dict[str, Any]:
    """严格校验 expectation，返回规范化副本；任何非法输入都抛出 ConfigError。

    这里必须自证，不能依赖 MCP 发布的 JSON Schema：schema 只在客户端生效。
    """
    if not isinstance(raw, dict):
        raise ConfigError("expectation entries must be objects / expectation 条目必须是对象")
    known = set(_STRING_FIELDS) | set(_NUMBER_FIELDS)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"unknown expectation field(s): {', '.join(unknown)} / expectation 存在未知字段"
        )
    validated: dict[str, Any] = {}
    for name in _STRING_FIELDS:
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"expectation {name} must be a non-empty string")
        validated[name] = value
    for name, (low, high, exclusive_low) in _NUMBER_FIELDS.items():
        value = raw.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"expectation {name} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise ConfigError(f"expectation {name} must be finite")
        if exclusive_low and number <= low:
            raise ConfigError(f"expectation {name} must be > {low:g}")
        if not exclusive_low and number < low:
            raise ConfigError(f"expectation {name} must be >= {low:g}")
        if number > high:
            raise ConfigError(f"expectation {name} must be <= {high:g}")
        validated[name] = number
    for left, right in _MUTUALLY_EXCLUSIVE:
        if left in validated and right in validated:
            raise ConfigError(f"expectation must not set both {left} and {right}")
    return validated


def evaluate_waveform_expectation(
    waveform: WaveformData,
    expectation: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_expectation(expectation)
    summary = waveform.summary(
        expected_frequency_hz=_number(validated, "frequency_hz"),
        frequency_tolerance_ratio=_number(validated, "frequency_tolerance_ratio", default=0.05),
    )
    checks: list[dict[str, Any]] = []
    _check_frequency(summary, validated, checks)
    _check_vpp(summary, validated, checks)
    _check_mean(summary, validated, checks)
    _check_duty(summary, validated, checks)
    _check_symmetry(waveform, validated, checks)
    if not checks:
        # 没有任何可执行检查时必须显式说明，不能伪装成 pass
        return {
            "status": "skipped",
            "channel": waveform.channel,
            "label": validated.get("label"),
            "shape": validated.get("shape"),
            "checks": [],
            "message": "expectation contains no checkable metric",
        }
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
        "label": validated.get("label"),
        "shape": validated.get("shape"),
        "checks": checks,
    }


def expectation_summary(results: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Keep confirmed failures; incomplete acceptance is never a pass."""
    statuses = {result["status"] for result in results.values()}
    if "fail" in statuses:
        status = "fail"
    elif "unavailable" in statuses:
        status = "partial" if statuses & {"pass", "warn"} else "unavailable"
    elif "warn" in statuses:
        status = "warn"
    elif "pass" in statuses:
        status = "pass"
    else:
        status = "skipped"
    return {
        "status": status,
        "channels": {str(channel): result["status"] for channel, result in sorted(results.items())},
    }


def estimate_triangle_symmetry_percent(
    waveform: WaveformData,
    *,
    expected_frequency_hz: float | None = None,
    hysteresis_ratio: float = _SYMMETRY_HYSTERESIS_RATIO,
) -> float | None:
    """估计三角波上升沿占整周期的百分比。

    真实示波器波形带有噪声、量化台阶和过冲，逐点比较相邻差分符号会被单个噪声样本打乱。
    这里先用滑动平均抑制噪声，再用滞回（相对 Vpp）确认极值反转，并用期望频率约束周期长度。
    """
    times = waveform.times_s
    values = np.asarray(waveform.voltages_v, dtype=np.float64)
    if times.size != values.size or values.size < 8:
        return None
    span = float(np.max(values) - np.min(values))
    if span <= 1e-12:
        return None
    samples_per_cycle = _samples_per_cycle(times, expected_frequency_hz)
    smoothed = _smooth(values, _smoothing_window(values.size, samples_per_cycle))
    extrema = _hysteresis_extrema(smoothed, hysteresis=max(span * hysteresis_ratio, 0.0))
    if len(extrema) < 3:
        return None
    # 滞回和滑窗都会把极值确认点推向信号内部；用相邻两段原始数据的拟合直线交点把
    # 极值时间还原回三角波的真实折点。
    refined = _refine_extrema(times, values, extrema)
    min_period_s = 0.5 / expected_frequency_hz if expected_frequency_hz else None
    fractions: list[float] = []
    for index in range(1, len(refined) - 1):
        left_kind, left_time = refined[index - 1]
        kind, peak_time = refined[index]
        right_kind, right_time = refined[index + 1]
        if (left_kind, kind, right_kind) != ("min", "max", "min"):
            continue
        period = right_time - left_time
        if period <= 0 or (min_period_s is not None and period < min_period_s):
            continue
        fractions.append(float((peak_time - left_time) / period * 100.0))
    if not fractions:
        return None
    return float(np.median(np.asarray(fractions, dtype=np.float64)))


def _refine_extrema(
    times: np.ndarray,
    values: np.ndarray,
    extrema: list[tuple[str, int]],
) -> list[tuple[str, float]]:
    refined: list[tuple[str, float]] = []
    for position, (kind, index) in enumerate(extrema):
        if position == 0 or position == len(extrema) - 1:
            refined.append((kind, float(times[index])))
            continue
        crossing = _segment_intersection_time(
            times,
            values,
            extrema[position - 1][1],
            index,
            extrema[position + 1][1],
        )
        refined.append((kind, crossing if crossing is not None else float(times[index])))
    return refined


def _segment_intersection_time(
    times: np.ndarray,
    values: np.ndarray,
    previous_index: int,
    index: int,
    next_index: int,
) -> float | None:
    rising = _fit_line(times, values, previous_index, index)
    falling = _fit_line(times, values, index, next_index)
    if rising is None or falling is None:
        return None
    slope_a, intercept_a = rising
    slope_b, intercept_b = falling
    if abs(slope_a - slope_b) <= 1e-18:
        return None
    crossing = (intercept_b - intercept_a) / (slope_a - slope_b)
    if not math.isfinite(crossing):
        return None
    return float(crossing)


def _fit_line(
    times: np.ndarray,
    values: np.ndarray,
    start_index: int,
    stop_index: int,
) -> tuple[float, float] | None:
    low, high = (start_index, stop_index) if start_index <= stop_index else (stop_index, start_index)
    if high - low < 2:
        return None
    # 裁掉两端靠近折点的部分，避免拐角处的采样点把拟合斜率拽偏
    margin = max(1, int((high - low) * 0.15))
    begin = low + margin
    end = high - margin
    if end - begin < 1:
        begin, end = low, high
    x = np.asarray(times[begin : end + 1], dtype=np.float64)
    y = np.asarray(values[begin : end + 1], dtype=np.float64)
    if x.size < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def _samples_per_cycle(times: np.ndarray, expected_frequency_hz: float | None) -> float | None:
    if not expected_frequency_hz or expected_frequency_hz <= 0 or times.size < 2:
        return None
    duration = float(times[-1] - times[0])
    if duration <= 0:
        return None
    return float(times.size) / (duration * expected_frequency_hz)


def _smoothing_window(size: int, samples_per_cycle: float | None) -> int:
    if samples_per_cycle is not None and samples_per_cycle > 64:
        candidate = int(samples_per_cycle // 20)
    else:
        candidate = int(size // 50)
    window = max(1, min(candidate, 51, size))
    if window > 1 and window % 2 == 0:
        window -= 1
    return window


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values
    kernel = np.ones(window, dtype=np.float64) / window
    pad = window // 2
    padded = np.pad(values, pad, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _hysteresis_extrema(values: np.ndarray, *, hysteresis: float) -> list[tuple[str, int]]:
    """用滞回跟踪方向变化，返回 [(kind, index), ...]，kind 为 min/max 且交替出现。

    方向未确认时锚点保持不变，否则单调斜坡会被“锚点跟着当前值走”抵消掉滞回判据。
    """
    extrema: list[tuple[str, int]] = []
    if values.size == 0:
        return extrema
    anchor = 0
    direction = 0
    for index in range(1, values.size):
        value = float(values[index])
        if direction == 0:
            if value >= float(values[anchor]) + hysteresis:
                direction = 1
            elif value <= float(values[anchor]) - hysteresis:
                direction = -1
            continue
        if direction > 0:
            if value > float(values[anchor]):
                anchor = index
            elif float(values[anchor]) - value > hysteresis:
                if anchor > 0:
                    extrema.append(("max", anchor))
                direction, anchor = -1, index
        else:
            if value < float(values[anchor]):
                anchor = index
            elif value - float(values[anchor]) > hysteresis:
                if anchor > 0:
                    extrema.append(("min", anchor))
                direction, anchor = 1, index
    return extrema


def _check_frequency(
    summary: dict[str, Any],
    expectation: dict[str, Any],
    checks: list[dict[str, Any]],
) -> None:
    expected = _number(expectation, "frequency_hz")
    if expected is None:
        return
    actual = summary.get("frequency_estimate_hz")
    tolerance = _number(expectation, "frequency_tolerance_ratio", default=0.05)
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
    expected = _number(expectation, "vpp_v")
    if expected is None:
        return
    actual = summary.get("voltage_vpp_v")
    tolerance = _number(expectation, "vpp_tolerance_ratio", default=0.10)
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
    expected = _number(expectation, "mean_v", fallback_field="offset_v")
    if expected is None:
        return
    actual = summary.get("voltage_mean_v")
    tolerance = _number(expectation, "mean_tolerance_v", default=0.05)
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
    expected = _number(expectation, "duty_cycle")
    if expected is None:
        percent = _number(expectation, "duty_percent")
        if percent is not None:
            expected = percent / 100.0
    if expected is None:
        return
    actual = summary.get("duty_cycle")
    tolerance = _number(expectation, "duty_tolerance", default=0.05)
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
    expected = _number(expectation, "symmetry_percent")
    if expected is None:
        return
    actual = estimate_triangle_symmetry_percent(
        waveform,
        expected_frequency_hz=_number(expectation, "frequency_hz"),
    )
    tolerance = _number(expectation, "symmetry_tolerance_percent", default=5.0)
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


def _number(
    expectation: dict[str, Any],
    name: str,
    *,
    default: float | None = None,
    fallback_field: str | None = None,
) -> float | None:
    """读取已校验的数值字段。``fallback_field`` 只用于同义字段，两者不会同时出现。"""
    value = expectation.get(name)
    if value is None and fallback_field is not None:
        value = expectation.get(fallback_field)
    if value is None:
        return default
    return float(value)
