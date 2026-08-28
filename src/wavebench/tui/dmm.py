from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Protocol

from wavebench.config import WaveBenchConfig, load_config
from wavebench.instruments.contracts import DmmDriver
from wavebench.instruments.dmm import normalize_dmm_function
from wavebench.instruments.models import DmmReading
from wavebench.logging import CommandLogger
from wavebench.services.dmm_service import DmmService
from wavebench.tui.state import DmmPanelState, _logger_lines, dmm_state_from_reading


class DmmPanelAdapter(Protocol):
    def read(self, function: str | None = None) -> DmmPanelState:
        ...

    def function_status(self) -> str:
        ...

    def set_function(self, function: str) -> DmmPanelState:
        ...


@dataclass
class DmmServicePanelAdapter:
    service: DmmService
    _instrument_id: str | None = None
    _active_function: str = "dcv"
    _dmm_session: DmmDriver | None = None

    @classmethod
    def from_config(
        cls, config_path: str | Path = "wavebench.toml", resource: str | None = None
    ) -> "DmmServicePanelAdapter":
        config = load_config(config_path)
        if resource:
            config = config.with_dmm_resource(resource)
        return cls(service=DmmService(config=config, logger=CommandLogger()))

    def function_status(self) -> str:
        session = self._persistent_session()
        if session is None:
            self._active_function = self.service.function_status()
            return self._active_function
        self._active_function = self._with_session(lambda dmm: dmm.function_status())
        return self._active_function

    def read(self, function: str | None = None) -> DmmPanelState:
        if self._instrument_id is None:
            self._instrument_id = self._idn()
        normalized_function = self._active_function if function is None else normalize_dmm_function(function)
        normalized_function = normalized_function or "dcv"
        self._active_function = normalized_function
        session = self._persistent_session()
        if session is None:
            reading = self.service.read(function=normalized_function)
        else:
            settle_s = self.service.config.dmm.settle_ms_before_read / 1000.0
            if settle_s > 0:
                time.sleep(settle_s)
            reading = self._with_session(lambda dmm: dmm.read(function=normalized_function))
        return build_dmm_panel_state(
            config=self.service.config,
            instrument_id=self._instrument_id,
            reading=reading,
            log_lines=_logger_lines(self.service.logger),
        )

    def set_function(self, function: str) -> DmmPanelState:
        if self._instrument_id is None:
            self._instrument_id = self._idn()
        requested_function = normalize_dmm_function(function) or "dcv"
        if requested_function == self._active_function:
            return self.read(function=requested_function)
        session = self._persistent_session()
        if session is None:
            applied_function = self.service.set_function(function=function)
        else:
            applied_function = self._with_session(lambda dmm: dmm.apply_function(function=function))
        self._active_function = applied_function
        settle_s = self.service.config.dmm.settle_ms_after_function_change / 1000.0
        if settle_s > 0:
            time.sleep(settle_s)
        if session is None:
            reading = self.service.read(function=applied_function)
        else:
            reading = self._with_session(lambda dmm: dmm.read(function=applied_function))
        return build_dmm_panel_state(
            config=self.service.config,
            instrument_id=self._instrument_id,
            reading=reading,
            log_lines=_logger_lines(self.service.logger),
        )

    def close(self) -> None:
        self._close_session()

    def _idn(self) -> str:
        session = self._persistent_session()
        if session is None:
            return self.service.idn()
        return self._with_session(lambda dmm: dmm.idn())

    def _persistent_session(self) -> DmmDriver | None:
        open_session = getattr(self.service, "open_session", None)
        if open_session is None:
            return None
        if self._dmm_session is None:
            self._dmm_session = open_session()
        return self._dmm_session

    def _with_session(self, operation):
        try:
            return operation(self._persistent_session())
        except Exception:
            self._close_session()
            raise

    def _close_session(self) -> None:
        if self._dmm_session is None:
            return
        try:
            self._dmm_session.close()
        finally:
            self._dmm_session = None


@dataclass
class FakeDmmPanelAdapter:
    readings: dict[str, DmmReading] = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)
    instrument_id: str = "RIGOL TECHNOLOGIES,DM3058,FAKE,0.0"
    current_function: str = "dcv"

    def __post_init__(self) -> None:
        if not self.readings:
            self.readings = {
                "dcv": DmmReading(function="dcv", value=1.2345, unit="V", raw="1.2345"),
                "acv": DmmReading(function="acv", value=0.0123, unit="V", raw="0.0123"),
                "res": DmmReading(function="res", value=1000.0, unit="ohm", raw="1000"),
            }

    def function_status(self) -> str:
        return self.current_function

    def set_function(self, function: str) -> DmmPanelState:
        key = function.strip().lower() or "dcv"
        aliases = {"vdc": "dcv", "vac": "acv", "ohm": "res", "r": "res", "cont": "continuity"}
        key = aliases.get(key, key)
        if key not in self.readings:
            self.readings[key] = DmmReading(function=key, value=0.0, unit="", raw="0")
        self.current_function = key
        self.log_lines.append(f"功能切换 / Function set fake DMM {self.current_function}")
        return self.read(function=self.current_function)

    def read(self, function: str | None = None) -> DmmPanelState:
        key = self.current_function if function is None else function.strip().lower()
        key = key or self.current_function or "dcv"
        aliases = {"vdc": "dcv", "vac": "acv", "ohm": "res", "r": "res", "cont": "continuity"}
        key = aliases.get(key, key)
        self.current_function = key
        reading = self.readings.get(
            key,
            DmmReading(function=key, value=0.0, unit="", raw="0"),
        )
        self.log_lines.append(f"读取 / Read fake DMM {reading.function}")
        return DmmPanelState(
            config_status="演示模式 / Fake mode: DM3000 snapshot",
            connection_status="已连接 / Connected",
            instrument_status=f"仪器 / Instrument: {self.instrument_id}",
            function=reading.function,
            value=f"{reading.value:.6g}",
            unit=reading.unit,
            raw_reading=reading.raw,
            log_lines=tuple(self.log_lines[-80:]),
        )


def build_dmm_panel_state(
    *,
    config: WaveBenchConfig,
    instrument_id: str,
    reading: DmmReading,
    log_lines: list[str] | tuple[str, ...] = (),
) -> DmmPanelState:
    return dmm_state_from_reading(
        config=config,
        instrument_id=instrument_id,
        reading=reading,
        log_lines=log_lines,
    )
