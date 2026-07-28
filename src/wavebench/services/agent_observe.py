from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from wavebench.config import load_config
from wavebench.data.expectations import evaluate_waveform_expectation, expectation_summary
from wavebench.data.relationships import analyze_waveform_relationships
from wavebench.errors import ConfigError, WaveBenchError
from wavebench.instruments.models import WaveformData
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService


def scope_observe_payload(
    *,
    config_path: str | Path,
    channel: int | None = None,
    channels: tuple[int, ...] | None = None,
    fetch_waveform: bool = False,
    allow_50ohm: bool = False,
    expectations: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    observed_channels = _scope_channels(
        channel=channel,
        channels=channels,
        default_channel=config.scope.default_channel,
    )
    normalized_expectations = _normalize_expectations(expectations)
    if normalized_expectations and not fetch_waveform:
        raise ConfigError("scope.observe expectations require fetch_waveform=true")
    service = ScopeService(config=config, logger=CommandLogger())
    sections: dict[str, Any] = {}
    warnings: list[str] = []
    fetched_waveforms: dict[int, WaveformData] = {}
    expectation_results: dict[int, dict[str, Any]] = {}

    sections["identity"] = _attempt(lambda: {"idn": service.idn()}, warnings=warnings, name="identity")
    channel_sections = [
        _observe_channel(
            service,
            observed_channel,
            fetch_waveform=fetch_waveform,
            allow_50ohm=allow_50ohm,
            warnings=warnings,
            fetched_waveforms=fetched_waveforms,
            expectations=normalized_expectations,
            expectation_results=expectation_results,
        )
        for observed_channel in observed_channels
    ]
    first_channel = channel_sections[0]
    sections["scope_status"] = first_channel["scope_status"]
    sections["coupling"] = first_channel["coupling"]
    sections["waveform"] = first_channel["waveform"]

    return {
        "status": "ok" if not warnings else "partial",
        "read_only": True,
        "mutates_instrument": fetch_waveform,
        "raw_scpi": False,
        "instrument_state_effects": _instrument_state_effects(fetch_waveform),
        "config": {
            "path": str(config.source_path),
            "scope_driver": config.scope.driver,
            "resource": config.connection.resource,
            "backend": config.connection.backend,
            "default_channel": config.scope.default_channel,
            "waveform_points": config.waveform.points,
        },
        "observation": {
            "instrument": "scope",
            "channel": observed_channels[0],
            "channels": list(observed_channels),
            "fetch_waveform": fetch_waveform,
            "allow_50ohm": allow_50ohm,
        },
        **sections,
        "channels": channel_sections,
        "relationships": (
            analyze_waveform_relationships(fetched_waveforms)
            if len(fetched_waveforms) >= 2
            else []
        ),
        "expectations": expectation_summary(expectation_results),
        "warnings": warnings,
        "agent_hints": _agent_hints(
            sections,
            warnings,
            channel_sections=channel_sections,
            fetched_waveforms=fetched_waveforms,
            expectation_results=expectation_results,
        ),
    }


def _scope_channels(
    *,
    channel: int | None,
    channels: tuple[int, ...] | None,
    default_channel: int,
) -> tuple[int, ...]:
    if channel is not None and channels is not None:
        raise ConfigError("scope.observe accepts either channel or channels, not both")
    candidates = channels if channels is not None else (default_channel if channel is None else channel,)
    if not candidates:
        raise ConfigError("scope.observe channels must not be empty")
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
            raise ConfigError("scope.observe channel must be a positive integer")
    if len(set(candidates)) != len(candidates):
        raise ConfigError("scope.observe channels must be unique")
    return candidates


def _normalize_expectations(
    expectations: dict[int, dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    if expectations is None:
        return {}
    normalized: dict[int, dict[str, Any]] = {}
    for channel, expectation in expectations.items():
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise ConfigError("scope.observe expectation channel must be a positive integer")
        if not isinstance(expectation, dict):
            raise ConfigError("scope.observe expectation entries must be objects")
        normalized[channel] = dict(expectation)
    return normalized


def _instrument_state_effects(fetch_waveform: bool) -> list[str]:
    if not fetch_waveform:
        return []
    return [
        "waveform transfer source/mode/format may be changed",
        "some drivers may enable the requested analog channel display before fetching",
    ]


def _observe_channel(
    service: ScopeService,
    channel: int,
    *,
    fetch_waveform: bool,
    allow_50ohm: bool,
    warnings: list[str],
    fetched_waveforms: dict[int, WaveformData],
    expectations: dict[int, dict[str, Any]],
    expectation_results: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    section = {
        "channel": channel,
        "scope_status": _attempt(
            lambda: asdict(service.status(channel=channel)),
            warnings=warnings,
            name=f"ch{channel}_scope_status",
        ),
        "coupling": _attempt(
            lambda: _coupling_payload(service, channel, allow_50ohm=allow_50ohm),
            warnings=warnings,
            name=f"ch{channel}_coupling",
        ),
    }
    if fetch_waveform:
        section["waveform"] = _attempt(
            lambda: _waveform_payload(
                service,
                channel,
                allow_50ohm=allow_50ohm,
                fetched_waveforms=fetched_waveforms,
            ),
            warnings=warnings,
            name=f"ch{channel}_waveform",
        )
    else:
        section["waveform"] = {
            "status": "skipped",
            "reason": "fetch_waveform=false",
        }
    if channel in expectations and channel in fetched_waveforms:
        result = evaluate_waveform_expectation(
            fetched_waveforms[channel],
            expectations[channel],
        )
        expectation_results[channel] = result
        section["expectation"] = {
            "status": "ok",
            "data": result,
        }
    elif channel in expectations:
        section["expectation"] = {
            "status": "unavailable",
            "reason": "waveform unavailable",
        }
    else:
        section["expectation"] = {
            "status": "skipped",
            "reason": "no expectation for channel",
        }
    return section


def _attempt(call, *, warnings: list[str], name: str) -> dict[str, Any]:
    try:
        return {"status": "ok", "data": call()}
    except WaveBenchError as exc:
        warnings.append(f"{name}_unavailable: {exc}")
        return {
            "status": "unavailable",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    except Exception as exc:
        warnings.append(f"{name}_unavailable: {type(exc).__name__}: {exc}")
        return {
            "status": "unavailable",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def _coupling_payload(
    service: ScopeService,
    channel: int,
    *,
    allow_50ohm: bool,
) -> dict[str, Any]:
    coupling = service.require_high_impedance(channel, allow_50ohm=allow_50ohm)
    return {
        "channel": channel,
        "coupling": coupling,
        "accepted_for_capture": True,
    }


def _waveform_payload(
    service: ScopeService,
    channel: int,
    *,
    allow_50ohm: bool,
    fetched_waveforms: dict[int, WaveformData],
) -> dict[str, Any]:
    service.require_high_impedance(channel, allow_50ohm=allow_50ohm)
    waveform = service.fetch_waveform(channel=channel)
    fetched_waveforms[channel] = waveform
    return {
        "channel": channel,
        "summary": waveform.summary(
            expected_frequency_hz=service.config.waveform.expected_frequency_hz,
            frequency_tolerance_ratio=service.config.waveform.frequency_tolerance_ratio,
        ),
        "raw_samples_included": False,
    }


def _agent_hints(
    sections: dict[str, Any],
    warnings: list[str],
    *,
    channel_sections: list[dict[str, Any]],
    fetched_waveforms: dict[int, WaveformData],
    expectation_results: dict[int, dict[str, Any]],
) -> list[str]:
    hints: list[str] = []
    for channel_section in channel_sections:
        waveform = channel_section.get("waveform", {})
        if waveform.get("status") != "ok":
            continue
        channel = channel_section.get("channel")
        summary = waveform.get("data", {}).get("summary", {})
        for warning in summary.get("quality_warnings", []) or []:
            hints.append(f"CH{channel}_waveform_quality_warning: {warning}")
        cycles = summary.get("estimated_cycles")
        if isinstance(cycles, (int, float)) and cycles < 5:
            hints.append(f"CH{channel}: consider capturing a wider time window for robust periodic analysis")
    if len(fetched_waveforms) >= 2:
        summaries = [waveform.summary() for waveform in fetched_waveforms.values()]
        frequencies = [
            summary.get("frequency_estimate_hz")
            for summary in summaries
            if isinstance(summary.get("frequency_estimate_hz"), (int, float))
            and not any(str(item).startswith("low_cycle_count") for item in summary.get("quality_warnings", []))
        ]
        if len(frequencies) >= 2 and min(frequencies) > 0 and max(frequencies) / min(frequencies) > 10:
            hints.append(
                "multi_channel_frequency_span_large: use separate time windows/profiles before judging waveform shape across channels"
            )
    expected_frequencies = [
        expectation.get("checks", [])
        for expectation in expectation_results.values()
    ]
    frequency_values: list[float] = []
    for checks in expected_frequencies:
        for check in checks:
            if check.get("metric") == "frequency_hz" and isinstance(check.get("expected"), (int, float)):
                frequency_values.append(float(check["expected"]))
    if len(frequency_values) >= 2 and min(frequency_values) > 0 and max(frequency_values) / min(frequency_values) > 10:
        hints.append(
            "expected_multi_channel_frequency_span_large: expectation frequencies span more than 10x; use separate acquisition windows for shape judgments"
        )
    for channel, result in sorted(expectation_results.items()):
        if result["status"] in {"warn", "fail"}:
            hints.append(f"CH{channel}_expectation_{result['status']}: inspect expectation checks")
    if sections.get("scope_status", {}).get("status") == "unavailable":
        hints.append("driver lacks scope.snapshot or the status query failed; use identity/waveform sections cautiously")
    if sections.get("coupling", {}).get("status") == "unavailable":
        hints.append("do not run capture until input coupling safety is confirmed")
    if warnings:
        hints.append("treat this observation as partial and avoid state-changing actions")
    return hints
