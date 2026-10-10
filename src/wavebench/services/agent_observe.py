from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from wavebench.config import load_config
from wavebench.data.expectations import (
    evaluate_waveform_expectation,
    expectation_summary,
    validate_expectation,
)
from wavebench.data.relationships import analyze_waveform_relationships
from wavebench.errors import (
    ConfigError, ConnectionError, DataError, SessionHealthError, TransportIOError, WaveBenchError,
)
from wavebench.instruments.models import WaveformData
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService
from wavebench.transport.session import SessionHealth

# 读取波形可能造成的仪器状态影响。读取前不恢复原采集状态，调用方必须先确认。
_WAVEFORM_STATE_EFFECTS = [
    "a running acquisition may be stopped",
    "waveform transfer source/mode/format/points may be changed",
    "some drivers may enable the requested analog channel display before fetching",
    "the previous acquisition run state is not restored",
    "the instrument error queue may be consumed when check_errors is enabled",
]


@dataclass
class _ObservationExecution:
    service: ScopeService
    stop_reason: str | None = None
    mutation_block_reason: str | None = None

    def io_failed(self, exc: Exception) -> None:
        state = self.service.session_state
        if (
            isinstance(exc, (SessionHealthError, TransportIOError, ConnectionError, OSError))
            or (state is not None and state.health is not SessionHealth.HEALTHY)
            or not isinstance(exc, (ConfigError, DataError))
        ):
            self.stop_reason = f"instrument I/O stopped after {type(exc).__name__}: {exc}"

    def check_health(self) -> None:
        state = self.service.session_state
        if state is not None and state.health is not SessionHealth.HEALTHY:
            self.stop_reason = f"instrument I/O stopped because session health is {state.health.value}"


def scope_observe_payload(
    *,
    config_path: str | Path,
    channel: int | None = None,
    channels: tuple[int, ...] | None = None,
    allow_50ohm: bool = False,
    resource: str | None = None,
) -> dict[str, Any]:
    """通过受限查询执行身份、可用 V2 快照和高阻安全判断。

    该函数不读取波形，观察阶段限制为文本查询；V2 快照以 descriptor 的 pure-read 合同为准。
    插件属于可信本地代码，guard 的 I/O 限制不替代插件查询语义的合同验证。
    需要波形、期望值检查或多通道关系时，请使用 ``scope_waveform_report_payload``
    （对应显式 CLI 命令 ``wavebench scope observe --fetch-waveform``）。
    """
    return _build_observation(
        config_path=config_path,
        channel=channel,
        channels=channels,
        allow_50ohm=allow_50ohm,
        resource=resource,
        fetch_waveform=False,
        expectations=None,
    )


def scope_waveform_report_payload(
    *,
    config_path: str | Path,
    channel: int | None = None,
    channels: tuple[int, ...] | None = None,
    allow_50ohm: bool = False,
    expectations: dict[int, dict[str, Any]] | None = None,
    resource: str | None = None,
) -> dict[str, Any]:
    """显式读取波形并给出摘要、期望值检查和多通道关系。

    读取波形属于写操作：可能停止正在运行的采集、修改波形传输参数并打开通道显示。
    所有通道和采集参数都在采集写入之前完成预检；型号相关通道验证可能需要只读查询。
    """
    normalized_expectations = _normalize_expectations(expectations)
    return _build_observation(
        config_path=config_path,
        channel=channel,
        channels=channels,
        allow_50ohm=allow_50ohm,
        resource=resource,
        fetch_waveform=True,
        expectations=normalized_expectations,
    )


def _build_observation(
    *,
    config_path: str | Path,
    channel: int | None,
    channels: tuple[int, ...] | None,
    allow_50ohm: bool,
    resource: str | None,
    fetch_waveform: bool,
    expectations: dict[int, dict[str, Any]] | None,
) -> dict[str, Any]:
    config = load_config(config_path)
    if resource:
        config = config.with_resource(resource)
    if not fetch_waveform and config.scope.access == "read_write":
        config = replace(config, scope=replace(config.scope, access="read_only"))
    observed_channels = _scope_channels(
        channel=channel,
        channels=channels,
        default_channel=config.scope.default_channel,
    )
    unknown_expectation_channels = sorted(
        item for item in (expectations or {}) if item not in observed_channels
    )
    if unknown_expectation_channels:
        raise ConfigError(
            "scope observe expectation channels must be observed channels: "
            f"{', '.join(str(item) for item in unknown_expectation_channels)}"
        )
    service = ScopeService(config=config, logger=CommandLogger())
    sections: dict[str, Any] = {}
    warnings: list[str] = []
    fetched_waveforms: dict[int, WaveformData] = {}
    expectation_results: dict[int, dict[str, Any]] = {}
    execution = _ObservationExecution(service)
    channel_sections: list[dict[str, Any]] = []
    access_validation = _attempt(
        service.validate_observation_access, warnings=warnings, name="observation_access",
    )
    if access_validation["status"] != "ok":
        execution.stop_reason = "observation access/capability validation failed before session open"
    if fetch_waveform:
        validation = _attempt(
            service.validate_observation_fetch, warnings=warnings, name="waveform_validation",
        )
        if validation["status"] != "ok":
            execution.stop_reason = "waveform configuration/access/capability validation failed before session open"

    def collect() -> None:
        with service.session_context(observation=True):
            sections["identity"] = _attempt(
                lambda: {"idn": service.observation_identity()},
                warnings=warnings, name="identity", execution=execution, io=True,
            )
            if fetch_waveform and execution.mutation_block_reason is None:
                preflight = _attempt(
                    lambda: service.preflight_observation_fetch(
                        observed_channels, allow_50ohm=allow_50ohm,
                    ),
                    warnings=warnings, name="waveform_preflight", execution=execution, io=True,
                )
                if preflight["status"] != "ok":
                    execution.mutation_block_reason = "all-channel waveform preflight failed"
            for observed_channel in observed_channels:
                channel_sections.append(observe_channel(observed_channel))

    def observe_channel(observed_channel: int) -> dict[str, Any]:
        return _observe_channel(
            service, observed_channel, fetch_waveform=fetch_waveform,
            allow_50ohm=allow_50ohm, warnings=warnings,
            fetched_waveforms=fetched_waveforms, expectations=expectations or {},
            expectation_results=expectation_results, execution=execution,
        )

    lifecycle = _attempt(collect, warnings=warnings, name="session", execution=execution, io=True)
    if lifecycle["status"] != "ok":
        execution.stop_reason = "observation session unavailable or close failed"
        sections["session"] = lifecycle
        sections.setdefault("identity", lifecycle)
        for observed_channel in observed_channels[len(channel_sections):]:
            channel_sections.append(observe_channel(observed_channel))
    first_channel = channel_sections[0]
    sections["scope_status"] = first_channel["scope_status"]
    sections["coupling"] = first_channel["coupling"]

    payload: dict[str, Any] = {
        "status": "ok" if not warnings else "partial",
        "read_only": not fetch_waveform,
        "query_only": not fetch_waveform,
        "mutates_instrument": fetch_waveform,
        "raw_scpi": False,
        "instrument_state_effects": list(_WAVEFORM_STATE_EFFECTS) if fetch_waveform else [],
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
        "warnings": warnings,
    }
    if fetch_waveform:
        # 共享 session/lease 不能证明各通道来自同一次 acquisition。
        payload["waveform_source"] = {
            "same_acquisition": False,
            "reason": "channels are fetched channel-by-channel, not in one acquisition",
        }
        payload["relationships"] = []
        if len(fetched_waveforms) >= 2:
            relationships = _attempt(
                lambda: analyze_waveform_relationships(fetched_waveforms, same_acquisition=False),
                warnings=warnings, name="relationships",
            )
            if relationships["status"] == "ok":
                payload["relationships"] = relationships["data"]
        payload["expectations"] = expectation_summary(expectation_results)
    payload["agent_hints"] = _agent_hints(
        sections,
        warnings,
        channel_sections=channel_sections,
        fetched_waveforms=fetched_waveforms,
        expectation_results=expectation_results,
        fetch_waveform=fetch_waveform,
    )
    payload["status"] = "ok" if not warnings else "partial"
    return payload


def _scope_channels(
    *,
    channel: int | None,
    channels: tuple[int, ...] | None,
    default_channel: int,
) -> tuple[int, ...]:
    if channel is not None and channels is not None:
        raise ConfigError("scope observe accepts either channel or channels, not both")
    candidates = channels if channels is not None else (default_channel if channel is None else channel,)
    if not candidates:
        raise ConfigError("scope observe channels must not be empty")
    for candidate in candidates:
        if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
            raise ConfigError("scope observe channel must be a positive integer")
    if len(set(candidates)) != len(candidates):
        raise ConfigError("scope observe channels must be unique")
    return candidates


def _normalize_expectations(
    expectations: dict[int, dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    if expectations is None:
        return {}
    normalized: dict[int, dict[str, Any]] = {}
    for channel, expectation in expectations.items():
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise ConfigError("scope observe expectation channel must be a positive integer")
        normalized[channel] = validate_expectation(expectation)
    return normalized


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
    execution: _ObservationExecution,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "channel": channel,
        "scope_status": _attempt(
            lambda: service.observation_status(channel=channel),
            warnings=warnings,
            name=f"ch{channel}_scope_status",
            execution=execution, io=True,
        ),
        "coupling": _attempt(
            lambda: _coupling_payload(service, channel, allow_50ohm=allow_50ohm),
            warnings=warnings,
            name=f"ch{channel}_coupling",
            execution=execution, io=True,
        ),
    }
    if not fetch_waveform:
        return section
    section["waveform"] = _attempt(
        lambda: _waveform_payload(
            service,
            channel,
            allow_50ohm=allow_50ohm,
            fetched_waveforms=fetched_waveforms,
            execution=execution,
        ),
        warnings=warnings,
        name=f"ch{channel}_waveform",
        execution=execution, mutation=True,
    )
    if channel in expectations and section["waveform"]["status"] == "ok":
        result = _attempt(
            lambda: evaluate_waveform_expectation(
                fetched_waveforms[channel],
                expectations[channel],
            ),
            warnings=warnings,
            name=f"ch{channel}_expectation",
        )
        section["expectation"] = result
    elif channel in expectations:
        section["expectation"] = {
            "status": "unavailable",
            "reason": "waveform unavailable",
        }
        warnings.append(f"ch{channel}_expectation_unavailable: waveform unavailable")
    if channel in expectations:
        result = section["expectation"]
        expectation_results[channel] = (
            result["data"] if result["status"] == "ok"
            else {**result, "channel": channel, "checks": []}
        )
    return section


def _attempt(
    call, *, warnings: list[str], name: str,
    execution: _ObservationExecution | None = None, io: bool = False, mutation: bool = False,
) -> dict[str, Any]:
    if execution is not None:
        execution.check_health()
        reason = execution.stop_reason or (execution.mutation_block_reason if mutation else None)
        if reason and (io or mutation):
            warnings.append(f"{name}_skipped: {reason}")
            return {"status": "skipped", "reason": reason}
    try:
        data = call()
        if execution is not None:
            execution.check_health()
        return {"status": "ok", "data": data}
    except WaveBenchError as exc:
        if execution is not None and io:
            execution.io_failed(exc)
        warnings.append(f"{name}_unavailable: {exc}")
        return {
            "status": "unavailable",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    except Exception as exc:
        if execution is not None and io:
            execution.io_failed(exc)
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
    return service.observation_input_safety(channel, allow_50ohm=allow_50ohm)


def _waveform_payload(
    service: ScopeService,
    channel: int,
    *,
    allow_50ohm: bool,
    fetched_waveforms: dict[int, WaveformData],
    execution: _ObservationExecution,
) -> dict[str, Any]:
    try:
        service.observation_input_safety(channel, allow_50ohm=allow_50ohm)
        execution.check_health()
        if execution.stop_reason:
            raise ConfigError(execution.stop_reason)
        waveform = service.fetch_waveform(channel=channel)
    except Exception as exc:
        execution.io_failed(exc)
        if isinstance(exc, ConfigError):
            execution.mutation_block_reason = f"waveform mutation stopped after ConfigError: {exc}"
        raise
    summary = waveform.summary(
        expected_frequency_hz=service.config.waveform.expected_frequency_hz,
        frequency_tolerance_ratio=service.config.waveform.frequency_tolerance_ratio,
    )
    fetched_waveforms[channel] = waveform
    return {
        "channel": channel,
        "summary": summary,
        "raw_samples_included": False,
    }


def _agent_hints(
    sections: dict[str, Any],
    warnings: list[str],
    *,
    channel_sections: list[dict[str, Any]],
    fetched_waveforms: dict[int, WaveformData],
    expectation_results: dict[int, dict[str, Any]],
    fetch_waveform: bool,
) -> list[str]:
    hints: list[str] = []
    if not fetch_waveform:
        hints.append(
            "read-only observation: waveforms were not read; use `wavebench scope observe --fetch-waveform` "
            "when waveform summaries, expectations or multi-channel relationships are required"
        )
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
        hints.append(
            "waveforms were read channel-by-channel, so they are not from one acquisition; "
            "timing relationships (phase/correlation/intersections) were skipped. "
            "Use `wavebench scope capture --channel ... --synchronized` for driver-proven single-acquisition capture"
        )
        frequencies = _trusted_frequencies(fetched_waveforms)
        if len(frequencies) >= 2 and min(frequencies) > 0 and max(frequencies) / min(frequencies) > 10:
            hints.append(
                "multi_channel_frequency_span_large: use separate time windows/profiles before judging waveform shape across channels"
            )
    for channel, result in sorted(expectation_results.items()):
        if result["status"] in {"warn", "fail"}:
            hints.append(f"CH{channel}_expectation_{result['status']}: inspect expectation checks")
        elif result["status"] == "skipped":
            hints.append(f"CH{channel}_expectation_skipped: expectation contains no checkable metric")
        elif result["status"] == "unavailable":
            hints.append(f"CH{channel}_expectation_unavailable: acceptance could not be evaluated")
    if sections.get("scope_status", {}).get("status") == "unavailable":
        hints.append("pure-query V2 snapshot is unavailable; identity alone cannot assess scope settings")
    if sections.get("coupling", {}).get("status") == "unavailable":
        hints.append("do not run capture until input coupling safety is confirmed")
    if warnings:
        hints.append("treat this observation as partial and avoid state-changing actions")
    return hints


def _trusted_frequencies(waveforms: dict[int, WaveformData]) -> list[float]:
    frequencies: list[float] = []
    for waveform in waveforms.values():
        summary = waveform.summary()
        value = summary.get("frequency_estimate_hz")
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        if any(
            str(item).startswith("low_cycle_count")
            for item in summary.get("quality_warnings", []) or []
        ):
            continue
        frequencies.append(float(value))
    return frequencies
