"""Read-only M0 service for RF signal sources."""

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
from wavebench.instruments.rf_source_extensions import RfSourceDriver, RfSourceSnapshot
from wavebench.logging import CommandLogger
from wavebench.services.access_policy import access_policy
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.session_alias import SessionStateAliasMixin
from wavebench.transport.base import InstrumentTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth


@dataclass
class RfSourceService(SessionStateAliasMixin):
    """Open one configured RF source session for an explicitly read-only operation."""

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
