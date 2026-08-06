from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from wavebench.errors import ConfigError
from wavebench.services.frequency_response import FrequencyResponsePoint


BASELINE_MODES = ("complex_transfer", "phase_only", "delay_only")


@dataclass(frozen=True)
class FrequencyResponseBaselineConfig:
    run_dir: str
    response: str | None = None
    mode: str = "complex_transfer"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_frequency_response_baseline(
    raw: Any, name: str = "baseline"
) -> FrequencyResponseBaselineConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{name} must be a TOML table")
    unknown = sorted(set(raw) - {"run_dir", "response", "mode"})
    if unknown:
        raise ConfigError(f"{name} has unsupported keys: {', '.join(unknown)}")
    run_dir = _text(raw.get("run_dir"), f"{name}.run_dir")
    response = raw.get("response")
    if response is not None:
        response = _text(response, f"{name}.response")
    mode = _text(raw.get("mode", "complex_transfer"), f"{name}.mode").lower()
    if mode not in BASELINE_MODES:
        raise ConfigError(f"{name}.mode must be one of: {', '.join(BASELINE_MODES)}")
    return FrequencyResponseBaselineConfig(run_dir=run_dir, response=response, mode=mode)


def apply_frequency_response_baseline(
    points: list[FrequencyResponsePoint],
    baseline_rows: Iterable[dict[str, Any]],
    config: FrequencyResponseBaselineConfig | dict[str, Any],
) -> tuple[list[FrequencyResponsePoint], dict[str, Any]]:
    """Apply an auditable software baseline without replacing raw response fields."""
    if isinstance(config, dict):
        config = normalize_frequency_response_baseline(config)
    baseline = _baseline_by_amplitude(baseline_rows)
    corrected: list[FrequencyResponsePoint] = []
    used_amplitudes: set[float | None] = set()
    for point in points:
        if point.status == "failed" or point.gain_db is None or point.phase_unwrapped_deg is None:
            corrected.append(point)
            continue
        key, frequencies, gains, phases = _select_baseline_slice(baseline, point.requested_vpp)
        frequency = point.requested_frequency_hz
        if frequency < frequencies[0] or frequency > frequencies[-1]:
            raise ConfigError(
                "baseline frequency domain does not cover response point "
                f"{frequency:.12g} Hz for requested_vpp {point.requested_vpp!r}"
            )
        x = np.log10(frequencies)
        point_x = float(np.log10(frequency))
        baseline_gain = float(np.interp(point_x, x, gains))
        baseline_phase = float(np.interp(point_x, x, phases))
        delay_s = _estimate_delay_s(frequencies, phases)
        gain_db = point.gain_db
        phase = point.phase_unwrapped_deg
        if config.mode == "complex_transfer":
            gain_db -= baseline_gain
            phase -= baseline_phase
        elif config.mode == "phase_only":
            phase -= baseline_phase
        else:
            # phase slope = -360 * delay, so remove a positive measured delay
            # by adding 360 * f * delay to the DUT's raw phase.
            phase += 360.0 * frequency * delay_s
        corrected.append(
            replace(
                point,
                baseline_gain_db=baseline_gain,
                baseline_phase_unwrapped_deg=baseline_phase,
                gain_db_corrected=gain_db,
                gain_linear_corrected=float(10.0 ** (gain_db / 20.0)),
                phase_unwrapped_corrected_deg=phase,
                phase_wrapped_corrected_deg=_wrap_phase_deg(phase),
            )
        )
        used_amplitudes.add(key)
    domains = {
        str(key) if key is not None else "unspecified": [float(values[0][0]), float(values[0][-1])]
        for key, values in baseline.items()
    }
    delays = {
        str(key) if key is not None else "unspecified": _estimate_delay_s(values[0], values[2])
        for key, values in baseline.items()
    }
    return corrected, {
        "schema_version": 1,
        "baseline": config.as_dict(),
        "mode": config.mode,
        "valid_domain_hz_by_requested_vpp": domains,
        "estimated_delay_s_by_requested_vpp": delays,
        "used_baseline_requested_vpp": sorted(value for value in used_amplitudes if value is not None),
    }


def write_frequency_response_baseline_json(path: str | Path, document: dict[str, Any]) -> Path:
    """Write the software-correction provenance without touching instrument state."""
    output = Path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(output)
    return output


def _baseline_by_amplitude(
    rows: Iterable[dict[str, Any]],
) -> dict[float | None, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    grouped: dict[float | None, dict[float, tuple[float, float]]] = {}
    for row in rows:
        if str(row.get("status", "ok")).lower() == "failed":
            continue
        frequency = _float(row.get("requested_frequency_hz"))
        gain = _first_float(row, "gain_db_corrected", "gain_db")
        phase = _first_float(
            row, "phase_unwrapped_corrected_deg", "phase_unwrapped_deg"
        )
        amplitude = _float(row.get("requested_vpp"))
        if frequency is None or frequency <= 0 or gain is None or phase is None:
            continue
        grouped.setdefault(amplitude, {})[frequency] = (gain, phase)
    result: dict[float | None, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for amplitude, samples in grouped.items():
        frequencies = np.asarray(sorted(samples), dtype=float)
        if frequencies.size < 2:
            continue
        result[amplitude] = (
            frequencies,
            np.asarray([samples[value][0] for value in frequencies], dtype=float),
            np.asarray([samples[value][1] for value in frequencies], dtype=float),
        )
    if not result:
        raise ConfigError("baseline response has no usable gain and unwrapped-phase points")
    return result


def _select_baseline_slice(
    baseline: dict[float | None, tuple[np.ndarray, np.ndarray, np.ndarray]],
    requested_vpp: float | None,
) -> tuple[float | None, np.ndarray, np.ndarray, np.ndarray]:
    if requested_vpp in baseline:
        frequencies, gains, phases = baseline[requested_vpp]
        return requested_vpp, frequencies, gains, phases
    if len(baseline) == 1:
        key, values = next(iter(baseline.items()))
        return key, *values
    if requested_vpp is None and None in baseline:
        frequencies, gains, phases = baseline[None]
        return None, frequencies, gains, phases
    choices = ", ".join(str(value) for value in sorted(value for value in baseline if value is not None))
    raise ConfigError(
        "baseline has multiple requested_vpp slices but no matching slice for "
        f"{requested_vpp!r}; available: {choices}"
    )


def _estimate_delay_s(frequencies: np.ndarray, phases_deg: np.ndarray) -> float:
    if frequencies.size < 2:
        return 0.0
    slope, _intercept = np.polyfit(frequencies, phases_deg, 1)
    return float(-slope / 360.0)


def _wrap_phase_deg(value: float) -> float:
    return float((value + 180.0) % 360.0 - 180.0)


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _first_float(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = _float(row.get(name))
        if value is not None:
            return value
    return None


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value.strip()
