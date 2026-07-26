from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import cast

from wavebench.config import DmmConfig, WaveBenchConfig
from wavebench.errors import ConfigError
from wavebench.instruments.contracts import (
    DmmDriver,
    DmmMeasurementProfileDriver,
    DmmVoltageConfigurationDriver,
)
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import (
    DmmDcvImpedanceConfiguration,
    DmmMeasurementProfile,
    DmmReading,
    DmmVoltageRangeConfiguration,
)
from wavebench.logging import CommandLogger
from wavebench.instruments.registry import resolve_instrument_descriptor


@dataclass
class DmmService:
    config: WaveBenchConfig
    logger: CommandLogger
    session: DmmDriver | None = None
    descriptor: InstrumentDescriptor | None = None

    def _require(self, operation: str, *capabilities: str) -> None:
        dmm = self._dmm_config()
        descriptor = self.descriptor or resolve_instrument_descriptor(
            dmm.driver,
            expected_kind="dmm",
        )
        require_capabilities(descriptor, capabilities, operation=operation)

    def _dmm_config(self) -> DmmConfig:
        if self.config.dmm is None or not self.config.dmm.resource:
            raise ConfigError("dmm resource is not configured. Set [dmm].resource or pass --resource.")
        return self.config.dmm

    def _open_dmm(self) -> DmmDriver:
        dmm = self._dmm_config()
        opened = open_instrument_driver(
            driver_reference=dmm.driver,
            expected_kind="dmm",
            resource=dmm.resource or "",
            configured_backend=dmm.backend,
            timeout_ms=dmm.timeout_ms,
            opc_timeout_ms=dmm.timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            options=getattr(dmm, "options", {}),
            serial_config=dmm,
        )
        self.descriptor = opened.descriptor
        return opened.driver

    def open_session(self) -> DmmDriver:
        return self._open_dmm()

    @contextmanager
    def _dmm_session(self) -> Iterator[DmmDriver]:
        if self.session is not None:
            yield self.session
            return
        dmm = self._open_dmm()
        try:
            yield dmm
        finally:
            dmm.close()

    def idn(self) -> str:
        self._require("dmm.idn", "dmm.idn")
        with self._dmm_session() as dmm:
            return dmm.idn()

    def function_status(self) -> str:
        self._require("dmm.function_status", "dmm.function_status")
        with self._dmm_session() as dmm:
            return dmm.function_status()

    def set_function(self, function: str) -> str:
        self._require("dmm.set_function", "dmm.set_function")
        with self._dmm_session() as dmm:
            return dmm.set_function(function=function)

    def measurement_profile(self) -> DmmMeasurementProfile:
        self._require("dmm.measurement_profile", "dmm.measurement_profile")
        with self._dmm_session() as dmm:
            return cast(DmmMeasurementProfileDriver, dmm).measurement_profile()

    def set_voltage_range(
        self,
        function: str,
        range_code: int,
    ) -> DmmVoltageRangeConfiguration:
        normalized = function.strip().lower()
        if normalized not in {"dcv", "acv"}:
            raise ConfigError("DMM voltage range function must be dcv or acv")
        if isinstance(range_code, bool) or not isinstance(range_code, int) or not 0 <= range_code <= 4:
            raise ConfigError("DMM voltage range code must be an integer from 0 to 4")
        self._require("dmm.set_voltage_range", "dmm.set_voltage_range")
        with self._dmm_session() as dmm:
            return cast(DmmVoltageConfigurationDriver, dmm).set_voltage_range(
                function=normalized,
                range_code=range_code,
            )

    def set_dcv_impedance(self, impedance: str) -> DmmDcvImpedanceConfiguration:
        normalized = impedance.strip().upper()
        if normalized not in {"10M", "10G"}:
            raise ConfigError("DMM DCV impedance must be 10M or 10G")
        self._require("dmm.set_dcv_impedance", "dmm.set_dcv_impedance")
        with self._dmm_session() as dmm:
            return cast(DmmVoltageConfigurationDriver, dmm).set_dcv_impedance(normalized)

    def read(self, function: str = "dcv") -> DmmReading:
        self._require("dmm.read", "dmm.read")
        with self._dmm_session() as dmm:
            settle_s = self._dmm_config().settle_ms_before_read / 1000.0
            if settle_s > 0:
                time.sleep(settle_s)
            return dmm.read(function=function)
