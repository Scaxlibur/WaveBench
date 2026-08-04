from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from math import isfinite, log2
from pathlib import Path
import tomllib
from typing import Any, Iterable

import numpy as np

from wavebench.errors import ConfigError


CALIBRATION_TARGET_MODES = ("passband_median", "explicit_gain_db", "unity_gain")
_SMOOTHING_ALPHAS = (0.0, 0.01, 0.1, 1.0, 10.0)
_CSV_FIELDS = (
    "frequency_hz",
    "requested_vpp",
    "fitted_gain_db",
    "correction_db",
    "correction_linear",
    "correction_limited",
    "slope_limited",
)


@dataclass(frozen=True)
class FrequencyResponseCalibrationConfig:
    enabled: bool = True
    model: str = "smoothing_spline_db"
    target_mode: str = "passband_median"
    target_gain_db: float | None = None
    target_frequency_min_hz: float | None = None
    target_frequency_max_hz: float | None = None
    correction_min_db: float = -12.0
    correction_max_db: float = 12.0
    max_slope_db_per_octave: float = 6.0
    chebyshev_degree: int = 3
    chebyshev_segment_count: int = 8

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_frequency_response_calibration(
    raw: Any, name: str = "calibration"
) -> FrequencyResponseCalibrationConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a TOML table")
    allowed = {
        "enabled",
        "model",
        "target_mode",
        "target_gain_db",
        "target_frequency_min_hz",
        "target_frequency_max_hz",
        "correction_min_db",
        "correction_max_db",
        "max_slope_db_per_octave",
        "chebyshev_degree",
        "chebyshev_segment_count",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{name} has unsupported keys: {', '.join(unknown)}")
    enabled = _bool(raw.get("enabled", True), f"{name}.enabled")
    model = _text(raw.get("model", "smoothing_spline_db"), f"{name}.model").lower()
    if model != "smoothing_spline_db":
        raise ConfigError(f"{name}.model must be 'smoothing_spline_db'")
    target_mode = _text(raw.get("target_mode", "passband_median"), f"{name}.target_mode").lower()
    if target_mode not in CALIBRATION_TARGET_MODES:
        choices = ", ".join(CALIBRATION_TARGET_MODES)
        raise ConfigError(f"{name}.target_mode must be one of: {choices}")
    target_gain_db = _optional_float(raw.get("target_gain_db"), f"{name}.target_gain_db")
    if target_mode == "explicit_gain_db" and target_gain_db is None:
        raise ConfigError(f"{name}.target_gain_db is required when target_mode = 'explicit_gain_db'")
    minimum = _optional_positive_float(
        raw.get("target_frequency_min_hz"), f"{name}.target_frequency_min_hz"
    )
    maximum = _optional_positive_float(
        raw.get("target_frequency_max_hz"), f"{name}.target_frequency_max_hz"
    )
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise ConfigError(f"{name}.target_frequency_min_hz must be less than target_frequency_max_hz")
    correction_min = _finite_float(raw.get("correction_min_db", -12.0), f"{name}.correction_min_db")
    correction_max = _finite_float(raw.get("correction_max_db", 12.0), f"{name}.correction_max_db")
    if correction_min > correction_max:
        raise ConfigError(f"{name}.correction_min_db must be <= correction_max_db")
    slope = _positive_float(
        raw.get("max_slope_db_per_octave", 6.0), f"{name}.max_slope_db_per_octave"
    )
    degree = _positive_int(raw.get("chebyshev_degree", 3), f"{name}.chebyshev_degree")
    if degree > 8:
        raise ConfigError(f"{name}.chebyshev_degree must be <= 8")
    segments = _positive_int(
        raw.get("chebyshev_segment_count", 8), f"{name}.chebyshev_segment_count"
    )
    return FrequencyResponseCalibrationConfig(
        enabled=enabled,
        model=model,
        target_mode=target_mode,
        target_gain_db=target_gain_db,
        target_frequency_min_hz=minimum,
        target_frequency_max_hz=maximum,
        correction_min_db=correction_min,
        correction_max_db=correction_max,
        max_slope_db_per_octave=slope,
        chebyshev_degree=degree,
        chebyshev_segment_count=segments,
    )


def load_frequency_response_calibration_config(path: str | Path) -> FrequencyResponseCalibrationConfig:
    config_path = Path(path)
    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except OSError as exc:
        raise ConfigError(f"cannot read calibration config: {config_path}: {exc}") from exc
    table = raw.get("calibration") if isinstance(raw, dict) else None
    if table is None:
        raise ConfigError(f"calibration config requires a [calibration] table: {config_path}")
    return normalize_frequency_response_calibration(table, "calibration")


def ensure_calibration_dependencies() -> None:
    try:
        from scipy.interpolate import UnivariateSpline  # noqa: F401
    except ImportError as exc:
        raise ConfigError(
            "frequency response calibration requires the optional analysis dependency; "
            "install WaveBench with `.[analysis]`"
        ) from exc


def build_frequency_response_calibration(
    rows: Iterable[dict[str, Any]],
    config: FrequencyResponseCalibrationConfig | dict[str, Any],
    *,
    source_csv: str | Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a deployable two-dimensional gain-correction LUT from an audited CSV."""
    if isinstance(config, dict):
        config = normalize_frequency_response_calibration(config)
    ensure_calibration_dependencies()
    groups = _measurement_groups(rows)
    if len(groups) < 2:
        raise ConfigError("two-dimensional calibration requires at least two valid requested_vpp slices")
    for amplitude, samples in groups.items():
        if len(samples) < 4:
            raise ConfigError(f"requested_vpp {amplitude:.12g} requires at least four valid frequency points")
    amplitudes = np.asarray(sorted(groups), dtype=float)
    common_frequencies = _common_frequency_grid(groups)
    if common_frequencies.size < 4:
        raise ConfigError("two-dimensional calibration requires at least four common valid frequency points")

    alpha, frequency_cv = _select_smoothing_alpha(groups)
    models = {
        amplitude: _fit_spline(*_samples_to_arrays(samples), alpha=alpha)
        for amplitude, samples in groups.items()
    }
    x_grid = np.log10(common_frequencies)
    fitted = np.asarray(
        [np.asarray(models[amplitude](x_grid), dtype=float) for amplitude in amplitudes], dtype=float
    )
    target_gain_db = _target_gain_db(fitted, common_frequencies, config)
    calibration_rows: list[dict[str, Any]] = []
    limit_counts = {"correction_limited": 0, "slope_limited": 0}
    for amplitude, gains in zip(amplitudes, fitted):
        raw_correction = target_gain_db - gains
        correction, correction_limited, slope_limited = _limit_correction(
            raw_correction,
            common_frequencies,
            config,
        )
        limit_counts["correction_limited"] += int(np.count_nonzero(correction_limited))
        limit_counts["slope_limited"] += int(np.count_nonzero(slope_limited))
        for frequency, gain_db, correction_db, limited, slope_flag in zip(
            common_frequencies, gains, correction, correction_limited, slope_limited
        ):
            calibration_rows.append(
                {
                    "frequency_hz": float(frequency),
                    "requested_vpp": float(amplitude),
                    "fitted_gain_db": float(gain_db),
                    "correction_db": float(correction_db),
                    "correction_linear": float(10.0 ** (correction_db / 20.0)),
                    "correction_limited": bool(limited),
                    "slope_limited": bool(slope_flag),
                }
            )
    chebyshev = [
        _chebyshev_document(
            amplitude,
            x_grid,
            models[amplitude],
            degree=config.chebyshev_degree,
            segment_count=config.chebyshev_segment_count,
        )
        for amplitude in amplitudes
    ]
    document = {
        "schema_version": 1,
        "source_frequency_response_csv": str(source_csv) if source_csv is not None else None,
        "model": config.model,
        "configuration": config.as_dict(),
        "x_transform": "log10(frequency_hz / Hz)",
        "interpolation": "smoothing spline in frequency; linear in requested_vpp; no extrapolation",
        "valid_domain": {
            "frequency_hz": [float(common_frequencies[0]), float(common_frequencies[-1])],
            "requested_vpp": [float(amplitudes[0]), float(amplitudes[-1])],
        },
        "grid": {
            "frequency_hz": [float(value) for value in common_frequencies],
            "requested_vpp": [float(value) for value in amplitudes],
        },
        "target_gain_db": float(target_gain_db),
        "smoothing": {"selected_alpha": alpha, "candidate_holdout_rmse_db": frequency_cv},
        "validation": {
            "frequency_holdout_rmse_db": _minimum_metric(frequency_cv),
            "amplitude_holdout_rmse_db": _amplitude_holdout_rmse(groups),
        },
        "limit_counts": limit_counts,
        "chebyshev": chebyshev,
        "lut": calibration_rows,
    }
    return document, calibration_rows


def write_frequency_response_calibration_csv(
    path: str | Path, rows: Iterable[dict[str, Any]]
) -> Path:
    output = Path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    return output


def write_frequency_response_calibration_json(path: str | Path, document: dict[str, Any]) -> Path:
    output = Path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    return output


def _measurement_groups(rows: Iterable[dict[str, Any]]) -> dict[float, list[tuple[float, float]]]:
    groups: dict[float, list[tuple[float, float]]] = {}
    for row in rows:
        if str(row.get("status", "")).strip().lower() == "failed":
            continue
        frequency = _row_float(row, "requested_frequency_hz")
        amplitude = _row_float(row, "requested_vpp")
        gain_db = _row_float(row, "gain_db")
        if gain_db is None:
            gain = _row_float(row, "gain_linear")
            gain_db = 20.0 * np.log10(gain) if gain is not None and gain > 0 else None
        if frequency is None or frequency <= 0 or amplitude is None or amplitude <= 0 or gain_db is None:
            continue
        groups.setdefault(amplitude, []).append((frequency, gain_db))
    normalized: dict[float, list[tuple[float, float]]] = {}
    for amplitude, samples in groups.items():
        by_frequency = {frequency: gain_db for frequency, gain_db in samples}
        normalized[amplitude] = sorted(by_frequency.items())
    return normalized


def _common_frequency_grid(groups: dict[float, list[tuple[float, float]]]) -> np.ndarray:
    frequency_sets = [{frequency for frequency, _gain in samples} for samples in groups.values()]
    common = set.intersection(*frequency_sets) if frequency_sets else set()
    return np.asarray(sorted(common), dtype=float)


def _samples_to_arrays(samples: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    frequencies = np.asarray([frequency for frequency, _gain in samples], dtype=float)
    gains = np.asarray([gain for _frequency, gain in samples], dtype=float)
    return np.log10(frequencies), gains


def _fit_spline(x: np.ndarray, y_db: np.ndarray, *, alpha: float):
    from scipy.interpolate import UnivariateSpline

    variance = float(np.var(y_db))
    smoothing = max(0.0, alpha * x.size * variance)
    return UnivariateSpline(x, y_db, k=min(3, x.size - 1), s=smoothing)


def _select_smoothing_alpha(
    groups: dict[float, list[tuple[float, float]]]
) -> tuple[float, dict[str, float | None]]:
    scores = {str(alpha): _frequency_holdout_rmse(groups, alpha) for alpha in _SMOOTHING_ALPHAS}
    usable = [(alpha, score) for alpha, score in zip(_SMOOTHING_ALPHAS, scores.values()) if score is not None]
    if not usable:
        return 0.1, scores
    return min(usable, key=lambda item: (item[1], item[0]))[0], scores


def _frequency_holdout_rmse(
    groups: dict[float, list[tuple[float, float]]], alpha: float
) -> float | None:
    errors: list[float] = []
    for samples in groups.values():
        x, y = _samples_to_arrays(samples)
        if x.size < 5:
            continue
        for fold in range(5):
            held = np.asarray(
                [index for index in range(1, x.size - 1) if index % 5 == fold], dtype=int
            )
            if not held.size:
                continue
            train = np.ones(x.size, dtype=bool)
            train[held] = False
            if int(np.count_nonzero(train)) < 4:
                continue
            prediction = _fit_spline(x[train], y[train], alpha=alpha)(x[held])
            errors.extend(float(value) for value in (y[held] - prediction))
    return float(np.sqrt(np.mean(np.square(errors)))) if errors else None


def _target_gain_db(
    fitted: np.ndarray, frequencies: np.ndarray, config: FrequencyResponseCalibrationConfig
) -> float:
    if config.target_mode == "explicit_gain_db":
        assert config.target_gain_db is not None
        return config.target_gain_db
    if config.target_mode == "unity_gain":
        return 0.0
    mask = np.ones(frequencies.size, dtype=bool)
    if config.target_frequency_min_hz is not None:
        mask &= frequencies >= config.target_frequency_min_hz
    if config.target_frequency_max_hz is not None:
        mask &= frequencies <= config.target_frequency_max_hz
    selected = fitted[:, mask]
    if not selected.size:
        raise ConfigError("calibration target frequency range does not contain any valid LUT point")
    return float(np.median(selected))


def _limit_correction(
    correction: np.ndarray,
    frequencies: np.ndarray,
    config: FrequencyResponseCalibrationConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    limited = np.clip(correction, config.correction_min_db, config.correction_max_db)
    correction_limited = ~np.isclose(limited, correction, rtol=0.0, atol=1e-12)
    slope_limited = np.zeros(limited.size, dtype=bool)
    for index in range(1, limited.size):
        delta_octaves = log2(float(frequencies[index] / frequencies[index - 1]))
        allowed = config.max_slope_db_per_octave * delta_octaves
        lower = limited[index - 1] - allowed
        upper = limited[index - 1] + allowed
        constrained = float(np.clip(limited[index], lower, upper))
        if not np.isclose(constrained, limited[index], rtol=0.0, atol=1e-12):
            slope_limited[index] = True
            limited[index] = constrained
    return limited, correction_limited, slope_limited


def _chebyshev_document(
    amplitude: float,
    x: np.ndarray,
    spline: Any,
    *,
    degree: int,
    segment_count: int,
) -> dict[str, Any]:
    count = min(segment_count, x.size - 1)
    edges = np.linspace(float(x[0]), float(x[-1]), count + 1)
    segments: list[dict[str, Any]] = []
    for index, (start, stop) in enumerate(zip(edges[:-1], edges[1:])):
        sample_count = max(degree + 1, 9)
        sample_x = np.linspace(start, stop, sample_count)
        sample_y = np.asarray(spline(sample_x), dtype=float)
        polynomial = np.polynomial.Chebyshev.fit(
            sample_x, sample_y, deg=min(degree, sample_count - 1), domain=[start, stop]
        )
        segments.append(
            {
                "index": index,
                "x_start": float(start),
                "x_stop": float(stop),
                "coefficients": [float(value) for value in polynomial.coef],
            }
        )
    return {
        "requested_vpp": float(amplitude),
        "formula": "G_dB = sum(c_k * T_k(t)); t maps x linearly from x_start..x_stop to -1..1",
        "segments": segments,
    }


def _amplitude_holdout_rmse(groups: dict[float, list[tuple[float, float]]]) -> float | None:
    amplitudes = sorted(groups)
    if len(amplitudes) < 3:
        return None
    errors: list[float] = []
    for index in range(1, len(amplitudes) - 1):
        lower, current, upper = amplitudes[index - 1], amplitudes[index], amplitudes[index + 1]
        lower_x, lower_y = _samples_to_arrays(groups[lower])
        current_x, current_y = _samples_to_arrays(groups[current])
        upper_x, upper_y = _samples_to_arrays(groups[upper])
        shared = sorted(set(lower_x).intersection(current_x, upper_x))
        if not shared:
            continue
        lower_values = np.interp(shared, lower_x, lower_y)
        upper_values = np.interp(shared, upper_x, upper_y)
        current_values = np.interp(shared, current_x, current_y)
        weight = (current - lower) / (upper - lower)
        errors.extend(current_values - (lower_values + weight * (upper_values - lower_values)))
    return float(np.sqrt(np.mean(np.square(errors)))) if errors else None


def _minimum_metric(metrics: dict[str, float | None]) -> float | None:
    values = [value for value in metrics.values() if value is not None]
    return min(values) if values else None


def _row_float(row: dict[str, Any], name: str) -> float | None:
    try:
        value = float(row.get(name))
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not isfinite(result):
        raise ConfigError(f"{name} must be finite")
    return result


def _optional_float(value: Any, name: str) -> float | None:
    return None if value is None else _finite_float(value, name)


def _positive_float(value: Any, name: str) -> float:
    result = _finite_float(value, name)
    if result <= 0:
        raise ConfigError(f"{name} must be > 0")
    return result


def _optional_positive_float(value: Any, name: str) -> float | None:
    return None if value is None else _positive_float(value, name)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if result != value or result <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return result
