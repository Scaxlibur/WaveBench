from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from wavebench.errors import ConfigError
from wavebench.services.agent_observe import scope_observe_payload


def scope_advise_payload(
    *,
    config_path: str | Path,
    channel: int | None = None,
    channels: tuple[int, ...] | None = None,
    allow_50ohm: bool = False,
    expected_frequencies_hz: dict[int, float] | None = None,
    target_cycles: float = 10.0,
    target_vertical_divisions: float = 5.0,
    resource: str | None = None,
) -> dict[str, Any]:
    """只读建议：基于示波器状态快照和调用方提供的期望频率给出显示/时基建议。

    该函数不读取波形、不改变仪器状态，也不基于低置信度的测量频率下结论。
    需要基于实测波形的建议时，使用 ``scope_waveform_report_payload`` 的结果调用
    ``scope_advise_from_observation``。
    """
    # 参数校验必须发生在打开任何仪器会话之前
    target_cycles, target_vertical_divisions = validate_scope_advice_targets(
        target_cycles=target_cycles,
        target_vertical_divisions=target_vertical_divisions,
    )
    expected_frequencies = _normalize_expected_frequencies(expected_frequencies_hz)
    observation = scope_observe_payload(
        config_path=config_path,
        channel=channel,
        channels=channels,
        allow_50ohm=allow_50ohm,
        resource=resource,
    )
    return scope_advise_from_observation(
        observation,
        expected_frequencies_hz=expected_frequencies,
        target_cycles=target_cycles,
        target_vertical_divisions=target_vertical_divisions,
    )


def scope_advise_from_observation(
    observation: dict[str, Any],
    *,
    expected_frequencies_hz: dict[int, float] | None = None,
    target_cycles: float = 10.0,
    target_vertical_divisions: float = 5.0,
) -> dict[str, Any]:
    target_cycles, target_vertical_divisions = validate_scope_advice_targets(
        target_cycles=target_cycles,
        target_vertical_divisions=target_vertical_divisions,
    )
    expected = _normalize_expected_frequencies(expected_frequencies_hz)
    recommendations = _recommendations(
        observation,
        expected_frequencies=expected,
        target_cycles=target_cycles,
        target_vertical_divisions=target_vertical_divisions,
    )
    return {
        "status": observation["status"],
        "read_only": observation["read_only"],
        "query_only": observation["query_only"],
        "mutates_instrument": observation["mutates_instrument"],
        "raw_scpi": False,
        "applies_recommendations": False,
        "instrument_state_effects": observation["instrument_state_effects"],
        "observation": {
            "channel": observation["observation"]["channel"],
            "channels": observation["observation"]["channels"],
            "fetch_waveform": observation["observation"]["fetch_waveform"],
        },
        "expected_frequencies_hz": {str(item): value for item, value in sorted(expected.items())},
        "recommendations": recommendations,
        "agent_hints": _agent_hints(observation, recommendations),
        "warnings": observation["warnings"],
    }


def validate_scope_advice_targets(
    *, target_cycles: float, target_vertical_divisions: float,
) -> tuple[float, float]:
    """Validate advice targets before observation or instrument I/O."""
    return (
        _positive_finite(target_cycles, name="scope.advise target_cycles"),
        _positive_finite(target_vertical_divisions, name="scope.advise target_vertical_divisions"),
    )


def _normalize_expected_frequencies(values: dict[int, float] | None) -> dict[int, float]:
    if values is None:
        return {}
    normalized: dict[int, float] = {}
    for channel, value in values.items():
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise ConfigError("expected frequency channel must be a positive integer")
        normalized[channel] = _positive_finite(value, name=f"expected frequency for channel {channel}")
    return normalized


def _positive_finite(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ConfigError(f"{name} must be finite and > 0")
    return number


def _recommendations(
    observation: dict[str, Any],
    *,
    expected_frequencies: dict[int, float],
    target_cycles: float,
    target_vertical_divisions: float,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    channels = observation.get("channels", [])
    channel_profiles: dict[int, dict[str, Any]] = {}
    for channel_section in channels:
        channel = channel_section.get("channel")
        if not isinstance(channel, int):
            continue
        recommendation_count = len(recommendations)
        summary = _waveform_summary(channel_section)
        snapshot = _scope_status_data(channel_section)
        frequency_hz, source, confidence, withheld_reason = _frequency_for_advice(
            summary,
            expected_frequencies.get(channel),
        )
        vertical_scale = _recommended_vertical_scale(
            summary,
            snapshot,
            target_vertical_divisions=target_vertical_divisions,
        )
        time_range = (
            _recommended_time_range(frequency_hz, target_cycles=target_cycles)
            if frequency_hz is not None
            else None
        )
        channel_profiles[channel] = {
            "channel": channel,
            "frequency_hz": frequency_hz,
            "frequency_source": source,
            "frequency_confidence": confidence,
            "time_range_s": time_range,
            "vertical_scale_v_per_div": vertical_scale,
        }
        snapshot_channel = None if snapshot is None else snapshot.get("channel")
        if isinstance(snapshot_channel, dict) and snapshot_channel.get("enabled") is False:
            recommendations.append(
                _command_recommendation(
                    "display_on",
                    "high",
                    channel,
                    "Channel display is off; enable it before human visual inspection.",
                    "display",
                    {"channel": channel, "state": "on"},
                )
            )
        if time_range is None and withheld_reason is not None:
            # 低置信度测量又没有可用的期望频率时，明确说明为何不给时基建议
            recommendations.append(
                {
                    "id": "timebase_advice_withheld",
                    "priority": "normal",
                    "channel": channel,
                    "action": "provide_expected_frequency",
                    "reason": withheld_reason,
                    "mutates_instrument_if_applied": False,
                    "raw_scpi": False,
                }
            )
        if time_range is not None or vertical_scale is not None:
            reason = _focus_reason(
                summary,
                frequency_hz,
                source,
                confidence,
                target_cycles=target_cycles,
            )
            priority = "high" if _needs_focus(summary, channel, expected_frequencies) else "normal"
            recommendations.append(
                _command_recommendation(
                    "focus_channel",
                    priority,
                    channel,
                    reason,
                    "focus",
                    {
                        "channel": channel,
                        "time_range_s": time_range,
                        "vertical_scale_v_per_div": vertical_scale,
                        "frequency_confidence": confidence,
                        "hide_other_channels": False,
                    },
                )
            )
        if len(recommendations) == recommendation_count:
            recommendations.append(_unavailable_advice(channel))
    span = _frequency_span(channel_profiles)
    if span is not None and span["ratio_high_over_low"] > 10.0:
        recommendations.append(
            {
                "id": "separate_timebase_profiles",
                "priority": "high",
                "action": "capture_or_observe_channels_separately",
                "reason": (
                    "Observed or expected channel frequencies span more than 10x; "
                    "do not judge every waveform shape on one timebase."
                ),
                "mutates_instrument_if_applied": False,
                "raw_scpi": False,
                "frequency_span": span,
                "profiles": [
                    profile
                    for _, profile in sorted(channel_profiles.items())
                    if profile["time_range_s"] is not None
                ],
            }
        )
    if not recommendations:
        recommendations.append(_unavailable_advice(None))
    return recommendations


def _unavailable_advice(channel: int | None) -> dict[str, Any]:
    recommendation: dict[str, Any] = {
        "id": "advice_unavailable",
        "priority": "normal",
        "action": "obtain_scope_evidence",
        "reason": (
            "No usable display settings, waveform metrics or expected frequency are available; "
            "the current settings could not be assessed."
        ),
        "mutates_instrument_if_applied": False,
        "raw_scpi": False,
    }
    if channel is not None:
        recommendation["channel"] = channel
    return recommendation


def _waveform_summary(channel_section: dict[str, Any]) -> dict[str, Any] | None:
    waveform = channel_section.get("waveform", {})
    if waveform.get("status") != "ok":
        return None
    summary = waveform.get("data", {}).get("summary")
    return summary if isinstance(summary, dict) else None


def _scope_status_data(channel_section: dict[str, Any]) -> dict[str, Any] | None:
    status = channel_section.get("scope_status", {})
    if status.get("status") not in {"ok", "partial"}:
        return None
    data = status.get("data")
    return data if isinstance(data, dict) else None


def _frequency_for_advice(
    summary: dict[str, Any] | None,
    expected_frequency_hz: float | None,
) -> tuple[float | None, str | None, str | None, str | None]:
    """返回 (频率, 来源, 置信度, 撤回建议的原因)。

    低置信度的测量频率（``low_cycle_count`` 等质量告警）不能用来推导时基建议；
    此时优先回退到调用方提供的期望频率，没有期望频率就不给时基建议。
    """
    measured = _summary_frequency(summary)
    if measured is not None and not _summary_frequency_low_confidence(summary):
        return measured, "measured", "measured", None
    if expected_frequency_hz is not None:
        source = "expected" if measured is None else "expected_over_low_confidence_measurement"
        return expected_frequency_hz, source, "configured", None
    if measured is not None:
        return (
            None,
            None,
            "low",
            "measured frequency is low confidence (few cycles in window) and no expected frequency was provided",
        )
    return None, None, None, None


def _summary_frequency(summary: dict[str, Any] | None) -> float | None:
    if summary is None:
        return None
    value = summary.get("frequency_estimate_hz")
    if (
        not isinstance(value, (int, float)) or isinstance(value, bool)
        or not math.isfinite(value) or value <= 0
    ):
        return None
    return float(value)


def _summary_frequency_low_confidence(summary: dict[str, Any] | None) -> bool:
    if summary is None:
        return False
    return any(
        str(item).startswith("low_cycle_count")
        for item in summary.get("quality_warnings", []) or []
    )


def _recommended_time_range(frequency_hz: float, *, target_cycles: float) -> float:
    return float(target_cycles / frequency_hz)


def _recommended_vertical_scale(
    summary: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    *,
    target_vertical_divisions: float,
) -> float | None:
    vpp = None if summary is None else summary.get("voltage_vpp_v")
    if (
        isinstance(vpp, (int, float)) and not isinstance(vpp, bool)
        and math.isfinite(vpp) and vpp > 0
    ):
        return float(vpp) / target_vertical_divisions
    scale = None
    if snapshot is not None:
        snapshot_channel = snapshot.get("channel")
        if isinstance(snapshot_channel, dict):
            scale = snapshot_channel.get("scale_v_per_div")
    if (
        isinstance(scale, (int, float)) and not isinstance(scale, bool)
        and math.isfinite(scale) and scale > 0
    ):
        return float(scale)
    return None


def _needs_focus(
    summary: dict[str, Any] | None,
    channel: int,
    expected_frequencies: dict[int, float],
) -> bool:
    if channel in expected_frequencies and summary is None:
        return True
    if summary is None:
        return False
    cycles = summary.get("estimated_cycles")
    if isinstance(cycles, (int, float)) and (cycles < 5.0 or cycles > 25.0):
        return True
    points_per_cycle = summary.get("points_per_cycle")
    if isinstance(points_per_cycle, (int, float)) and points_per_cycle < 20.0:
        return True
    return bool(summary.get("quality_warnings"))


def _focus_reason(
    summary: dict[str, Any] | None,
    frequency_hz: float | None,
    frequency_source: str | None,
    frequency_confidence: str | None,
    *,
    target_cycles: float,
) -> str:
    parts: list[str] = []
    if frequency_hz is not None:
        parts.append(
            f"use {frequency_source} frequency {frequency_hz:.6g} Hz "
            f"(confidence={frequency_confidence}) to show about {target_cycles:.3g} cycles"
        )
    if summary is not None:
        cycles = summary.get("estimated_cycles")
        if isinstance(cycles, (int, float)):
            parts.append(f"current window contains about {cycles:.3g} cycles")
        points = summary.get("points_per_cycle")
        if isinstance(points, (int, float)):
            parts.append(f"current sampling density is about {points:.3g} points/cycle")
    return "; ".join(parts) if parts else "focus the selected channel for visual inspection"


def _frequency_span(profiles: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    values = [
        (channel, profile["frequency_hz"])
        for channel, profile in profiles.items()
        if isinstance(profile.get("frequency_hz"), (int, float)) and profile["frequency_hz"] > 0
    ]
    if len(values) < 2:
        return None
    low_channel, low = min(values, key=lambda item: item[1])
    high_channel, high = max(values, key=lambda item: item[1])
    return {
        "low_channel": low_channel,
        "low_hz": low,
        "high_channel": high_channel,
        "high_hz": high,
        "ratio_high_over_low": float(high / low),
    }


def _command_recommendation(
    recommendation_id: str,
    priority: str,
    channel: int,
    reason: str,
    command: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": recommendation_id,
        "priority": priority,
        "channel": channel,
        "action": f"scope.{command}",
        "reason": reason,
        "command": _command_text(command, parameters),
        "parameters": parameters,
        "mutates_instrument_if_applied": True,
        "raw_scpi": False,
    }


def _command_text(command: str, parameters: dict[str, Any]) -> str:
    if command == "display":
        return (
            "wavebench scope display "
            f"--channel {parameters['channel']} {parameters['state']}"
        )
    pieces = ["wavebench", "scope", "focus", "--channel", str(parameters["channel"])]
    if parameters.get("time_range_s") is not None:
        pieces.extend(["--time-range", f"{parameters['time_range_s']:.12g}"])
    if parameters.get("vertical_scale_v_per_div") is not None:
        pieces.extend([
            "--vertical-scale",
            f"{parameters['channel']}={parameters['vertical_scale_v_per_div']:.12g}",
        ])
    if parameters.get("hide_other_channels"):
        pieces.append("--hide-others")
    return " ".join(pieces)


def _agent_hints(
    observation: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> list[str]:
    hints = list(observation.get("agent_hints", []))
    if any(item["id"] == "advice_unavailable" for item in recommendations):
        hints.append("advise: insufficient evidence is not a recommendation to keep current settings")
    if any(item["id"] == "separate_timebase_profiles" for item in recommendations):
        hints.append("advise: run focus/observe per channel when frequencies differ greatly")
    if any(item["id"] == "timebase_advice_withheld" for item in recommendations):
        hints.append(
            "advise: timebase advice withheld for at least one channel because the measured frequency "
            "is low confidence and no expected frequency was provided"
        )
    if observation.get("mutates_instrument"):
        hints.append("advise: recommendations were computed from an explicit waveform read and were not applied")
    else:
        hints.append("advise: recommendations were computed without reading waveforms or changing instrument state")
    return hints
