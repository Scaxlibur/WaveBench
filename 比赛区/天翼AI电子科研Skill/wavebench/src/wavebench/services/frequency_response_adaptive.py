from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, log10
from typing import Any

from wavebench.errors import ConfigError
from wavebench.services.frequency_response import (
    FrequencyResponsePoint,
    response_gain_db,
    response_phase_unwrapped_deg,
)


@dataclass(frozen=True)
class FrequencyResponseAdaptiveConfig:
    enabled: bool = True
    gain_threshold_db: float = 0.5
    phase_threshold_deg: float = 10.0
    max_levels: int = 2
    max_frequency_points: int = 1000

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdaptiveFrequency:
    frequency_hz: float
    level: int
    parent_start_hz: float
    parent_stop_hz: float


@dataclass(frozen=True)
class AdaptiveRefinement:
    frequencies: tuple[AdaptiveFrequency, ...]
    budget_limited: bool


def normalize_frequency_response_adaptive(
    raw: Any, name: str = "adaptive"
) -> FrequencyResponseAdaptiveConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a TOML table")
    allowed = {"enabled", "gain_threshold_db", "phase_threshold_deg", "max_levels", "max_frequency_points"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{name} has unsupported keys: {', '.join(unknown)}")
    enabled = _bool(raw.get("enabled", True), f"{name}.enabled")
    gain = _positive_float(raw.get("gain_threshold_db", 0.5), f"{name}.gain_threshold_db")
    phase = _positive_float(raw.get("phase_threshold_deg", 10.0), f"{name}.phase_threshold_deg")
    levels = _positive_int(raw.get("max_levels", 2), f"{name}.max_levels")
    points = _positive_int(raw.get("max_frequency_points", 1000), f"{name}.max_frequency_points")
    return FrequencyResponseAdaptiveConfig(
        enabled=enabled,
        gain_threshold_db=gain,
        phase_threshold_deg=phase,
        max_levels=levels,
        max_frequency_points=points,
    )


def select_adaptive_frequency_refinement(
    points: list[FrequencyResponsePoint],
    *,
    spacing: str,
    level: int,
    config: FrequencyResponseAdaptiveConfig | dict[str, Any],
    existing_frequencies_hz: set[float],
) -> AdaptiveRefinement:
    """Select a rectangular-grid refinement from gain or phase changes in any Vpp slice."""
    if isinstance(config, dict):
        config = normalize_frequency_response_adaptive(config)
    if not config.enabled or level > config.max_levels:
        return AdaptiveRefinement((), False)
    if spacing not in {"linear", "log"}:
        raise ConfigError("adaptive refinement spacing must be 'linear' or 'log'")
    by_amplitude: dict[int, list[FrequencyResponsePoint]] = {}
    for point in points:
        if point.adaptive_level > level - 1:
            continue
        by_amplitude.setdefault(point.amplitude_index, []).append(point)
    selected: dict[float, AdaptiveFrequency] = {}
    for samples in by_amplitude.values():
        ordered = sorted(samples, key=lambda point: point.requested_frequency_hz)
        for left, right in zip(ordered, ordered[1:]):
            if not _requires_refinement(left, right, config):
                continue
            midpoint = _midpoint(left.requested_frequency_hz, right.requested_frequency_hz, spacing)
            if midpoint in existing_frequencies_hz or midpoint in selected:
                continue
            selected[midpoint] = AdaptiveFrequency(
                frequency_hz=midpoint,
                level=level,
                parent_start_hz=left.requested_frequency_hz,
                parent_stop_hz=right.requested_frequency_hz,
            )
    budget = max(0, config.max_frequency_points - len(existing_frequencies_hz))
    candidates = sorted(selected.values(), key=lambda item: item.frequency_hz)
    return AdaptiveRefinement(tuple(candidates[:budget]), len(candidates) > budget)


def _requires_refinement(
    left: FrequencyResponsePoint,
    right: FrequencyResponsePoint,
    config: FrequencyResponseAdaptiveConfig,
) -> bool:
    left_gain, right_gain = response_gain_db(left), response_gain_db(right)
    left_phase, right_phase = response_phase_unwrapped_deg(left), response_phase_unwrapped_deg(right)
    gain_change = (
        left_gain is not None and right_gain is not None and abs(right_gain - left_gain) >= config.gain_threshold_db
    )
    phase_change = (
        left_phase is not None
        and right_phase is not None
        and abs(right_phase - left_phase) >= config.phase_threshold_deg
    )
    return gain_change or phase_change


def _midpoint(start: float, stop: float, spacing: str) -> float:
    midpoint = (start + stop) / 2.0 if spacing == "linear" else 10.0 ** ((log10(start) + log10(stop)) / 2.0)
    if not isfinite(midpoint) or not start < midpoint < stop:
        raise ConfigError(f"cannot refine frequency interval {start:.12g} Hz .. {stop:.12g} Hz")
    return midpoint


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false")
    return value


def _positive_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not isfinite(result) or result <= 0:
        raise ConfigError(f"{name} must be > 0")
    return result


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if result != value or result <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return result
