from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from wavebench.config import WaveBenchConfig, load_config
from wavebench.errors import WaveBenchError
from wavebench.instruments.contracts import PowerDriver
from wavebench.instruments.models import PowerMeasurement, PowerProtectionStatus, PowerStatus
from wavebench.logging import CommandLogger
from wavebench.services.power_service import PowerService
from wavebench.tui.state import PowerPanelState, _logger_lines, channel_state_from_status, config_status


class PowerPanelAdapter(Protocol):
    def refresh(self) -> PowerPanelState:
        ...

    def refresh_measurements(self) -> PowerPanelState:
        ...

    def refresh_protection(self) -> PowerPanelState:
        ...

    def set_output(self, channel: int, enabled: bool) -> PowerPanelState:
        ...

    def set_voltage_current_limit(
        self, channel: int, voltage_v: float, current_limit_a: float
    ) -> PowerPanelState:
        ...

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
    ) -> PowerPanelState:
        ...


@dataclass
class PowerServicePanelAdapter:
    service: PowerService
    channels: tuple[int, ...] = (1, 2, 3)
    _instrument_id: str | None = None
    _statuses: dict[int, PowerStatus] = field(default_factory=dict)
    _protections: dict[int, PowerProtectionStatus] = field(default_factory=dict)
    _ui_log_lines: list[str] = field(default_factory=list)
    _needs_full_refresh: bool = True
    _power_session: PowerDriver | None = None

    @classmethod
    def from_config(
        cls, config_path: str | Path = "wavebench.toml", resource: str | None = None
    ) -> "PowerServicePanelAdapter":
        config = load_config(config_path)
        if resource:
            config = config.with_power_resource(resource)
        return cls(service=PowerService(config=config, logger=CommandLogger()))

    def refresh(self) -> PowerPanelState:
        if self._instrument_id is None:
            self._instrument_id = self._idn()
        statuses = [self._status(channel) for channel in self.channels]
        protections = [self._protection_status(channel) for channel in self.channels]
        self._statuses = {status.channel: status for status in statuses}
        self._protections = {protection.channel: protection for protection in protections}
        self._needs_full_refresh = False
        return self._build_state(statuses=statuses)

    def refresh_measurements(self) -> PowerPanelState:
        if self._needs_full_refresh or not self._has_full_cache():
            return self.refresh()
        try:
            for channel in self.channels:
                self._merge_status(self._status(channel))
                if self._power_session is None:
                    self._merge_measurement(self.service.measurement(channel=channel))
                self._protections[channel] = self._protection_status(channel)
        except Exception as exc:
            self._needs_full_refresh = True
            raise WaveBenchError(
                "实时状态刷新失败，下次将执行完整刷新 / "
                f"Live status refresh failed; next refresh will run full refresh: {exc}"
            ) from exc
        return self._build_state(statuses=[self._statuses[channel] for channel in self.channels])

    def refresh_protection(self) -> PowerPanelState:
        if self._needs_full_refresh or not self._has_full_cache():
            return self.refresh()
        protections = [self._protection_status(channel) for channel in self.channels]
        self._protections = {protection.channel: protection for protection in protections}
        return self._build_state(statuses=[self._statuses[channel] for channel in self.channels])

    def _build_state(self, *, statuses: list[PowerStatus]) -> PowerPanelState:
        return build_power_panel_state(
            config=self.service.config,
            instrument_id=self._instrument_id,
            statuses=statuses,
            protections=[self._protections.get(status.channel) for status in statuses],
            log_lines=self._log_lines(),
        )

    def set_output(self, channel: int, enabled: bool) -> PowerPanelState:
        session = self._persistent_session()
        if session is None:
            self._merge_status(self.service.set_output(channel=channel, enabled=enabled))
        else:
            power_cfg = self.service._power_config()
            if enabled:
                status = self._with_session(lambda power: power.get_status(channel))
                self.service._check_power_limits(
                    voltage_v=status.set_voltage_v,
                    current_limit_a=status.set_current_a,
                )
            self._merge_status(
                self._with_session(
                    lambda power: power.set_output(
                        channel,
                        enabled,
                        check_errors=power_cfg.check_errors,
                        settle_ms_after_output=power_cfg.settle_ms_after_output,
                    )
                )
            )
        return self.refresh_measurements()

    def set_voltage_current_limit(
        self, channel: int, voltage_v: float, current_limit_a: float
    ) -> PowerPanelState:
        session = self._persistent_session()
        if session is None:
            self._merge_status(
                self.service.set_voltage_current_limit(
                    channel=channel,
                    voltage_v=voltage_v,
                    current_limit_a=current_limit_a,
                )
            )
        else:
            power_cfg = self.service._power_config()
            self.service._check_power_limits(voltage_v=voltage_v, current_limit_a=current_limit_a)
            self._merge_status(
                self._with_session(
                    lambda power: power.set_voltage_current_limit(
                        channel,
                        voltage_v,
                        current_limit_a,
                        check_errors=power_cfg.check_errors,
                        settle_ms_after_set=power_cfg.settle_ms_after_set,
                    )
                )
            )
        return self.refresh_measurements()

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
    ) -> PowerPanelState:
        session = self._persistent_session()
        if session is None:
            self.service.set_protection(
                channel=channel,
                ovp_threshold_v=ovp_threshold_v,
                ovp_enabled=ovp_enabled,
                ocp_threshold_a=ocp_threshold_a,
                ocp_enabled=ocp_enabled,
            )
        else:
            power_cfg = self.service._power_config()
            if (
                ovp_threshold_v is None
                and ovp_enabled is None
                and ocp_threshold_a is None
                and ocp_enabled is None
            ):
                raise WaveBenchError("no protection change requested / 未请求保护设置变更")
            self.service._check_power_limits(voltage_v=ovp_threshold_v, current_limit_a=ocp_threshold_a)
            setpoints = self._with_session(lambda power: power.get_status(channel))
            protection = self._with_session(lambda power: power.get_protection_status(channel))
            self.service._check_protection_relationship(
                set_voltage_v=setpoints.set_voltage_v,
                set_current_a=setpoints.set_current_a,
                ovp_threshold_v=ovp_threshold_v if ovp_threshold_v is not None else protection.ovp_threshold_v,
                ocp_threshold_a=ocp_threshold_a if ocp_threshold_a is not None else protection.ocp_threshold_a,
            )
            self._with_session(
                lambda power: power.set_protection(
                    channel,
                    ovp_threshold_v=ovp_threshold_v,
                    ovp_enabled=ovp_enabled,
                    ocp_threshold_a=ocp_threshold_a,
                    ocp_enabled=ocp_enabled,
                    check_errors=power_cfg.check_errors,
                )
            )
        self._ui_log_lines.append(f"CH{channel} 保护设定完成 / Protection set complete")
        self.refresh()
        return self.refresh_measurements()

    def close(self) -> None:
        self._close_session()

    def _idn(self) -> str:
        session = self._persistent_session()
        if session is None:
            return self.service.idn()
        return self._with_session(lambda power: power.idn())

    def _status(self, channel: int) -> PowerStatus:
        session = self._persistent_session()
        if session is None:
            return self.service.status(channel=channel)
        return self._with_session(lambda power: power.get_status(channel))

    def _protection_status(self, channel: int) -> PowerProtectionStatus:
        session = self._persistent_session()
        if session is None:
            return self.service.protection_status(channel=channel)
        return self._with_session(lambda power: power.get_protection_status(channel))

    def _persistent_session(self) -> PowerDriver | None:
        open_session = getattr(self.service, "open_session", None)
        if open_session is None:
            return None
        if self._power_session is None:
            self._power_session = open_session()
        return self._power_session

    def _with_session(self, operation):
        session = self._persistent_session()
        if session is None:
            raise WaveBenchError("power session is not available / 电源会话不可用")
        try:
            return operation(session)
        except Exception:
            self._close_session()
            raise

    def _close_session(self) -> None:
        if self._power_session is None:
            return
        try:
            self._power_session.close()
        finally:
            self._power_session = None

    def _has_full_cache(self) -> bool:
        return all(channel in self._statuses for channel in self.channels)

    def _merge_status(self, status: PowerStatus) -> None:
        self._statuses[status.channel] = status

    def _merge_measurement(self, measurement: PowerMeasurement) -> None:
        self._statuses[measurement.channel] = merge_power_measurement(
            self._statuses[measurement.channel],
            measurement,
        )

    def _log_lines(self) -> list[str]:
        return (_logger_lines(self.service.logger) + self._ui_log_lines)[-80:]


@dataclass
class FakePowerPanelAdapter:
    statuses: dict[int, PowerStatus] = field(default_factory=dict)
    protections: dict[int, PowerProtectionStatus] = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)
    instrument_id: str = "RIGOL TECHNOLOGIES,DP832A,FAKE,0.0"

    def __post_init__(self) -> None:
        if not self.statuses:
            self.statuses = {
                channel: PowerStatus(
                    channel=channel,
                    output="OFF",
                    mode="CV",
                    rating="30V/3A" if channel < 3 else "5V/3A",
                    set_voltage_v=0.0,
                    set_current_a=0.1,
                    measured_voltage_v=0.0,
                    measured_current_a=0.0,
                    measured_power_w=0.0,
                )
                for channel in (1, 2, 3)
            }
        if not self.protections:
            self.protections = {
                channel: PowerProtectionStatus(
                    channel=channel,
                    ovp_enabled="ON",
                    ovp_threshold_v=6.0 if channel < 3 else 5.5,
                    ovp_tripped="NO",
                    ocp_enabled="ON",
                    ocp_threshold_a=0.5,
                    ocp_tripped="NO",
                )
                for channel in (1, 2, 3)
            }

    def refresh(self) -> PowerPanelState:
        self.log_lines.append("刷新 / Refresh fake DP832A snapshot")
        return PowerPanelState(
            config_status="演示模式 / Fake mode: DP832A snapshot",
            instrument_status=f"仪器 / Instrument: {self.instrument_id}",
            channels=tuple(
                channel_state_from_status(self.statuses[channel], self.protections[channel])
                for channel in (1, 2, 3)
            ),
            log_lines=tuple(self.log_lines[-80:]),
        )

    def refresh_measurements(self) -> PowerPanelState:
        self.log_lines.append("实时状态刷新 / Live status refresh fake DP832A snapshot")
        for channel, old in self.statuses.items():
            voltage = old.set_voltage_v if old.output == "ON" else 0.0
            measurement = PowerMeasurement(
                channel=channel,
                measured_voltage_v=voltage,
                measured_current_a=old.measured_current_a,
                measured_power_w=(voltage or 0.0) * (old.measured_current_a or 0.0),
            )
            self.statuses[channel] = merge_power_measurement(old, measurement)
        return self.refresh()

    def refresh_protection(self) -> PowerPanelState:
        self.log_lines.append("保护刷新 / Protection refresh fake DP832A snapshot")
        return self.refresh()

    def set_output(self, channel: int, enabled: bool) -> PowerPanelState:
        old = self.statuses[channel]
        self.statuses[channel] = PowerStatus(
            channel=old.channel,
            output="ON" if enabled else "OFF",
            mode=old.mode,
            rating=old.rating,
            set_voltage_v=old.set_voltage_v,
            set_current_a=old.set_current_a,
            measured_voltage_v=old.set_voltage_v if enabled else 0.0,
            measured_current_a=old.measured_current_a,
            measured_power_w=(old.set_voltage_v or 0.0) * (old.measured_current_a or 0.0),
        )
        self.log_lines.append(f"CH{channel} 输出 / Output -> {'ON' if enabled else 'OFF'}")
        return self.refresh_measurements()

    def set_voltage_current_limit(
        self, channel: int, voltage_v: float, current_limit_a: float
    ) -> PowerPanelState:
        old = self.statuses[channel]
        self.statuses[channel] = PowerStatus(
            channel=old.channel,
            output=old.output,
            mode=old.mode,
            rating=old.rating,
            set_voltage_v=voltage_v,
            set_current_a=current_limit_a,
            measured_voltage_v=voltage_v if old.output == "ON" else 0.0,
            measured_current_a=old.measured_current_a,
            measured_power_w=(voltage_v if old.output == "ON" else 0.0) * (old.measured_current_a or 0.0),
        )
        self.log_lines.append(f"CH{channel} 设定 / Set {voltage_v:g} V, {current_limit_a:g} A")
        return self.refresh_measurements()

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
    ) -> PowerPanelState:
        old = self.protections[channel]
        self.protections[channel] = PowerProtectionStatus(
            channel=old.channel,
            ovp_enabled=old.ovp_enabled if ovp_enabled is None else ("ON" if ovp_enabled else "OFF"),
            ovp_threshold_v=old.ovp_threshold_v if ovp_threshold_v is None else ovp_threshold_v,
            ovp_tripped=old.ovp_tripped,
            ocp_enabled=old.ocp_enabled if ocp_enabled is None else ("ON" if ocp_enabled else "OFF"),
            ocp_threshold_a=old.ocp_threshold_a if ocp_threshold_a is None else ocp_threshold_a,
            ocp_tripped=old.ocp_tripped,
        )
        self.log_lines.append(
            f"CH{channel} 保护设定 / Protection set: "
            f"OVP={self.protections[channel].ovp_enabled} {self.protections[channel].ovp_threshold_v:g} V, "
            f"OCP={self.protections[channel].ocp_enabled} {self.protections[channel].ocp_threshold_a:g} A"
        )
        return self.refresh_measurements()


def merge_power_measurement(status: PowerStatus, measurement: PowerMeasurement) -> PowerStatus:
    return replace(
        status,
        measured_voltage_v=measurement.measured_voltage_v,
        measured_current_a=measurement.measured_current_a,
        measured_power_w=measurement.measured_power_w,
    )


def build_power_panel_state(
    *,
    config: WaveBenchConfig,
    instrument_id: str,
    statuses: list[PowerStatus],
    protections: list[PowerProtectionStatus | None] | None = None,
    log_lines: list[str] | tuple[str, ...] = (),
) -> PowerPanelState:
    protections = [None] * len(statuses) if protections is None else protections
    return PowerPanelState(
        config_status=config_status(config),
        instrument_status=f"仪器 / Instrument: {instrument_id}",
        channels=tuple(
            channel_state_from_status(status, protection)
            for status, protection in zip(statuses, protections, strict=True)
        ),
        log_lines=tuple(log_lines),
    )
