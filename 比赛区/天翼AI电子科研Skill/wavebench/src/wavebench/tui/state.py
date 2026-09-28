from __future__ import annotations

from dataclasses import dataclass

from wavebench.config import WaveBenchConfig
from wavebench.instruments.models import DmmReading, PowerProtectionStatus, PowerStatus, SourceStatus
from wavebench.logging import CommandLogger


@dataclass(frozen=True)
class PowerChannelState:
    channel: int
    output: str
    mode: str
    rating: str
    set_voltage: str
    set_current: str
    measured_voltage: str
    measured_current: str
    measured_power: str
    ovp_enabled: str = "未知 / N/A"
    ovp_threshold: str = "未知 / N/A"
    ovp_tripped: str = "未知 / N/A"
    ocp_enabled: str = "未知 / N/A"
    ocp_threshold: str = "未知 / N/A"
    ocp_tripped: str = "未知 / N/A"


@dataclass(frozen=True)
class PowerPanelState:
    config_status: str
    instrument_status: str
    channels: tuple[PowerChannelState, ...]
    log_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class DmmPanelState:
    config_status: str
    connection_status: str
    instrument_status: str
    function: str
    value: str
    unit: str
    raw_reading: str
    log_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcePanelState:
    config_status: str
    connection_status: str
    instrument_status: str
    channel: int
    output_raw: str
    output: str
    function: str
    frequency_hz: str
    amplitude_vpp: str
    offset_v: str
    log_lines: tuple[str, ...] = ()


POWER_TABLE_COLUMNS = (
    "通道 / CH",
    "行 / Row",
    "项目 A / Item A",
    "项目 B / Item B",
)

SOURCE_TABLE_COLUMNS = (
    "通道 / CH",
    "输出 / Output",
    "波形 / Function",
    "频率 / Freq Hz",
    "幅度 / Vpp",
    "偏置 / Offset V",
)


def _logger_lines(logger: CommandLogger) -> list[str]:
    return [f"{entry.timestamp} {entry.direction} {entry.text}" for entry in logger.entries[-80:]]


def format_optional_number(value: float | None, unit: str = "", digits: int = 6) -> str:
    if value is None:
        return "未知 / N/A"
    text = f"{value:.{digits}g}"
    return f"{text}{unit}"


def format_output_state(output: str) -> str:
    normalized = output.strip().upper()
    if normalized in {"ON", "1"}:
        return "开 / ON"
    if normalized in {"OFF", "0"}:
        return "关 / OFF"
    return f"未知 / {output}"


def format_enabled_state(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"ON", "1", "YES"}:
        return "启用 / ON"
    if normalized in {"OFF", "0", "NO"}:
        return "禁用 / OFF"
    return f"未知 / {value}"


def format_tripped_state(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"YES", "ON", "1"}:
        return "已触发 / YES"
    if normalized in {"NO", "OFF", "0"}:
        return "未触发 / NO"
    return f"未知 / {value}"


def channel_state_from_status(
    status: PowerStatus,
    protection: PowerProtectionStatus | None = None,
) -> PowerChannelState:
    return PowerChannelState(
        channel=status.channel,
        output=format_output_state(status.output),
        mode=status.mode or "未知 / N/A",
        rating=status.rating or "未知 / N/A",
        set_voltage=format_optional_number(status.set_voltage_v, " V"),
        set_current=format_optional_number(status.set_current_a, " A"),
        measured_voltage=format_optional_number(status.measured_voltage_v, " V"),
        measured_current=format_optional_number(status.measured_current_a, " A"),
        measured_power=format_optional_number(status.measured_power_w, " W"),
        ovp_enabled="未知 / N/A" if protection is None else format_enabled_state(protection.ovp_enabled),
        ovp_threshold="未知 / N/A" if protection is None else format_optional_number(protection.ovp_threshold_v, " V"),
        ovp_tripped="未知 / N/A" if protection is None else format_tripped_state(protection.ovp_tripped),
        ocp_enabled="未知 / N/A" if protection is None else format_enabled_state(protection.ocp_enabled),
        ocp_threshold="未知 / N/A" if protection is None else format_optional_number(protection.ocp_threshold_a, " A"),
        ocp_tripped="未知 / N/A" if protection is None else format_tripped_state(protection.ocp_tripped),
    )


def config_status(config: WaveBenchConfig) -> str:
    if config.power is None:
        return "电源配置缺失 / Power config missing"
    resource = config.power.resource or "未配置 / not configured"
    return (
        f"驱动 / Driver: {config.power.driver} | "
        f"资源 / Resource: {resource} | "
        f"默认通道 / Default CH: {config.power.default_channel}"
    )


def dmm_config_status(config: WaveBenchConfig) -> str:
    if config.dmm is None:
        return "万用表配置缺失 / DMM config missing"
    resource = config.dmm.resource or "未配置 / not configured"
    return (
        f"驱动 / Driver: {config.dmm.driver} | "
        f"后端 / Backend: {config.dmm.backend} | "
        f"资源 / Resource: {resource}"
    )


def source_config_status(config: WaveBenchConfig) -> str:
    if config.source is None:
        return "信号源配置缺失 / Source config missing"
    resource = config.source.resource or "未配置 / not configured"
    return (
        f"驱动 / Driver: {config.source.driver} | "
        f"资源 / Resource: {resource} | "
        f"默认通道 / Default CH: {config.source.default_channel}"
    )


def source_state_from_status(
    *,
    config: WaveBenchConfig,
    instrument_id: str,
    status: SourceStatus,
    log_lines: list[str] | tuple[str, ...] = (),
) -> SourcePanelState:
    amplitude_vpp = "未知 / N/A"
    amplitude_unit = (status.amplitude_unit or "").strip().upper()
    if status.amplitude is not None:
        if amplitude_unit == "VPP":
            amplitude_vpp = f"{status.amplitude:.6g}"
        else:
            unit = status.amplitude_unit or ""
            amplitude_vpp = f"非VPP / not VPP ({status.amplitude:.6g} {unit})"
    return SourcePanelState(
        config_status=source_config_status(config),
        connection_status="已连接 / Connected",
        instrument_status=f"仪器 / Instrument: {instrument_id}",
        channel=status.channel,
        output_raw=status.output.strip().upper(),
        output=format_output_state(status.output),
        function=status.function,
        frequency_hz=format_optional_number(status.frequency_hz),
        amplitude_vpp=amplitude_vpp,
        offset_v=format_optional_number(status.offset_v),
        log_lines=tuple(log_lines),
    )


def dmm_state_from_reading(
    *,
    config: WaveBenchConfig,
    instrument_id: str,
    reading: DmmReading,
    log_lines: list[str] | tuple[str, ...] = (),
) -> DmmPanelState:
    return DmmPanelState(
        config_status=dmm_config_status(config),
        connection_status="已连接 / Connected",
        instrument_status=f"仪器 / Instrument: {instrument_id}",
        function=reading.function,
        value=format_optional_number(reading.value),
        unit=reading.unit,
        raw_reading=reading.raw,
        log_lines=tuple(log_lines),
    )
