from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from wavebench.config import WaveBenchConfig, load_config
from wavebench.instruments.models import SourceStatus
from wavebench.logging import CommandLogger
from wavebench.services.source_service import SourceService
from wavebench.tui.state import SourcePanelState, _logger_lines, source_state_from_status


class SourcePanelAdapter(Protocol):
    def refresh(self) -> SourcePanelState:
        ...

    def set_output(self, enabled: bool) -> SourcePanelState:
        ...

    def set_function(self, function: str) -> SourcePanelState:
        ...

    def set_frequency(self, value_hz: float) -> SourcePanelState:
        ...

    def set_amplitude_vpp(self, value_vpp: float) -> SourcePanelState:
        ...


@dataclass
class SourceServicePanelAdapter:
    service: SourceService
    _instrument_id: str | None = None
    _default_channel: int | None = None

    @classmethod
    def from_config(
        cls, config_path: str | Path = "wavebench.toml", resource: str | None = None
    ) -> "SourceServicePanelAdapter":
        config = load_config(config_path)
        if resource:
            config = config.with_source_resource(resource)
        return cls(service=SourceService(config=config, logger=CommandLogger()))

    def refresh(self) -> SourcePanelState:
        status = self.service.status(channel=self._channel())
        return self._to_panel_state(status)

    def set_output(self, enabled: bool) -> SourcePanelState:
        status = self.service.set_output(channel=self._channel(), enabled=enabled)
        return self._to_panel_state(status)

    def set_function(self, function: str) -> SourcePanelState:
        status = self.service.set_function(channel=self._channel(), function=function)
        return self._to_panel_state(status)

    def set_frequency(self, value_hz: float) -> SourcePanelState:
        status = self.service.set_frequency(channel=self._channel(), value_hz=value_hz)
        return self._to_panel_state(status)

    def set_amplitude_vpp(self, value_vpp: float) -> SourcePanelState:
        status = self.service.set_amplitude_vpp(channel=self._channel(), value_vpp=value_vpp)
        return self._to_panel_state(status)

    def _channel(self) -> int | None:
        if self._default_channel is None:
            source = self.service.config.source
            self._default_channel = 1 if source is None else source.default_channel
        return self._default_channel

    def _to_panel_state(self, status: SourceStatus) -> SourcePanelState:
        if self._instrument_id is None:
            self._instrument_id = self.service.idn()
        return build_source_panel_state(
            config=self.service.config,
            instrument_id=self._instrument_id,
            status=status,
            log_lines=_logger_lines(self.service.logger),
        )


@dataclass
class FakeSourcePanelAdapter:
    status: SourceStatus = field(
        default_factory=lambda: SourceStatus(
            channel=1,
            output="OFF",
            function="SIN",
            frequency_hz=1000.0,
            amplitude=1.0,
            amplitude_unit="VPP",
            offset_v=0.0,
            phase_deg=0.0,
            frequency_mode="FIX",
            sweep_enabled="OFF",
            apply_raw="SIN,1000,1.0,0.0",
            square_duty_cycle_percent=50.0,
        )
    )
    log_lines: list[str] = field(default_factory=list)
    instrument_id: str = "RIGOL TECHNOLOGIES,DG4202,FAKE,0.0"

    def refresh(self) -> SourcePanelState:
        self.log_lines.append(f"刷新 / Refresh fake source CH{self.status.channel}")
        return self._build_state()

    def set_output(self, enabled: bool) -> SourcePanelState:
        self.status = replace(self.status, output="ON" if enabled else "OFF")
        self.log_lines.append(f"输出设定 / Output set fake source CH{self.status.channel} -> {self.status.output}")
        return self._build_state()

    def set_function(self, function: str) -> SourcePanelState:
        normalized = function.strip().upper()
        aliases = {
            "SINE": "SIN",
            "SIN": "SIN",
            "SQUARE": "SQU",
            "SQU": "SQU",
            "RAMP": "RAMP",
            "TRIANGLE": "RAMP",
            "TRI": "RAMP",
            "PULSE": "PULS",
            "PULS": "PULS",
            "NOISE": "NOIS",
            "NOIS": "NOIS",
            "DC": "DC",
        }
        self.status = replace(self.status, function=aliases.get(normalized, normalized or "SIN"))
        self.log_lines.append(f"波形设定 / Function set fake source CH{self.status.channel} -> {self.status.function}")
        return self._build_state()

    def set_frequency(self, value_hz: float) -> SourcePanelState:
        self.status = replace(self.status, frequency_hz=value_hz, frequency_mode="FIX", sweep_enabled="OFF")
        self.log_lines.append(f"频率设定 / Frequency set fake source CH{self.status.channel} -> {value_hz:.6g} Hz")
        return self._build_state()

    def set_amplitude_vpp(self, value_vpp: float) -> SourcePanelState:
        self.status = replace(self.status, amplitude=value_vpp, amplitude_unit="VPP")
        self.log_lines.append(f"幅度设定 / Vpp set fake source CH{self.status.channel} -> {value_vpp:.6g} Vpp")
        return self._build_state()

    def _build_state(self) -> SourcePanelState:
        return SourcePanelState(
            config_status="演示模式 / Fake mode: DG4202 snapshot",
            connection_status="已连接 / Connected",
            instrument_status=f"仪器 / Instrument: {self.instrument_id}",
            channel=self.status.channel,
            output_raw=self.status.output.strip().upper(),
            output="开 / ON" if self.status.output.strip().upper() == "ON" else "关 / OFF",
            function=self.status.function,
            frequency_hz=f"{self.status.frequency_hz:.6g}" if self.status.frequency_hz is not None else "未知 / N/A",
            amplitude_vpp=f"{self.status.amplitude:.6g}" if self.status.amplitude is not None else "未知 / N/A",
            offset_v=f"{self.status.offset_v:.6g}" if self.status.offset_v is not None else "未知 / N/A",
            log_lines=tuple(self.log_lines[-80:]),
        )


def build_source_panel_state(
    *,
    config: WaveBenchConfig,
    instrument_id: str,
    status: SourceStatus,
    log_lines: list[str] | tuple[str, ...] = (),
) -> SourcePanelState:
    return source_state_from_status(
        config=config,
        instrument_id=instrument_id,
        status=status,
        log_lines=log_lines,
    )
