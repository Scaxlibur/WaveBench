from __future__ import annotations

import csv
import json
import os
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import numpy as np

from wavebench.config import WaveBenchConfig
from wavebench.data.package import new_package_dir
from wavebench.errors import ConfigError, DataError, SessionHealthError, WaveBenchError
from wavebench.instruments.api import InstrumentDescriptor, ScopeCouplingPolicy
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.contracts import (
    MultiChannelScopeDriver,
    ScopeAcquisitionStatusDriver,
    ScopeAverageCaptureDriver,
    ScopeAnalysisReadDriver,
    ScopeChannelInputStateDriverV2,
    ScopeDriver,
    ScopeDigitalStatusDriver,
    ScopeDigitalStatusDriverV2,
    ScopeDigitalWaveformDriver,
    ScopeHistoryTimestampsDriver,
    ScopeMeasurementStatisticsDriver,
    ScopeMeasurementStatisticsDriverV2,
    ScopeSnapshotDriver,
    ScopeSnapshotDriverV2,
)
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import (
    ScopeAcquisitionStatus,
    ScopeAverageCaptureRequest,
    ScopeAverageCaptureResult,
    ScopeChannelInputStateV2,
    ScopeCursorReadout,
    ScopeDerivedWaveformMetadata,
    ScopeDigitalChannelStatus,
    ScopeDigitalChannelStatusV2,
    ScopeDigitalWaveform,
    ScopeDigitalWaveformRequest,
    ScopeFftStatus,
    ScopeHistoryTimestamps,
    ScopeMeasurementStatistics,
    ScopeMeasurementStatisticsRequestV2,
    ScopeMeasurementStatisticsV2,
    ScopeSnapshot,
    ScopeSnapshotV2,
    WaveformData,
)
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.instruments.scope_extensions import (
    ErrorCheckSpec,
    ScopeContinuousAcquisitionRequest,
    ScopeAcquisitionStatusDriverV2,
    ScopeAcquisitionStatusProfileV2,
    ScopeAcquisitionStatusV2,
    ScopeMeasurementStatisticsProfileV2,
    ScopeScreenshotRequest,
    ScopeSnapshotProfileV2,
    ScopeTraceRef,
    ScopeWaveformBinaryProfile,
)
from wavebench.logging import CommandLogger
from wavebench.services.access_policy import access_policy
from wavebench.services.operation_specs import OperationSpec, require_operation_spec
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.session_alias import SessionStateAliasMixin
from wavebench.services.scope_extension_service import (
    ScopeExtensionOperationResult,
    ScopeExtensionService,
)
from wavebench.services.scope_waveform_executor import BoundedWaveformExecutor
from wavebench.transport.base import InstrumentTransport
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import (
    InstrumentSessionState,
    SessionHealth,
    SessionPurpose,
    SessionTransactionCoordinator,
)

HIGH_IMPEDANCE_COUPLINGS = {"DCL", "DCLIMIT", "ACL", "ACLIMIT"}
LOW_IMPEDANCE_COUPLINGS = {"DC", "AC"}


@dataclass(frozen=True)
class ScopeStatusSummary:
    """Stable scope status result that can represent a partial capability set."""

    status: str
    channel: int
    snapshot: ScopeSnapshot | None = None
    idn: str | None = None
    coupling: str | None = None
    missing_capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "channel": self.channel,
            "idn": self.idn,
            "coupling": self.coupling,
            "missing_capabilities": list(self.missing_capabilities),
        }
        if self.snapshot is not None:
            payload["snapshot"] = asdict(self.snapshot)
        return payload


def normalize_coupling(value: str) -> str:
    return value.strip().upper()


def is_high_impedance_coupling(value: str) -> bool:
    return normalize_coupling(value) in HIGH_IMPEDANCE_COUPLINGS


def assert_scope_high_impedance(
    coupling: str,
    *,
    channel: int,
    allow_50ohm: bool = False,
    driver: str = "rtm2032",
    coupling_policy: ScopeCouplingPolicy | None = None,
) -> str:
    normalized = normalize_coupling(coupling)
    policy = coupling_policy
    if policy is None:
        policy = resolve_instrument_descriptor(driver, expected_kind="scope").scope_coupling_policy
    if policy == "fixed-high-impedance":
        if normalized in {"AC", "DC", "GND"}:
            return normalized
        raise ConfigError(
            f"scope CH{channel} coupling {normalized!r} is not recognized for RIGOL DS1000Z; "
            "expected AC, DC, or GND. / "
            f"示波器 CH{channel} 耦合值 {normalized!r} 不是已知的 RIGOL DS1000Z 耦合方式。"
        )
    if policy == "switchable-termination" and normalized in HIGH_IMPEDANCE_COUPLINGS:
        return normalized
    if policy == "switchable-termination" and allow_50ohm and normalized in LOW_IMPEDANCE_COUPLINGS:
        return normalized
    if policy == "switchable-termination" and normalized in LOW_IMPEDANCE_COUPLINGS:
        raise ConfigError(
            f"scope CH{channel} coupling is {normalized}, which may use 50 ohm termination; "
            "default capture requires high impedance. Pass --allow-50ohm or set "
            "safety.allow_50ohm = true only when the test setup explicitly accepts this. "
            f"/ 示波器 CH{channel} 当前耦合为 {normalized}，可能是 50Ω 输入；默认要求高阻测量。"
            "只有明确允许 50Ω 时才使用 --allow-50ohm 或 safety.allow_50ohm = true。"
        )
    raise ConfigError(
        f"scope CH{channel} coupling {normalized!r} is not recognized; refusing capture by default. "
        "Known high-impedance values: ACL, ACLimit, DCL, DCLimit. "
        f"/ 示波器 CH{channel} 耦合值 {normalized!r} 无法确认是否高阻，默认拒绝采集。"
    )


def assert_scope_input_state_safe(
    state: ScopeChannelInputStateV2,
    *,
    allow_50ohm: bool = False,
) -> ScopeChannelInputStateV2:
    """Apply the V2 termination policy without changing any legacy capture route."""

    if state.termination == "high_z":
        return state
    if state.termination == "50_ohm" and allow_50ohm is True:
        return state
    if state.termination == "50_ohm":
        raise ConfigError(
            f"scope CH{state.channel} input termination is 50 ohm; default use requires "
            "high impedance. Pass --allow-50ohm only when the test setup explicitly "
            "accepts the measured 50 ohm input."
        )
    raise ConfigError(
        f"scope CH{state.channel} input termination is unknown; refusing use even when "
        "--allow-50ohm was requested."
    )


@dataclass(frozen=True)
class CaptureResult:
    package_dir: Path
    waveform: WaveformData
    metadata_path: Path
    csv_path: Path | None
    npy_path: Path | None
    screenshot_path: Path | None
    commands_log_path: Path | None

@dataclass(frozen=True)
class MultiCaptureResult:
    package_dir: Path
    waveforms: dict[int, WaveformData]
    metadata_path: Path
    files: dict[str, dict[str, str]]
    screenshot_path: Path | None
    commands_log_path: Path | None

@dataclass
class ScopeService(SessionStateAliasMixin):
    config: WaveBenchConfig
    logger: CommandLogger
    session: ScopeDriver | None = None
    descriptor: InstrumentDescriptor | None = None
    transport: InstrumentTransport | None = None
    session_state: InstrumentSessionState | None = None
    lease: ResourceLease | None = None

    def _require(self, operation: str, *capabilities: str) -> OperationSpec:
        spec = require_operation_spec(operation)
        if spec.session_purpose != "normal":
            raise ConfigError(
                f"public scope operation {operation!r} cannot use session purpose "
                f"{spec.session_purpose!r}"
            )
        access_policy(getattr(self.config.scope, "access", "read_write"), "scope.access").require(
            spec,
            operation=operation,
        )
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        require_capabilities(descriptor, capabilities, operation=operation)
        return spec

    def _operation_timeout_ms(self, spec: OperationSpec) -> int:
        if spec.timeout_source == "connection.timeout_ms":
            return self.config.connection.timeout_ms
        if spec.timeout_source == "operation.timeout_ms" and spec.operation_timeout_ms is not None:
            return min(spec.operation_timeout_ms, self.config.connection.timeout_ms)
        raise ConfigError(
            f"unsupported timeout source {spec.timeout_source!r} for {spec.operation!r}"
        )

    def _session_preflight(
        self,
        operation: str,
        scope: ScopeDriver,
    ) -> dict[str, str]:
        """Verify required fields for a normal operation before it can mutate I/O state."""

        spec = require_operation_spec(operation)
        required = frozenset(spec.required_verified_fields)
        if not required:
            return {}
        state = self.session_state
        if state is None:
            # Test doubles and legacy no-transport drivers have no physical
            # connection epoch.  A real core transport must always expose its
            # state; fail closed if only that alias is missing.
            if self.transport is not None:
                raise ConfigError(
                    f"operation {operation!r} requires a shared instrument session state"
                )
            return {}
        with state.transaction_lock:
            if state.health is not SessionHealth.HEALTHY:
                raise SessionHealthError(
                    "normal operation requires a healthy session",
                    health=state.health.value,
                    io_kind="operation_preflight",
                    epoch_id=state.epoch_id,
                )

            missing = required - state.verified_fields
            unsupported = missing - {"scope.identity"}
            if unsupported:
                raise ConfigError(
                    f"operation {operation!r} has no verifier for required fields: "
                    + ", ".join(sorted(unsupported))
                )
            evidence: dict[str, str] = {}
            if "scope.identity" in missing:
                evidence["scope.identity"] = self._verify_scope_identity(
                    scope,
                    spec=spec,
                )
            remaining = required - state.verified_fields
            if remaining:
                raise ConfigError(
                    f"operation {operation!r} is missing verified fields: "
                    + ", ".join(sorted(remaining))
                )
            return evidence

    def _verify_scope_identity(self, scope: ScopeDriver, *, spec: OperationSpec) -> str:
        state = self.session_state
        if state is None:
            return scope.idn()
        with state.transaction_lock:
            if state.health is not SessionHealth.HEALTHY:
                raise SessionHealthError(
                    "normal operation requires a healthy session",
                    health=state.health.value,
                    io_kind="operation_preflight",
                    epoch_id=state.epoch_id,
                )
            coordinator = SessionTransactionCoordinator(state)
            fields = ("scope.identity",)
            with coordinator.authorize(
                operation_id=f"{spec.operation}.identity",
                purpose=SessionPurpose.VERIFICATION,
                allowed_io=("query",),
                fields=fields,
                timeout_ms=self._operation_timeout_ms(spec),
                max_steps=1,
                evidence_fields={"query": fields},
            ) as authorization:
                identity = scope.idn()
                if not isinstance(identity, str) or not identity.strip():
                    raise DataError("scope identity verification returned an empty response")
                coordinator.record_evidence(authorization, "query", fields)
                coordinator.complete_verification(authorization)
                return identity

    def _open_scope(self) -> ScopeDriver:
        self._prepare_session_open("scope")
        if self.lease is None:
            self.lease = ResourceLease(
                resource=self.config.connection.resource,
                operation="scope.session",
            )
        opened = open_instrument_driver(
            driver_reference=self.config.scope.driver,
            expected_kind="scope",
            resource=self.config.connection.resource,
            configured_backend=self.config.connection.backend,
            timeout_ms=self.config.connection.timeout_ms,
            opc_timeout_ms=self.config.connection.opc_timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            settings={"check_errors": self.config.scope.check_errors},
            options=getattr(self.config.scope, "options", {}),
            access=getattr(self.config.scope, "access", "read_write"),
            lease=self.lease,
        )
        self.descriptor = opened.descriptor
        self.transport = opened.transport
        self.session_state = getattr(opened, "session_state", None)
        return opened.driver

    def audit_snapshot(self) -> dict[str, Any] | None:
        snapshot = getattr(self.transport, "audit_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def open_session(self) -> ScopeDriver:
        return self._open_scope()

    @contextmanager
    def _scope_session(self) -> Iterator[ScopeDriver]:
        if self.session is not None:
            yield self.session
            return
        scope = self._open_scope()
        try:
            yield scope
        finally:
            scope.close()

    def idn(self) -> str:
        spec = self._require("scope.idn", "scope.idn")
        with self._scope_session() as scope:
            state = self.session_state
            if state is None or "scope.identity" in state.verified_fields:
                return scope.idn()
            if state.health is not SessionHealth.HEALTHY:
                raise SessionHealthError(
                    "normal operation requires a healthy session",
                    health=state.health.value,
                    io_kind="operation_preflight",
                    epoch_id=state.epoch_id,
                )
            return self._verify_scope_identity(scope, spec=spec)

    def errors(self) -> list[str]:
        self._require("scope.errors", "scope.errors")
        with self._scope_session() as scope:
            return scope.errors()

    def status(self, channel: int) -> ScopeSnapshot:
        self._require("scope.status", "scope.snapshot")
        with self._scope_session() as scope:
            return cast(ScopeSnapshotDriver, scope).get_snapshot(channel)

    def snapshot_v2(self, channel: int) -> ScopeSnapshotV2:
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise ConfigError("scope snapshot V2 channel must be a positive integer")
        spec = self._require("scope.snapshot_v2", "scope.snapshot_v2")
        profile = self._snapshot_v2_profile()
        if profile is None:
            raise ConfigError("scope snapshot V2 requires scope_extensions.snapshot_profile_v2")
        with self._scope_session() as scope:
            return self._execute_snapshot_v2(
                cast(ScopeSnapshotDriverV2, scope),
                channel=channel,
                profile=profile,
                spec=spec,
            )

    def _execute_snapshot_v2(
        self,
        scope: ScopeSnapshotDriverV2,
        *,
        channel: int,
        profile: ScopeSnapshotProfileV2,
        spec: OperationSpec,
    ) -> ScopeSnapshotV2:
        state = self.session_state
        guarded_transport = (
            self.transport if isinstance(self.transport, GuardedAuditedTransport) else None
        )
        query_calls_before = (
            guarded_transport.counters.query_calls if guarded_transport is not None else None
        )
        if state is None:
            result = scope.get_snapshot_v2(channel, fields=profile.readable_fields)
        else:
            timeout_ms = self._operation_timeout_ms(spec)
            coordinator = SessionTransactionCoordinator(state)
            with coordinator.authorize_normal(
                operation_id=spec.operation,
                allowed_io=("query",),
                fields=("scope.snapshot_v2",),
                timeout_ms=timeout_ms,
                max_steps=profile.max_queries,
                context_id="scope_snapshot_v2",
                correlation_id=uuid4().hex,
                phase="main",
                absolute_deadline=time.monotonic() + (timeout_ms / 1000.0),
            ):
                result = scope.get_snapshot_v2(channel, fields=profile.readable_fields)
        if query_calls_before is not None and guarded_transport is not None:
            query_calls = guarded_transport.counters.query_calls - query_calls_before
            if query_calls > profile.max_queries:
                raise DataError("scope snapshot V2 exceeded its descriptor query budget")
        try:
            profile.validate_result(result, channel=channel)
        except (TypeError, ValueError) as exc:
            raise DataError(f"scope snapshot V2 driver returned an invalid result: {exc}") from exc
        return result

    def status_summary(self, channel: int, *, strict: bool = False) -> ScopeStatusSummary:
        """Return a complete snapshot when available, otherwise a read-only partial summary."""

        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        if "scope.snapshot" in descriptor.capabilities:
            snapshot = self.status(channel)
            return ScopeStatusSummary(
                status="ok",
                channel=channel,
                snapshot=snapshot,
                coupling=snapshot.channel.coupling,
            )
        if strict:
            self._require("scope.status", "scope.snapshot")
        self._require("scope.status", "scope.idn")
        missing = ["scope.snapshot"]
        identity: str | None = None
        coupling: str | None = None
        with self._scope_session() as scope:
            identity = scope.idn()
            if "scope.channel_coupling" in descriptor.capabilities:
                coupling = scope.channel_coupling(channel)
            else:
                missing.append("scope.channel_coupling")
        return ScopeStatusSummary(
            status="partial",
            channel=channel,
            idn=identity,
            coupling=coupling,
            missing_capabilities=tuple(missing),
        )

    def acquisition_status(self) -> ScopeAcquisitionStatus:
        self._require("scope.acquisition_status", "scope.acquisition_status")
        with self._scope_session() as scope:
            return cast(ScopeAcquisitionStatusDriver, scope).get_acquisition_status()

    def acquisition_status_v2(self) -> ScopeAcquisitionStatusV2:
        spec = self._require(
            "scope.acquisition_status_v2",
            "scope.acquisition_status_v2",
        )
        profile = self._acquisition_status_v2_profile()
        if profile is None:
            raise ConfigError(
                "scope acquisition status V2 requires "
                "scope_extensions.acquisition_status_profile_v2"
            )
        with self._scope_session() as scope:
            return self._execute_acquisition_status_v2(
                cast(ScopeAcquisitionStatusDriverV2, scope),
                profile=profile,
                spec=spec,
            )

    def _execute_acquisition_status_v2(
        self,
        scope: ScopeAcquisitionStatusDriverV2,
        *,
        profile: ScopeAcquisitionStatusProfileV2,
        spec: OperationSpec,
    ) -> ScopeAcquisitionStatusV2:
        state = self.session_state
        guarded_transport = (
            self.transport if isinstance(self.transport, GuardedAuditedTransport) else None
        )
        query_calls_before = (
            guarded_transport.counters.query_calls if guarded_transport is not None else None
        )
        if state is None:
            result = scope.get_acquisition_status_v2(fields=profile.readable_fields)
        else:
            timeout_ms = self._operation_timeout_ms(spec)
            coordinator = SessionTransactionCoordinator(state)
            with coordinator.authorize_normal(
                operation_id=spec.operation,
                allowed_io=("query",),
                fields=("scope.acquisition_status_v2",),
                timeout_ms=timeout_ms,
                max_steps=profile.max_queries,
                context_id="scope_acquisition_status_v2",
                correlation_id=uuid4().hex,
                phase="main",
                absolute_deadline=time.monotonic() + (timeout_ms / 1000.0),
            ):
                result = scope.get_acquisition_status_v2(fields=profile.readable_fields)
        if query_calls_before is not None and guarded_transport is not None:
            query_calls = guarded_transport.counters.query_calls - query_calls_before
            if query_calls > profile.max_queries:
                raise DataError(
                    "scope acquisition status V2 exceeded its descriptor query budget"
                )
        try:
            profile.validate_result(result)
        except (TypeError, ValueError) as exc:
            raise DataError(
                f"scope acquisition status V2 driver returned an invalid result: {exc}"
            ) from exc
        return result

    def capture_average(
        self,
        *,
        channels: tuple[int, ...],
        average_count: int,
        acquisition_stopped: bool,
        allow_50ohm: bool = False,
    ) -> ScopeAverageCaptureResult:
        request = ScopeAverageCaptureRequest(
            channels=channels,
            average_count=average_count,
            acquisition_stopped=acquisition_stopped,
        )
        self._require(
            "scope.capture_average",
            "scope.capture_average",
            "scope.channel_coupling",
        )
        with self._scope_session() as scope:
            descriptor = self.descriptor or resolve_instrument_descriptor(
                self.config.scope.driver,
                expected_kind="scope",
            )
            for channel in request.channels:
                assert_scope_high_impedance(
                    scope.channel_coupling(channel),
                    channel=channel,
                    allow_50ohm=allow_50ohm,
                    driver=descriptor.driver_id,
                    coupling_policy=descriptor.scope_coupling_policy,
                )
            return cast(ScopeAverageCaptureDriver, scope).capture_average(request)

    def history_timestamps(self, channel: int) -> ScopeHistoryTimestamps:
        self._require("scope.history_timestamps", "scope.history_timestamps")
        with self._scope_session() as scope:
            return cast(ScopeHistoryTimestampsDriver, scope).get_history_timestamps(channel)

    def digital_status(self, channel: int) -> ScopeDigitalChannelStatus:
        self._require("scope.digital_status", "scope.digital_status")
        with self._scope_session() as scope:
            return cast(ScopeDigitalStatusDriver, scope).get_digital_status(channel)

    def digital_status_v2(self, channel: int) -> ScopeDigitalChannelStatusV2:
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 0:
            raise ConfigError("digital status V2 channel must be a non-negative integer")
        self._require("scope.digital_status_v2", "scope.digital_status_v2")
        with self._scope_session() as scope:
            result = cast(ScopeDigitalStatusDriverV2, scope).get_digital_status_v2(channel)
        if not isinstance(result, ScopeDigitalChannelStatusV2):
            raise DataError("digital status V2 driver returned an invalid result")
        if result.channel != channel:
            raise DataError("digital status V2 driver returned the wrong channel")
        return result

    def digital_waveform(
        self,
        *,
        channels: tuple[int, ...],
        acquisition_stopped: bool,
    ) -> ScopeDigitalWaveform:
        try:
            request = ScopeDigitalWaveformRequest(
                channels=channels,
                acquisition_stopped=acquisition_stopped,
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        self._require("scope.digital_waveform", "scope.digital_waveform")
        with self._scope_session() as scope:
            return cast(ScopeDigitalWaveformDriver, scope).get_digital_waveform(
                request
            )

    def measurement_statistics(
        self,
        slot: int,
        *,
        configured_slot: bool,
        include_buffer: bool = False,
        acquisition_stopped: bool = False,
    ) -> ScopeMeasurementStatistics:
        self._require("scope.measurement_statistics", "scope.measurement_statistics")
        with self._scope_session() as scope:
            return cast(ScopeMeasurementStatisticsDriver, scope).get_measurement_statistics(
                slot,
                configured_slot=configured_slot,
                include_buffer=include_buffer,
                acquisition_stopped=acquisition_stopped,
            )

    def measurement_statistics_v2(
        self,
        request: ScopeMeasurementStatisticsRequestV2,
    ) -> ScopeMeasurementStatisticsV2:
        if not isinstance(request, ScopeMeasurementStatisticsRequestV2):
            raise ConfigError("measurement statistics V2 request has an invalid type")
        spec = self._require(
            "scope.measurement_statistics_v2",
            "scope.measurement_statistics_v2",
        )
        profile = self._measurement_statistics_v2_profile()
        if profile is None:
            raise ConfigError(
                "scope measurement statistics V2 requires "
                "scope_extensions.measurement_statistics_profile_v2"
            )
        try:
            profile.validate_request(request)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid measurement statistics V2 request: {exc}") from exc
        with self._scope_session() as scope:
            return self._execute_measurement_statistics_v2(
                cast(ScopeMeasurementStatisticsDriverV2, scope),
                request=request,
                profile=profile,
                spec=spec,
            )

    def _execute_measurement_statistics_v2(
        self,
        scope: ScopeMeasurementStatisticsDriverV2,
        *,
        request: ScopeMeasurementStatisticsRequestV2,
        profile: ScopeMeasurementStatisticsProfileV2,
        spec: OperationSpec,
    ) -> ScopeMeasurementStatisticsV2:
        state = self.session_state
        guarded_transport = (
            self.transport if isinstance(self.transport, GuardedAuditedTransport) else None
        )
        query_calls_before = (
            guarded_transport.counters.query_calls if guarded_transport is not None else None
        )
        if state is None:
            result = scope.get_measurement_statistics_v2(request)
        else:
            timeout_ms = self._operation_timeout_ms(spec)
            coordinator = SessionTransactionCoordinator(state)
            with coordinator.authorize_normal(
                operation_id=spec.operation,
                allowed_io=("query",),
                fields=("scope.measurement_statistics_v2",),
                timeout_ms=timeout_ms,
                max_steps=profile.max_queries,
                context_id="scope_measurement_statistics_v2",
                correlation_id=uuid4().hex,
                phase="main",
                absolute_deadline=time.monotonic() + (timeout_ms / 1000.0),
            ):
                result = scope.get_measurement_statistics_v2(request)
        if query_calls_before is not None and guarded_transport is not None:
            query_calls = guarded_transport.counters.query_calls - query_calls_before
            if query_calls > profile.max_queries:
                raise DataError(
                    "scope measurement statistics V2 exceeded its descriptor query budget"
                )
        try:
            profile.validate_result(result, request=request)
        except (TypeError, ValueError) as exc:
            raise DataError(
                f"scope measurement statistics V2 driver returned an invalid result: {exc}"
            ) from exc
        return result

    def math_waveform_metadata(self, math_index: int) -> ScopeDerivedWaveformMetadata:
        self._require("scope.math_metadata", "scope.math_metadata")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_math_waveform_metadata(
                math_index
            )

    def fft_status(self, math_index: int, *, configured_fft: bool) -> ScopeFftStatus:
        self._require("scope.fft_status", "scope.fft_status")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_fft_status(
                math_index,
                configured_fft=configured_fft,
            )

    def reference_waveform_metadata(
        self,
        reference_index: int,
    ) -> ScopeDerivedWaveformMetadata:
        self._require("scope.reference_metadata", "scope.reference_metadata")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_reference_waveform_metadata(
                reference_index
            )

    def cursor_readout(
        self,
        cursor_index: int,
        *,
        configured_cursor: bool,
    ) -> ScopeCursorReadout:
        self._require("scope.cursor_readout", "scope.cursor_readout")
        with self._scope_session() as scope:
            return cast(ScopeAnalysisReadDriver, scope).get_cursor_readout(
                cursor_index,
                configured_cursor=configured_cursor,
            )

    def _scope_extension_service(self, scope: object) -> ScopeExtensionService:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        if self.session_state is None:
            raise ConfigError("scope extension operations require a shared session state")
        default_error_check = ErrorCheckSpec(
            "if_supported" if self.config.scope.check_errors else "disabled"
        )
        return ScopeExtensionService(
            driver=scope,
            descriptor=descriptor,
            session_state=self.session_state,
            connection_timeout_ms=self.config.connection.timeout_ms,
            access=getattr(self.config.scope, "access", "read_write"),
            instrument_error_default=default_error_check,
        )

    def screenshot_profile(
        self,
        *,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require("scope.screenshot_profile", "scope.screenshot_profile")
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).screenshot_profile(deadline=deadline)

    def screenshot_v2(
        self,
        request: ScopeScreenshotRequest,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require("scope.screenshot_v2", "scope.screenshot_v2")
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).screenshot_v2(
                request,
                error_check=error_check,
                deadline=deadline,
            )

    def acquisition_run_state(
        self,
        *,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require("scope.acquisition_run_state", "scope.acquisition_run_state")
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).acquisition_run_state(deadline=deadline)

    def start_acquisition(
        self,
        request: ScopeContinuousAcquisitionRequest,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require(
            "scope.acquisition_start",
            "scope.acquisition_control",
            "scope.acquisition_run_state",
        )
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).start_acquisition(
                request,
                error_check=error_check,
                deadline=deadline,
            )

    def acquire_single(
        self,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require(
            "scope.acquisition_single",
            "scope.acquisition_control",
            "scope.acquisition_run_state",
        )
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).acquire_single(
                error_check=error_check,
                deadline=deadline,
            )

    def stop_acquisition(
        self,
        *,
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require(
            "scope.acquisition_stop",
            "scope.acquisition_control",
            "scope.acquisition_run_state",
        )
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).stop_acquisition(
                error_check=error_check,
                deadline=deadline,
            )

    def trace_metadata(
        self,
        source: ScopeTraceRef,
        *,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require("scope.trace_metadata", "scope.trace_metadata")
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).trace_metadata(
                source,
                deadline=deadline,
            )

    def fetch_trace(
        self,
        source: ScopeTraceRef,
        *,
        points: str | int = "dmax",
        error_check: ErrorCheckSpec | None = None,
        deadline: float | None = None,
    ) -> ScopeExtensionOperationResult:
        self._require("scope.fetch_trace", "scope.fetch_trace")
        with self._scope_session() as scope:
            return self._scope_extension_service(scope).fetch_trace(
                source,
                points=points,
                error_check=error_check,
                deadline=deadline,
            )

    def channel_coupling(self, channel: int) -> str:
        self._require("scope.channel_coupling", "scope.channel_coupling")
        with self._scope_session() as scope:
            return scope.channel_coupling(channel)

    def channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2:
        if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
            raise ConfigError("scope input-state channel must be a positive integer")
        self._require("scope.channel_input_state_v2", "scope.channel_input_state_v2")
        with self._scope_session() as scope:
            result = cast(ScopeChannelInputStateDriverV2, scope).get_channel_input_state_v2(
                channel
            )
        if not isinstance(result, ScopeChannelInputStateV2):
            raise DataError("scope input-state V2 driver returned an invalid result")
        if result.channel != channel:
            raise DataError("scope input-state V2 driver returned the wrong channel")
        return result

    def require_high_impedance(self, channel: int, *, allow_50ohm: bool = False) -> str:
        coupling = self.channel_coupling(channel)
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        return assert_scope_high_impedance(
            coupling,
            channel=channel,
            allow_50ohm=allow_50ohm,
            driver=self.config.scope.driver,
            coupling_policy=descriptor.scope_coupling_policy,
        )

    def autoscale(self) -> None:
        required = ["scope.autoscale"]
        if self.config.autoscale.check_errors:
            required.append("scope.errors")
        self._require("scope.autoscale", *required)
        with self._scope_session() as scope:
            scope.autoscale(
                wait_opc=self.config.autoscale.wait_opc,
                check_errors=self.config.autoscale.check_errors,
            )

    def fetch_waveform(self, channel: int) -> WaveformData:
        if self.config.waveform.format.lower() != "real":
            raise ConfigError("MVP-1 only supports waveform.format = 'real'")
        if self.config.waveform.byte_order.lower() != "lsbf":
            raise ConfigError("MVP-1 only supports waveform.byte_order = 'lsbf'")
        bounded_profile = self._waveform_binary_profile()
        required = ["scope.fetch_waveform"]
        if bounded_profile is not None:
            required.append("scope.idn")
            if self.config.scope.check_errors:
                required.append("scope.error_drain_v1")
        elif self.config.scope.check_errors:
            required.append("scope.errors")
        self._require("scope.fetch_waveform", *required)
        with self._scope_session() as scope:
            if bounded_profile is not None:
                result = self._bounded_waveform_executor(scope).fetch(
                    channel=channel,
                    points=self.config.waveform.points,
                    check_errors=self.config.scope.check_errors,
                )
                assert isinstance(result.value, WaveformData)
                return result.value
            self._session_preflight("scope.fetch_waveform", scope)
            return scope.fetch_waveform(
                channel=channel,
                points=self.config.waveform.points,
                check_errors=self.config.scope.check_errors,
            )

    def _write_waveform_files(self, package_dir: Path, channel: int, waveform: WaveformData) -> dict[str, str]:
        times = waveform.times_s
        files: dict[str, str] = {}
        staged: list[tuple[Path, Path, str]] = []
        promoted: list[Path] = []
        try:
            if self.config.output.save_csv:
                csv_path = package_dir / f"ch{channel}.csv"
                csv_tmp_path = package_dir / f".ch{channel}.csv.tmp"
                staged.append((csv_tmp_path, csv_path, "csv"))
                with csv_tmp_path.open("w", newline="", encoding="utf-8") as file:
                    writer = csv.writer(file)
                    writer.writerow(["index", "time_s", "voltage_v"])
                    for index, (time_s, voltage_v) in enumerate(
                        zip(times, waveform.voltages_v)
                    ):
                        writer.writerow(
                            [index, f"{time_s:.12e}", f"{float(voltage_v):.12e}"]
                        )
            if self.config.output.save_npy:
                npy_path = package_dir / f"ch{channel}.npy"
                npy_tmp_path = package_dir / f".ch{channel}.npy.tmp"
                staged.append((npy_tmp_path, npy_path, "npy"))
                with npy_tmp_path.open("wb") as file:
                    np.save(file, np.column_stack((times, waveform.voltages_v)))
            for temporary, final, kind in staged:
                os.replace(temporary, final)
                promoted.append(final)
                files[kind] = str(final)
        except Exception:
            for final in promoted:
                final.unlink(missing_ok=True)
            raise
        finally:
            for temporary, _, _ in staged:
                temporary.unlink(missing_ok=True)
        return files


    def _write_screenshot_file(self, package_dir: Path, scope: ScopeDriver) -> tuple[Path | None, dict[str, str] | None]:
        if not self.config.output.save_screenshot:
            return None, None
        screenshot_path = package_dir / "screenshot.png"
        try:
            screenshot_path.write_bytes(scope.screenshot_png(include_menu=False, color_scheme="COL"))
        except Exception as exc:
            return None, {"type": type(exc).__name__, "message": str(exc)}
        return screenshot_path, None

    def _legacy_capture_screenshot_capability(self) -> str:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        if "scope.screenshot" in descriptor.capabilities:
            return "scope.screenshot"
        if "scope.screenshot_v2" in descriptor.capabilities:
            raise ConfigError(
                "scope capture cannot embed scope.screenshot_v2 without the parent-operation "
                "field-closure runtime; use 'wavebench scope screenshot capture'"
            )
        return "scope.screenshot"

    def _waveform_binary_profile(self) -> ScopeWaveformBinaryProfile | None:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        extensions = getattr(descriptor, "scope_extensions", None)
        return getattr(extensions, "waveform_binary_profile", None)

    def _snapshot_v2_profile(self) -> ScopeSnapshotProfileV2 | None:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        extensions = getattr(descriptor, "scope_extensions", None)
        profile = getattr(extensions, "snapshot_profile_v2", None)
        if profile is not None and not isinstance(profile, ScopeSnapshotProfileV2):
            raise ConfigError("scope snapshot V2 descriptor profile has an invalid type")
        return profile

    def _acquisition_status_v2_profile(self) -> ScopeAcquisitionStatusProfileV2 | None:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        extensions = getattr(descriptor, "scope_extensions", None)
        profile = getattr(extensions, "acquisition_status_profile_v2", None)
        if profile is not None and not isinstance(profile, ScopeAcquisitionStatusProfileV2):
            raise ConfigError("scope acquisition status V2 descriptor profile has an invalid type")
        if (
            profile is not None
            and "run_state" in profile.readable_fields
            and "scope.acquisition_run_state" not in descriptor.capabilities
        ):
            raise ConfigError(
                "scope acquisition status V2 profile reads run_state but the descriptor "
                "does not declare scope.acquisition_run_state"
            )
        return profile

    def _measurement_statistics_v2_profile(
        self,
    ) -> ScopeMeasurementStatisticsProfileV2 | None:
        descriptor = self.descriptor or resolve_instrument_descriptor(
            self.config.scope.driver,
            expected_kind="scope",
        )
        extensions = getattr(descriptor, "scope_extensions", None)
        profile = getattr(extensions, "measurement_statistics_profile_v2", None)
        if profile is not None and not isinstance(profile, ScopeMeasurementStatisticsProfileV2):
            raise ConfigError("scope measurement statistics V2 descriptor profile has an invalid type")
        return profile

    def _bounded_waveform_executor(self, scope: object) -> BoundedWaveformExecutor:
        if self.descriptor is None or self.session_state is None:
            raise ConfigError(
                "bounded waveform operations require a factory-owned descriptor and session"
            )
        return BoundedWaveformExecutor(
            driver=scope,
            descriptor=self.descriptor,
            session_state=self.session_state,
            connection_timeout_ms=self.config.connection.timeout_ms,
            transport=self.transport if isinstance(self.transport, GuardedAuditedTransport) else None,
        )

    def _can_attempt_failure_screenshot(self) -> bool:
        return self.session_state is None or self.session_state.health is not SessionHealth.POISONED

    def _waveform_metadata(self, waveform: WaveformData) -> dict[str, Any]:
        return {
            "header": {
                "x_start_s": waveform.header.x_start,
                "x_stop_s": waveform.header.x_stop,
                "x_increment_s": waveform.header.x_increment,
                "points": waveform.header.points,
                "segment": waveform.header.segment,
            },
            "summary": waveform.summary(
                expected_frequency_hz=self.config.waveform.expected_frequency_hz,
                frequency_tolerance_ratio=self.config.waveform.frequency_tolerance_ratio,
                min_signal_vpp=self.config.waveform.min_signal_vpp,
            ),
        }

    def _failed_capture_package(
        self,
        *,
        package_dir: Path,
        operation: dict[str, Any],
        exc: Exception,
        commands_log_path: Path | None,
        partial: dict[str, Any] | None = None,
    ) -> None:
        failed_dir = package_dir.with_name(package_dir.name + "_failed")
        package_dir.rename(failed_dir)

        def rewrite_failed_paths(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: rewrite_failed_paths(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite_failed_paths(item) for item in value]
            if isinstance(value, str):
                prefix = str(package_dir)
                if value == prefix or value.startswith(prefix + os.sep):
                    return str(failed_dir) + value[len(prefix) :]
            return value
        (failed_dir / "error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
        partial_metadata: dict[str, Any] = {
            "instrument": {"resource": self.config.connection.resource},
            "operation": {**operation, "failed": True},
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "files": {"commands": str(failed_dir / "commands.log")} if commands_log_path is not None else {},
        }
        diagnostics = getattr(exc, "scope_operation_diagnostics", None)
        if isinstance(diagnostics, Mapping):
            partial_metadata["scope_operation_diagnostics"] = dict(diagnostics)
        if partial is not None:
            partial_metadata.update(rewrite_failed_paths(partial))
        (failed_dir / "metadata.partial.json").write_text(
            json.dumps(partial_metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def capture_waveform(self, channel: int, label: str) -> CaptureResult:
        bounded_profile = self._waveform_binary_profile()
        required = ["scope.idn", "scope.capture_waveform"]
        if bounded_profile is not None and self.config.scope.check_errors:
            required.append("scope.error_drain_v1")
        elif self.config.scope.check_errors:
            required.append("scope.errors")
        if self.config.output.save_screenshot:
            required.append(self._legacy_capture_screenshot_capability())
        self._require("scope.capture", *required)
        package_dir = new_package_dir(self.config.output.directory, label)
        package_dir.mkdir(parents=True, exist_ok=False)
        commands_log_path = package_dir / "commands.log" if self.config.output.save_commands_log else None
        if commands_log_path is not None:
            self.logger.path = commands_log_path
        operation = {
            "command": "scope capture",
            "channel": channel,
            "label": label,
            "time_range_s": self.config.waveform.time_range_s,
            "expected_frequency_hz": self.config.waveform.expected_frequency_hz,
            "target_cycles": self.config.waveform.target_cycles,
            "window_frequency_hz": self.config.waveform.window_frequency_hz,
            "frequency_tolerance_ratio": self.config.waveform.frequency_tolerance_ratio,
            "vertical_scale_v_per_div": self.config.waveform.vertical_scale_v_per_div,
            "target_vpp": self.config.waveform.target_vpp,
            "min_signal_vpp": self.config.waveform.min_signal_vpp,
        }
        screenshot_path: Path | None = None
        screenshot_error: dict[str, str] | None = None
        try:
            with self._scope_session() as scope:
                if bounded_profile is not None:
                    result = self._bounded_waveform_executor(scope).capture_single(
                        channel=channel,
                        points=self.config.waveform.points,
                        time_range_s=self.config.waveform.time_range_s,
                        vertical_scale_v_per_div=self.config.waveform.vertical_scale_v_per_div,
                        check_errors=self.config.scope.check_errors,
                    )
                    assert isinstance(result.value, WaveformData)
                    waveform = result.value
                    instrument_idn = result.identity
                else:
                    evidence = self._session_preflight("scope.capture", scope)
                    instrument_idn = evidence.get("scope.identity") or scope.idn()
                    capture_kwargs = {
                        "channel": channel,
                        "points": self.config.waveform.points,
                        "check_errors": self.config.scope.check_errors,
                        "time_range_s": self.config.waveform.time_range_s,
                    }
                    if self.config.waveform.vertical_scale_v_per_div is not None:
                        capture_kwargs["vertical_scale_v_per_div"] = self.config.waveform.vertical_scale_v_per_div
                    waveform = scope.capture_waveform(**capture_kwargs)
                screenshot_path, screenshot_error = self._write_screenshot_file(package_dir, scope)
        except Exception as exc:
            self._failed_capture_package(
                package_dir=package_dir,
                operation=operation,
                exc=exc,
                commands_log_path=commands_log_path,
            )
            if isinstance(exc, WaveBenchError):
                raise
            raise

        files = self._write_waveform_files(package_dir, channel, waveform)
        if self.config.output.save_screenshot:
            files["screenshot"] = str(screenshot_path) if screenshot_path is not None else None
        metadata: dict[str, Any] = {
            "instrument": {"idn": instrument_idn, "resource": self.config.connection.resource},
            "operation": {**operation, "triggered_single": True},
            "waveform": self._waveform_metadata(waveform),
            "files": files,
        }
        if screenshot_error is not None:
            metadata["screenshot_error"] = screenshot_error
        metadata_path = package_dir / "metadata.json"
        if self.config.output.save_json:
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return CaptureResult(
            package_dir=package_dir,
            waveform=waveform,
            metadata_path=metadata_path,
            csv_path=Path(files["csv"]) if "csv" in files else None,
            npy_path=Path(files["npy"]) if "npy" in files else None,
            screenshot_path=screenshot_path,
            commands_log_path=commands_log_path,
        )

    def capture_waveforms(self, channels: list[int], label: str) -> MultiCaptureResult:
        if not channels:
            raise ConfigError("at least one channel is required")
        if len(set(channels)) != len(channels):
            raise ConfigError("duplicate channels are not allowed")
        bounded_profile = self._waveform_binary_profile()
        required = ["scope.idn", "scope.capture_waveforms"]
        if bounded_profile is not None and self.config.scope.check_errors:
            required.append("scope.error_drain_v1")
        elif self.config.scope.check_errors:
            required.append("scope.errors")
        if self.config.output.save_screenshot:
            required.append(self._legacy_capture_screenshot_capability())
        self._require("scope.capture_multiple", *required)
        package_dir = new_package_dir(self.config.output.directory, label)
        package_dir.mkdir(parents=True, exist_ok=False)
        commands_log_path = package_dir / "commands.log" if self.config.output.save_commands_log else None
        if commands_log_path is not None:
            self.logger.path = commands_log_path
        operation = {
            "command": "scope capture",
            "channels": channels,
            "label": label,
            "time_range_s": self.config.waveform.time_range_s,
            "expected_frequency_hz": self.config.waveform.expected_frequency_hz,
            "target_cycles": self.config.waveform.target_cycles,
            "window_frequency_hz": self.config.waveform.window_frequency_hz,
            "frequency_tolerance_ratio": self.config.waveform.frequency_tolerance_ratio,
            "vertical_scale_v_per_div": self.config.waveform.vertical_scale_v_per_div,
            "target_vpp": self.config.waveform.target_vpp,
            "min_signal_vpp": self.config.waveform.min_signal_vpp,
        }
        waveforms: dict[int, WaveformData] = {}
        files: dict[str, dict[str, str]] = {}
        channel_metadata: dict[str, Any] = {}
        completed_channels: list[int] = []
        failed_channel: int | None = None
        stage = "open_session"
        screenshot_path: Path | None = None
        screenshot_error: dict[str, str] | None = None
        scope: MultiChannelScopeDriver | None = None
        try:
            with self._scope_session() as opened_scope:
                scope = opened_scope
                try:
                    stage = "identify"

                    def start_channel(channel: int | None) -> None:
                        nonlocal failed_channel, stage
                        if channel is None:
                            failed_channel = None
                            stage = "check_errors"
                        else:
                            failed_channel = channel
                            stage = "read_waveform"

                    def save_waveform(channel: int, waveform: WaveformData) -> None:
                        nonlocal failed_channel, stage
                        failed_channel = channel
                        stage = "write_waveform"
                        key = str(channel)
                        files[key] = self._write_waveform_files(package_dir, channel, waveform)
                        channel_metadata[key] = self._waveform_metadata(waveform)
                        waveforms[channel] = waveform
                        completed_channels.append(channel)
                        failed_channel = None
                        stage = "read_waveform"

                    stage = "acquire"
                    failed_channel = None
                    if bounded_profile is not None:
                        result = self._bounded_waveform_executor(scope).capture_multiple(
                            channels=channels,
                            points=self.config.waveform.points,
                            time_range_s=self.config.waveform.time_range_s,
                            vertical_scale_v_per_div=self.config.waveform.vertical_scale_v_per_div,
                            check_errors=self.config.scope.check_errors,
                            on_channel_start=start_channel,
                            on_waveform=save_waveform,
                        )
                        assert isinstance(result.value, dict)
                        returned_waveforms = result.value
                        instrument_idn = result.identity
                    else:
                        evidence = self._session_preflight("scope.capture_multiple", scope)
                        instrument_idn = evidence.get("scope.identity") or scope.idn()
                        capture_kwargs: dict[str, Any] = {
                            "channels": channels,
                            "points": self.config.waveform.points,
                            "check_errors": self.config.scope.check_errors,
                            "time_range_s": self.config.waveform.time_range_s,
                        }
                        if self.config.waveform.vertical_scale_v_per_div is not None:
                            capture_kwargs["vertical_scale_v_per_div"] = self.config.waveform.vertical_scale_v_per_div
                        capture_kwargs["on_channel_start"] = start_channel
                        capture_kwargs["on_waveform"] = save_waveform
                        returned_waveforms = scope.capture_waveforms(**capture_kwargs)
                    for channel in channels:
                        if channel not in waveforms:
                            save_waveform(channel, returned_waveforms[channel])
                    failed_channel = None
                    stage = "screenshot"
                    screenshot_path, screenshot_error = self._write_screenshot_file(
                        package_dir, scope
                    )
                except Exception:
                    if self.config.output.save_screenshot and self._can_attempt_failure_screenshot():
                        screenshot_path, screenshot_error = self._write_screenshot_file(
                            package_dir, scope
                        )
                    raise
        except Exception as exc:
            partial_files: dict[str, Any] = dict(files)
            if commands_log_path is not None:
                partial_files["commands"] = str(package_dir / "commands.log")
            if screenshot_path is not None:
                partial_files["screenshot"] = str(package_dir / "screenshot.png")
            self._failed_capture_package(
                package_dir=package_dir,
                operation=operation,
                exc=exc,
                commands_log_path=commands_log_path,
                partial={
                    "completed_channels": completed_channels,
                    "failed_channel": failed_channel,
                    "stage": stage,
                    "channels": channel_metadata,
                    "files": partial_files,
                    **(
                        {"screenshot_error": screenshot_error}
                        if screenshot_error is not None
                        else {}
                    ),
                },
            )
            if isinstance(exc, WaveBenchError):
                raise
            raise

        metadata_files: dict[str, Any] = dict(files)
        if self.config.output.save_screenshot:
            metadata_files["screenshot"] = str(screenshot_path) if screenshot_path is not None else None
        metadata: dict[str, Any] = {
            "instrument": {"idn": instrument_idn, "resource": self.config.connection.resource},
            "operation": {**operation, "triggered_single": True, "trigger_mode": "single_acquisition"},
            "channels": channel_metadata,
            "files": metadata_files,
        }
        if screenshot_error is not None:
            metadata["screenshot_error"] = screenshot_error
        metadata_path = package_dir / "metadata.json"
        if self.config.output.save_json:
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return MultiCaptureResult(
            package_dir=package_dir,
            waveforms=waveforms,
            metadata_path=metadata_path,
            files=files,
            screenshot_path=screenshot_path,
            commands_log_path=commands_log_path,
        )
