"""M0 read-only and M1 OFF-only CW service for RF signal sources."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from wavebench.config import RfSourceConfig, WaveBenchConfig
from wavebench.errors import ConfigError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.instruments.rf_source_extensions import (
    RfAvailability,
    RfCwProfile,
    RfCwRequest,
    RfCwResult,
    RfFeature,
    RfFeatureDirection,
    RfModulationState,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceDriver,
    RfSourceSnapshot,
    RfSweepState,
)
from wavebench.logging import CommandLogger
from wavebench.services.access_policy import access_policy
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.session_alias import SessionStateAliasMixin
from wavebench.transport.base import InstrumentTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth


@dataclass
class RfSourceService(SessionStateAliasMixin):
    """Open one configured RF source session for a bounded RF operation."""

    config: WaveBenchConfig
    logger: CommandLogger
    session: RfSourceDriver | None = None
    descriptor: InstrumentDescriptor | None = None
    transport: InstrumentTransport | None = None
    session_state: InstrumentSessionState | None = None
    lease: ResourceLease | None = None

    def _require(self, operation: str, *capabilities: str) -> None:
        rf_source = self._rf_source_config()
        access_policy(getattr(rf_source, "access", "read_write"), "rf_source.access").require(
            require_operation_spec(operation),
            operation=operation,
        )
        descriptor = self.descriptor or resolve_instrument_descriptor(
            rf_source.driver,
            expected_kind="rf_source",
        )
        self.descriptor = descriptor
        require_capabilities(descriptor, capabilities, operation=operation)

    def _rf_source_config(self) -> RfSourceConfig:
        if self.config.rf_source is None or not self.config.rf_source.resource:
            raise ConfigError(
                "rf_source resource is not configured. Set [rf_source].resource or pass --resource."
            )
        return self.config.rf_source

    def _open_rf_source(self) -> RfSourceDriver:
        rf_source = self._rf_source_config()
        self._prepare_session_open("rf_source")
        if self.lease is None:
            self.lease = ResourceLease(
                resource=rf_source.resource or "",
                operation="rf_source.session",
            )
        opened = open_instrument_driver(
            driver_reference=rf_source.driver,
            expected_kind="rf_source",
            resource=rf_source.resource or "",
            configured_backend=self.config.connection.backend,
            timeout_ms=self.config.connection.timeout_ms,
            opc_timeout_ms=self.config.connection.opc_timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            options=getattr(rf_source, "options", {}),
            access=getattr(rf_source, "access", "read_write"),
            lease=self.lease,
        )
        self.descriptor = opened.descriptor
        self.transport = opened.transport
        self.session_state = getattr(opened, "session_state", None)
        return cast(RfSourceDriver, opened.driver)

    def audit_snapshot(self) -> dict[str, Any] | None:
        snapshot = getattr(self.transport, "audit_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def open_session(self) -> RfSourceDriver:
        return self._open_rf_source()

    @contextmanager
    def _rf_source_session(self) -> Iterator[RfSourceDriver]:
        if self.session is not None:
            yield self.session
            return
        rf_source = self._open_rf_source()
        try:
            yield rf_source
        finally:
            rf_source.close()

    def idn(self) -> str:
        self._require("rf_source.idn", "rf_source.idn")
        with self._rf_source_session() as rf_source:
            return rf_source.idn()

    def snapshot(self) -> RfSourceSnapshot:
        self._require("rf_source.snapshot", "rf_source.snapshot")
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                return rf_source.get_rf_snapshot()
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError("rf_source.snapshot requires a healthy session")
                return rf_source.get_rf_snapshot()

    def configure_cw(self, request: RfCwRequest) -> RfCwResult:
        """Apply one OFF-only CW field and independently confirm the result.

        M1 deliberately permits exactly one write per call.  It does not retry a
        failed or mismatched write and leaves RF OFF recovery to the later M2
        output transaction.
        """

        if not isinstance(request, RfCwRequest):
            raise ConfigError("rf_source CW configuration requires RfCwRequest")
        operation = (
            "rf_source.set_frequency"
            if request.frequency_hz is not None
            else "rf_source.set_power_dbm"
        )
        self._require(operation, "rf_source.snapshot", "rf_source.cw_configure")
        port_profile, cw_profile = self._validate_cw_descriptor(request, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                self._validate_cw_preflight(
                    request,
                    preflight_snapshot,
                    port_profile,
                    cw_profile,
                    operation=operation,
                )
                main_entered = False
                try:
                    main_entered = True
                    rf_source.configure_cw(request)
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    return self._validate_cw_postcondition(
                        request,
                        postcondition_snapshot,
                        port_profile,
                        cw_profile,
                        operation=operation,
                    )
                except BaseException:
                    if main_entered and session_state.health is SessionHealth.HEALTHY:
                        session_state.degrade(
                            SessionHealth.UNCERTAIN,
                            reason="rf_cw_postcondition_unverified",
                        )
                    raise

    def _validate_cw_descriptor(
        self,
        request: RfCwRequest,
        operation: str,
    ) -> tuple[RfOutputPortProfile, RfCwProfile]:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.rf_source_extensions
        if not isinstance(extensions, RfSourceDescriptorExtensions):
            raise ConfigError(f"{operation} requires validated rf_source_extensions")
        port_profile = next(
            (port for port in extensions.topology.ports if port.port_id == request.port_id),
            None,
        )
        if port_profile is None:
            raise ConfigError(f"{operation} references an undeclared RF port")
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.CW),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.CONFIGURE not in feature.directions
            or request.port_id not in feature.port_ids
            or not isinstance(feature.profile, RfCwProfile)
        ):
            raise ConfigError(f"{operation} requires a configurable CW profile for the target port")
        profile = feature.profile
        if request.frequency_hz is not None:
            if not profile.frequency_configurable:
                raise ConfigError(f"{operation} requires a configurable CW frequency profile")
            if not port_profile.frequency_min_hz <= request.frequency_hz <= port_profile.frequency_max_hz:
                raise ConfigError(f"{operation} request frequency_hz is outside the descriptor range")
        else:
            assert request.power_dbm is not None
            if not profile.power_configurable:
                raise ConfigError(f"{operation} requires a configurable CW power profile")
            if not port_profile.power_min_dbm <= request.power_dbm <= port_profile.power_max_dbm:
                raise ConfigError(f"{operation} request power_dbm is outside the descriptor range")
        return port_profile, profile

    def _validate_cw_preflight(
        self,
        request: RfCwRequest,
        snapshot: RfSourceSnapshot,
        port_profile: RfOutputPortProfile,
        profile: RfCwProfile,
        *,
        operation: str,
    ) -> RfPortSnapshot:
        del port_profile, profile
        port = self._snapshot_port(snapshot, request.port_id, operation=operation)
        output_enabled = self._observed_value(
            port.output_enabled,
            f"{operation} requires a readable RF output state",
        )
        if output_enabled is not False:
            raise ConfigError(f"{operation} requires target RF output OFF")
        modulation = self._observed_value(
            port.modulation,
            f"{operation} requires a readable modulation state",
        )
        if modulation is not RfModulationState.DISABLED:
            raise ConfigError(f"{operation} requires modulation disabled")
        pulse = self._observed_value(
            port.pulse,
            f"{operation} requires a readable Pulse state",
        )
        if pulse is not RfPulseState.DISABLED:
            raise ConfigError(f"{operation} requires Pulse disabled")
        sweep = self._observed_value(
            port.sweep,
            f"{operation} requires a readable Sweep state",
        )
        if sweep is not RfSweepState.DISABLED:
            raise ConfigError(f"{operation} requires Sweep disabled")
        protection = self._observed_value(
            snapshot.protection,
            f"{operation} requires a readable protection state",
        )
        if not isinstance(protection, RfProtectionStatus):  # defensive: snapshot validates this.
            raise ConfigError(f"{operation} requires a valid protection state")
        if protection.active_codes:
            raise ConfigError(f"{operation} requires no active protection condition")
        return port

    def _validate_cw_postcondition(
        self,
        request: RfCwRequest,
        snapshot: RfSourceSnapshot,
        port_profile: RfOutputPortProfile,
        profile: RfCwProfile,
        *,
        operation: str,
    ) -> RfCwResult:
        port = self._validate_cw_preflight(
            request,
            snapshot,
            port_profile,
            profile,
            operation=operation,
        )
        if request.frequency_hz is not None:
            frequency_hz = self._observed_value(
                port.frequency_hz,
                f"{operation} requires a readable frequency_hz readback",
            )
            if frequency_hz != request.frequency_hz:
                raise ConfigError(f"{operation} frequency_hz readback does not match request")
            return RfCwResult(port_id=request.port_id, frequency_hz=float(frequency_hz))
        power_dbm = self._observed_value(
            port.power_dbm,
            f"{operation} requires a readable power_dbm readback",
        )
        assert request.power_dbm is not None
        if power_dbm != request.power_dbm:
            raise ConfigError(f"{operation} power_dbm readback does not match request")
        return RfCwResult(port_id=request.port_id, power_dbm=float(power_dbm))

    @staticmethod
    def _snapshot_port(
        snapshot: RfSourceSnapshot,
        port_id: str,
        *,
        operation: str,
    ) -> RfPortSnapshot:
        port = next((item for item in snapshot.ports if item.port_id == port_id), None)
        if port is None:
            raise ConfigError(f"{operation} snapshot omitted the target RF port")
        return port

    @staticmethod
    def _observed_value(observed: object, message: str) -> object:
        if getattr(observed, "availability", None) is not RfAvailability.VALUE:
            raise ConfigError(message)
        return getattr(observed, "value", None)
