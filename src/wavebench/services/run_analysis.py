from __future__ import annotations

from typing import Any

import numpy as np

from wavebench.data.fft import analyze_fft


def capture_fft_summary(capture: Any) -> dict[str, Any]:
    npy_path = getattr(capture, "npy_path", None)
    if npy_path is None:
        return {"status": "unavailable", "error": "missing npy artifact"}
    try:
        analysis = analyze_fft(np.load(npy_path), max_harmonic_order=5)
    except Exception as exc:  # noqa: BLE001 - run artifacts should explain expected analysis failures
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok", **analysis}


def step_status(artifact: dict[str, Any]) -> str:
    response = artifact.get("frequency_response", {})
    if isinstance(response, dict) and response.get("status") == "failed":
        return "failed"
    if artifact.get("expect", {}).get("status") == "failed":
        return "failed"
    if artifact.get("expect_fft", {}).get("status") == "failed":
        return "failed"
    if isinstance(response, dict) and response.get("status") == "warning":
        return "warning"
    return "ok"


def evaluate_expect(values: dict[str, Any], expect: dict[str, dict[str, float]]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    failures: list[str] = []
    for metric, limits in expect.items():
        raw_value = values.get(metric)
        if raw_value is None:
            checks[metric] = {"status": "failed", "reason": "unavailable", "limits": limits}
            failures.append(f"{metric}: unavailable")
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            checks[metric] = {
                "status": "failed",
                "reason": "not_numeric",
                "value": raw_value,
                "limits": limits,
            }
            failures.append(f"{metric}: not numeric")
            continue
        status = "ok"
        reasons: list[str] = []
        minimum = limits.get("min")
        maximum = limits.get("max")
        if minimum is not None and value < minimum:
            status = "failed"
            reasons.append(f"below min {minimum}")
        if maximum is not None and value > maximum:
            status = "failed"
            reasons.append(f"above max {maximum}")
        checks[metric] = {
            "status": status,
            "value": value,
            "limits": limits,
        }
        if reasons:
            checks[metric]["reasons"] = reasons
            failures.append(f"{metric}: {value} {'; '.join(reasons)}")
    return {"status": "failed" if failures else "ok", "checks": checks, "failures": failures}


def capture_consistency(artifacts: list[dict[str, Any]], quality_config: Any) -> dict[str, Any]:
    required = quality_config.consistency_required_captures
    if len(artifacts) < required:
        return {
            "status": "insufficient_captures",
            "required_captures": required,
            "actual_captures": len(artifacts),
        }
    window = artifacts[-required:]
    checks: dict[str, Any] = {}
    checks["frequency_estimate_hz"] = _relative_consistency(
        _metric_values(window, "frequency_estimate_hz"), quality_config.frequency_consistency_ratio
    )
    checks["voltage_vpp_v"] = _relative_consistency(
        _metric_values(window, "voltage_vpp_v"), quality_config.voltage_vpp_consistency_ratio
    )
    checks["voltage_mean_v"] = _absolute_consistency(
        _metric_values(window, "voltage_mean_v"), quality_config.voltage_mean_consistency_v
    )
    checks["duty_cycle"] = _absolute_consistency(
        _metric_values(window, "duty_cycle"), quality_config.duty_consistency
    )
    usable = {name: check for name, check in checks.items() if check["status"] != "unavailable"}
    if not usable:
        return {
            "status": "no_comparable_metrics",
            "required_captures": required,
            "actual_captures": len(artifacts),
            "checks": checks,
        }
    status = (
        "consistent" if all(check["status"] == "ok" for check in usable.values()) else "diverged"
    )
    return {
        "status": status,
        "required_captures": required,
        "actual_captures": len(artifacts),
        "checks": checks,
    }


def _metric_values(artifacts: list[dict[str, Any]], metric: str) -> list[float] | None:
    values: list[float] = []
    for artifact in artifacts:
        value = artifact.get("quality", {}).get(metric)
        if value is None:
            return None
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            return None
    return values


def _relative_consistency(values: list[float] | None, tolerance_ratio: float) -> dict[str, Any]:
    if not values:
        return {"status": "unavailable"}
    span = max(values) - min(values)
    reference = max(max(abs(value) for value in values), 1e-12)
    ratio = span / reference
    return {
        "status": "ok" if ratio <= tolerance_ratio else "diverged",
        "span": span,
        "ratio": ratio,
        "tolerance_ratio": tolerance_ratio,
    }


def _absolute_consistency(values: list[float] | None, tolerance: float) -> dict[str, Any]:
    if not values:
        return {"status": "unavailable"}
    span = max(values) - min(values)
    return {
        "status": "ok" if span <= tolerance else "diverged",
        "span": span,
        "tolerance": tolerance,
    }
