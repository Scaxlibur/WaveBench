from __future__ import annotations

from pathlib import Path
from typing import Any

from wavebench.errors import ConfigError
from wavebench.services.agent_observe import scope_observe_payload


def scope_advise_payload(
    *,
    config_path: str | Path,
    channel: int | None = None,
    channels: tuple[int, ...] | None = None,
    fetch_waveform: bool = False,
    allow_50ohm: bool = False,
    expectations: dict[int, dict[str, Any]] | None = None,
    target_cycles: float = 10.0,
    target_vertical_divisions: float = 5.0,
) -> dict[str, Any]:
    if target_cycles <= 0:
        raise ConfigError("scope.advise target_cycles must be > 0")
    if target_vertical_divisions <= 0:
        raise ConfigError("scope.advise target_vertical_divisions must be > 0")
    observation = scope_observe_payload(
        config_path=config_path,
        channel=channel,
        channels=channels,
        fetch_waveform=fetch_waveform,
        allow_50ohm=allow_50ohm,
        expectations=expectations if fetch_waveform else None,
    )
    recommendations = _recommendations(
        observation,
        expectations=expectations or {},
        target_cycles=float(target_cycles),
        target_vertical_divisions=float(target_vertical_divisions),
    )
    return {
        "status": observation["status"],
        "read_only": observation["read_only"],
        "query_only": observation.get("query_only", observation["read_only"]),
        "mutates_instrument": observation["mutates_instrument"],
        "raw_scpi": False,
        "applies_recommendations": False,
        "instrument_state_effects": observation["instrument_state_effects"],
        "observation": {
            "channel": observation["observation"]["channel"],
            "channels": observation["observation"]["channels"],
            "fetch_waveform": observation["observation"]["fetch_waveform"],
        },
        "recommendations": recommendations,
        "agent_hints": _agent_hints(observation, recommendations),
        "warnings": observation["warnings"],
    }


def _recommendations(
    observation: dict[str, Any],
    *,
    expectations: dict[int, dict[str, Any]],
    target_cycles: float,
    target_vertical_divisions: float,
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    channels = observation.get("channels", [])
    expected_frequencies = _expected_frequencies(expectations)
    channel_profiles: dict[int, dict[str, Any]] = {}
    for channel_section in channels:
        channel = channel_section.get("channel")
        if not isinstance(channel, int):
            continue
        summary = _waveform_summary(channel_section)
        snapshot = _scope_status_data(channel_section)
        frequency_hz, source, confidence = _frequency_for_advice(
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
            "frequency_source": source if frequency_hz is not None else None,
            "frequency_confidence": confidence,
            "time_range_s": time_range,
            "vertical_scale_v_per_div": vertical_scale,
        }
        if snapshot and snapshot.get("channel", {}).get("enabled") is False:
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
        if time_range is not None or vertical_scale is not None:
            reason = _focus_reason(
                summary,
                frequency_hz,
                source,
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
        recommendations.append(
            {
                "id": "no_adjustment_needed",
                "priority": "low",
                "action": "keep_current_scope_settings",
                "reason": "No obvious display or acquisition-window issue was found.",
                "mutates_instrument_if_applied": False,
                "raw_scpi": False,
            }
        )
    return recommendations


def _waveform_summary(channel_section: dict[str, Any]) -> dict[str, Any] | None:
    waveform = channel_section.get("waveform", {})
    if waveform.get("status") != "ok":
        return None
    summary = waveform.get("data", {}).get("summary")
    return summary if isinstance(summary, dict) else None


def _scope_status_data(channel_section: dict[str, Any]) -> dict[str, Any] | None:
    status = channel_section.get("scope_status", {})
    data = status.get("data")
    return data if isinstance(data, dict) else None


def _summary_frequency(summary: dict[str, Any] | None) -> float | None:
    if summary is None:
        return None
    value = summary.get("frequency_estimate_hz")
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _summary_frequency_confidence(summary: dict[str, Any] | None) -> str | None:
    if summary is None or _summary_frequency(summary) is None:
        return None
    warnings = summary.get("quality_warnings", [])
    if any(str(item).startswith("low_cycle_count") for item in warnings):
        return "low"
    return "measured"


def _frequency_for_advice(
    summary: dict[str, Any] | None,
    expected_frequency_hz: float | None,
) -> tuple[float | None, str | None, str | None]:
    measured = _summary_frequency(summary)
    confidence = _summary_frequency_confidence(summary)
    if expected_frequency_hz is not None and confidence == "low":
        return expected_frequency_hz, "expected", "configured"
    if measured is not None:
        return measured, "measured", confidence
    if expected_frequency_hz is not None:
        return expected_frequency_hz, "expected", "configured"
    return None, None, None


def _expected_frequencies(expectations: dict[int, dict[str, Any]]) -> dict[int, float]:
    values: dict[int, float] = {}
    for channel, expectation in expectations.items():
        value = expectation.get("frequency_hz")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            values[channel] = float(value)
    return values


def _recommended_time_range(frequency_hz: float, *, target_cycles: float) -> float:
    return float(target_cycles / frequency_hz)


def _recommended_vertical_scale(
    summary: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    *,
    target_vertical_divisions: float,
) -> float | None:
    vpp = None if summary is None else summary.get("voltage_vpp_v")
    if isinstance(vpp, (int, float)) and vpp > 0:
        return float(vpp) / target_vertical_divisions
    scale = None
    if snapshot is not None:
        scale = snapshot.get("channel", {}).get("scale_v_per_div")
    if isinstance(scale, (int, float)) and scale > 0:
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
    quality = summary.get("quality_warnings", [])
    return bool(quality)


def _focus_reason(
    summary: dict[str, Any] | None,
    frequency_hz: float | None,
    frequency_source: str,
    *,
    target_cycles: float,
) -> str:
    parts: list[str] = []
    if frequency_hz is not None:
        confidence_note = (
            " (low-confidence estimate)"
            if frequency_source == "measured" and _summary_frequency_confidence(summary) == "low"
            else ""
        )
        parts.append(
            f"use {frequency_source} frequency {frequency_hz:.6g} Hz{confidence_note} "
            f"to show about {target_cycles:.3g} cycles"
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
        pieces.extend(["--vertical-scale", f"{parameters['vertical_scale_v_per_div']:.12g}"])
    if parameters.get("hide_other_channels"):
        pieces.append("--hide-other-channels")
    return " ".join(pieces)


def _agent_hints(
    observation: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> list[str]:
    hints = list(observation.get("agent_hints", []))
    if any(item["id"] == "separate_timebase_profiles" for item in recommendations):
        hints.append("advise: run focus/observe per channel when frequencies differ greatly")
    if observation.get("mutates_instrument"):
        hints.append("advise: waveform fetch was used only to compute advice; recommendations were not applied")
    else:
        hints.append("advise: recommendations were computed without applying instrument changes")
    return hints
