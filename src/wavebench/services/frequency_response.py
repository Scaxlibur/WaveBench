from __future__ import annotations

import csv
from dataclasses import dataclass, replace
import json
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np

from wavebench.errors import ConfigError


FIT_METHODS = ("linear_log", "polynomial", "pchip")
BASE_CSV_FIELDS = (
    "index",
    "requested_frequency_hz",
    "reference_frequency_hz",
    "response_frequency_hz",
    "reference_amplitude_peak_v",
    "response_amplitude_peak_v",
    "reference_vpp_v",
    "response_vpp_v",
    "gain_linear",
    "gain_db",
    "phase_wrapped_deg",
    "phase_unwrapped_deg",
    "status",
    "warnings",
    "error",
    "capture_package",
    "metadata_path",
)


@dataclass(frozen=True)
class FrequencyResponsePoint:
    index: int
    requested_frequency_hz: float
    reference_frequency_hz: float | None
    response_frequency_hz: float | None
    reference_amplitude_peak_v: float | None
    response_amplitude_peak_v: float | None
    reference_vpp_v: float | None
    response_vpp_v: float | None
    gain_linear: float | None
    gain_db: float | None
    phase_wrapped_deg: float | None
    phase_unwrapped_deg: float | None
    status: str
    warnings: tuple[str, ...] = ()
    error: str = ""
    capture_package: str = ""
    metadata_path: str = ""

    @property
    def usable_for_fit(self) -> bool:
        return (
            self.status != "failed"
            and self.gain_linear is not None
            and isfinite(self.gain_linear)
            and self.gain_linear > 0
        )

    def as_csv_row(self, fit_values: dict[str, tuple[float | None, float | None]] | None = None) -> dict[str, object]:
        row: dict[str, object] = {
            "index": self.index,
            "requested_frequency_hz": self.requested_frequency_hz,
            "reference_frequency_hz": self.reference_frequency_hz,
            "response_frequency_hz": self.response_frequency_hz,
            "reference_amplitude_peak_v": self.reference_amplitude_peak_v,
            "response_amplitude_peak_v": self.response_amplitude_peak_v,
            "reference_vpp_v": self.reference_vpp_v,
            "response_vpp_v": self.response_vpp_v,
            "gain_linear": self.gain_linear,
            "gain_db": self.gain_db,
            "phase_wrapped_deg": self.phase_wrapped_deg,
            "phase_unwrapped_deg": self.phase_unwrapped_deg,
            "status": self.status,
            "warnings": " | ".join(self.warnings),
            "error": self.error,
            "capture_package": self.capture_package,
            "metadata_path": self.metadata_path,
        }
        for method, values in (fit_values or {}).items():
            row[f"fit_{method}_gain_linear"] = values[0]
            row[f"fit_{method}_residual"] = values[1]
        return row


def analyze_frequency_response_point(
    *,
    index: int,
    requested_frequency_hz: float,
    reference_waveform: Any,
    response_waveform: Any,
    frequency_tolerance_ratio: float,
    capture_package: str,
    metadata_path: str,
) -> FrequencyResponsePoint:
    """Compute one transfer-function point from a simultaneous two-channel capture."""
    try:
        reference_phasor, reference_amplitude = _fit_sine_phasor(
            reference_waveform, requested_frequency_hz
        )
        response_phasor, response_amplitude = _fit_sine_phasor(
            response_waveform, requested_frequency_hz
        )
        if reference_amplitude <= _amplitude_floor(reference_waveform):
            raise ValueError("reference fundamental amplitude is zero or too small")
        if response_amplitude <= _amplitude_floor(response_waveform):
            raise ValueError("response fundamental amplitude is zero or too small")
        transfer = response_phasor / reference_phasor
        gain_linear = float(abs(transfer))
        if not isfinite(gain_linear) or gain_linear <= 0:
            raise ValueError("gain is not finite and positive")
        gain_db = float(20.0 * np.log10(gain_linear))
        phase_wrapped_deg = _wrap_phase_deg(float(np.degrees(np.angle(transfer))))
        reference_summary = _summary(reference_waveform, requested_frequency_hz, frequency_tolerance_ratio)
        response_summary = _summary(response_waveform, requested_frequency_hz, frequency_tolerance_ratio)
        warnings = _quality_warnings(reference_summary, "reference") + _quality_warnings(
            response_summary, "response"
        )
        return FrequencyResponsePoint(
            index=index,
            requested_frequency_hz=requested_frequency_hz,
            reference_frequency_hz=_finite_or_none(reference_summary.get("frequency_estimate_hz")),
            response_frequency_hz=_finite_or_none(response_summary.get("frequency_estimate_hz")),
            reference_amplitude_peak_v=reference_amplitude,
            response_amplitude_peak_v=response_amplitude,
            reference_vpp_v=_finite_or_none(reference_summary.get("voltage_vpp_v")),
            response_vpp_v=_finite_or_none(response_summary.get("voltage_vpp_v")),
            gain_linear=gain_linear,
            gain_db=gain_db,
            phase_wrapped_deg=phase_wrapped_deg,
            phase_unwrapped_deg=None,
            status="warning" if warnings else "ok",
            warnings=tuple(warnings),
            capture_package=capture_package,
            metadata_path=metadata_path,
        )
    except Exception as exc:  # noqa: BLE001 - every point must remain auditable
        return FrequencyResponsePoint(
            index=index,
            requested_frequency_hz=requested_frequency_hz,
            reference_frequency_hz=None,
            response_frequency_hz=None,
            reference_amplitude_peak_v=None,
            response_amplitude_peak_v=None,
            reference_vpp_v=None,
            response_vpp_v=None,
            gain_linear=None,
            gain_db=None,
            phase_wrapped_deg=None,
            phase_unwrapped_deg=None,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            capture_package=capture_package,
            metadata_path=metadata_path,
        )


def failed_frequency_response_point(
    *,
    index: int,
    requested_frequency_hz: float,
    error: Exception | str,
) -> FrequencyResponsePoint:
    text = str(error)
    if isinstance(error, Exception):
        text = f"{type(error).__name__}: {error}"
    return FrequencyResponsePoint(
        index=index,
        requested_frequency_hz=requested_frequency_hz,
        reference_frequency_hz=None,
        response_frequency_hz=None,
        reference_amplitude_peak_v=None,
        response_amplitude_peak_v=None,
        reference_vpp_v=None,
        response_vpp_v=None,
        gain_linear=None,
        gain_db=None,
        phase_wrapped_deg=None,
        phase_unwrapped_deg=None,
        status="failed",
        error=text,
    )


def unwrap_frequency_response_phase(points: list[FrequencyResponsePoint]) -> list[FrequencyResponsePoint]:
    result: list[FrequencyResponsePoint] = []
    block: list[FrequencyResponsePoint] = []

    def flush() -> None:
        if not block:
            return
        unwrapped = np.degrees(np.unwrap(np.radians([point.phase_wrapped_deg for point in block])))
        result.extend(
            replace(point, phase_unwrapped_deg=float(value))
            for point, value in zip(block, unwrapped)
        )
        block.clear()

    for point in points:
        if point.status == "failed" or point.phase_wrapped_deg is None:
            flush()
            result.append(point)
        else:
            block.append(point)
    flush()
    return result


def ensure_fit_dependencies(fit: dict[str, Any] | None) -> None:
    methods = _fit_methods(fit)
    if "pchip" not in methods:
        return
    try:
        from scipy.interpolate import PchipInterpolator  # noqa: F401
    except ImportError as exc:
        raise ConfigError(
            "fit method 'pchip' requires the optional analysis dependency; "
            "install WaveBench with `.[analysis]`"
        ) from exc


def build_fit_document(
    points: list[FrequencyResponsePoint], fit: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, dict[int, tuple[float | None, float | None]]]]:
    if fit is None:
        return None, {}
    methods = _fit_methods(fit)
    degree = int(fit.get("polynomial_degree", 3))
    sample_count = int(fit.get("sample_count", 240))
    usable = sorted((point for point in points if point.usable_for_fit), key=lambda point: point.requested_frequency_hz)
    excluded = [
        {"index": point.index, "frequency_hz": point.requested_frequency_hz, "reason": point.error or point.status}
        for point in points
        if not point.usable_for_fit
    ]
    document: dict[str, Any] = {
        "schema_version": 1,
        "x_transform": "log10(frequency_hz / Hz)",
        "valid_points": [point.index for point in usable],
        "excluded_points": excluded,
        "methods": {},
    }
    fit_values: dict[str, dict[int, tuple[float | None, float | None]]] = {}
    if usable:
        document["valid_domain_hz"] = [
            usable[0].requested_frequency_hz,
            usable[-1].requested_frequency_hz,
        ]
    else:
        document["valid_domain_hz"] = None

    x = np.log10(np.asarray([point.requested_frequency_hz for point in usable], dtype=float))
    y = np.asarray([point.gain_linear for point in usable], dtype=float)
    for method in methods:
        result, values = _fit_method(
            method=method,
            x=x,
            y=y,
            points=usable,
            polynomial_degree=degree,
            sample_count=sample_count,
        )
        document["methods"][method] = result
        fit_values[method] = values
    return document, fit_values


def write_frequency_response_csv(
    path: str | Path,
    points: list[FrequencyResponsePoint],
    fit_values: dict[str, dict[int, tuple[float | None, float | None]]] | None = None,
) -> Path:
    output = Path(path)
    methods = tuple((fit_values or {}).keys())
    fieldnames = list(BASE_CSV_FIELDS)
    for method in methods:
        fieldnames.extend((f"fit_{method}_gain_linear", f"fit_{method}_residual"))
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for point in points:
            method_values = {
                method: (fit_values or {}).get(method, {}).get(point.index, (None, None))
                for method in methods
            }
            writer.writerow(point.as_csv_row(method_values))
    temporary.replace(output)
    return output


def write_fit_document(path: str | Path, document: dict[str, Any] | None) -> Path | None:
    if document is None:
        return None
    output = Path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    return output


def _fit_sine_phasor(waveform: Any, frequency_hz: float) -> tuple[complex, float]:
    times = np.asarray(getattr(waveform, "times_s"), dtype=float)
    voltages = np.asarray(getattr(waveform, "voltages_v"), dtype=float)
    if not isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError("frequency must be finite and positive")
    if times.ndim != 1 or voltages.ndim != 1:
        raise ValueError("waveform time and voltage samples must be one-dimensional")
    if times.size != voltages.size:
        raise ValueError("waveform time and voltage sample counts must match")
    if times.size < 4:
        raise ValueError("need at least four waveform samples")
    if not np.all(np.isfinite(times)) or not np.all(np.isfinite(voltages)):
        raise ValueError("waveform samples must be finite")
    if np.any(np.diff(times) <= 0):
        raise ValueError("waveform time samples must be strictly increasing")

    time_origin = float(times[0])
    omega_times = 2.0 * np.pi * frequency_hz * (times - time_origin)
    design = np.column_stack((np.ones(times.size), np.sin(omega_times), np.cos(omega_times)))
    coefficients, _, rank, _ = np.linalg.lstsq(design, voltages, rcond=None)
    if rank < 3:
        raise ValueError("sine fit is rank deficient")
    local_phasor = complex(float(coefficients[1]), float(coefficients[2]))
    phasor = local_phasor * np.exp(-1j * 2.0 * np.pi * frequency_hz * time_origin)
    amplitude = float(abs(phasor))
    if not isfinite(amplitude):
        raise ValueError("sine fit amplitude is not finite")
    return phasor, amplitude


def _amplitude_floor(waveform: Any) -> float:
    values = np.asarray(getattr(waveform, "voltages_v"), dtype=float)
    finite = values[np.isfinite(values)]
    scale = float(np.max(np.abs(finite))) if finite.size else 0.0
    return max(1e-12, scale * 1e-12)


def _summary(waveform: Any, frequency_hz: float, tolerance_ratio: float) -> dict[str, Any]:
    summary = waveform.summary(
        expected_frequency_hz=frequency_hz,
        frequency_tolerance_ratio=tolerance_ratio,
    )
    return summary if isinstance(summary, dict) else {}


def _quality_warnings(summary: dict[str, Any], channel: str) -> list[str]:
    raw = summary.get("quality_warnings", [])
    if isinstance(raw, list):
        return [f"{channel}: {item}" for item in raw if item]
    return [f"{channel}: {raw}"] if raw else []


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _wrap_phase_deg(value: float) -> float:
    return float((value + 180.0) % 360.0 - 180.0)


def _fit_methods(fit: dict[str, Any] | None) -> tuple[str, ...]:
    if fit is None:
        return ()
    methods = fit.get("methods", FIT_METHODS)
    if not isinstance(methods, list):
        raise ConfigError("frequency response fit methods must be a list")
    normalized = tuple(str(method).strip().lower() for method in methods)
    if not normalized or any(method not in FIT_METHODS for method in normalized):
        choices = ", ".join(FIT_METHODS)
        raise ConfigError(f"frequency response fit methods must use: {choices}")
    if len(set(normalized)) != len(normalized):
        raise ConfigError("frequency response fit methods must not contain duplicates")
    return normalized


def _fit_method(
    *,
    method: str,
    x: np.ndarray,
    y: np.ndarray,
    points: list[FrequencyResponsePoint],
    polynomial_degree: int,
    sample_count: int,
) -> tuple[dict[str, Any], dict[int, tuple[float | None, float | None]]]:
    if x.size < 2:
        return _unavailable_fit(method, "at least two valid points are required")
    curve_x = np.linspace(float(x[0]), float(x[-1]), max(2, sample_count))
    try:
        if method == "linear_log":
            predicted = np.interp(x, x, y)
            curve_y = np.interp(curve_x, x, y)
            segments = _linear_segments(x, y)
            result = {
                "status": "ok",
                "display": "Log-frequency piecewise linear interpolation",
                "formula": "x = log10(f / Hz); G = m_i * x + b_i within segment i",
                "parameters": {"segments": segments},
            }
        elif method == "polynomial":
            if polynomial_degree < 1 or polynomial_degree > 5:
                return _unavailable_fit(method, "polynomial degree must be from 1 through 5")
            if x.size < polynomial_degree + 1:
                return _unavailable_fit(
                    method, f"degree {polynomial_degree} requires at least {polynomial_degree + 1} valid points"
                )
            coefficients = np.polyfit(x, y, polynomial_degree)
            predicted = np.polyval(coefficients, x)
            curve_y = np.polyval(coefficients, curve_x)
            terms = [
                {"power": polynomial_degree - offset, "coefficient": float(value)}
                for offset, value in enumerate(coefficients)
            ]
            result = {
                "status": "ok",
                "display": f"Degree-{polynomial_degree} polynomial in log frequency",
                "formula": "x = log10(f / Hz); G = sum(c_k * x^k)",
                "parameters": {"degree": polynomial_degree, "terms": terms},
            }
        elif method == "pchip":
            try:
                from scipy.interpolate import PchipInterpolator
            except ImportError as exc:  # pragma: no cover - guarded in run check
                return _unavailable_fit(method, f"optional dependency unavailable: {exc}")
            interpolator = PchipInterpolator(x, y, extrapolate=False)
            predicted = np.asarray(interpolator(x), dtype=float)
            curve_y = np.asarray(interpolator(curve_x), dtype=float)
            result = {
                "status": "ok",
                "display": "PCHIP shape-preserving cubic interpolation",
                "formula": "x = log10(f / Hz); G_i(x) = c3*(x-x_i)^3 + c2*(x-x_i)^2 + c1*(x-x_i) + c0",
                "parameters": {
                    "segments": _pchip_segments(interpolator),
                },
            }
        else:  # pragma: no cover - normalized before dispatch
            return _unavailable_fit(method, "unsupported method")
    except Exception as exc:  # noqa: BLE001 - optional fit must not discard measurements
        return _unavailable_fit(method, f"{type(exc).__name__}: {exc}")

    predicted = np.asarray(predicted, dtype=float)
    curve_y = np.asarray(curve_y, dtype=float)
    if not np.all(np.isfinite(predicted)) or not np.all(np.isfinite(curve_y)):
        return _unavailable_fit(method, "fit produced non-finite values")
    residuals = y - predicted
    result["metrics"] = _fit_metrics(y, predicted)
    result["curve"] = [
        {"frequency_hz": float(10.0**x_value), "gain_linear": float(y_value)}
        for x_value, y_value in zip(curve_x, curve_y)
    ]
    values = {
        point.index: (float(value), float(residual))
        for point, value, residual in zip(points, predicted, residuals)
    }
    return result, values


def _unavailable_fit(method: str, reason: str) -> tuple[dict[str, Any], dict[int, tuple[float | None, float | None]]]:
    return {
        "status": "unavailable",
        "display": method,
        "reason": reason,
        "curve": [],
    }, {}


def _linear_segments(x: np.ndarray, y: np.ndarray) -> list[dict[str, float]]:
    segments: list[dict[str, float]] = []
    for index in range(x.size - 1):
        slope = float((y[index + 1] - y[index]) / (x[index + 1] - x[index]))
        segments.append(
            {
                "x_start": float(x[index]),
                "x_stop": float(x[index + 1]),
                "gain_start": float(y[index]),
                "slope": slope,
                "intercept": float(y[index] - slope * x[index]),
            }
        )
    return segments


def _pchip_segments(interpolator: Any) -> list[dict[str, Any]]:
    return [
        {
            "x_start": float(interpolator.x[index]),
            "x_stop": float(interpolator.x[index + 1]),
            "coefficients": [float(value) for value in interpolator.c[:, index]],
        }
        for index in range(len(interpolator.x) - 1)
    ]


def _fit_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float | None]:
    residual = actual - predicted
    rmse = float(np.sqrt(np.mean(np.square(residual))))
    mae = float(np.mean(np.abs(residual)))
    total = float(np.sum(np.square(actual - np.mean(actual))))
    if total <= 0:
        r_squared = 1.0 if np.allclose(residual, 0.0, rtol=0.0, atol=1e-12) else None
    else:
        r_squared = float(1.0 - np.sum(np.square(residual)) / total)
    return {"rmse": rmse, "mae": mae, "r_squared": r_squared}
