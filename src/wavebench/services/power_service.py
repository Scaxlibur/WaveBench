from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import math
from typing import Any

from wavebench.config import PowerConfig, WaveBenchConfig
from wavebench.errors import ConfigError
from wavebench.instruments.contracts import PowerDriver
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import PowerMeasurement, PowerProtectionStatus, PowerStatus
from wavebench.logging import CommandLogger
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.services.access_policy import access_policy
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.state_guard import PowerStateGuard
from wavebench.transport.base import InstrumentTransport


@dataclass
class PowerService:
    config: WaveBenchConfig
    logger: CommandLogger
    session: PowerDriver | None = None
    descriptor: InstrumentDescriptor | None = None
    transport: InstrumentTransport | None = None
    lease: ResourceLease | None = None
    state_guard: PowerStateGuard | None = None

    def _require(self, operation: str, *capabilities: str) -> None:
        power = self._power_config()
        access_policy(getattr(power, "access", "read_write"), "power.access").require(
            require_operation_spec(operation),
            operation=operation,
        )
        descriptor = self.descriptor or resolve_instrument_descriptor(
            power.driver,
            expected_kind="power",
        )
        require_capabilities(descriptor, capabilities, operation=operation)

    def _power_config(self) -> PowerConfig:
        if self.config.power is None or not self.config.power.resource:
            raise ConfigError("power resource is not configured. Set [power].resource or pass --resource.")
        return self.config.power

    def _open_power(self) -> PowerDriver:
        power = self._power_config()
        opened = open_instrument_driver(
            driver_reference=power.driver,
            expected_kind="power",
            resource=power.resource or "",
            configured_backend=self.config.connection.backend,
            timeout_ms=self.config.connection.timeout_ms,
            opc_timeout_ms=self.config.connection.opc_timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            settings={"check_errors": power.check_errors},
            options=getattr(power, "options", {}),
            access=getattr(power, "access", "read_write"),
            lease=self.lease,
        )
        self.descriptor = opened.descriptor
        self.transport = opened.transport
        return opened.driver

    def audit_snapshot(self) -> dict[str, Any] | None:
        snapshot = getattr(self.transport, "audit_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def state_guard_snapshot(self) -> dict[str, dict[str, object]] | None:
        return self.state_guard.snapshot() if self.state_guard is not None else None

    def _state_guard_before_write(
        self,
        power: PowerDriver,
        channel: int,
        *,
        force_off: bool = False,
    ) -> None:
        if self.state_guard is None:
            return
        self.state_guard.before_write(power.get_status(channel), force_off=force_off)

    def _state_guard_after_write(self, status: PowerStatus) -> None:
        if self.state_guard is not None:
            self.state_guard.after_write(status)

    def open_session(self) -> PowerDriver:
        return self._open_power()

    @contextmanager
    def _power_session(self) -> Iterator[PowerDriver]:
        if self.session is not None:
            yield self.session
            return
        power = self._open_power()
        try:
            yield power
        finally:
            power.close()

    def idn(self) -> str:
        self._require("power.idn", "power.idn")
        with self._power_session() as power:
            return power.idn()

    def status(self, channel: int | None = None) -> PowerStatus:
        power_cfg = self._power_config()
        channel = power_cfg.default_channel if channel is None else channel
        self._require("power.status", "power.status")
        with self._power_session() as power:
            status = power.get_status(channel)
            if self.state_guard is not None:
                self.state_guard.observe(status)
            return status

    def measurement(self, channel: int | None = None) -> PowerMeasurement:
        power_cfg = self._power_config()
        channel = power_cfg.default_channel if channel is None else channel
        self._require("power.measurement", "power.measurement")
        with self._power_session() as power:
            return power.get_measurement(channel)

    def protection_status(self, channel: int | None = None) -> PowerProtectionStatus:
        power_cfg = self._power_config()
        channel = power_cfg.default_channel if channel is None else channel
        self._require("power.protection_status", "power.protection")
        with self._power_session() as power:
            return power.get_protection_status(channel)

    def set_protection(
        self,
        channel: int | None,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
    ) -> PowerProtectionStatus:
        power_cfg = self._power_config()
        channel = power_cfg.default_channel if channel is None else channel
        if (
            ovp_threshold_v is None
            and ovp_enabled is None
            and ocp_threshold_a is None
            and ocp_enabled is None
        ):
            raise ConfigError("no protection change requested / 未请求保护设置变更")
        self._check_power_limits(voltage_v=ovp_threshold_v, current_limit_a=ocp_threshold_a)
        self._require("power.set_protection", "power.status", "power.protection")
        with self._power_session() as power:
            setpoints = power.get_status(channel)
            protection = power.get_protection_status(channel)
            effective_ovp = ovp_threshold_v if ovp_threshold_v is not None else protection.ovp_threshold_v
            effective_ocp = ocp_threshold_a if ocp_threshold_a is not None else protection.ocp_threshold_a
            self._check_protection_relationship(
                set_voltage_v=setpoints.set_voltage_v,
                set_current_a=setpoints.set_current_a,
                ovp_threshold_v=effective_ovp,
                ocp_threshold_a=effective_ocp,
            )
            return power.set_protection(
                channel,
                ovp_threshold_v=ovp_threshold_v,
                ovp_enabled=ovp_enabled,
                ocp_threshold_a=ocp_threshold_a,
                ocp_enabled=ocp_enabled,
                check_errors=power_cfg.check_errors,
            )

    def set_voltage_current_limit(
        self, channel: int | None, voltage_v: float, current_limit_a: float
    ) -> PowerStatus:
        power_cfg = self._power_config()
        self._check_power_limits(voltage_v=voltage_v, current_limit_a=current_limit_a)
        channel = power_cfg.default_channel if channel is None else channel
        required = ["power.set_voltage_current_limit"]
        if self.state_guard is not None:
            required.append("power.status")
        self._require("power.set_voltage_current_limit", *required)
        with self._power_session() as power:
            self._state_guard_before_write(power, channel)
            result = power.set_voltage_current_limit(
                channel,
                voltage_v,
                current_limit_a,
                check_errors=power_cfg.check_errors,
                settle_ms_after_set=power_cfg.settle_ms_after_set,
            )
            self._state_guard_after_write(result)
            return result

    def _check_power_limits(self, *, voltage_v: float | None, current_limit_a: float | None) -> None:
        if voltage_v is not None and not math.isfinite(voltage_v):
            raise ConfigError("power voltage must be finite / 电源电压必须是有限数")
        if current_limit_a is not None and not math.isfinite(current_limit_a):
            raise ConfigError("power current limit must be finite / 电源限流必须是有限数")
        max_voltage = self.config.safety_limits.max_power_voltage_v
        if max_voltage is not None and voltage_v is not None and voltage_v > max_voltage:
            raise ConfigError(
                f"safety limit exceeded / 安全上限已超出: power voltage {voltage_v:.12g} V "
                f"> max_power_voltage_v {max_voltage:.12g} V"
            )
        max_current = self.config.safety_limits.max_power_current_limit_a
        if max_current is not None and current_limit_a is not None and current_limit_a > max_current:
            raise ConfigError(
                f"safety limit exceeded / 安全上限已超出: power current limit {current_limit_a:.12g} A "
                f"> max_power_current_limit_a {max_current:.12g} A"
            )

    def _check_protection_relationship(
        self,
        *,
        set_voltage_v: float | None,
        set_current_a: float | None,
        ovp_threshold_v: float | None,
        ocp_threshold_a: float | None,
    ) -> None:
        if set_voltage_v is not None and ovp_threshold_v is not None and ovp_threshold_v < set_voltage_v:
            raise ConfigError(
                f"protection threshold unsafe / 保护阈值不安全: OVP {ovp_threshold_v:.12g} V "
                f"< set voltage {set_voltage_v:.12g} V"
            )
        if set_current_a is not None and ocp_threshold_a is not None and ocp_threshold_a < set_current_a:
            raise ConfigError(
                f"protection threshold unsafe / 保护阈值不安全: OCP {ocp_threshold_a:.12g} A "
                f"< current limit {set_current_a:.12g} A"
            )

    def set_output(self, channel: int | None, enabled: bool) -> PowerStatus:
        power_cfg = self._power_config()
        channel = power_cfg.default_channel if channel is None else channel
        required = ["power.output"]
        if enabled:
            required.extend(("power.status", "power.protection"))
        if self.state_guard is not None and "power.status" not in required:
            required.append("power.status")
        self._require("power.output", *required)
        with self._power_session() as power:
            current = None
            if self.state_guard is not None:
                current = power.get_status(channel)
                self.state_guard.before_write(current, force_off=not enabled)
            if enabled:
                status = current if current is not None else power.get_status(channel)
                protection = power.get_protection_status(channel)
                self._check_power_limits(
                    voltage_v=status.set_voltage_v,
                    current_limit_a=status.set_current_a,
                )
                self._check_protection_relationship(
                    set_voltage_v=status.set_voltage_v,
                    set_current_a=status.set_current_a,
                    ovp_threshold_v=protection.ovp_threshold_v,
                    ocp_threshold_a=protection.ocp_threshold_a,
                )
                if protection.ovp_tripped != "NO" or protection.ocp_tripped != "NO":
                    raise ConfigError(
                        "power output ON rejected: protection trip is active / "
                        "电源输出开启被拒绝：保护已触发"
                    )
            result = power.set_output(
                channel,
                enabled,
                check_errors=power_cfg.check_errors,
                settle_ms_after_output=power_cfg.settle_ms_after_output,
            )
            self._state_guard_after_write(result)
            return result
