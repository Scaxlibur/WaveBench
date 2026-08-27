"""Read-only, OFF-only CW/modulation, and guarded RF-output service for RF sources."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from wavebench.config import RfPortSafetyConfig, RfSourceConfig, WaveBenchConfig
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
    RfModulatedOutputProfile,
    RfModulatedOutputRequest,
    RfModulatedOutputResult,
    RfModulationDisableRequest,
    RfModulationDisableResult,
    RfModulationKind,
    RfModulationModeProfile,
    RfModulationProfile,
    RfModulationRequest,
    RfModulationResult,
    RfModulationStateSnapshot,
    RfModulationSnapshot,
    RfModulationSource,
    RfModulationState,
    RfModulationWaveform,
    RfOutputPortProfile,
    RfOutputProfile,
    RfOutputRequest,
    RfOutputResult,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseConfigureRequest,
    RfPulseConfigureResult,
    RfPulseMode,
    RfPulseModeProfile,
    RfPulseProfile,
    RfPulseSnapshot,
    RfPulseSource,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceDriver,
    RfSourceSnapshot,
    RfSweepConfigureRequest,
    RfSweepConfigureResult,
    RfSweepDirection,
    RfSweepModeProfile,
    RfSweepProfile,
    RfSweepShape,
    RfSweepSnapshot,
    RfSweepSpacing,
    RfSweepState,
    RfSweepType,
    RfTriggerProfile,
    RfTriggerSnapshot,
    rf_source_cw_operation_artifact,
    rf_source_modulation_disable_operation_artifact,
    rf_source_modulated_output_operation_artifact,
    rf_source_modulation_operation_artifact,
    rf_source_output_operation_artifact,
    rf_source_pulse_operation_artifact,
    rf_source_sweep_operation_artifact,
    rf_source_trigger_snapshot_operation_artifact,
)
from wavebench.logging import CommandLogger
from wavebench.services.access_policy import access_policy
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.session_alias import SessionStateAliasMixin
from wavebench.transport.base import InstrumentTransport
from wavebench.transport.session import (
    InstrumentSessionState,
    SessionHealth,
    SessionPurpose,
    SessionTransactionCoordinator,
)


@dataclass(frozen=True)
class _RfCwTransaction:
    result: RfCwResult
    preflight_snapshot: RfSourceSnapshot
    postcondition_snapshot: RfSourceSnapshot


@dataclass(frozen=True)
class _RfModulationTransaction:
    result: RfModulationResult
    preflight_snapshot: RfSourceSnapshot
    preflight_modulation_state: RfModulationStateSnapshot
    postcondition_snapshot: RfSourceSnapshot
    postcondition_modulation_snapshot: RfModulationSnapshot


@dataclass(frozen=True)
class _RfModulationDisableTransaction:
    result: RfModulationDisableResult
    preflight_snapshot: RfSourceSnapshot
    preflight_modulation_state: RfModulationStateSnapshot
    postcondition_snapshot: RfSourceSnapshot
    postcondition_modulation_state: RfModulationStateSnapshot


@dataclass(frozen=True)
class _RfModulatedOutputTransaction:
    result: RfModulatedOutputResult
    preflight_snapshot: RfSourceSnapshot
    preflight_modulation_snapshot: RfModulationSnapshot
    postcondition_snapshot: RfSourceSnapshot
    postcondition_modulation_snapshot: RfModulationSnapshot


@dataclass(frozen=True)
class _RfPulseTransaction:
    result: RfPulseConfigureResult
    preflight_snapshot: RfSourceSnapshot
    postcondition_snapshot: RfSourceSnapshot
    postcondition_pulse_snapshot: RfPulseSnapshot


@dataclass(frozen=True)
class _RfSweepTransaction:
    result: RfSweepConfigureResult
    preflight_snapshot: RfSourceSnapshot
    postcondition_snapshot: RfSourceSnapshot
    postcondition_sweep_snapshot: RfSweepSnapshot


@dataclass(frozen=True)
class _RfOutputTransaction:
    result: RfOutputResult
    preflight_snapshot: RfSourceSnapshot
    postcondition_snapshot: RfSourceSnapshot


class RfModulationPostconditionError(ConfigError):
    """Strict M3 postcondition failure with already-read typed evidence.

    The exception never changes the fail-closed result.  It only retains the
    snapshots that were independently read before the strict comparison failed,
    so a private evidence harness can record redacted field-level diagnostics
    without issuing another query on an uncertain session.
    """

    def __init__(
        self,
        message: str,
        *,
        postcondition_snapshot: RfSourceSnapshot,
        postcondition_modulation_snapshot: RfModulationSnapshot,
    ) -> None:
        super().__init__(message)
        self.postcondition_snapshot = postcondition_snapshot
        self.postcondition_modulation_snapshot = postcondition_modulation_snapshot


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

    def trigger_snapshot(self, port_id: str) -> RfTriggerSnapshot:
        """Read a declared logical trigger configuration without changing state."""

        operation = "rf_source.trigger_snapshot"
        self._require(operation, "rf_source.trigger_snapshot")
        profile = self._validate_trigger_snapshot_descriptor(port_id, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                snapshot = rf_source.get_rf_trigger_snapshot(port_id)
            else:
                with session_state.transaction_lock:
                    if session_state.health is not SessionHealth.HEALTHY:
                        raise ConfigError(f"{operation} requires a healthy session")
                    snapshot = rf_source.get_rf_trigger_snapshot(port_id)
        self._validate_trigger_snapshot_readback(
            port_id,
            snapshot,
            profile,
            operation=operation,
        )
        return snapshot

    def trigger_snapshot_with_artifact(
        self,
        port_id: str,
    ) -> tuple[RfTriggerSnapshot, dict[str, object]]:
        """Read a declared trigger configuration and retain redacted evidence."""

        snapshot = self.trigger_snapshot(port_id)
        return snapshot, rf_source_trigger_snapshot_operation_artifact(snapshot)

    def configure_cw(self, request: RfCwRequest) -> RfCwResult:
        return self._configure_cw_transaction(request).result

    def configure_cw_with_artifact(
        self,
        request: RfCwRequest,
    ) -> tuple[RfCwResult, dict[str, object]]:
        """Apply M1 CW once and retain typed pre/postcondition evidence."""

        transaction = self._configure_cw_transaction(request)
        return (
            transaction.result,
            rf_source_cw_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                postcondition_snapshot=transaction.postcondition_snapshot,
            ),
        )

    def _configure_cw_transaction(self, request: RfCwRequest) -> _RfCwTransaction:
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
                    result = self._validate_cw_postcondition(
                        request,
                        postcondition_snapshot,
                        port_profile,
                        cw_profile,
                        operation=operation,
                    )
                    return _RfCwTransaction(
                        result=result,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                    )
                except BaseException:
                    if main_entered and session_state.health is SessionHealth.HEALTHY:
                        session_state.degrade(
                            SessionHealth.UNCERTAIN,
                            reason="rf_cw_postcondition_unverified",
                        )
                    raise

    def configure_modulation(self, request: RfModulationRequest) -> RfModulationResult:
        return self._configure_modulation_transaction(request).result

    def configure_modulation_with_artifact(
        self,
        request: RfModulationRequest,
    ) -> tuple[RfModulationResult, dict[str, object]]:
        """Apply bounded M3 internal-sine modulation and retain typed evidence."""

        transaction = self._configure_modulation_transaction(request)
        return (
            transaction.result,
            rf_source_modulation_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                preflight_modulation_state=transaction.preflight_modulation_state,
                postcondition_snapshot=transaction.postcondition_snapshot,
                postcondition_modulation_snapshot=transaction.postcondition_modulation_snapshot,
            ),
        )

    def _configure_modulation_transaction(
        self,
        request: RfModulationRequest,
    ) -> _RfModulationTransaction:
        """Apply one bounded M3 profile without retry or output recovery.

        M3 never enables RF output.  It requires all modulation modes to be
        disabled before the fixed driver sequence, then independently confirms
        only the requested internal-sine mode and global modulation switch.
        A failed or mismatched sequence is not retried and leaves the session
        uncertain because the instrument-side profile may be partially changed.
        """

        if not isinstance(request, RfModulationRequest):
            raise ConfigError("rf_source modulation configuration requires RfModulationRequest")
        operation = "rf_source.modulation_configure"
        self._require(operation, "rf_source.snapshot", "rf_source.modulation_configure")
        mode_profile = self._validate_modulation_descriptor(request, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                preflight_modulation_state = rf_source.get_rf_modulation_state(request.port_id)
                self._validate_modulation_preflight(
                    request,
                    preflight_snapshot,
                    preflight_modulation_state,
                    operation=operation,
                )
                main_entered = False
                try:
                    main_entered = True
                    rf_source.configure_rf_modulation(request)
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    postcondition_modulation_snapshot = rf_source.get_rf_modulation_snapshot(
                        request.port_id,
                        request.kind,
                    )
                    try:
                        result = self._validate_modulation_postcondition(
                            request,
                            postcondition_snapshot,
                            postcondition_modulation_snapshot,
                            mode_profile,
                            operation=operation,
                        )
                    except ConfigError as exc:
                        raise RfModulationPostconditionError(
                            str(exc),
                            postcondition_snapshot=postcondition_snapshot,
                            postcondition_modulation_snapshot=postcondition_modulation_snapshot,
                        ) from exc
                    return _RfModulationTransaction(
                        result=result,
                        preflight_snapshot=preflight_snapshot,
                        preflight_modulation_state=preflight_modulation_state,
                        postcondition_snapshot=postcondition_snapshot,
                        postcondition_modulation_snapshot=postcondition_modulation_snapshot,
                    )
                except BaseException:
                    if main_entered and session_state.health is SessionHealth.HEALTHY:
                        session_state.degrade(
                            SessionHealth.UNCERTAIN,
                            reason="rf_modulation_postcondition_unverified",
                        )
                    raise

    def disable_modulation(
        self,
        request: RfModulationDisableRequest,
    ) -> RfModulationDisableResult:
        return self._disable_modulation_transaction(request).result

    def disable_modulation_with_artifact(
        self,
        request: RfModulationDisableRequest,
    ) -> tuple[RfModulationDisableResult, dict[str, object]]:
        """Disable one active M3 mode and retain typed pre/postcondition evidence."""

        transaction = self._disable_modulation_transaction(request)
        return (
            transaction.result,
            rf_source_modulation_disable_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                preflight_modulation_state=transaction.preflight_modulation_state,
                postcondition_snapshot=transaction.postcondition_snapshot,
                postcondition_modulation_state=transaction.postcondition_modulation_state,
            ),
        )

    def _disable_modulation_transaction(
        self,
        request: RfModulationDisableRequest,
    ) -> _RfModulationDisableTransaction:
        """Disable one known active M3 mode without retry or RF-output recovery.

        The operation is intentionally narrow: a write is only permitted when
        RF is OFF and typed state proves that exactly ``request.kind`` is active.
        An already disabled, internally consistent state is a no-write success.
        Any write failure or ambiguous postcondition leaves the session uncertain
        for a fresh, independently preflighted recovery attempt.
        """

        if not isinstance(request, RfModulationDisableRequest):
            raise ConfigError("rf_source modulation disable requires RfModulationDisableRequest")
        operation = "rf_source.modulation_disable"
        self._require(operation, "rf_source.snapshot", "rf_source.modulation_disable")
        self._validate_modulation_disable_descriptor(request, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                preflight_modulation_state = rf_source.get_rf_modulation_state(request.port_id)
                write_required = self._validate_modulation_disable_preflight(
                    request,
                    preflight_snapshot,
                    preflight_modulation_state,
                    operation=operation,
                )
                if not write_required:
                    return _RfModulationDisableTransaction(
                        result=RfModulationDisableResult(
                            port_id=request.port_id,
                            kind=request.kind,
                            write_completed=False,
                        ),
                        preflight_snapshot=preflight_snapshot,
                        preflight_modulation_state=preflight_modulation_state,
                        postcondition_snapshot=preflight_snapshot,
                        postcondition_modulation_state=preflight_modulation_state,
                    )

                main_entered = False
                try:
                    main_entered = True
                    rf_source.disable_rf_modulation(request)
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    postcondition_modulation_state = rf_source.get_rf_modulation_state(
                        request.port_id
                    )
                    self._validate_modulation_disable_postcondition(
                        request,
                        postcondition_snapshot,
                        postcondition_modulation_state,
                        operation=operation,
                    )
                    return _RfModulationDisableTransaction(
                        result=RfModulationDisableResult(
                            port_id=request.port_id,
                            kind=request.kind,
                            write_completed=True,
                        ),
                        preflight_snapshot=preflight_snapshot,
                        preflight_modulation_state=preflight_modulation_state,
                        postcondition_snapshot=postcondition_snapshot,
                        postcondition_modulation_state=postcondition_modulation_state,
                    )
                except BaseException:
                    if main_entered and session_state.health is SessionHealth.HEALTHY:
                        session_state.degrade(
                            SessionHealth.UNCERTAIN,
                            reason="rf_modulation_disable_postcondition_unverified",
                        )
                    raise

    def configure_pulse(self, request: RfPulseConfigureRequest) -> RfPulseConfigureResult:
        return self._configure_pulse_transaction(request).result

    def configure_pulse_with_artifact(
        self,
        request: RfPulseConfigureRequest,
    ) -> tuple[RfPulseConfigureResult, dict[str, object]]:
        """Apply one RF-OFF internal single-pulse profile with typed evidence."""

        transaction = self._configure_pulse_transaction(request)
        return (
            transaction.result,
            rf_source_pulse_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                postcondition_snapshot=transaction.postcondition_snapshot,
                postcondition_pulse_snapshot=transaction.postcondition_pulse_snapshot,
            ),
        )

    def _configure_pulse_transaction(
        self,
        request: RfPulseConfigureRequest,
    ) -> _RfPulseTransaction:
        """Configure one disabled internal single-pulse profile without triggering.

        The initial M4 slice intentionally cannot arm or trigger a pulse. It
        configures timing and polarity while the target RF output, pulse state,
        modulation, and Sweep are all OFF, then requires the pulse to remain
        disabled after independent profile readback. A failed main write or
        postcondition is never retried and leaves the session uncertain.
        """

        if not isinstance(request, RfPulseConfigureRequest):
            raise ConfigError("rf_source pulse configuration requires RfPulseConfigureRequest")
        operation = "rf_source.pulse_configure"
        self._require(operation, "rf_source.snapshot", "rf_source.pulse_configure")
        mode_profile = self._validate_pulse_descriptor(request, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                self._validate_pulse_preflight(
                    request,
                    preflight_snapshot,
                    operation=operation,
                )
                main_entered = False
                try:
                    main_entered = True
                    rf_source.configure_rf_pulse(request)
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    postcondition_pulse_snapshot = rf_source.get_rf_pulse_snapshot(request.port_id)
                    result = self._validate_pulse_postcondition(
                        request,
                        postcondition_snapshot,
                        postcondition_pulse_snapshot,
                        mode_profile,
                        operation=operation,
                    )
                    return _RfPulseTransaction(
                        result=result,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        postcondition_pulse_snapshot=postcondition_pulse_snapshot,
                    )
                except BaseException:
                    if main_entered and session_state.health is SessionHealth.HEALTHY:
                        session_state.degrade(
                            SessionHealth.UNCERTAIN,
                            reason="rf_pulse_postcondition_unverified",
                        )
                    raise

    def configure_sweep(self, request: RfSweepConfigureRequest) -> RfSweepConfigureResult:
        return self._configure_sweep_transaction(request).result

    def configure_sweep_with_artifact(
        self,
        request: RfSweepConfigureRequest,
    ) -> tuple[RfSweepConfigureResult, dict[str, object]]:
        """Apply one RF-OFF Step Sweep profile while Sweep stays disabled."""

        transaction = self._configure_sweep_transaction(request)
        return (
            transaction.result,
            rf_source_sweep_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                postcondition_snapshot=transaction.postcondition_snapshot,
                postcondition_sweep_snapshot=transaction.postcondition_sweep_snapshot,
            ),
        )

    def _configure_sweep_transaction(
        self,
        request: RfSweepConfigureRequest,
    ) -> _RfSweepTransaction:
        """Configure one disabled, frequency-only Step Sweep without triggering.

        This first Sweep slice cannot arm, trigger, fire, or select a Level
        Sweep. It writes only the descriptor-bounded frequency profile and
        explicitly requires RF output, modulation, Pulse, and Sweep to remain
        disabled through the independent readback.
        """

        if not isinstance(request, RfSweepConfigureRequest):
            raise ConfigError("rf_source Sweep configuration requires RfSweepConfigureRequest")
        operation = "rf_source.sweep_configure"
        self._require(operation, "rf_source.snapshot", "rf_source.sweep_configure")
        mode_profile = self._validate_sweep_descriptor(request, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                self._validate_sweep_preflight(
                    request,
                    preflight_snapshot,
                    operation=operation,
                )
                main_entered = False
                try:
                    main_entered = True
                    rf_source.configure_rf_sweep(request)
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    postcondition_sweep_snapshot = rf_source.get_rf_sweep_snapshot(request.port_id)
                    result = self._validate_sweep_postcondition(
                        request,
                        postcondition_snapshot,
                        postcondition_sweep_snapshot,
                        mode_profile,
                        operation=operation,
                    )
                    return _RfSweepTransaction(
                        result=result,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        postcondition_sweep_snapshot=postcondition_sweep_snapshot,
                    )
                except BaseException:
                    if main_entered and session_state.health is SessionHealth.HEALTHY:
                        session_state.degrade(
                            SessionHealth.UNCERTAIN,
                            reason="rf_sweep_postcondition_unverified",
                        )
                    raise

    def set_output(self, request: RfOutputRequest) -> RfOutputResult:
        return self._set_output_transaction(request).result

    def set_output_with_artifact(
        self,
        request: RfOutputRequest,
    ) -> tuple[RfOutputResult, dict[str, object]]:
        """Apply M2 RF output once and retain typed pre/postcondition evidence."""

        transaction = self._set_output_transaction(request)
        return (
            transaction.result,
            rf_source_output_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                postcondition_snapshot=transaction.postcondition_snapshot,
            ),
        )

    def enable_modulated_output(
        self,
        request: RfModulatedOutputRequest,
    ) -> RfModulatedOutputResult:
        return self._enable_modulated_output_transaction(request).result

    def enable_modulated_output_with_artifact(
        self,
        request: RfModulatedOutputRequest,
    ) -> tuple[RfModulatedOutputResult, dict[str, object]]:
        """Enable RF once for one exactly verified active modulation profile.

        This does not configure modulation, turn RF back off after success, or
        disable modulation. Those actions remain explicit, separate operations.
        On an uncertain RF-ON result it uses the existing one-shot guarded OFF
        recovery, never retries RF ON, and keeps the session uncertain.
        """

        transaction = self._enable_modulated_output_transaction(request)
        return (
            transaction.result,
            rf_source_modulated_output_operation_artifact(
                request,
                transaction.result,
                preflight_snapshot=transaction.preflight_snapshot,
                preflight_modulation_snapshot=transaction.preflight_modulation_snapshot,
                postcondition_snapshot=transaction.postcondition_snapshot,
                postcondition_modulation_snapshot=transaction.postcondition_modulation_snapshot,
            ),
        )

    def _enable_modulated_output_transaction(
        self,
        request: RfModulatedOutputRequest,
    ) -> _RfModulatedOutputTransaction:
        """Run one explicit, profile-bound RF-ON transaction under active modulation."""

        if not isinstance(request, RfModulatedOutputRequest):
            raise ConfigError(
                "rf_source modulated-output enable requires RfModulatedOutputRequest"
            )
        operation = "rf_source.modulated_output_enable"
        self._require(
            operation,
            "rf_source.snapshot",
            "rf_source.output",
            "rf_source.modulation_configure",
            "rf_source.modulated_output_enable",
        )
        (
            port_profile,
            output_profile,
            modulated_output_profile,
            extensions,
        ) = self._validate_modulated_output_descriptor(request, operation)
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                preflight_modulation_snapshot = rf_source.get_rf_modulation_snapshot(
                    request.port_id,
                    request.kind,
                )
                self._validate_modulated_output_enable_snapshot(
                    request,
                    preflight_snapshot,
                    preflight_modulation_snapshot,
                    port_profile,
                    output_profile,
                    modulated_output_profile,
                    extensions,
                    expected_output_enabled=False,
                    operation=operation,
                )
                main_entered = False
                try:
                    main_entered = True
                    rf_source.set_rf_output(RfOutputRequest(port_id=request.port_id, enabled=True))
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    postcondition_modulation_snapshot = rf_source.get_rf_modulation_snapshot(
                        request.port_id,
                        request.kind,
                    )
                    modulation_result = self._validate_modulated_output_enable_snapshot(
                        request,
                        postcondition_snapshot,
                        postcondition_modulation_snapshot,
                        port_profile,
                        output_profile,
                        modulated_output_profile,
                        extensions,
                        expected_output_enabled=True,
                        operation=operation,
                    )
                    return _RfModulatedOutputTransaction(
                        result=RfModulatedOutputResult(
                            modulation=modulation_result,
                            write_completed=True,
                        ),
                        preflight_snapshot=preflight_snapshot,
                        preflight_modulation_snapshot=preflight_modulation_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        postcondition_modulation_snapshot=postcondition_modulation_snapshot,
                    )
                except BaseException as exc:
                    if main_entered:
                        self._degrade_output_session_uncertain(session_state)
                        recovery = self._recover_rf_output_off(
                            rf_source,
                            request.port_id,
                            operation=operation,
                        )
                        try:
                            setattr(exc, "rf_source_recovery", recovery)
                        except Exception:
                            pass
                    raise

    def _set_output_transaction(self, request: RfOutputRequest) -> _RfOutputTransaction:
        """Execute one per-port M2 output transaction with bounded OFF recovery."""

        if not isinstance(request, RfOutputRequest):
            raise ConfigError("rf_source output control requires RfOutputRequest")
        operation = "rf_source.output_enable" if request.enabled else "rf_source.output_disable"
        self._require(operation, "rf_source.snapshot", "rf_source.output")
        port_profile, output_profile, extensions = self._validate_output_descriptor(
            request,
            operation,
        )
        with self._rf_source_session() as rf_source:
            session_state = self.session_state
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            with session_state.transaction_lock:
                if session_state.health is not SessionHealth.HEALTHY:
                    raise ConfigError(f"{operation} requires a healthy session")
                preflight_snapshot = rf_source.get_rf_snapshot()
                if request.enabled:
                    current_enabled = self._validate_output_enable_snapshot(
                        request,
                        preflight_snapshot,
                        port_profile,
                        output_profile,
                        extensions,
                        operation=operation,
                    )
                else:
                    current_enabled = self._output_state_if_observed(
                        self._snapshot_port(
                            preflight_snapshot,
                            request.port_id,
                            operation=operation,
                        )
                    )
                if current_enabled is request.enabled:
                    return _RfOutputTransaction(
                        result=RfOutputResult(
                            port_id=request.port_id,
                            enabled=request.enabled,
                            write_completed=False,
                        ),
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=preflight_snapshot,
                    )

                main_entered = False
                try:
                    main_entered = True
                    rf_source.set_rf_output(request)
                    postcondition_snapshot = rf_source.get_rf_snapshot()
                    if request.enabled:
                        postcondition_enabled = self._validate_output_enable_snapshot(
                            request,
                            postcondition_snapshot,
                            port_profile,
                            output_profile,
                            extensions,
                            operation=operation,
                        )
                        if postcondition_enabled is not True:
                            raise ConfigError(
                                f"{operation} postcondition reports RF output OFF or unknown"
                            )
                    else:
                        self._validate_output_disable_snapshot(
                            request,
                            postcondition_snapshot,
                            operation=operation,
                        )
                    return _RfOutputTransaction(
                        result=RfOutputResult(
                            port_id=request.port_id,
                            enabled=request.enabled,
                            write_completed=True,
                        ),
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                    )
                except BaseException as exc:
                    if main_entered:
                        if request.enabled:
                            self._degrade_output_session_uncertain(session_state)
                            recovery = self._recover_rf_output_off(
                                rf_source,
                                request.port_id,
                                operation=operation,
                            )
                            try:
                                setattr(exc, "rf_source_recovery", recovery)
                            except Exception:
                                pass
                        else:
                            self._degrade_output_session_poisoned(session_state)
                    raise

    def _validate_output_descriptor(
        self,
        request: RfOutputRequest,
        operation: str,
    ) -> tuple[RfOutputPortProfile, RfOutputProfile, RfSourceDescriptorExtensions]:
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
            (item for item in extensions.features if item.feature is RfFeature.OUTPUT),
            None,
        )
        if (
            feature is None
            or request.port_id not in feature.port_ids
            or RfFeatureDirection.ENABLE not in feature.directions
            or RfFeatureDirection.DISABLE not in feature.directions
            or not isinstance(feature.profile, RfOutputProfile)
            or not feature.profile.output_readable
        ):
            raise ConfigError(f"{operation} requires a readable output profile for the target port")
        return port_profile, feature.profile, extensions

    def _validate_modulated_output_descriptor(
        self,
        request: RfModulatedOutputRequest,
        operation: str,
    ) -> tuple[
        RfOutputPortProfile,
        RfOutputProfile,
        RfModulatedOutputProfile,
        RfSourceDescriptorExtensions,
    ]:
        port_profile, output_profile, extensions = self._validate_output_descriptor(
            RfOutputRequest(port_id=request.port_id, enabled=True),
            operation,
        )
        self._validate_modulation_descriptor(request.modulation, operation)
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.MODULATED_OUTPUT),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.ENABLE not in feature.directions
            or request.port_id not in feature.port_ids
            or not isinstance(feature.profile, RfModulatedOutputProfile)
        ):
            raise ConfigError(
                f"{operation} requires a modulated-output enable profile for the target port"
            )
        mode_profile = next(
            (item for item in feature.profile.mode_profiles if item.kind is request.kind),
            None,
        )
        if mode_profile is None:
            raise ConfigError(f"{operation} does not support the requested modulation kind")
        if (
            mode_profile.source is not RfModulationSource.INTERNAL
            or mode_profile.waveform is not RfModulationWaveform.SINE
            or mode_profile.value_unit is not request.modulation.value_unit
        ):
            raise ConfigError(f"{operation} requires an internal-sine profile for the requested kind")
        if not mode_profile.value_min <= request.modulation.value <= mode_profile.value_max:
            raise ConfigError(f"{operation} request value is outside the descriptor range")
        if not (
            mode_profile.internal_frequency_min_hz
            <= request.modulation.internal_frequency_hz
            <= mode_profile.internal_frequency_max_hz
        ):
            raise ConfigError(
                f"{operation} request internal_frequency_hz is outside the descriptor range"
            )
        return port_profile, output_profile, feature.profile, extensions

    def _validate_modulated_output_enable_snapshot(
        self,
        request: RfModulatedOutputRequest,
        snapshot: RfSourceSnapshot,
        modulation_snapshot: RfModulationSnapshot,
        port_profile: RfOutputPortProfile,
        output_profile: RfOutputProfile,
        modulated_output_profile: RfModulatedOutputProfile,
        extensions: RfSourceDescriptorExtensions,
        *,
        expected_output_enabled: bool,
        operation: str,
    ) -> RfModulationResult:
        del output_profile
        port = self._snapshot_port(snapshot, request.port_id, operation=operation)
        safety_port = self._output_safety_port(request.port_id, operation=operation)
        frequency_hz = self._observed_value(
            port.frequency_hz,
            f"{operation} requires a readable RF frequency",
        )
        power_dbm = self._observed_value(
            port.power_dbm,
            f"{operation} requires a readable RF power",
        )
        output_enabled = self._observed_value(
            port.output_enabled,
            f"{operation} requires a readable RF output state",
        )
        modulation = self._observed_value(
            port.modulation,
            f"{operation} requires a readable modulation state",
        )
        pulse = self._observed_value(
            port.pulse,
            f"{operation} requires a readable Pulse state",
        )
        sweep = self._observed_value(
            port.sweep,
            f"{operation} requires a readable Sweep state",
        )
        protection = self._observed_value(
            snapshot.protection,
            f"{operation} requires a readable protection state",
        )
        if not isinstance(frequency_hz, (int, float)) or isinstance(frequency_hz, bool):
            raise ConfigError(f"{operation} requires a valid RF frequency")
        if not isinstance(power_dbm, (int, float)) or isinstance(power_dbm, bool):
            raise ConfigError(f"{operation} requires a valid RF power")
        if not isinstance(output_enabled, bool):
            raise ConfigError(f"{operation} requires a valid RF output state")
        if output_enabled is not expected_output_enabled:
            expected = "ON" if expected_output_enabled else "OFF"
            raise ConfigError(f"{operation} requires target RF output {expected}")
        if modulation is not RfModulationState.ENABLED:
            raise ConfigError(f"{operation} requires modulation enabled")
        if pulse is not RfPulseState.DISABLED:
            raise ConfigError(f"{operation} requires Pulse disabled")
        if sweep is not RfSweepState.DISABLED:
            raise ConfigError(f"{operation} requires Sweep disabled")
        if not isinstance(protection, RfProtectionStatus):
            raise ConfigError(f"{operation} requires a valid protection state")
        if not (
            port_profile.frequency_min_hz <= frequency_hz <= port_profile.frequency_max_hz
            and safety_port.minimum_frequency_hz
            <= frequency_hz
            <= safety_port.maximum_frequency_hz
        ):
            raise ConfigError(f"{operation} requires RF frequency within descriptor and safety ranges")
        if not (
            port_profile.power_min_dbm <= power_dbm <= port_profile.power_max_dbm
            and power_dbm <= safety_port.maximum_power_dbm
            and power_dbm <= modulated_output_profile.maximum_power_dbm
        ):
            raise ConfigError(
                f"{operation} requires RF power within descriptor, modulated-output, and safety ranges"
            )
        if safety_port.actual_termination_ohm != port_profile.power_reference_impedance_ohm:
            raise ConfigError(f"{operation} requires actual termination to match RF power reference")
        policies = {item.code: item for item in extensions.protection_conditions}
        unknown = sorted(set(protection.active_codes) - set(policies))
        if unknown:
            raise ConfigError(f"{operation} rejects an unknown active protection condition")
        if any(policies[code].blocks_output_enable for code in protection.active_codes):
            raise ConfigError(f"{operation} rejects an active blocking protection condition")
        mode_profile = next(
            (
                item
                for item in modulated_output_profile.mode_profiles
                if item.kind is request.kind
            ),
            None,
        )
        if mode_profile is None:  # defensive: descriptor validation already requires it.
            raise ConfigError(f"{operation} does not support the requested modulation kind")
        self._validate_modulation_snapshot_identity(
            request.modulation,
            modulation_snapshot,
            mode_profile,
            require_target_profile=True,
            require_selected_fm_pm_kind=True,
            operation=operation,
        )
        if modulation_snapshot.enabled_modes != (request.kind,):
            raise ConfigError(f"{operation} requires only the requested modulation mode")
        if modulation_snapshot.global_enabled is not True:
            raise ConfigError(f"{operation} requires global modulation enabled")
        if modulation_snapshot.fault_codes:
            raise ConfigError(f"{operation} rejects an active modulation fault condition")
        if (
            modulation_snapshot.internal_frequency_hz != request.modulation.internal_frequency_hz
            or modulation_snapshot.value != request.modulation.value
            or modulation_snapshot.value_unit is not request.modulation.value_unit
        ):
            raise ConfigError(f"{operation} modulation readback does not match request")
        return self._modulation_result(request.modulation)

    def _validate_output_enable_snapshot(
        self,
        request: RfOutputRequest,
        snapshot: RfSourceSnapshot,
        port_profile: RfOutputPortProfile,
        output_profile: RfOutputProfile,
        extensions: RfSourceDescriptorExtensions,
        *,
        operation: str,
    ) -> bool:
        del output_profile
        port = self._snapshot_port(snapshot, request.port_id, operation=operation)
        safety_port = self._output_safety_port(request.port_id, operation=operation)
        frequency_hz = self._observed_value(
            port.frequency_hz,
            f"{operation} requires a readable RF frequency",
        )
        power_dbm = self._observed_value(
            port.power_dbm,
            f"{operation} requires a readable RF power",
        )
        output_enabled = self._observed_value(
            port.output_enabled,
            f"{operation} requires a readable RF output state",
        )
        modulation = self._observed_value(
            port.modulation,
            f"{operation} requires a readable modulation state",
        )
        pulse = self._observed_value(
            port.pulse,
            f"{operation} requires a readable Pulse state",
        )
        sweep = self._observed_value(
            port.sweep,
            f"{operation} requires a readable Sweep state",
        )
        protection = self._observed_value(
            snapshot.protection,
            f"{operation} requires a readable protection state",
        )
        if not isinstance(frequency_hz, (int, float)) or isinstance(frequency_hz, bool):
            raise ConfigError(f"{operation} requires a valid RF frequency")
        if not isinstance(power_dbm, (int, float)) or isinstance(power_dbm, bool):
            raise ConfigError(f"{operation} requires a valid RF power")
        if not isinstance(output_enabled, bool):
            raise ConfigError(f"{operation} requires a valid RF output state")
        if modulation is not RfModulationState.DISABLED:
            raise ConfigError(f"{operation} requires modulation disabled")
        if pulse is not RfPulseState.DISABLED:
            raise ConfigError(f"{operation} requires Pulse disabled")
        if sweep is not RfSweepState.DISABLED:
            raise ConfigError(f"{operation} requires Sweep disabled")
        if not isinstance(protection, RfProtectionStatus):
            raise ConfigError(f"{operation} requires a valid protection state")
        if not (
            port_profile.frequency_min_hz <= frequency_hz <= port_profile.frequency_max_hz
            and safety_port.minimum_frequency_hz
            <= frequency_hz
            <= safety_port.maximum_frequency_hz
        ):
            raise ConfigError(f"{operation} requires RF frequency within descriptor and safety ranges")
        if not (
            port_profile.power_min_dbm <= power_dbm <= port_profile.power_max_dbm
            and power_dbm <= safety_port.maximum_power_dbm
        ):
            raise ConfigError(f"{operation} requires RF power within descriptor and safety ranges")
        if safety_port.actual_termination_ohm != port_profile.power_reference_impedance_ohm:
            raise ConfigError(f"{operation} requires actual termination to match RF power reference")
        policies = {item.code: item for item in extensions.protection_conditions}
        unknown = sorted(set(protection.active_codes) - set(policies))
        if unknown:
            raise ConfigError(f"{operation} rejects an unknown active protection condition")
        if any(policies[code].blocks_output_enable for code in protection.active_codes):
            raise ConfigError(f"{operation} rejects an active blocking protection condition")
        return output_enabled

    def _validate_output_disable_snapshot(
        self,
        request: RfOutputRequest,
        snapshot: RfSourceSnapshot,
        *,
        operation: str,
    ) -> None:
        port = self._snapshot_port(snapshot, request.port_id, operation=operation)
        output_enabled = self._observed_value(
            port.output_enabled,
            f"{operation} requires a readable RF output state",
        )
        if output_enabled is not False:
            raise ConfigError(f"{operation} postcondition reports RF output ON or unknown")

    def _output_safety_port(self, port_id: str, *, operation: str) -> RfPortSafetyConfig:
        safety_port = next(
            (item for item in self._rf_source_config().safety_ports if item.port_id == port_id),
            None,
        )
        if safety_port is None:
            raise ConfigError(f"{operation} requires complete safety configuration for the target RF port")
        return safety_port

    @staticmethod
    def _output_state_if_observed(port: RfPortSnapshot) -> bool | None:
        if port.output_enabled.availability is not RfAvailability.VALUE:
            return None
        value = port.output_enabled.value
        return value if isinstance(value, bool) else None

    def _recover_rf_output_off(
        self,
        rf_source: RfSourceDriver,
        port_id: str,
        *,
        operation: str,
    ) -> dict[str, str]:
        """Attempt exactly one bounded, same-port OFF recovery after a failed ON."""

        session_state = self.session_state
        if session_state is None or session_state.health in {
            SessionHealth.POISONED,
            SessionHealth.CLOSED,
        }:
            return {"status": "not_attempted", "reason": "session_unavailable"}
        coordinator = SessionTransactionCoordinator(session_state)
        fields = ("rf_source.port.output_enabled",)
        timeout_ms = self.config.connection.timeout_ms
        request = RfOutputRequest(port_id=port_id, enabled=False)
        try:
            with coordinator.authorize(
                operation_id="rf_source.output_recovery",
                purpose=SessionPurpose.RECOVERY,
                allowed_io={"write"},
                fields=fields,
                timeout_ms=timeout_ms,
                max_steps=1,
            ):
                rf_source.set_rf_output(request)
        except BaseException:
            return {"status": "off_failed", "session_health": session_state.health.value}
        if session_state.health in {SessionHealth.POISONED, SessionHealth.CLOSED}:
            return {"status": "off_sent_unverified", "reason": "session_unavailable"}
        try:
            with coordinator.authorize(
                operation_id="rf_source.output_recovery_verify",
                purpose=SessionPurpose.RECOVERY,
                allowed_io={"query"},
                fields=fields,
                timeout_ms=timeout_ms,
                max_steps=16,
            ):
                snapshot = rf_source.get_rf_snapshot()
                self._validate_output_disable_snapshot(
                    request,
                    snapshot,
                    operation=operation,
                )
        except BaseException:
            return {"status": "off_sent_unverified", "session_health": session_state.health.value}
        return {"status": "off_verified", "session_health": session_state.health.value}

    @staticmethod
    def _degrade_output_session_uncertain(session_state: InstrumentSessionState) -> None:
        if session_state.health is SessionHealth.HEALTHY:
            session_state.degrade(
                SessionHealth.UNCERTAIN,
                reason="rf_output_enable_unverified",
            )

    @staticmethod
    def _degrade_output_session_poisoned(session_state: InstrumentSessionState) -> None:
        if session_state.health not in {SessionHealth.POISONED, SessionHealth.CLOSED}:
            session_state.degrade(
                SessionHealth.POISONED,
                reason="rf_output_disable_unverified",
            )

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

    def _validate_trigger_snapshot_descriptor(
        self,
        port_id: str,
        operation: str,
    ) -> RfTriggerProfile:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.rf_source_extensions
        if not isinstance(extensions, RfSourceDescriptorExtensions):
            raise ConfigError(f"{operation} requires validated rf_source_extensions")
        if not any(port.port_id == port_id for port in extensions.topology.ports):
            raise ConfigError(f"{operation} references an undeclared RF port")
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.TRIGGER),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.READ not in feature.directions
            or port_id not in feature.port_ids
            or not isinstance(feature.profile, RfTriggerProfile)
            or not feature.profile.state_readable
        ):
            raise ConfigError(
                f"{operation} requires a readable trigger profile for the target port"
            )
        return feature.profile

    @staticmethod
    def _validate_trigger_snapshot_readback(
        port_id: str,
        snapshot: object,
        profile: RfTriggerProfile,
        *,
        operation: str,
    ) -> None:
        if not isinstance(snapshot, RfTriggerSnapshot):
            raise ConfigError(f"{operation} driver returned an invalid trigger snapshot")
        if snapshot.port_id != port_id:
            raise ConfigError(f"{operation} trigger snapshot does not match the requested port")
        if snapshot.pulse_trigger_mode not in profile.pulse_trigger_modes:
            raise ConfigError(f"{operation} readback pulse trigger mode is outside the descriptor profile")
        if snapshot.pulse_external_trigger_edge not in profile.pulse_external_trigger_edges:
            raise ConfigError(f"{operation} readback external trigger edge is outside the descriptor profile")
        if snapshot.pulse_external_gate_polarity not in profile.pulse_external_gate_polarities:
            raise ConfigError(f"{operation} readback external gate polarity is outside the descriptor profile")
        if snapshot.sweep_mode not in profile.sweep_modes:
            raise ConfigError(f"{operation} readback Sweep mode is outside the descriptor profile")
        if snapshot.sweep_period_trigger_mode not in profile.sweep_period_trigger_modes:
            raise ConfigError(
                f"{operation} readback Sweep-period trigger mode is outside the descriptor profile"
            )
        if snapshot.sweep_point_trigger_mode not in profile.sweep_point_trigger_modes:
            raise ConfigError(
                f"{operation} readback Sweep-point trigger mode is outside the descriptor profile"
            )

    def _validate_pulse_descriptor(
        self,
        request: RfPulseConfigureRequest,
        operation: str,
    ) -> RfPulseModeProfile:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.rf_source_extensions
        if not isinstance(extensions, RfSourceDescriptorExtensions):
            raise ConfigError(f"{operation} requires validated rf_source_extensions")
        if not any(port.port_id == request.port_id for port in extensions.topology.ports):
            raise ConfigError(f"{operation} references an undeclared RF port")
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.PULSE),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.CONFIGURE not in feature.directions
            or RfFeatureDirection.READ not in feature.directions
            or request.port_id not in feature.port_ids
            or not isinstance(feature.profile, RfPulseProfile)
            or not feature.profile.configuration_readable
        ):
            raise ConfigError(
                f"{operation} requires a readable configurable pulse profile for the target port"
            )
        mode_profile = next(
            (
                item
                for item in feature.profile.mode_profiles
                if item.source is RfPulseSource.INTERNAL and item.mode is RfPulseMode.SINGLE
            ),
            None,
        )
        if mode_profile is None:
            raise ConfigError(f"{operation} requires an internal single-pulse profile")
        if request.polarity not in mode_profile.polarities:
            raise ConfigError(f"{operation} request polarity is outside the descriptor profile")
        if not mode_profile.period_min_s <= request.period_s <= mode_profile.period_max_s:
            raise ConfigError(f"{operation} request period_s is outside the descriptor range")
        if not mode_profile.width_min_s <= request.width_s <= mode_profile.width_max_s:
            raise ConfigError(f"{operation} request width_s is outside the descriptor range")
        if request.width_s > request.period_s - mode_profile.minimum_off_time_s:
            raise ConfigError(f"{operation} request width_s violates the descriptor minimum off time")
        return mode_profile

    def _validate_pulse_preflight(
        self,
        request: RfPulseConfigureRequest,
        snapshot: RfSourceSnapshot,
        *,
        operation: str,
    ) -> RfPortSnapshot:
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
        if not isinstance(protection, RfProtectionStatus):
            raise ConfigError(f"{operation} requires a valid protection state")
        if protection.active_codes:
            raise ConfigError(f"{operation} requires no active protection condition")
        return port

    def _validate_pulse_postcondition(
        self,
        request: RfPulseConfigureRequest,
        snapshot: RfSourceSnapshot,
        pulse_snapshot: RfPulseSnapshot,
        mode_profile: RfPulseModeProfile,
        *,
        operation: str,
    ) -> RfPulseConfigureResult:
        self._validate_pulse_preflight(request, snapshot, operation=operation)
        if pulse_snapshot.port_id != request.port_id:
            raise ConfigError(f"{operation} pulse snapshot does not match the requested port")
        if pulse_snapshot.source is not mode_profile.source or pulse_snapshot.mode is not mode_profile.mode:
            raise ConfigError(f"{operation} postcondition requires the declared internal single-pulse profile")
        if pulse_snapshot.state is not RfPulseState.DISABLED:
            raise ConfigError(f"{operation} postcondition requires Pulse disabled")
        if (
            pulse_snapshot.period_s != request.period_s
            or pulse_snapshot.width_s != request.width_s
            or pulse_snapshot.polarity is not request.polarity
        ):
            raise ConfigError(f"{operation} pulse readback does not match request")
        return RfPulseConfigureResult(
            port_id=request.port_id,
            period_s=request.period_s,
            width_s=request.width_s,
            polarity=request.polarity,
        )

    def _validate_sweep_descriptor(
        self,
        request: RfSweepConfigureRequest,
        operation: str,
    ) -> RfSweepModeProfile:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.rf_source_extensions
        if not isinstance(extensions, RfSourceDescriptorExtensions):
            raise ConfigError(f"{operation} requires validated rf_source_extensions")
        if not any(port.port_id == request.port_id for port in extensions.topology.ports):
            raise ConfigError(f"{operation} references an undeclared RF port")
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.SWEEP),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.CONFIGURE not in feature.directions
            or RfFeatureDirection.READ not in feature.directions
            or request.port_id not in feature.port_ids
            or not isinstance(feature.profile, RfSweepProfile)
            or not feature.profile.configuration_readable
        ):
            raise ConfigError(
                f"{operation} requires a readable configurable Sweep profile for the target port"
            )
        mode_profile = next(
            (
                item
                for item in feature.profile.mode_profiles
                if (
                    item.sweep_type is RfSweepType.STEP
                    and item.direction is RfSweepDirection.FORWARD
                    and item.shape is RfSweepShape.RAMP
                    and item.spacing is RfSweepSpacing.LINEAR
                )
            ),
            None,
        )
        if mode_profile is None:
            raise ConfigError(
                f"{operation} requires a frequency-only forward linear Step Sweep profile"
            )
        if not (
            mode_profile.frequency_min_hz
            <= request.start_frequency_hz
            <= mode_profile.frequency_max_hz
        ):
            raise ConfigError(f"{operation} request start_frequency_hz is outside the descriptor range")
        if not (
            mode_profile.frequency_min_hz
            <= request.stop_frequency_hz
            <= mode_profile.frequency_max_hz
        ):
            raise ConfigError(f"{operation} request stop_frequency_hz is outside the descriptor range")
        if not mode_profile.points_min <= request.points <= mode_profile.points_max:
            raise ConfigError(f"{operation} request points is outside the descriptor range")
        if not mode_profile.dwell_min_s <= request.dwell_s <= mode_profile.dwell_max_s:
            raise ConfigError(f"{operation} request dwell_s is outside the descriptor range")
        return mode_profile

    def _validate_sweep_preflight(
        self,
        request: RfSweepConfigureRequest,
        snapshot: RfSourceSnapshot,
        *,
        operation: str,
    ) -> RfPortSnapshot:
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
        if not isinstance(protection, RfProtectionStatus):
            raise ConfigError(f"{operation} requires a valid protection state")
        if protection.active_codes:
            raise ConfigError(f"{operation} requires no active protection condition")
        return port

    def _validate_sweep_postcondition(
        self,
        request: RfSweepConfigureRequest,
        snapshot: RfSourceSnapshot,
        sweep_snapshot: RfSweepSnapshot,
        mode_profile: RfSweepModeProfile,
        *,
        operation: str,
    ) -> RfSweepConfigureResult:
        self._validate_sweep_preflight(request, snapshot, operation=operation)
        if sweep_snapshot.port_id != request.port_id:
            raise ConfigError(f"{operation} Sweep snapshot does not match the requested port")
        if (
            sweep_snapshot.sweep_type is not mode_profile.sweep_type
            or sweep_snapshot.direction is not mode_profile.direction
            or sweep_snapshot.shape is not mode_profile.shape
            or sweep_snapshot.spacing is not mode_profile.spacing
        ):
            raise ConfigError(
                f"{operation} postcondition requires the declared frequency-only Step Sweep profile"
            )
        if sweep_snapshot.state is not RfSweepState.DISABLED:
            raise ConfigError(f"{operation} postcondition requires Sweep disabled")
        if (
            sweep_snapshot.start_frequency_hz != request.start_frequency_hz
            or sweep_snapshot.stop_frequency_hz != request.stop_frequency_hz
            or sweep_snapshot.points != request.points
            or sweep_snapshot.dwell_s != request.dwell_s
        ):
            raise ConfigError(f"{operation} Sweep readback does not match request")
        return RfSweepConfigureResult(
            port_id=request.port_id,
            start_frequency_hz=request.start_frequency_hz,
            stop_frequency_hz=request.stop_frequency_hz,
            points=request.points,
            dwell_s=request.dwell_s,
        )

    def _validate_modulation_descriptor(
        self,
        request: RfModulationRequest,
        operation: str,
    ) -> RfModulationModeProfile:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.rf_source_extensions
        if not isinstance(extensions, RfSourceDescriptorExtensions):
            raise ConfigError(f"{operation} requires validated rf_source_extensions")
        if not any(port.port_id == request.port_id for port in extensions.topology.ports):
            raise ConfigError(f"{operation} references an undeclared RF port")
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.MODULATION),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.CONFIGURE not in feature.directions
            or RfFeatureDirection.READ not in feature.directions
            or request.port_id not in feature.port_ids
            or not isinstance(feature.profile, RfModulationProfile)
            or not feature.profile.configuration_readable
        ):
            raise ConfigError(
                f"{operation} requires a readable configurable modulation profile for the target port"
            )
        mode_profile = next(
            (item for item in feature.profile.mode_profiles if item.kind is request.kind),
            None,
        )
        if mode_profile is None:
            raise ConfigError(f"{operation} does not support the requested modulation kind")
        if (
            mode_profile.source is not RfModulationSource.INTERNAL
            or mode_profile.waveform is not RfModulationWaveform.SINE
            or mode_profile.value_unit is not request.value_unit
        ):
            raise ConfigError(f"{operation} requires an internal-sine profile for the requested kind")
        if not mode_profile.value_min <= request.value <= mode_profile.value_max:
            raise ConfigError(f"{operation} request value is outside the descriptor range")
        if not (
            mode_profile.internal_frequency_min_hz
            <= request.internal_frequency_hz
            <= mode_profile.internal_frequency_max_hz
        ):
            raise ConfigError(
                f"{operation} request internal_frequency_hz is outside the descriptor range"
            )
        return mode_profile

    def _validate_modulation_disable_descriptor(
        self,
        request: RfModulationDisableRequest,
        operation: str,
    ) -> None:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.rf_source_extensions
        if not isinstance(extensions, RfSourceDescriptorExtensions):
            raise ConfigError(f"{operation} requires validated rf_source_extensions")
        if not any(port.port_id == request.port_id for port in extensions.topology.ports):
            raise ConfigError(f"{operation} references an undeclared RF port")
        feature = next(
            (item for item in extensions.features if item.feature is RfFeature.MODULATION),
            None,
        )
        if (
            feature is None
            or RfFeatureDirection.DISABLE not in feature.directions
            or RfFeatureDirection.READ not in feature.directions
            or request.port_id not in feature.port_ids
            or not isinstance(feature.profile, RfModulationProfile)
            or not feature.profile.state_readable
        ):
            raise ConfigError(
                f"{operation} requires a readable disable-capable modulation profile for the target port"
            )

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

    def _validate_modulation_preflight(
        self,
        request: RfModulationRequest,
        snapshot: RfSourceSnapshot,
        modulation_state: RfModulationStateSnapshot,
        *,
        operation: str,
    ) -> None:
        self._validate_modulation_rf_snapshot(
            request,
            snapshot,
            expected_modulation_state=RfModulationState.DISABLED,
            operation=operation,
        )
        if modulation_state.port_id != request.port_id:
            raise ConfigError(f"{operation} modulation state does not match the requested port")
        if modulation_state.global_enabled or modulation_state.enabled_modes:
            raise ConfigError(f"{operation} requires all modulation modes disabled")
        if modulation_state.fault_codes:
            raise ConfigError(f"{operation} requires no active modulation fault condition")

    def _validate_modulation_postcondition(
        self,
        request: RfModulationRequest,
        snapshot: RfSourceSnapshot,
        modulation_snapshot: RfModulationSnapshot,
        mode_profile: RfModulationModeProfile,
        *,
        operation: str,
    ) -> RfModulationResult:
        self._validate_modulation_rf_snapshot(
            request,
            snapshot,
            expected_modulation_state=RfModulationState.ENABLED,
            operation=operation,
        )
        self._validate_modulation_snapshot_identity(
            request,
            modulation_snapshot,
            mode_profile,
            require_target_profile=True,
            require_selected_fm_pm_kind=True,
            operation=operation,
        )
        if modulation_snapshot.enabled_modes != (request.kind,):
            raise ConfigError(f"{operation} postcondition requires only the requested modulation mode")
        if modulation_snapshot.global_enabled is not True:
            raise ConfigError(f"{operation} postcondition requires global modulation enabled")
        if modulation_snapshot.fault_codes:
            raise ConfigError(f"{operation} postcondition reports an active modulation fault condition")
        if (
            modulation_snapshot.internal_frequency_hz != request.internal_frequency_hz
            or modulation_snapshot.value != request.value
            or modulation_snapshot.value_unit is not request.value_unit
        ):
            raise ConfigError(f"{operation} modulation readback does not match request")
        return self._modulation_result(request)

    @staticmethod
    def _modulation_result(request: RfModulationRequest) -> RfModulationResult:
        if request.kind is RfModulationKind.AM:
            return RfModulationResult(
                port_id=request.port_id,
                kind=request.kind,
                internal_frequency_hz=request.internal_frequency_hz,
                depth_percent=request.value,
            )
        if request.kind is RfModulationKind.FM:
            return RfModulationResult(
                port_id=request.port_id,
                kind=request.kind,
                internal_frequency_hz=request.internal_frequency_hz,
                frequency_deviation_hz=request.value,
            )
        return RfModulationResult(
            port_id=request.port_id,
            kind=request.kind,
            internal_frequency_hz=request.internal_frequency_hz,
            phase_deviation_rad=request.value,
        )

    def _validate_modulation_disable_preflight(
        self,
        request: RfModulationDisableRequest,
        snapshot: RfSourceSnapshot,
        modulation_state: RfModulationStateSnapshot,
        *,
        operation: str,
    ) -> bool:
        modulation = self._validate_modulation_safe_rf_snapshot(
            request.port_id,
            snapshot,
            operation=operation,
        )
        if modulation_state.port_id != request.port_id:
            raise ConfigError(f"{operation} modulation state does not match the requested port")
        if modulation_state.fault_codes:
            raise ConfigError(f"{operation} requires no active modulation fault condition")
        if modulation is RfModulationState.DISABLED:
            if modulation_state.global_enabled or modulation_state.enabled_modes:
                raise ConfigError(
                    f"{operation} rejects a disabled RF snapshot with active modulation state"
                )
            return False
        if modulation_state.enabled_modes != (request.kind,):
            raise ConfigError(f"{operation} requires only the requested modulation mode to be active")
        if modulation_state.global_enabled is not True:
            raise ConfigError(f"{operation} requires global modulation enabled")
        return True

    def _validate_modulation_disable_postcondition(
        self,
        request: RfModulationDisableRequest,
        snapshot: RfSourceSnapshot,
        modulation_state: RfModulationStateSnapshot,
        *,
        operation: str,
    ) -> None:
        modulation = self._validate_modulation_safe_rf_snapshot(
            request.port_id,
            snapshot,
            operation=operation,
        )
        if modulation is not RfModulationState.DISABLED:
            raise ConfigError(f"{operation} postcondition requires modulation disabled")
        if modulation_state.port_id != request.port_id:
            raise ConfigError(f"{operation} modulation state does not match the requested port")
        if modulation_state.enabled_modes:
            raise ConfigError(f"{operation} postcondition requires all modulation modes disabled")
        if modulation_state.global_enabled:
            raise ConfigError(f"{operation} postcondition requires global modulation disabled")
        if modulation_state.fault_codes:
            raise ConfigError(f"{operation} postcondition reports an active modulation fault condition")

    def _validate_modulation_rf_snapshot(
        self,
        request: RfModulationRequest,
        snapshot: RfSourceSnapshot,
        *,
        expected_modulation_state: RfModulationState,
        operation: str,
    ) -> None:
        modulation = self._validate_modulation_safe_rf_snapshot(
            request.port_id,
            snapshot,
            operation=operation,
        )
        if modulation is not expected_modulation_state:
            expected = expected_modulation_state.value
            raise ConfigError(f"{operation} requires modulation state {expected}")

    def _validate_modulation_safe_rf_snapshot(
        self,
        port_id: str,
        snapshot: RfSourceSnapshot,
        *,
        operation: str,
    ) -> RfModulationState:
        port = self._snapshot_port(snapshot, port_id, operation=operation)
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
        if not isinstance(modulation, RfModulationState):
            raise ConfigError(f"{operation} requires a valid modulation state")
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
        if not isinstance(protection, RfProtectionStatus):
            raise ConfigError(f"{operation} requires a valid protection state")
        if protection.active_codes:
            raise ConfigError(f"{operation} requires no active protection condition")
        return modulation

    @staticmethod
    def _validate_modulation_snapshot_identity(
        request: RfModulationRequest,
        snapshot: RfModulationSnapshot,
        mode_profile: RfModulationModeProfile,
        *,
        require_target_profile: bool,
        require_selected_fm_pm_kind: bool,
        operation: str,
    ) -> None:
        if snapshot.port_id != request.port_id or snapshot.kind is not request.kind:
            raise ConfigError(f"{operation} modulation snapshot does not match the requested port and kind")
        if require_target_profile and (
            snapshot.source is not mode_profile.source or snapshot.waveform is not mode_profile.waveform
        ):
            raise ConfigError(
                f"{operation} postcondition requires the requested internal-sine source and waveform"
            )
        if request.kind is RfModulationKind.AM:
            if snapshot.selected_fm_pm_kind is not None:
                raise ConfigError(f"{operation} AM snapshot has an unexpected FM/PM selection")
            return
        if snapshot.selected_fm_pm_kind not in {
            RfModulationKind.FM,
            RfModulationKind.PM,
        }:
            raise ConfigError(f"{operation} requires a readable FM/PM selection")
        if (
            require_selected_fm_pm_kind
            and snapshot.selected_fm_pm_kind is not request.kind
        ):
            raise ConfigError(f"{operation} postcondition does not select the requested FM/PM kind")

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
