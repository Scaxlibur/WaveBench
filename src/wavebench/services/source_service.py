from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
from hashlib import sha256
from math import isfinite
import time
from typing import cast
from uuid import uuid4

from wavebench.arbitrary import build_dg4000_dac14_binary_block, load_arbitrary_waveform
from wavebench.config import SourceConfig, WaveBenchConfig
from wavebench.errors import ConfigError
from wavebench.instruments.contracts import (
    SourceAmModulationControlDriver,
    SourceAmModulationProfileDriver,
    SourceBurstControlDriver,
    SourceBurstProfileDriver,
    SourceChannelProfileDriver,
    SourceCouplingDriver,
    SourceCounterProfileDriver,
    SourceDriver,
    SourceFmModulationControlDriver,
    SourceFmModulationProfileDriver,
    SourceHarmonicControlDriver,
    SourceHarmonicProfileDriver,
    SourcePmModulationControlDriver,
    SourcePmModulationProfileDriver,
    SourcePulseControlDriver,
    SourcePulseProfileDriver,
    SourcePwmModulationControlDriver,
    SourcePwmModulationProfileDriver,
    SourceSweepControlDriver,
    SourceSweepProfileDriver,
)
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.capabilities import require_capabilities
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import (
    ArbitraryQueryProbeResult,
    SourceAmModulationConfiguration,
    SourceAmModulationProfile,
    SourceBurstConfiguration,
    SourceBurstProfile,
    SourceChannelProfile,
    SourceCouplingConfiguration,
    SourceCouplingProfile,
    SourceCounterProfile,
    SourceFmModulationConfiguration,
    SourceFmModulationProfile,
    SourceHarmonicConfiguration,
    SourceHarmonicProfile,
    SourcePmModulationConfiguration,
    SourcePmModulationProfile,
    SourcePulseConfiguration,
    SourcePulseProfile,
    SourcePwmModulationConfiguration,
    SourcePwmModulationProfile,
    SourceSweepConfiguration,
    SourceSweepProfile,
    SourceStatus,
)
from wavebench.instruments.source_extensions import (
    Availability,
    ArbitraryFacet,
    BasicWaveFacet,
    BurstFacet,
    HarmonicFacet,
    ModulationFacet,
    Observed,
    OutputFacet,
    PulseFacet,
    SweepFacet,
    PatchAction,
    PatchValue,
    SOURCE_ARBITRARY_SELECT_V2_OPERATION_CONTRACT,
    SOURCE_ARBITRARY_STORAGE_V2_OPERATION_CONTRACT,
    SOURCE_ARBITRARY_VOLATILE_REPLACE_V2_OPERATION_CONTRACT,
    SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_BASIC_LIVE_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_BURST_FIRE_V2_OPERATION_CONTRACT,
    SOURCE_COUNTER_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_COUNTER_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_COUNTER_ENABLE_V2_OPERATION_CONTRACT,
    SOURCE_COMBINE_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_COUPLING_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_CONTRACT_VERSION,
    SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_HARMONICS_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT,
    SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_PWM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_PHASE_RELATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_SWEEP_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_SWEEP_FIRE_V2_OPERATION_CONTRACT,
    SOURCE_TRACKING_CONFIGURE_V2_OPERATION_CONTRACT,
    SnapshotConsistencyState,
    SourceDescriptorExtensions,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceArbitraryCapabilityProfile,
    SourceArbitraryPlaybackMode,
    SourceArbitrarySelectRequest,
    SourceArbitrarySelectResult,
    SourceArbitrarySelectV2Driver,
    SourceArbitraryStorageRequest,
    SourceArbitraryStorageResult,
    SourceArbitraryStorageSlot,
    SourceArbitraryStorageV2Driver,
    SourceArbitraryVolatileReplaceRequest,
    SourceArbitraryVolatileReplaceResult,
    SourceArbitraryVolatileReplaceV2Driver,
    SourceBasicCapabilityProfile,
    SourceBasicConfigureRequest,
    SourceBasicConfigureResult,
    SourceBasicConfigureV2Driver,
    SourceBasicLiveConfigureResult,
    SourceBasicLiveConfigureV2Driver,
    SourceBasicPatch,
    SourceBurstCapabilityProfile,
    SourceBurstConfigureRequest,
    SourceBurstConfigureResult,
    SourceBurstConfigureV2Driver,
    SourceBurstFireV2Driver,
    SourceBurstMode,
    SourceCombineConfigureRequest,
    SourceCombineConfigureV2Driver,
    SourceCouplingCapabilityProfile,
    SourceCouplingConfigureRequest,
    SourceCouplingConfigureV2Driver,
    SourceCouplingState,
    SourceCounterCapabilityProfile,
    SourceCounterConfigurationField,
    SourceCounterConfigureRequest,
    SourceCounterConfigureResult,
    SourceCounterConfigureV2Driver,
    SourceCounterEnableRequest,
    SourceCounterEnableResult,
    SourceCounterEnableV2Driver,
    SourceCounterInputState,
    SourceCounterMeasureRequest,
    SourceCounterMeasureResult,
    SourceCounterMeasureV2Driver,
    SourceCrossChannelCapabilityProfile,
    SourceCrossChannelConfigureResult,
    SourceFacetScope,
    SourceFieldId,
    SourceFieldRef,
    SourceFeature,
    SourceFeatureDirection,
    SourceFrequencyMode,
    SourceFmModulationConfigureRequest,
    SourceFmModulationConfigureResult,
    SourceFmModulationConfigureV2Driver,
    SourceFireRequest,
    SourceFireResult,
    SourceHarmonicCapabilityProfile,
    SourceHarmonicDisableRequest,
    SourceHarmonicDisableResult,
    SourceHarmonicDisableV2Driver,
    SourceHarmonicConfigureRequest,
    SourceHarmonicConfigureResult,
    SourceHarmonicConfigureV2Driver,
    SourceModulationCapabilityProfile,
    SourceModulationConfigureRequest,
    SourceModulationConfigureResult,
    SourceModulationConfigureV2Driver,
    SourceModulationKind,
    SourceModulationParameter,
    SourceModulationParameterKind,
    SourceModulationSource,
    SourceOperationContract,
    SourceOutputRequest,
    SourceOutputResult,
    SourceOutputV2Driver,
    SourcePulseCapabilityProfile,
    SourcePulseConfigureRequest,
    SourcePulseConfigureResult,
    SourcePulseConfigureV2Driver,
    SourcePulseHoldBasis,
    SourcePhaseRelationConfigureRequest,
    SourcePhaseRelationConfigureV2Driver,
    SourcePmModulationConfigureRequest,
    SourcePmModulationConfigureResult,
    SourcePmModulationConfigureV2Driver,
    SourcePwmModulationConfigureRequest,
    SourcePwmModulationConfigureResult,
    SourcePwmModulationConfigureV2Driver,
    SourceScopeRef,
    SourceStorageWriteMode,
    SourceSweepCapabilityProfile,
    SourceSweepConfigureRequest,
    SourceSweepConfigureResult,
    SourceSweepConfigureV2Driver,
    SourceSweepFireV2Driver,
    SourceSweepMarker,
    SourceSnapshotV2,
    SourceSnapshotV2Driver,
    SourceSystemStateV2,
    SourceTriggerOutput,
    SourceTriggerSlope,
    SourceTriggerSource,
    SourceTriggerState,
    SourceRelationGraph,
    SourceRelationOutputState,
    SourceRelationState,
    SourceTrackingConfigureRequest,
    SourceTrackingConfigureV2Driver,
    SourceV1WriteRouteId,
    SourceWaveformKind,
    SourceQueryEffect,
    SupportState,
    source_v2_digest,
    source_v2_to_data,
)
from wavebench.logging import CommandLogger
from wavebench.instruments.registry import resolve_instrument_descriptor
from wavebench.services.source_state import RestorableSourceState
from wavebench.services.access_policy import access_policy
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.resource_lease import ResourceLease
from wavebench.services.session_alias import SessionStateAliasMixin
from wavebench.services.state_guard import SourceStateGuard
from wavebench.transport.base import InstrumentTransport
from wavebench.transport.session import (
    InstrumentSessionState,
    SessionHealth,
    SessionTransactionCoordinator,
)
from wavebench.services.source_snapshot_v2 import (
    SOURCE_SNAPSHOT_OPERATION_TIMEOUT_MS,
    SourceSnapshotContractError,
    build_source_snapshot,
    build_source_snapshot_plan,
    new_source_snapshot_context,
)
from wavebench.services.source_operation_context import (
    SourceOperationContextCoordinator,
    SourceOperationPhase,
)


@dataclass(frozen=True, slots=True)
class _SourceBasicConfigureV2Transaction:
    """Core transaction result shared by public and V1-adapter routes."""

    result: SourceBasicConfigureResult | SourceBasicLiveConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


class _SourceV2BasicLegacyFallback(ConfigError):
    """A V1 setter has no lossless representation in the active V2 basic profile."""


class _SourceV2BasicRequiresLiveMutation(ConfigError):
    """An OFF-only basic transaction found the target output enabled."""


class _SourceV2BasicRequiresOffMutation(ConfigError):
    """A live basic transaction found the target output disabled."""


@dataclass(frozen=True, slots=True)
class _SourceHarmonicConfigureV2Transaction:
    """Core transaction result shared by the Harmonic public route."""

    result: SourceHarmonicConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceHarmonicDisableV2Transaction:
    """Core transaction result shared by the Harmonic-disable public route."""

    result: SourceHarmonicDisableResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceModulationConfigureV2Transaction:
    """Core transaction result shared by the internal AM public route."""

    result: SourceModulationConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourcePmModulationConfigureV2Transaction:
    """Core transaction result shared by the internal PM public route."""

    result: SourcePmModulationConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceFmModulationConfigureV2Transaction:
    """Core transaction result shared by the internal FM public route."""

    result: SourceFmModulationConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourcePwmModulationConfigureV2Transaction:
    """Core transaction result shared by the internal PWM public route."""

    result: SourcePwmModulationConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceSweepConfigureV2Transaction:
    """Core transaction result shared by the internal Sweep public route."""

    result: SourceSweepConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceBurstConfigureV2Transaction:
    """Core transaction result shared by the internal Triggered Burst route."""

    result: SourceBurstConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceFireV2Transaction:
    """Core transaction result shared by Burst and Sweep fire routes."""

    result: SourceFireResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourcePulseConfigureV2Transaction:
    """Core transaction result shared by the WIDTH Pulse public route."""

    result: SourcePulseConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceOutputV2Transaction:
    """Core transaction result shared by public and V1-adapter routes."""

    result: SourceOutputResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceArbitraryStorageV2Transaction:
    """Core transaction result for one named ARB storage mutation."""

    result: SourceArbitraryStorageResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceArbitrarySelectV2Transaction:
    """Core transaction result for one OFF-only ARB selection."""

    result: SourceArbitrarySelectResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceArbitraryVolatileReplaceV2Transaction:
    """Core transaction result for one unnamed, volatile ARB workspace replacement."""

    result: SourceArbitraryVolatileReplaceResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceCounterV2Transaction:
    """Core transaction result for one independently verified Counter mutation."""

    result: SourceCounterConfigureResult | SourceCounterEnableResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceCrossChannelConfigureV2Transaction:
    """Core transaction result shared by the four M6-C relation routes."""

    result: SourceCrossChannelConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceCrossChannelClosure:
    """The graph-derived, precomputed OFF and readback range for one relation write."""

    feature: SourceFeature
    relation_field: SourceFieldId
    relation: SourceRelationState | SourceCouplingState
    relation_graph: SourceRelationGraph
    affected_channels: tuple[int, ...]
    fields: tuple[SourceFieldRef, ...]
    output_fields: tuple[SourceFieldRef, ...]


@dataclass
class SourceService(SessionStateAliasMixin):
    config: WaveBenchConfig
    logger: CommandLogger
    session: SourceDriver | None = None
    descriptor: InstrumentDescriptor | None = None
    transport: InstrumentTransport | None = None
    session_state: InstrumentSessionState | None = None
    lease: ResourceLease | None = None
    state_guard: SourceStateGuard | None = None
    _v2_fire_receipts: dict[tuple[SourceFeature, int, str], str] = dataclass_field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def _require(self, operation: str, *capabilities: str) -> None:
        source = self._source_config()
        access_policy(getattr(source, "access", "read_write"), "source.access").require(
            require_operation_spec(operation),
            operation=operation,
        )
        descriptor = self.descriptor or resolve_instrument_descriptor(
            source.driver,
            expected_kind="source",
        )
        self.descriptor = descriptor
        require_capabilities(descriptor, capabilities, operation=operation)

    def _declared_source_capabilities(self) -> tuple[str, ...]:
        """Best-effort descriptor lookup for an optional V1-to-V2 route decision.

        The legacy method still owns validation when no descriptor is available.
        This keeps V1 tests, mock sessions, and their original configuration
        errors on the V1 path instead of making a route probe authoritative.
        """

        if self.descriptor is not None:
            return self.descriptor.capabilities
        try:
            source = self._source_config()
            driver_reference = getattr(source, "driver", None)
            if not isinstance(driver_reference, str) or not driver_reference.strip():
                return ()
            descriptor = resolve_instrument_descriptor(
                driver_reference,
                expected_kind="source",
            )
        except (AttributeError, ConfigError):
            return ()
        self.descriptor = descriptor
        return descriptor.capabilities

    def _declares_source_v2_capability(self, capability: str) -> bool:
        return capability in self._declared_source_capabilities()

    def _declares_source_v2_basic_restore(self) -> bool:
        capabilities = set(self._declared_source_capabilities())
        return {
            "source.snapshot_v2",
            "source.basic_configure_v2",
            "source.output_v2",
        }.issubset(capabilities)

    def _clear_source_v2_fire_receipt(
        self,
        feature: SourceFeature,
        channel: int,
    ) -> None:
        for receipt in tuple(self._v2_fire_receipts):
            if receipt[:2] == (feature, channel):
                del self._v2_fire_receipts[receipt]

    def _record_source_v2_fire_receipt(
        self,
        feature: SourceFeature,
        channel: int,
        feature_state: BurstFacet | SweepFacet,
    ) -> None:
        session_state = self.session_state
        if self.session is None or session_state is None:
            return
        self._v2_fire_receipts[(feature, channel, session_state.epoch_id)] = (
            source_v2_digest(feature_state)
        )

    def _require_source_v2_fire_receipt(
        self,
        feature: SourceFeature,
        channel: int,
        *,
        operation: str,
    ) -> str:
        if self.session is None:
            raise ConfigError(f"{operation} requires a persistent source session")
        session_state = self.session_state
        if session_state is None:
            raise ConfigError(f"{operation} requires a connection-bound session state")
        receipt = self._v2_fire_receipts.get(
            (feature, channel, session_state.epoch_id)
        )
        if receipt is None:
            raise ConfigError(
                f"{operation} requires {feature.value} configuration from the same session"
            )
        return receipt

    def _reject_v1_route_for_source_v2(
        self,
        route: SourceV1WriteRouteId,
        *overlapping_capabilities: str,
    ) -> None:
        declared = set(self._declared_source_capabilities())
        overlaps = tuple(
            capability for capability in overlapping_capabilities if capability in declared
        )
        if overlaps:
            joined = ", ".join(overlaps)
            raise ConfigError(
                f"{route.value} cannot run for a Source V2 write driver "
                f"({joined}); use the dedicated Source V2 operation"
            )

    def _source_config(self) -> SourceConfig:
        if self.config.source is None or not self.config.source.resource:
            raise ConfigError("source resource is not configured. Set [source].resource or pass --resource.")
        return self.config.source

    def _open_source(self) -> SourceDriver:
        source = self._source_config()
        self._prepare_session_open("source")
        if self.lease is None:
            self.lease = ResourceLease(
                resource=source.resource or "",
                operation="source.session",
            )
        opened = open_instrument_driver(
            driver_reference=source.driver,
            expected_kind="source",
            resource=source.resource or "",
            configured_backend=self.config.connection.backend,
            timeout_ms=self.config.connection.timeout_ms,
            opc_timeout_ms=self.config.connection.opc_timeout_ms,
            read_retry_attempts=self.config.connection.read_retry_attempts,
            read_retry_delay_ms=self.config.connection.read_retry_delay_ms,
            logger=self.logger,
            settings={"check_errors": source.check_errors},
            options=getattr(source, "options", {}),
            access=getattr(source, "access", "read_write"),
            lease=self.lease,
        )
        self.descriptor = opened.descriptor
        self.transport = opened.transport
        self.session_state = getattr(opened, "session_state", None)
        return opened.driver

    def audit_snapshot(self) -> dict[str, object] | None:
        snapshot = getattr(self.transport, "audit_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def state_guard_snapshot(self) -> dict[str, dict[str, object]] | None:
        return self.state_guard.snapshot() if self.state_guard is not None else None

    def _state_guard_before_write(
        self,
        source: SourceDriver,
        channel: int,
        *,
        force_off: bool = False,
    ) -> None:
        if self.state_guard is None:
            return
        self.state_guard.before_write(source.get_status(channel), force_off=force_off)

    def _state_guard_after_write(self, status: SourceStatus) -> None:
        if self.state_guard is not None:
            self.state_guard.after_write(status)

    def open_session(self) -> SourceDriver:
        return self._open_source()

    @contextmanager
    def _source_session(self) -> Iterator[SourceDriver]:
        if self.session is not None:
            yield self.session
            return
        source = self._open_source()
        try:
            yield source
        finally:
            source.close()

    def idn(self) -> str:
        self._require("source.idn", "source.idn")
        with self._source_session() as source:
            return source.idn()

    def errors(self) -> list[str]:
        self._require("source.errors", "source.errors")
        with self._source_session() as source:
            return source.errors()

    def status(self, channel: int | None = None) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.status", "source.status")
        with self._source_session() as source:
            status = source.get_status(channel)
            if self.state_guard is not None:
                self.state_guard.observe(status)
            return status

    def snapshot_v2(self, *, correlation_id: str | None = None) -> SourceSnapshotV2:
        self._require("source.snapshot_v2", "source.snapshot_v2")
        with self._source_session() as source:
            session_state = self.session_state
            if session_state is None:
                raise SourceSnapshotContractError(
                    "source.snapshot_v2 requires a connection-bound session state"
                )
            with session_state.transaction_lock:
                return self._snapshot_v2_with_open_source(
                    source,
                    correlation_id=correlation_id,
                )

    def configure_basic_v2(
        self,
        request: SourceBasicConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceBasicConfigureResult, dict[str, object]]:
        """Configure one OFF source channel and return its typed result and artifact."""

        transaction = self._configure_basic_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        assert isinstance(transaction.result, SourceBasicConfigureResult)
        return transaction.result, transaction.artifact

    def configure_basic_live_v2(
        self,
        request: SourceBasicConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceBasicLiveConfigureResult, dict[str, object]]:
        """Change one declared frequency or Vpp field while output remains enabled."""

        transaction = self._configure_basic_live_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        assert isinstance(transaction.result, SourceBasicLiveConfigureResult)
        return transaction.result, transaction.artifact

    def configure_harmonics_v2(
        self,
        request: SourceHarmonicConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceHarmonicConfigureResult, dict[str, object]]:
        """Configure one OFF source channel's declared Harmonic preset."""

        transaction = self._configure_harmonics_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def disable_harmonics_v2(
        self,
        request: SourceHarmonicDisableRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceHarmonicDisableResult, dict[str, object]]:
        """Disable one declared Harmonic state while its output is OFF."""

        transaction = self._disable_harmonics_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_modulation_v2(
        self,
        request: SourceModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceModulationConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the declared internal AM scope."""

        transaction = self._configure_modulation_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_pm_modulation_v2(
        self,
        request: SourcePmModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourcePmModulationConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the declared internal PM scope."""

        transaction = self._configure_pm_modulation_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_fm_modulation_v2(
        self,
        request: SourceFmModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceFmModulationConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the declared internal FM scope."""

        transaction = self._configure_fm_modulation_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_pwm_modulation_v2(
        self,
        request: SourcePwmModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourcePwmModulationConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the declared internal PWM scope."""

        transaction = self._configure_pwm_modulation_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_sweep_v2(
        self,
        request: SourceSweepConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceSweepConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the declared internal Sweep scope."""

        if isinstance(request, SourceSweepConfigureRequest):
            self._clear_source_v2_fire_receipt(SourceFeature.SWEEP, request.channel)
        transaction = self._configure_sweep_v2_transaction(request, correlation_id=correlation_id)
        _, configured_sweep, _ = self._source_v2_sweep_target(
            transaction.snapshot,
            transaction.result.channel,
            operation="source.sweep_configure_v2",
        )
        self._record_source_v2_fire_receipt(
            SourceFeature.SWEEP,
            transaction.result.channel,
            configured_sweep,
        )
        return transaction.result, transaction.artifact

    def fire_sweep_v2(
        self,
        request: SourceFireRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceFireResult, dict[str, object]]:
        """Fire one configured internal Sweep on its persistent source session."""

        transaction = self._fire_source_v2_transaction(
            request,
            feature=SourceFeature.SWEEP,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_burst_v2(
        self,
        request: SourceBurstConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceBurstConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the internal Triggered Burst scope."""

        if isinstance(request, SourceBurstConfigureRequest):
            self._clear_source_v2_fire_receipt(SourceFeature.BURST, request.channel)
        transaction = self._configure_burst_v2_transaction(request, correlation_id=correlation_id)
        configured_burst, _ = self._source_v2_burst_target(
            transaction.snapshot,
            transaction.result.channel,
            operation="source.burst_configure_v2",
        )
        self._record_source_v2_fire_receipt(
            SourceFeature.BURST,
            transaction.result.channel,
            configured_burst,
        )
        return transaction.result, transaction.artifact

    def fire_burst_v2(
        self,
        request: SourceFireRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceFireResult, dict[str, object]]:
        """Fire one configured internal Burst on its persistent source session."""

        transaction = self._fire_source_v2_transaction(
            request,
            feature=SourceFeature.BURST,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_pulse_v2(
        self,
        request: SourcePulseConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourcePulseConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the declared WIDTH Pulse scope."""

        transaction = self._configure_pulse_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def set_output_v2(
        self,
        request: SourceOutputRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceOutputResult, dict[str, object]]:
        """Apply one Source V2 output transition and return its typed result and artifact."""

        transaction = self._set_output_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def mutate_arbitrary_storage_v2(
        self,
        request: SourceArbitraryStorageRequest,
        *,
        payload: bytes,
        correlation_id: str | None = None,
    ) -> tuple[SourceArbitraryStorageResult, dict[str, object]]:
        """Write one explicitly named ARB slot without selecting or enabling it."""

        transaction = self._mutate_arbitrary_storage_v2_transaction(
            request,
            payload=payload,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def select_arbitrary_v2(
        self,
        request: SourceArbitrarySelectRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceArbitrarySelectResult, dict[str, object]]:
        """Select one stored ARB waveform while its target output is OFF."""

        transaction = self._select_arbitrary_v2_transaction(
            request,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def replace_arbitrary_volatile_v2(
        self,
        request: SourceArbitraryVolatileReplaceRequest,
        *,
        payload: bytes,
        correlation_id: str | None = None,
    ) -> tuple[SourceArbitraryVolatileReplaceResult, dict[str, object]]:
        """Replace one unnamed volatile ARB workspace while its target output is OFF."""

        transaction = self._replace_arbitrary_volatile_v2_transaction(
            request,
            payload=payload,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_counter_v2(
        self,
        request: SourceCounterConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceCounterConfigureResult, dict[str, object]]:
        """Apply one independently readable Counter input setting."""

        transaction = self._mutate_counter_v2_transaction(
            request,
            operation="source.counter_configure_v2",
            operation_contract=SOURCE_COUNTER_CONFIGURE_V2_OPERATION_CONTRACT,
            correlation_id=correlation_id,
        )
        assert isinstance(transaction.result, SourceCounterConfigureResult)
        return transaction.result, transaction.artifact

    def set_counter_enabled_v2(
        self,
        request: SourceCounterEnableRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceCounterEnableResult, dict[str, object]]:
        """Enable or disable Counter without changing its input configuration."""

        if not isinstance(request, SourceCounterEnableRequest):
            raise ConfigError("source.counter_enable_v2 requires SourceCounterEnableRequest")
        operation = "source.counter_enable_v2" if request.enabled else "source.counter_disable_v2"
        transaction = self._mutate_counter_v2_transaction(
            request,
            operation=operation,
            operation_contract=(
                SOURCE_COUNTER_ENABLE_V2_OPERATION_CONTRACT
                if request.enabled
                else SOURCE_COUNTER_DISABLE_V2_OPERATION_CONTRACT
            ),
            correlation_id=correlation_id,
        )
        assert isinstance(transaction.result, SourceCounterEnableResult)
        return transaction.result, transaction.artifact

    def measure_counter_v2(
        self,
        request: SourceCounterMeasureRequest,
        *,
        correlation_id: str | None = None,
    ) -> SourceCounterMeasureResult:
        """Read one already-enabled Counter input without changing any Counter setting."""

        return self._measure_counter_v2(request, correlation_id=correlation_id)

    def configure_combine_v2(
        self,
        request: SourceCombineConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceCrossChannelConfigureResult, dict[str, object]]:
        """Enable or disable one declared Combine relation while affected outputs are OFF."""

        transaction = self._configure_cross_channel_v2_transaction(
            request,
            operation="source.combine_configure_v2",
            feature=SourceFeature.COMBINE,
            relation_field=SourceFieldId.COMBINE,
            operation_contract=SOURCE_COMBINE_CONFIGURE_V2_OPERATION_CONTRACT,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_coupling_v2(
        self,
        request: SourceCouplingConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceCrossChannelConfigureResult, dict[str, object]]:
        """Enable or disable one declared Coupling relation while affected outputs are OFF."""

        transaction = self._configure_cross_channel_v2_transaction(
            request,
            operation="source.coupling_configure_v2",
            feature=SourceFeature.COUPLING,
            relation_field=SourceFieldId.COUPLING,
            operation_contract=SOURCE_COUPLING_CONFIGURE_V2_OPERATION_CONTRACT,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_tracking_v2(
        self,
        request: SourceTrackingConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceCrossChannelConfigureResult, dict[str, object]]:
        """Enable or disable one declared Tracking relation while affected outputs are OFF."""

        transaction = self._configure_cross_channel_v2_transaction(
            request,
            operation="source.tracking_configure_v2",
            feature=SourceFeature.TRACKING,
            relation_field=SourceFieldId.TRACKING,
            operation_contract=SOURCE_TRACKING_CONFIGURE_V2_OPERATION_CONTRACT,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def configure_phase_relation_v2(
        self,
        request: SourcePhaseRelationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceCrossChannelConfigureResult, dict[str, object]]:
        """Enable or disable one declared phase relation while affected outputs are OFF."""

        transaction = self._configure_cross_channel_v2_transaction(
            request,
            operation="source.phase_relation_configure_v2",
            feature=SourceFeature.PHASE_RELATION,
            relation_field=SourceFieldId.PHASE_RELATION,
            operation_contract=SOURCE_PHASE_RELATION_CONFIGURE_V2_OPERATION_CONTRACT,
            correlation_id=correlation_id,
        )
        return transaction.result, transaction.artifact

    def _snapshot_v2_with_open_source(
        self,
        source: SourceDriver,
        *,
        correlation_id: str | None,
        allow_uncertain_session: bool = False,
        deadline: float | None = None,
    ) -> SourceSnapshotV2:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.source_extensions
        if not isinstance(extensions, SourceDescriptorExtensions):
            raise SourceSnapshotContractError(
                "source.snapshot_v2 requires validated source_extensions"
            )
        session_state = self.session_state
        if session_state is None:
            raise SourceSnapshotContractError(
                "source.snapshot_v2 requires a connection-bound session state"
            )
        accepted_health = {SessionHealth.HEALTHY}
        if allow_uncertain_session:
            accepted_health.add(SessionHealth.UNCERTAIN)
        if session_state.health not in accepted_health:
            raise SourceSnapshotContractError(
                "source.snapshot_v2 requires a healthy session"
            )
        timeout_ms = min(
            SOURCE_SNAPSHOT_OPERATION_TIMEOUT_MS,
            extensions.query_contract.timeout_ms,
            self.config.connection.timeout_ms,
        )
        if deadline is not None:
            remaining_ms = int((deadline - time.monotonic()) * 1000.0)
            if remaining_ms < 1:
                raise SourceSnapshotContractError("source snapshot query deadline was exceeded")
            timeout_ms = min(timeout_ms, remaining_ms)
        context = new_source_snapshot_context(
            session_epoch=session_state.epoch_id,
            session_health_before=session_state.health.value,
            descriptor_extensions=extensions,
            timeout_ms=timeout_ms,
            correlation_id=correlation_id,
        )
        plan = build_source_snapshot_plan(context)
        execution = cast(
            SourceSnapshotV2Driver,
            source,
        ).execute_source_query_plan_v2(plan)
        return build_source_snapshot(
            context=context,
            plan=plan,
            execution=execution,
            session_health_after=session_state.health.value,
            allow_uncertain_session=allow_uncertain_session,
        )

    def _configure_basic_v2_transaction(
        self,
        request: SourceBasicConfigureRequest,
        *,
        correlation_id: str | None = None,
        _live: bool = False,
    ) -> _SourceBasicConfigureV2Transaction:
        """Execute the shared OFF-only or restricted live basic transaction."""

        operation = (
            "source.basic_live_configure_v2"
            if _live
            else "source.basic_configure_v2"
        )
        contract = (
            SOURCE_BASIC_LIVE_CONFIGURE_V2_OPERATION_CONTRACT
            if _live
            else SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT
        )
        if not isinstance(request, SourceBasicConfigureRequest):
            raise ConfigError(f"{operation} requires SourceBasicConfigureRequest")
        capabilities = ["source.snapshot_v2", contract.capability]
        if _live:
            capabilities.extend(("source.basic_configure_v2", "source.output_v2"))
        self._require(operation, *capabilities)
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_basic_v2_fields(request.channel)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=contract,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(
                    ()
                    if _live
                    else (
                        SourceScopeRef(
                            SourceFacetScope.CHANNEL,
                            channel=request.channel,
                        ),
                    )
                ),
                emergency_off_outputs=(
                    SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel),
                ),
                restore_order=(),
                non_restorable_fields=(
                    next(field for field in fields if field.field is SourceFieldId.BASIC),
                    output_field,
                ),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceBasicConfigureResult | SourceBasicLiveConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_basic, preflight_output = self._source_v2_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    if _live:
                        self._validate_source_basic_live_v2_preflight(
                            request,
                            preflight_snapshot,
                            preflight_basic,
                            preflight_output,
                        )
                    else:
                        self._validate_source_basic_v2_preflight(
                            request,
                            preflight_snapshot,
                            preflight_basic,
                            preflight_output,
                        )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest((request.channel, preflight_basic, preflight_output))
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(
                        next(field for field in fields if field.field is SourceFieldId.BASIC),
                    ),
                    max_steps=contract.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        if _live:
                            result = cast(
                                SourceBasicLiveConfigureV2Driver,
                                source,
                            ).configure_source_basic_live_v2(request)
                            self._validate_source_basic_live_v2_result(request, result)
                        else:
                            result = cast(
                                SourceBasicConfigureV2Driver,
                                source,
                            ).configure_source_basic_v2(request)
                            self._validate_source_basic_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(
                                next(
                                    field
                                    for field in fields
                                    if field.field is SourceFieldId.BASIC
                                ),
                                output_field,
                            ),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_basic, postcondition_output = (
                                self._source_v2_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            if _live:
                                assert isinstance(result, SourceBasicLiveConfigureResult)
                                self._validate_source_basic_live_v2_postcondition(
                                    request,
                                    result,
                                    postcondition_snapshot,
                                    postcondition_basic,
                                    postcondition_output,
                                )
                            else:
                                assert isinstance(result, SourceBasicConfigureResult)
                                self._validate_source_basic_v2_postcondition(
                                    request,
                                    result,
                                    postcondition_snapshot,
                                    postcondition_basic,
                                    postcondition_output,
                                )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(
                                    next(
                                        field
                                        for field in fields
                                        if field.field is SourceFieldId.BASIC
                                    ),
                                    output_field,
                                ),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_basic_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                            capability=contract.capability,
                            output_expected=("on" if _live else "off"),
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceBasicConfigureV2Transaction(
                    result=result,
                    artifact=self._source_basic_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                        capability=contract.capability,
                        output_expected=("on" if _live else "off"),
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_basic_live_v2_transaction(
        self,
        request: SourceBasicConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceBasicConfigureV2Transaction:
        return self._configure_basic_v2_transaction(
            request,
            correlation_id=correlation_id,
            _live=True,
        )

    def _set_output_v2_transaction(
        self,
        request: SourceOutputRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceOutputV2Transaction:
        """Execute the private M5-C single-port output transaction."""

        if not isinstance(request, SourceOutputRequest):
            raise ConfigError("source.output_v2 requires SourceOutputRequest")
        operation = (
            "source.output_enable_v2" if request.enabled else "source.output_disable_v2"
        )
        contract = (
            SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT
            if request.enabled
            else SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT
        )
        self._require(operation, "source.snapshot_v2", "source.output_v2")
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_output_v2_fields(
                request.channel,
                include_basic=request.enabled,
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            basic_field = next(
                (field for field in fields if field.field is SourceFieldId.BASIC),
                None,
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=contract,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=((target_scope,) if request.enabled else ()),
                emergency_off_outputs=((target_scope,) if request.enabled else ()),
                restore_order=(),
                non_restorable_fields=tuple(
                    field for field in fields if field.field is not SourceFieldId.IDENTITY
                ),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceOutputResult | None = None
            main_entered = False
            wrote_main = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    if request.enabled:
                        basic, output = self._source_v2_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                        wrote_main = self._validate_source_output_enable_preflight(
                            request,
                            preflight_snapshot,
                            basic,
                            output,
                        )
                        baseline_payload = (request.channel, basic, output)
                    else:
                        output = self._source_v2_output_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                        wrote_main = self._validate_source_output_disable_preflight(
                            request,
                            preflight_snapshot,
                            output,
                        )
                        baseline_payload = (request.channel, output)
                    context.bind_baseline_snapshot_digest(source_v2_digest(baseline_payload))
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                if wrote_main:
                    main = context.make_phase_spec(
                        SourceOperationPhase.MAIN,
                        allowed_io={"write"},
                        fields=(output_field,),
                        max_steps=contract.main_max_steps,
                    )
                    try:
                        with context.authorize_phase(main):
                            main_entered = True
                            result = cast(SourceOutputV2Driver, source).set_source_output_v2(
                                request
                            )
                            self._validate_source_output_v2_result(
                                request,
                                result,
                                operation=operation,
                            )
                    except BaseException as exc:
                        failure = exc
                else:
                    result = self._source_output_v2_noop_result(
                        request,
                        preflight_snapshot,
                        operation=operation,
                    )

                if failure is None and wrote_main:
                    try:
                        postcondition_fields = (
                            (basic_field, output_field)
                            if request.enabled
                            else (output_field,)
                        )
                        assert all(field is not None for field in postcondition_fields)
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=postcondition_fields,
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            assert result is not None
                            if request.enabled:
                                basic, output = self._source_v2_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                                self._validate_source_output_enable_postcondition(
                                    result,
                                    postcondition_snapshot,
                                    basic,
                                    output,
                                )
                            else:
                                output = self._source_v2_output_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                                self._validate_source_output_disable_postcondition(output)
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=postcondition_fields,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = (
                                self._recover_source_v2_output_off(
                                    context,
                                    source,
                                    request.channel,
                                    extensions,
                                    output_field,
                                    operation=operation,
                                )
                                if request.enabled
                                else {
                                    "status": "not_attempted",
                                    "reason": "off_result_unknown_not_retried",
                                }
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_output_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            wrote_main=wrote_main,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                return _SourceOutputV2Transaction(
                    result=result,
                    artifact=self._source_output_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                        wrote_main=wrote_main,
                    ),
                    snapshot=(
                        postcondition_snapshot
                        if postcondition_snapshot is not None
                        else preflight_snapshot
                    ),
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_harmonics_v2_transaction(
        self,
        request: SourceHarmonicConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceHarmonicConfigureV2Transaction:
        """Execute one declared Harmonic preset transaction while output is OFF."""

        operation = "source.harmonics_configure_v2"
        if not isinstance(request, SourceHarmonicConfigureRequest):
            raise ConfigError(f"{operation} requires SourceHarmonicConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.harmonics_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_harmonics_v2_fields(request.channel)
            harmonic_field = next(
                field for field in fields if field.field is SourceFieldId.HARMONICS
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(harmonic_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceHarmonicConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_harmonics, preflight_output = self._source_v2_harmonic_preflight_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    self._validate_source_harmonic_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_harmonics,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest((request.channel, preflight_harmonics, preflight_output))
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(harmonic_field,),
                    max_steps=SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(SourceHarmonicConfigureV2Driver, source).configure_source_harmonics_v2(
                            request
                        )
                        self._validate_source_harmonic_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(harmonic_field, output_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_harmonics, postcondition_output = (
                                self._source_v2_harmonic_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_harmonic_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_harmonics,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(harmonic_field, output_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_harmonic_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceHarmonicConfigureV2Transaction(
                    result=result,
                    artifact=self._source_harmonic_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _disable_harmonics_v2_transaction(
        self,
        request: SourceHarmonicDisableRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceHarmonicDisableV2Transaction:
        """Disable one declared Harmonic state while output is OFF."""

        operation = "source.harmonics_disable_v2"
        if not isinstance(request, SourceHarmonicDisableRequest):
            raise ConfigError(f"{operation} requires SourceHarmonicDisableRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.harmonics_disable_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_harmonics_v2_fields(request.channel)
            harmonic_field = next(
                field for field in fields if field.field is SourceFieldId.HARMONICS
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_HARMONICS_DISABLE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(harmonic_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceHarmonicDisableResult | None = None
            main_entered = False
            wrote_main = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_harmonics, preflight_output = self._source_v2_harmonic_preflight_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    harmonics = self._validate_source_harmonic_disable_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_harmonics,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest((request.channel, harmonics, preflight_output))
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                if harmonics.enabled.value is False:
                    result = SourceHarmonicDisableResult(
                        channel=request.channel,
                        harmonics=harmonics,
                        output_enabled=False,
                    )
                else:
                    main = context.make_phase_spec(
                        SourceOperationPhase.MAIN,
                        allowed_io={"write"},
                        fields=(harmonic_field,),
                        max_steps=SOURCE_HARMONICS_DISABLE_V2_OPERATION_CONTRACT.main_max_steps,
                    )
                    try:
                        with context.authorize_phase(main):
                            main_entered = True
                            wrote_main = True
                            result = cast(
                                SourceHarmonicDisableV2Driver,
                                source,
                            ).disable_source_harmonics_v2(request)
                            self._validate_source_harmonic_disable_v2_result(request, result)
                    except BaseException as exc:
                        failure = exc

                    if failure is None:
                        try:
                            postcondition = context.make_phase_spec(
                                SourceOperationPhase.POSTCONDITION,
                                allowed_io={"query"},
                                fields=(harmonic_field, output_field),
                                max_steps=extensions.query_contract.max_queries,
                            )
                            with context.authorize_phase(postcondition) as authorization:
                                postcondition_snapshot = self._snapshot_v2_with_open_source(
                                    source,
                                    correlation_id=context.correlation_id,
                                    deadline=authorization.deadline,
                                )
                                postcondition_harmonics, postcondition_output = (
                                    self._source_v2_harmonic_target(
                                        postcondition_snapshot,
                                        request.channel,
                                        operation=operation,
                                    )
                                )
                                assert result is not None
                                self._validate_source_harmonic_disable_v2_postcondition(
                                    request,
                                    result,
                                    postcondition_snapshot,
                                    postcondition_harmonics,
                                    postcondition_output,
                                )
                                context.complete_phase_verification(
                                    authorization,
                                    io_kind="query",
                                    fields=(harmonic_field, output_field),
                                )
                        except BaseException as exc:
                            failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_harmonic_disable_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            wrote_main=wrote_main,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                return _SourceHarmonicDisableV2Transaction(
                    result=result,
                    artifact=self._source_harmonic_disable_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                        wrote_main=wrote_main,
                    ),
                    snapshot=(
                        preflight_snapshot
                        if postcondition_snapshot is None
                        else postcondition_snapshot
                    ),
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_modulation_v2_transaction(
        self,
        request: SourceModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceModulationConfigureV2Transaction:
        """Execute one declared internal AM transaction while output is OFF."""

        operation = "source.modulation_configure_v2"
        if not isinstance(request, SourceModulationConfigureRequest):
            raise ConfigError(f"{operation} requires SourceModulationConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.modulation_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_modulation_v2_fields(request.channel)
            modulation_field = next(
                field for field in fields if field.field is SourceFieldId.MODULATION
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(modulation_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceModulationConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_modulation, preflight_output = (
                        self._source_v2_modulation_preflight_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                    )
                    self._validate_source_modulation_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_modulation,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (request.channel, preflight_modulation, preflight_output)
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(modulation_field,),
                    max_steps=SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceModulationConfigureV2Driver,
                            source,
                        ).configure_source_modulation_v2(request)
                        self._validate_source_modulation_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(modulation_field, output_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_modulation, postcondition_output = (
                                self._source_v2_modulation_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_modulation_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_modulation,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(modulation_field, output_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_modulation_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceModulationConfigureV2Transaction(
                    result=result,
                    artifact=self._source_modulation_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_pm_modulation_v2_transaction(
        self,
        request: SourcePmModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourcePmModulationConfigureV2Transaction:
        """Execute one declared internal PM transaction while output is OFF."""

        operation = "source.modulation_pm_configure_v2"
        if not isinstance(request, SourcePmModulationConfigureRequest):
            raise ConfigError(f"{operation} requires SourcePmModulationConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.modulation_pm_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_modulation_v2_fields(request.channel)
            modulation_field = next(
                field for field in fields if field.field is SourceFieldId.MODULATION
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(modulation_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourcePmModulationConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_modulation, preflight_output = (
                        self._source_v2_modulation_preflight_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                    )
                    self._validate_source_pm_modulation_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_modulation,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (request.channel, preflight_modulation, preflight_output)
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(modulation_field,),
                    max_steps=SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourcePmModulationConfigureV2Driver,
                            source,
                        ).configure_source_pm_modulation_v2(request)
                        self._validate_source_pm_modulation_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(modulation_field, output_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_modulation, postcondition_output = (
                                self._source_v2_modulation_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_pm_modulation_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_modulation,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(modulation_field, output_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_pm_modulation_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourcePmModulationConfigureV2Transaction(
                    result=result,
                    artifact=self._source_pm_modulation_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_fm_modulation_v2_transaction(
        self,
        request: SourceFmModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceFmModulationConfigureV2Transaction:
        """Execute one declared internal FM transaction while output is OFF."""

        operation = "source.modulation_fm_configure_v2"
        if not isinstance(request, SourceFmModulationConfigureRequest):
            raise ConfigError(f"{operation} requires SourceFmModulationConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.modulation_fm_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_modulation_v2_fields(request.channel)
            modulation_field = next(
                field for field in fields if field.field is SourceFieldId.MODULATION
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(modulation_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceFmModulationConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_modulation, preflight_output = (
                        self._source_v2_modulation_preflight_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                    )
                    self._validate_source_fm_modulation_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_modulation,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (request.channel, preflight_modulation, preflight_output)
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(modulation_field,),
                    max_steps=SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceFmModulationConfigureV2Driver,
                            source,
                        ).configure_source_fm_modulation_v2(request)
                        self._validate_source_fm_modulation_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(modulation_field, output_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_modulation, postcondition_output = (
                                self._source_v2_modulation_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_fm_modulation_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_modulation,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(modulation_field, output_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_fm_modulation_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceFmModulationConfigureV2Transaction(
                    result=result,
                    artifact=self._source_fm_modulation_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_pwm_modulation_v2_transaction(
        self,
        request: SourcePwmModulationConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourcePwmModulationConfigureV2Transaction:
        """Execute one declared internal PWM transaction while output is OFF."""

        operation = "source.modulation_pwm_configure_v2"
        if not isinstance(request, SourcePwmModulationConfigureRequest):
            raise ConfigError(f"{operation} requires SourcePwmModulationConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.modulation_pwm_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_modulation_v2_fields(request.channel)
            modulation_field = next(
                field for field in fields if field.field is SourceFieldId.MODULATION
            )
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_PWM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(modulation_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourcePwmModulationConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_modulation, preflight_output = (
                        self._source_v2_modulation_preflight_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                    )
                    self._validate_source_pwm_modulation_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_modulation,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (request.channel, preflight_modulation, preflight_output)
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(modulation_field,),
                    max_steps=SOURCE_PWM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourcePwmModulationConfigureV2Driver,
                            source,
                        ).configure_source_pwm_modulation_v2(request)
                        self._validate_source_pwm_modulation_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(modulation_field, output_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_modulation, postcondition_output = (
                                self._source_v2_modulation_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_pwm_modulation_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_modulation,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(modulation_field, output_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_pwm_modulation_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourcePwmModulationConfigureV2Transaction(
                    result=result,
                    artifact=self._source_pwm_modulation_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_sweep_v2_transaction(
        self,
        request: SourceSweepConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceSweepConfigureV2Transaction:
        """Execute one declared internal Sweep transaction while output is OFF."""

        operation = "source.sweep_configure_v2"
        if not isinstance(request, SourceSweepConfigureRequest):
            raise ConfigError(f"{operation} requires SourceSweepConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.sweep_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_sweep_v2_fields(request.channel)
            basic_field = next(field for field in fields if field.field is SourceFieldId.BASIC)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            sweep_field = next(field for field in fields if field.field is SourceFieldId.SWEEP)
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_SWEEP_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=tuple(
                    field
                    for field in fields
                    if field.field
                    in {
                        SourceFieldId.BASIC,
                        SourceFieldId.OUTPUT,
                        SourceFieldId.SWEEP,
                    }
                ),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceSweepConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_basic, preflight_sweep, preflight_output = (
                        self._source_v2_sweep_preflight_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                    )
                    self._validate_source_sweep_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_basic,
                        preflight_sweep,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (
                                request.channel,
                                preflight_basic,
                                preflight_sweep,
                                preflight_output,
                            )
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(basic_field, sweep_field),
                    max_steps=SOURCE_SWEEP_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceSweepConfigureV2Driver,
                            source,
                        ).configure_source_sweep_v2(request)
                        self._validate_source_sweep_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(basic_field, output_field, sweep_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            (
                                postcondition_basic,
                                postcondition_sweep,
                                postcondition_output,
                            ) = self._source_v2_sweep_target(
                                postcondition_snapshot,
                                request.channel,
                                operation=operation,
                            )
                            assert result is not None
                            self._validate_source_sweep_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_basic,
                                postcondition_sweep,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(basic_field, output_field, sweep_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_sweep_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceSweepConfigureV2Transaction(
                    result=result,
                    artifact=self._source_sweep_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_burst_v2_transaction(
        self,
        request: SourceBurstConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceBurstConfigureV2Transaction:
        """Execute one declared internal Triggered Burst transaction while output is OFF."""

        operation = "source.burst_configure_v2"
        if not isinstance(request, SourceBurstConfigureRequest):
            raise ConfigError(f"{operation} requires SourceBurstConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.burst_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_burst_v2_fields(request.channel)
            burst_field = next(field for field in fields if field.field is SourceFieldId.BURST)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(burst_field, output_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceBurstConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_burst, preflight_output = self._source_v2_burst_preflight_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    self._validate_source_burst_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_burst,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest((request.channel, preflight_burst, preflight_output))
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(burst_field,),
                    max_steps=SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceBurstConfigureV2Driver,
                            source,
                        ).configure_source_burst_v2(request)
                        self._validate_source_burst_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(burst_field, output_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_burst, postcondition_output = (
                                self._source_v2_burst_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_burst_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_burst,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(burst_field, output_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_burst_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceBurstConfigureV2Transaction(
                    result=result,
                    artifact=self._source_burst_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _fire_source_v2_transaction(
        self,
        request: SourceFireRequest,
        *,
        feature: SourceFeature,
        correlation_id: str | None = None,
    ) -> _SourceFireV2Transaction:
        """Fire one configured Burst or Sweep without retrying the command."""

        if feature is SourceFeature.BURST:
            operation = "source.burst_fire_v2"
            capability = "source.burst_fire_v2"
            configure_capability = "source.burst_configure_v2"
            contract = SOURCE_BURST_FIRE_V2_OPERATION_CONTRACT
            feature_field_id = SourceFieldId.BURST
        elif feature is SourceFeature.SWEEP:
            operation = "source.sweep_fire_v2"
            capability = "source.sweep_fire_v2"
            configure_capability = "source.sweep_configure_v2"
            contract = SOURCE_SWEEP_FIRE_V2_OPERATION_CONTRACT
            feature_field_id = SourceFieldId.SWEEP
        else:  # pragma: no cover - private callers pass one of the two declared features.
            raise ValueError("source fire feature must be Burst or Sweep")
        if not isinstance(request, SourceFireRequest):
            raise ConfigError(f"{operation} requires SourceFireRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            capability,
            configure_capability,
            "source.output_v2",
        )
        configuration_digest = self._require_source_v2_fire_receipt(
            feature,
            request.channel,
            operation=operation,
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_fire_v2_fields(request.channel, feature_field_id)
            feature_field = next(field for field in fields if field.field is feature_field_id)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(
                SourceFacetScope.CHANNEL,
                channel=request.channel,
            )
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=contract,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=tuple(
                    item
                    for item in fields
                    if item.field in {feature_field_id, SourceFieldId.OUTPUT}
                ),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceFireResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_basic, preflight_feature, preflight_output = (
                        self._source_v2_fire_target(
                            preflight_snapshot,
                            request.channel,
                            feature=feature,
                            operation=operation,
                        )
                    )
                    self._validate_source_fire_v2_preflight(
                        request,
                        feature=feature,
                        snapshot=preflight_snapshot,
                        basic=preflight_basic,
                        feature_state=preflight_feature,
                        output=preflight_output,
                        configuration_digest=configuration_digest,
                        operation=operation,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (
                                request.channel,
                                preflight_basic,
                                preflight_feature,
                                preflight_output,
                            )
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(feature_field,),
                    max_steps=contract.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        if feature is SourceFeature.BURST:
                            result = cast(
                                SourceBurstFireV2Driver,
                                source,
                            ).fire_source_burst_v2(request)
                        else:
                            result = cast(
                                SourceSweepFireV2Driver,
                                source,
                            ).fire_source_sweep_v2(request)
                        self._validate_source_fire_v2_result(
                            request,
                            result,
                            operation=operation,
                        )
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=fields,
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            post_basic, post_feature, post_output = (
                                self._source_v2_fire_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    feature=feature,
                                    operation=operation,
                                )
                            )
                            self._validate_source_fire_v2_postcondition(
                                request,
                                feature=feature,
                                snapshot=postcondition_snapshot,
                                basic=post_basic,
                                feature_state=post_feature,
                                output=post_output,
                                configuration_digest=configuration_digest,
                                operation=operation,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=fields,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        self._clear_source_v2_fire_receipt(feature, request.channel)
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_fire_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            feature=feature,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceFireV2Transaction(
                    result=result,
                    artifact=self._source_fire_v2_artifact(
                        context=context,
                        request=request,
                        feature=feature,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _configure_pulse_v2_transaction(
        self,
        request: SourcePulseConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourcePulseConfigureV2Transaction:
        """Execute one declared WIDTH Pulse transaction while output is OFF."""

        operation = "source.pulse_configure_v2"
        if not isinstance(request, SourcePulseConfigureRequest):
            raise ConfigError(f"{operation} requires SourcePulseConfigureRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.pulse_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_pulse_v2_fields(request.channel)
            pulse_field = next(field for field in fields if field.field is SourceFieldId.PULSE)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=(output_field, pulse_field),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourcePulseConfigureResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_pulse, preflight_output = self._source_v2_pulse_preflight_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    self._validate_source_pulse_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_pulse,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest((request.channel, preflight_pulse, preflight_output))
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(pulse_field,),
                    max_steps=SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourcePulseConfigureV2Driver,
                            source,
                        ).configure_source_pulse_v2(request)
                        self._validate_source_pulse_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(output_field, pulse_field),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_pulse, postcondition_output = (
                                self._source_v2_pulse_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            assert result is not None
                            self._validate_source_pulse_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_pulse,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(output_field, pulse_field),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_pulse_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourcePulseConfigureV2Transaction(
                    result=result,
                    artifact=self._source_pulse_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _mutate_arbitrary_storage_v2_transaction(
        self,
        request: SourceArbitraryStorageRequest,
        *,
        payload: bytes,
        correlation_id: str | None = None,
    ) -> _SourceArbitraryStorageV2Transaction:
        """Write one named ARB storage slot without selecting or enabling it."""

        operation = "source.arbitrary_storage_v2"
        if not isinstance(request, SourceArbitraryStorageRequest):
            raise ConfigError(f"{operation} requires SourceArbitraryStorageRequest")
        self._validate_source_arbitrary_storage_payload(request, payload)
        self._require(
            operation,
            "source.snapshot_v2",
            "source.arbitrary_storage_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_arbitrary_storage_v2_fields(request.channel)
            storage_field = next(
                field
                for field in fields
                if field.field is SourceFieldId.ARBITRARY_STORAGE
            )
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_ARBITRARY_STORAGE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(),
                emergency_off_outputs=(),
                restore_order=(),
                non_restorable_fields=(storage_field,),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            preflight_slot: SourceArbitraryStorageSlot | None = None
            postcondition_slot: SourceArbitraryStorageSlot | None = None
            result: SourceArbitraryStorageResult | None = None
            main_entered = False
            failure: BaseException | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries + 1,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_arbitrary, preflight_output = self._source_v2_arbitrary_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    preflight_slot = self._read_source_arbitrary_storage_v2_slot(
                        source,
                        request.channel,
                        request.slot_id,
                        operation=operation,
                    )
                    self._validate_source_arbitrary_storage_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_arbitrary,
                        preflight_output,
                        preflight_slot,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (
                                request.channel,
                                preflight_arbitrary,
                                preflight_output,
                                preflight_slot,
                            )
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write_bytes"},
                    fields=(storage_field,),
                    max_steps=SOURCE_ARBITRARY_STORAGE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceArbitraryStorageV2Driver,
                            source,
                        ).mutate_source_arbitrary_storage_v2(request, payload)
                        self._validate_source_arbitrary_storage_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=fields,
                            max_steps=extensions.query_contract.max_queries + 1,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            postcondition_arbitrary, postcondition_output = (
                                self._source_v2_arbitrary_target(
                                    postcondition_snapshot,
                                    request.channel,
                                    operation=operation,
                                )
                            )
                            postcondition_slot = self._read_source_arbitrary_storage_v2_slot(
                                source,
                                request.channel,
                                request.slot_id,
                                operation=operation,
                            )
                            assert preflight_arbitrary is not None
                            assert preflight_output is not None
                            assert result is not None
                            self._validate_source_arbitrary_storage_v2_postcondition(
                                request,
                                result,
                                preflight_snapshot,
                                preflight_arbitrary,
                                preflight_output,
                                postcondition_snapshot,
                                postcondition_arbitrary,
                                postcondition_output,
                                postcondition_slot,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=fields,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    context.complete()
                    if main_entered:
                        self._attach_source_arbitrary_storage_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            preflight_slot=preflight_slot,
                            postcondition_slot=postcondition_slot,
                            result=result,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                assert preflight_slot is not None
                assert postcondition_slot is not None
                return _SourceArbitraryStorageV2Transaction(
                    result=result,
                    artifact=self._source_arbitrary_storage_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        preflight_slot=preflight_slot,
                        postcondition_slot=postcondition_slot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _select_arbitrary_v2_transaction(
        self,
        request: SourceArbitrarySelectRequest,
        *,
        correlation_id: str | None = None,
    ) -> _SourceArbitrarySelectV2Transaction:
        """Select one stored ARB waveform while the target output remains OFF."""

        operation = "source.arbitrary_select_v2"
        if not isinstance(request, SourceArbitrarySelectRequest):
            raise ConfigError(f"{operation} requires SourceArbitrarySelectRequest")
        self._require(
            operation,
            "source.snapshot_v2",
            "source.arbitrary_select_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_arbitrary_select_v2_fields(request.channel)
            selection_field = next(
                field
                for field in fields
                if field.field is SourceFieldId.ARBITRARY_SELECTION
            )
            basic_field = next(field for field in fields if field.field is SourceFieldId.BASIC)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_ARBITRARY_SELECT_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=tuple(
                    field
                    for field in fields
                    if field.field
                    in {
                        SourceFieldId.ARBITRARY_SELECTION,
                        SourceFieldId.BASIC,
                        SourceFieldId.OUTPUT,
                    }
                ),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceArbitrarySelectResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_basic, preflight_arbitrary, preflight_output = (
                        self._source_v2_arbitrary_select_target(
                            preflight_snapshot,
                            request.channel,
                            operation=operation,
                        )
                    )
                    self._validate_source_arbitrary_select_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_basic,
                        preflight_arbitrary,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (
                                request.channel,
                                preflight_basic,
                                preflight_arbitrary,
                                preflight_output,
                            )
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write"},
                    fields=(selection_field, basic_field),
                    max_steps=SOURCE_ARBITRARY_SELECT_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceArbitrarySelectV2Driver,
                            source,
                        ).select_source_arbitrary_v2(request)
                        self._validate_source_arbitrary_select_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=fields,
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            (
                                postcondition_basic,
                                postcondition_arbitrary,
                                postcondition_output,
                            ) = self._source_v2_arbitrary_select_target(
                                postcondition_snapshot,
                                request.channel,
                                operation=operation,
                            )
                            assert result is not None
                            self._validate_source_arbitrary_select_v2_postcondition(
                                request,
                                result,
                                postcondition_snapshot,
                                postcondition_basic,
                                postcondition_arbitrary,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=fields,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_arbitrary_select_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceArbitrarySelectV2Transaction(
                    result=result,
                    artifact=self._source_arbitrary_select_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _replace_arbitrary_volatile_v2_transaction(
        self,
        request: SourceArbitraryVolatileReplaceRequest,
        *,
        payload: bytes,
        correlation_id: str | None = None,
    ) -> _SourceArbitraryVolatileReplaceV2Transaction:
        """Replace one unnamed volatile ARB workspace without claiming content readback."""

        operation = "source.arbitrary_volatile_replace_v2"
        if not isinstance(request, SourceArbitraryVolatileReplaceRequest):
            raise ConfigError(f"{operation} requires SourceArbitraryVolatileReplaceRequest")
        self._validate_source_arbitrary_volatile_replace_v2_payload(request, payload)
        self._require(
            operation,
            "source.snapshot_v2",
            "source.arbitrary_volatile_replace_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_arbitrary_volatile_replace_v2_fields(request.channel)
            selection_field = next(
                field
                for field in fields
                if field.field is SourceFieldId.ARBITRARY_SELECTION
            )
            storage_field = next(
                field
                for field in fields
                if field.field is SourceFieldId.ARBITRARY_STORAGE
            )
            basic_field = next(field for field in fields if field.field is SourceFieldId.BASIC)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            target_scope = SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel)
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=SOURCE_ARBITRARY_VOLATILE_REPLACE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(target_scope,),
                emergency_off_outputs=(target_scope,),
                restore_order=(),
                non_restorable_fields=tuple(
                    field
                    for field in fields
                    if field.field
                    in {
                        SourceFieldId.ARBITRARY_SELECTION,
                        SourceFieldId.ARBITRARY_STORAGE,
                        SourceFieldId.BASIC,
                        SourceFieldId.OUTPUT,
                    }
                ),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceArbitraryVolatileReplaceResult | None = None
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    (
                        preflight_basic,
                        preflight_arbitrary,
                        preflight_output,
                    ) = self._source_v2_arbitrary_select_target(
                        preflight_snapshot,
                        request.channel,
                        operation=operation,
                    )
                    self._validate_source_arbitrary_volatile_replace_v2_preflight(
                        request,
                        preflight_snapshot,
                        preflight_basic,
                        preflight_arbitrary,
                        preflight_output,
                    )
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest(
                            (
                                request.channel,
                                preflight_basic,
                                preflight_arbitrary,
                                preflight_output,
                            )
                        )
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                main = context.make_phase_spec(
                    SourceOperationPhase.MAIN,
                    allowed_io={"write_bytes"},
                    fields=(selection_field, storage_field, basic_field),
                    max_steps=SOURCE_ARBITRARY_VOLATILE_REPLACE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(
                            SourceArbitraryVolatileReplaceV2Driver,
                            source,
                        ).replace_source_arbitrary_volatile_v2(request, payload)
                        self._validate_source_arbitrary_volatile_replace_v2_result(request, result)
                except BaseException as exc:
                    failure = exc

                if failure is None:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=fields,
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            (
                                postcondition_basic,
                                postcondition_arbitrary,
                                postcondition_output,
                            ) = self._source_v2_arbitrary_select_target(
                                postcondition_snapshot,
                                request.channel,
                                operation=operation,
                            )
                            assert result is not None
                            self._validate_source_arbitrary_volatile_replace_v2_postcondition(
                                result,
                                postcondition_snapshot,
                                postcondition_basic,
                                postcondition_arbitrary,
                                postcondition_output,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=fields,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_output_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_arbitrary_volatile_replace_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceArbitraryVolatileReplaceV2Transaction(
                    result=result,
                    artifact=self._source_arbitrary_volatile_replace_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _mutate_counter_v2_transaction(
        self,
        request: SourceCounterConfigureRequest | SourceCounterEnableRequest,
        *,
        operation: str,
        operation_contract: SourceOperationContract,
        correlation_id: str | None = None,
    ) -> _SourceCounterV2Transaction:
        """Run one Counter configuration or enable-state mutation with no rollback."""

        if operation == "source.counter_configure_v2":
            if not isinstance(request, SourceCounterConfigureRequest):
                raise ConfigError(f"{operation} requires SourceCounterConfigureRequest")
        elif operation in {"source.counter_enable_v2", "source.counter_disable_v2"}:
            if not isinstance(request, SourceCounterEnableRequest):
                raise ConfigError(f"{operation} requires SourceCounterEnableRequest")
        else:  # pragma: no cover - private callers fix the operation set above.
            raise ValueError("unsupported Counter V2 mutation operation")
        self._require(operation, "source.snapshot_v2", operation_contract.capability)
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            fields = self._source_counter_v2_fields(request.input_id)
            counter_field = next(
                field for field in fields if field.field is SourceFieldId.COUNTER
            )
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=operation_contract,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(),
                emergency_off_outputs=(),
                restore_order=(),
                non_restorable_fields=(counter_field,),
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceCounterConfigureResult | SourceCounterEnableResult | None = None
            wrote_main = False
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    counter, profile = self._source_v2_counter_target(
                        preflight_snapshot,
                        request.input_id,
                        direction=operation_contract.direction,
                        operation=operation,
                    )
                    if isinstance(request, SourceCounterConfigureRequest):
                        wrote_main = self._validate_source_counter_configure_v2_preflight(
                            request,
                            preflight_snapshot,
                            counter,
                            profile,
                        )
                        if not wrote_main:
                            result = SourceCounterConfigureResult(request.input_id, counter)
                    else:
                        wrote_main = self._validate_source_counter_enable_v2_preflight(
                            request,
                            preflight_snapshot,
                            counter,
                            profile,
                            operation=operation,
                        )
                        if not wrote_main:
                            result = SourceCounterEnableResult(request.input_id, request.enabled)
                    context.bind_baseline_snapshot_digest(
                        source_v2_digest((request.input_id, counter))
                    )
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=fields,
                    )

                if wrote_main:
                    main = context.make_phase_spec(
                        SourceOperationPhase.MAIN,
                        allowed_io={"write"},
                        fields=(counter_field,),
                        max_steps=operation_contract.main_max_steps,
                    )
                    try:
                        with context.authorize_phase(main):
                            main_entered = True
                            if isinstance(request, SourceCounterConfigureRequest):
                                result = cast(
                                    SourceCounterConfigureV2Driver,
                                    source,
                                ).configure_source_counter_v2(request)
                                self._validate_source_counter_configure_v2_result(request, result)
                            else:
                                result = cast(
                                    SourceCounterEnableV2Driver,
                                    source,
                                ).set_source_counter_enabled_v2(request)
                                self._validate_source_counter_enable_v2_result(
                                    request,
                                    result,
                                    operation=operation,
                                )
                    except BaseException as exc:
                        failure = exc

                if failure is None and wrote_main:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=(counter_field,),
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            counter, profile = self._source_v2_counter_target(
                                postcondition_snapshot,
                                request.input_id,
                                direction=operation_contract.direction,
                                operation=operation,
                            )
                            assert result is not None
                            if isinstance(request, SourceCounterConfigureRequest):
                                self._validate_source_counter_configure_v2_postcondition(
                                    request,
                                    result,
                                    postcondition_snapshot,
                                    counter,
                                    profile,
                                )
                            else:
                                self._validate_source_counter_enable_v2_postcondition(
                                    request,
                                    result,
                                    postcondition_snapshot,
                                    counter,
                                    profile,
                                    operation=operation,
                                )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=(counter_field,),
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is not None:
                    if main_entered:
                        context.mark_failure_required()
                        recovery = {
                            "status": "not_attempted",
                            "reason": "counter_state_not_rollback_safe",
                        }
                    context.complete()
                    if main_entered:
                        self._attach_source_counter_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            wrote_main=wrote_main,
                            recovery=recovery,
                            capability=operation_contract.capability,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                return _SourceCounterV2Transaction(
                    result=result,
                    artifact=self._source_counter_v2_artifact(
                        context=context,
                        request=request,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                        wrote_main=wrote_main,
                        capability=operation_contract.capability,
                    ),
                    snapshot=(
                        postcondition_snapshot
                        if postcondition_snapshot is not None
                        else preflight_snapshot
                    ),
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    def _measure_counter_v2(
        self,
        request: SourceCounterMeasureRequest,
        *,
        correlation_id: str | None = None,
    ) -> SourceCounterMeasureResult:
        operation = "source.counter_measure_v2"
        if not isinstance(request, SourceCounterMeasureRequest):
            raise ConfigError(f"{operation} requires SourceCounterMeasureRequest")
        self._require(operation, "source.snapshot_v2", "source.counter_measure_v2")
        spec = require_operation_spec(operation)
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")
            timeout_ms = spec.operation_timeout_ms
            if timeout_ms is None:  # pragma: no cover - registry invariant.
                raise ConfigError(f"{operation} requires an operation timeout")
            timeout_ms = min(timeout_ms, self.config.connection.timeout_ms)
            with session_state.transaction_lock:
                snapshot = self._snapshot_v2_with_open_source(
                    source,
                    correlation_id=correlation_id,
                )
                counter, profile = self._source_v2_counter_target(
                    snapshot,
                    request.input_id,
                    direction=SourceFeatureDirection.READ,
                    operation=operation,
                )
                self._validate_source_counter_measure_v2_preflight(
                    snapshot,
                    counter,
                    profile,
                    operation=operation,
                )
                coordinator = SessionTransactionCoordinator(session_state)
                with coordinator.authorize_normal(
                    operation_id=operation,
                    allowed_io=("query",),
                    fields=(SourceFieldId.COUNTER.value,),
                    timeout_ms=timeout_ms,
                    max_steps=1,
                    context_id="source_counter_measure_v2",
                    correlation_id=uuid4().hex,
                    phase="main",
                    absolute_deadline=time.monotonic() + (timeout_ms / 1000.0),
                ):
                    result = cast(
                        SourceCounterMeasureV2Driver,
                        source,
                    ).measure_source_counter_v2(request)
            self._validate_source_counter_measure_v2_result(
                request,
                result,
                profile,
                operation=operation,
            )
            return result

    def _configure_cross_channel_v2_transaction(
        self,
        request: object,
        *,
        operation: str,
        feature: SourceFeature,
        relation_field: SourceFieldId,
        operation_contract: SourceOperationContract,
        correlation_id: str | None = None,
    ) -> _SourceCrossChannelConfigureV2Transaction:
        """Run one graph-bounded M6-C relation mutation.

        The discovery snapshot exists solely to freeze a complete affected-port
        closure before creating the operation context.  The authorized
        preflight repeats that discovery and rejects graph drift before MAIN.
        It never broadens independent, unrelated ports into this transaction.
        """

        channels, enabled = self._source_cross_channel_request_parts(
            request,
            feature=feature,
            operation=operation,
        )
        self._require(operation, "source.snapshot_v2", operation)
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(f"{operation} requires validated source_extensions")
            if session_state is None:
                raise ConfigError(f"{operation} requires a connection-bound session state")

            discovery_snapshot = self._snapshot_v2_with_open_source(
                source,
                correlation_id=correlation_id,
            )
            discovery_closure = self._source_cross_channel_closure(
                discovery_snapshot,
                channels=channels,
                feature=feature,
                relation_field=relation_field,
                operation=operation,
            )
            self._validate_source_cross_channel_preflight(
                discovery_snapshot,
                discovery_closure,
                operation=operation,
            )
            if (
                len(discovery_closure.output_fields)
                > operation_contract.recovery_max_steps
            ):
                raise ConfigError(
                    f"{operation} affected output range exceeds the declared recovery bound"
                )
            output_scopes = tuple(
                SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
                for channel in discovery_closure.affected_channels
            )
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec(operation),
                operation_contract=operation_contract,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=discovery_closure.fields,
                required_off_outputs=output_scopes,
                emergency_off_outputs=output_scopes,
                restore_order=(),
                non_restorable_fields=discovery_closure.fields,
                correlation_id=correlation_id,
            )
            preflight_snapshot: SourceSnapshotV2 | None = None
            postcondition_snapshot: SourceSnapshotV2 | None = None
            result: SourceCrossChannelConfigureResult | None = None
            closure = discovery_closure
            wrote_main = False
            main_entered = False
            failure: BaseException | None = None
            recovery: dict[str, object] | None = None

            try:
                preflight = context.make_phase_spec(
                    SourceOperationPhase.PREFLIGHT,
                    allowed_io={"query"},
                    fields=closure.fields,
                    max_steps=extensions.query_contract.max_queries,
                )
                with context.authorize_phase(preflight) as authorization:
                    preflight_snapshot = self._snapshot_v2_with_open_source(
                        source,
                        correlation_id=context.correlation_id,
                        deadline=authorization.deadline,
                    )
                    preflight_closure = self._source_cross_channel_closure(
                        preflight_snapshot,
                        channels=channels,
                        feature=feature,
                        relation_field=relation_field,
                        operation=operation,
                    )
                    if preflight_closure != discovery_closure:
                        raise ConfigError(
                            f"{operation} relation graph changed before the transaction"
                        )
                    closure = preflight_closure
                    self._validate_source_cross_channel_preflight(
                        preflight_snapshot,
                        closure,
                        operation=operation,
                    )
                    context.bind_baseline_snapshot_digest(source_v2_digest(preflight_snapshot))
                    context.complete_phase_verification(
                        authorization,
                        io_kind="query",
                        fields=closure.fields,
                    )

                if closure.relation.enabled.value is enabled:
                    result = self._source_cross_channel_result_from_snapshot(
                        closure,
                        enabled=enabled,
                    )
                else:
                    relation_target = SourceFieldRef(
                        relation_field,
                        SourceScopeRef(SourceFacetScope.CHANNEL_SET, channels=channels),
                    )
                    main = context.make_phase_spec(
                        SourceOperationPhase.MAIN,
                        allowed_io={"write"},
                        fields=(relation_target,),
                        max_steps=operation_contract.main_max_steps,
                    )
                    try:
                        with context.authorize_phase(main):
                            main_entered = True
                            wrote_main = True
                            result = self._invoke_source_cross_channel_v2_driver(
                                source,
                                request,
                                feature=feature,
                                operation=operation,
                            )
                            self._validate_source_cross_channel_result(
                                result,
                                channels=channels,
                                enabled=enabled,
                                closure=closure,
                                feature=feature,
                                operation=operation,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is None and wrote_main:
                    try:
                        postcondition = context.make_phase_spec(
                            SourceOperationPhase.POSTCONDITION,
                            allowed_io={"query"},
                            fields=closure.fields,
                            max_steps=extensions.query_contract.max_queries,
                        )
                        with context.authorize_phase(postcondition) as authorization:
                            postcondition_snapshot = self._snapshot_v2_with_open_source(
                                source,
                                correlation_id=context.correlation_id,
                                deadline=authorization.deadline,
                            )
                            assert result is not None
                            self._validate_source_cross_channel_postcondition(
                                result,
                                postcondition_snapshot,
                                closure=closure,
                                channels=channels,
                                enabled=enabled,
                                feature=feature,
                                relation_field=relation_field,
                                operation=operation,
                            )
                            context.complete_phase_verification(
                                authorization,
                                io_kind="query",
                                fields=closure.fields,
                            )
                    except BaseException as exc:
                        failure = exc

                if failure is None and not wrote_main:
                    postcondition_snapshot = preflight_snapshot

                if failure is not None:
                    if main_entered:
                        try:
                            context.mark_failure_required()
                            recovery = self._recover_source_v2_outputs_off(
                                context,
                                source,
                                closure.affected_channels,
                                extensions,
                                closure.output_fields,
                                operation=operation,
                            )
                        except BaseException:
                            recovery = {
                                "status": "recovery_setup_failed",
                                "session_health": session_state.health.value,
                            }
                    context.complete()
                    if main_entered:
                        self._attach_source_cross_channel_v2_diagnostics(
                            failure,
                            context=context,
                            request=request,
                            operation=operation,
                            closure=closure,
                            preflight_snapshot=preflight_snapshot,
                            postcondition_snapshot=postcondition_snapshot,
                            result=result,
                            wrote_main=wrote_main,
                            recovery=recovery,
                        )
                    raise failure

                context.complete()
                assert result is not None
                assert preflight_snapshot is not None
                assert postcondition_snapshot is not None
                return _SourceCrossChannelConfigureV2Transaction(
                    result=result,
                    artifact=self._source_cross_channel_v2_artifact(
                        context=context,
                        request=request,
                        operation=operation,
                        closure=closure,
                        preflight_snapshot=preflight_snapshot,
                        postcondition_snapshot=postcondition_snapshot,
                        result=result,
                        wrote_main=wrote_main,
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

    @staticmethod
    def _source_cross_channel_request_parts(
        request: object,
        *,
        feature: SourceFeature,
        operation: str,
    ) -> tuple[tuple[int, ...], bool]:
        request_types = {
            SourceFeature.COMBINE: SourceCombineConfigureRequest,
            SourceFeature.COUPLING: SourceCouplingConfigureRequest,
            SourceFeature.TRACKING: SourceTrackingConfigureRequest,
            SourceFeature.PHASE_RELATION: SourcePhaseRelationConfigureRequest,
        }
        request_type = request_types[feature]
        if not isinstance(request, request_type):
            raise ConfigError(f"{operation} requires {request_type.__name__}")
        return request.channels, request.enabled

    @staticmethod
    def _source_cross_channel_relation_field(feature: SourceFeature) -> SourceFieldId:
        relation_fields = {
            SourceFeature.COMBINE: SourceFieldId.COMBINE,
            SourceFeature.COUPLING: SourceFieldId.COUPLING,
            SourceFeature.TRACKING: SourceFieldId.TRACKING,
            SourceFeature.COPY: SourceFieldId.COPY,
            SourceFeature.PHASE_RELATION: SourceFieldId.PHASE_RELATION,
        }
        try:
            return relation_fields[feature]
        except KeyError as exc:  # pragma: no cover - every current caller is frozen above.
            raise ConfigError("Source feature is not a cross-channel relation") from exc

    def _source_cross_channel_closure(
        self,
        snapshot: SourceSnapshotV2,
        *,
        channels: tuple[int, ...],
        feature: SourceFeature,
        relation_field: SourceFieldId,
        operation: str,
    ) -> _SourceCrossChannelClosure:
        if snapshot.cross_channel.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable cross-channel state")
        cross_channel = snapshot.cross_channel.value
        relation = next(
            (
                item
                for item in cross_channel.relations
                if item.feature is feature and item.channels == channels
            ),
            None,
        )
        if relation is None or relation.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable declared relation state")
        if cross_channel.relation_graph.availability is not Availability.VALUE or not isinstance(
            cross_channel.relation_graph.value,
            SourceRelationGraph,
        ):
            raise ConfigError(f"{operation} requires a readable relation graph")
        graph = cross_channel.relation_graph.value
        self._validate_source_cross_channel_runtime_profile(
            snapshot,
            channels=channels,
            feature=feature,
            operation=operation,
        )

        requested = set(channels)
        target_edges = tuple(
            edge
            for edge in graph.edges
            if edge.feature is feature
            and set(edge.sources + edge.targets) <= requested
            and set(edge.sources + edge.targets) & requested
        )
        target_participants = {
            channel
            for edge in target_edges
            for channel in (*edge.sources, *edge.targets)
        }
        if target_participants != requested:
            raise ConfigError(
                f"{operation} relation graph cannot prove the declared affected channels"
            )

        affected = set(channels)
        selected_edges: list[object] = []
        changed = True
        while changed:
            changed = False
            for edge in graph.edges:
                participants = set(edge.sources + edge.targets)
                if not participants & affected:
                    continue
                if edge not in selected_edges:
                    selected_edges.append(edge)
                before = len(affected)
                affected.update(participants)
                changed = changed or len(affected) != before
        affected_channels = tuple(sorted(affected))
        fields = self._source_cross_channel_fields(
            cross_channel.relations,
            feature=feature,
            relation_field=relation_field,
            channels=channels,
            affected_channels=affected_channels,
            edges=tuple(selected_edges),
            operation=operation,
        )
        output_fields = tuple(
            field for field in fields if field.field is SourceFieldId.OUTPUT
        )
        return _SourceCrossChannelClosure(
            feature=feature,
            relation_field=relation_field,
            relation=relation,
            relation_graph=graph,
            affected_channels=affected_channels,
            fields=fields,
            output_fields=output_fields,
        )

    def _validate_source_cross_channel_runtime_profile(
        self,
        snapshot: SourceSnapshotV2,
        *,
        channels: tuple[int, ...],
        feature: SourceFeature,
        operation: str,
    ) -> None:
        profile_type = (
            SourceCouplingCapabilityProfile
            if feature is SourceFeature.COUPLING
            else SourceCrossChannelCapabilityProfile
        )
        configurable = next(
            (
                item
                for item in snapshot.runtime_profile.features
                if item.feature is feature
                and item.scope is SourceFacetScope.CHANNEL_SET
                and item.channels == channels
                and item.support is SupportState.SUPPORTED
                and SourceFeatureDirection.READ in item.directions
                and SourceFeatureDirection.CONFIGURE in item.directions
                and isinstance(item.profile, profile_type)
            ),
            None,
        )
        if configurable is None:
            raise ConfigError(f"{operation} is not available for the runtime channel set")
        profile = configurable.profile
        if isinstance(profile, SourceCouplingCapabilityProfile):
            readable = (
                channels in profile.supported_channel_sets
                and profile.global_state_readable
                and profile.configuration_readable
            )
        else:
            readable = (
                feature in profile.relation_kinds
                and channels in profile.supported_channel_sets
                and profile.configuration_readable
            )
        if not readable:
            raise ConfigError(f"{operation} requires readable declared relation configuration")
        graph = next(
            (
                item
                for item in snapshot.runtime_profile.features
                if item.feature is feature
                and item.scope is SourceFacetScope.INSTRUMENT
                and item.support is SupportState.SUPPORTED
                and SourceFeatureDirection.READ in item.directions
                and isinstance(item.profile, profile_type)
                and item.profile.relation_graph_readable
            ),
            None,
        )
        if graph is None:
            raise ConfigError(f"{operation} requires readable runtime relation graph support")

    def _source_cross_channel_fields(
        self,
        relations: tuple[SourceRelationState | SourceCouplingState, ...],
        *,
        feature: SourceFeature,
        relation_field: SourceFieldId,
        channels: tuple[int, ...],
        affected_channels: tuple[int, ...],
        edges: tuple[object, ...],
        operation: str,
    ) -> tuple[SourceFieldRef, ...]:
        del feature
        field_ids = {
            SourceFieldId.IDENTITY,
            SourceFieldId.OUTPUT,
            SourceFieldId.RELATION_GRAPH,
            relation_field,
        }
        for edge in edges:
            if not hasattr(edge, "affected_fields") or not hasattr(
                edge,
                "implicit_changed_fields",
            ):
                raise ConfigError(f"{operation} relation graph has an invalid edge")
            field_ids.update(edge.affected_fields)
            field_ids.update(edge.implicit_changed_fields)

        channel_fields = {
            SourceFieldId.BASIC,
            SourceFieldId.OUTPUT,
            SourceFieldId.DISPLAY_LOAD,
            SourceFieldId.HARMONICS,
            SourceFieldId.MODULATION,
            SourceFieldId.SWEEP,
            SourceFieldId.BURST,
            SourceFieldId.PULSE,
            SourceFieldId.ARBITRARY_SELECTION,
            SourceFieldId.SYNC,
            SourceFieldId.NOISE_OVERLAY,
        }
        relation_fields = {
            SourceFieldId.COMBINE,
            SourceFieldId.COUPLING,
            SourceFieldId.TRACKING,
            SourceFieldId.COPY,
            SourceFieldId.PHASE_RELATION,
        }
        instrument_fields = {
            SourceFieldId.IDENTITY,
            SourceFieldId.RELATION_GRAPH,
            SourceFieldId.REFERENCE_CLOCK,
            SourceFieldId.CASCADE,
            SourceFieldId.SHARED_POWER,
        }
        refs: set[SourceFieldRef] = set()
        for field_id in field_ids:
            if field_id in channel_fields:
                refs.update(
                    SourceFieldRef(
                        field_id,
                        SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel),
                    )
                    for channel in affected_channels
                )
                continue
            if field_id in relation_fields:
                matched = tuple(
                    relation
                    for relation in relations
                    if self._source_cross_channel_relation_field(relation.feature) is field_id
                    and set(relation.channels) & set(affected_channels)
                )
                if not matched:
                    raise ConfigError(
                        f"{operation} relation graph references an unreadable relation field"
                    )
                refs.update(
                    SourceFieldRef(
                        field_id,
                        SourceScopeRef(
                            SourceFacetScope.CHANNEL_SET,
                            channels=relation.channels,
                        ),
                    )
                    for relation in matched
                )
                continue
            if field_id in instrument_fields:
                refs.add(
                    SourceFieldRef(
                        field_id,
                        SourceScopeRef(SourceFacetScope.INSTRUMENT),
                    )
                )
                continue
            raise ConfigError(
                f"{operation} relation graph references a field without a safe readback scope"
            )
        requested_relation = SourceFieldRef(
            relation_field,
            SourceScopeRef(SourceFacetScope.CHANNEL_SET, channels=channels),
        )
        refs.add(requested_relation)
        return tuple(
            sorted(
                refs,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    def _validate_source_cross_channel_preflight(
        self,
        snapshot: SourceSnapshotV2,
        closure: _SourceCrossChannelClosure,
        *,
        operation: str,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        self._validate_source_cross_channel_closure_readback(
            snapshot,
            closure,
            operation=operation,
        )
        for channel in closure.affected_channels:
            output = self._source_v2_output_target(
                snapshot,
                channel,
                operation=operation,
            )
            if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
                raise ConfigError(f"{operation} requires every affected output OFF")

    def _validate_source_cross_channel_closure_readback(
        self,
        snapshot: SourceSnapshotV2,
        closure: _SourceCrossChannelClosure,
        *,
        operation: str,
    ) -> None:
        for field in closure.fields:
            observed = self._source_cross_channel_snapshot_observation(
                snapshot,
                field,
                operation=operation,
            )
            if observed.availability is not Availability.VALUE:
                raise ConfigError(
                    f"{operation} requires readable affected field {field.field.value}"
                )

    @staticmethod
    def _source_cross_channel_snapshot_observation(
        snapshot: SourceSnapshotV2,
        field: SourceFieldRef,
        *,
        operation: str,
    ) -> Observed[object]:
        if field.field is SourceFieldId.IDENTITY:
            return Observed.value_of(snapshot.runtime_profile.identity)
        if field.target.scope is SourceFacetScope.CHANNEL:
            target = next(
                (item for item in snapshot.channels if item.channel == field.target.channel),
                None,
            )
            if target is None:
                raise ConfigError(f"{operation} affected channel is absent from snapshot")
            channel_values = {
                SourceFieldId.BASIC: target.basic,
                SourceFieldId.OUTPUT: target.output,
                SourceFieldId.HARMONICS: target.harmonics,
                SourceFieldId.MODULATION: target.modulation,
                SourceFieldId.SWEEP: target.sweep,
                SourceFieldId.BURST: target.burst,
                SourceFieldId.PULSE: target.pulse,
                SourceFieldId.ARBITRARY_SELECTION: target.arbitrary,
                SourceFieldId.SYNC: target.sync,
                SourceFieldId.NOISE_OVERLAY: target.noise_overlay,
            }
            if field.field is SourceFieldId.DISPLAY_LOAD:
                if target.output.availability is not Availability.VALUE or not isinstance(
                    target.output.value,
                    OutputFacet,
                ):
                    return target.output
                return target.output.value.display_load
            try:
                return channel_values[field.field]
            except KeyError as exc:
                raise ConfigError(
                    f"{operation} affected field has no channel snapshot projection"
                ) from exc
        if field.target.scope is SourceFacetScope.CHANNEL_SET:
            if snapshot.cross_channel.availability is not Availability.VALUE:
                return snapshot.cross_channel
            relation = next(
                (
                    item
                    for item in snapshot.cross_channel.value.relations
                    if item.channels == field.target.channels
                    and SourceService._source_cross_channel_relation_field(item.feature)
                    is field.field
                ),
                None,
            )
            if relation is None:
                raise ConfigError(f"{operation} affected relation is absent from snapshot")
            return Observed.value_of(relation)
        if field.target.scope is not SourceFacetScope.INSTRUMENT:
            raise ConfigError(f"{operation} affected field has an unsupported snapshot scope")
        if field.field is SourceFieldId.RELATION_GRAPH:
            return snapshot.cross_channel if snapshot.cross_channel.availability is not Availability.VALUE else snapshot.cross_channel.value.relation_graph
        if field.field is SourceFieldId.SHARED_POWER:
            return snapshot.cross_channel if snapshot.cross_channel.availability is not Availability.VALUE else snapshot.cross_channel.value.shared_power
        if snapshot.system.availability is not Availability.VALUE:
            return snapshot.system
        system_values = {
            SourceFieldId.REFERENCE_CLOCK: snapshot.system.value.reference_clock,
            SourceFieldId.CASCADE: snapshot.system.value.cascade,
        }
        try:
            return system_values[field.field]
        except KeyError as exc:
            raise ConfigError(
                f"{operation} affected field has no instrument snapshot projection"
            ) from exc

    @staticmethod
    def _source_cross_channel_result_from_snapshot(
        closure: _SourceCrossChannelClosure,
        *,
        enabled: bool,
    ) -> SourceCrossChannelConfigureResult:
        return SourceCrossChannelConfigureResult(
            feature=closure.feature,
            channels=closure.relation.channels,
            enabled=enabled,
            relation=closure.relation,
            outputs=tuple(
                SourceRelationOutputState(channel=channel, enabled=False)
                for channel in closure.affected_channels
            ),
        )

    @staticmethod
    def _invoke_source_cross_channel_v2_driver(
        source: SourceDriver,
        request: object,
        *,
        feature: SourceFeature,
        operation: str,
    ) -> object:
        if feature is SourceFeature.COMBINE:
            return cast(SourceCombineConfigureV2Driver, source).configure_source_combine_v2(
                cast(SourceCombineConfigureRequest, request)
            )
        if feature is SourceFeature.COUPLING:
            return cast(SourceCouplingConfigureV2Driver, source).configure_source_coupling_v2(
                cast(SourceCouplingConfigureRequest, request)
            )
        if feature is SourceFeature.TRACKING:
            return cast(SourceTrackingConfigureV2Driver, source).configure_source_tracking_v2(
                cast(SourceTrackingConfigureRequest, request)
            )
        if feature is SourceFeature.PHASE_RELATION:
            return cast(
                SourcePhaseRelationConfigureV2Driver,
                source,
            ).configure_source_phase_relation_v2(
                cast(SourcePhaseRelationConfigureRequest, request)
            )
        raise ConfigError(f"{operation} has an unsupported relation feature")

    @staticmethod
    def _validate_source_cross_channel_result(
        result: object,
        *,
        channels: tuple[int, ...],
        enabled: bool,
        closure: _SourceCrossChannelClosure,
        feature: SourceFeature,
        operation: str,
    ) -> None:
        if not isinstance(result, SourceCrossChannelConfigureResult):
            raise ConfigError(
                f"{operation} driver returned an invalid SourceCrossChannelConfigureResult"
            )
        if (
            result.feature is not feature
            or result.channels != channels
            or result.enabled is not enabled
        ):
            raise ConfigError(f"{operation} result does not match the request")
        if tuple(item.channel for item in result.outputs) != closure.affected_channels:
            raise ConfigError(f"{operation} result does not cover every affected output")

    def _validate_source_cross_channel_postcondition(
        self,
        result: SourceCrossChannelConfigureResult,
        snapshot: SourceSnapshotV2,
        *,
        closure: _SourceCrossChannelClosure,
        channels: tuple[int, ...],
        enabled: bool,
        feature: SourceFeature,
        relation_field: SourceFieldId,
        operation: str,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        postcondition_closure = self._source_cross_channel_closure(
            snapshot,
            channels=channels,
            feature=feature,
            relation_field=relation_field,
            operation=operation,
        )
        if not set(postcondition_closure.affected_channels) <= set(closure.affected_channels):
            raise ConfigError(
                f"{operation} postcondition expands the precomputed affected output range"
            )
        self._validate_source_cross_channel_closure_readback(
            snapshot,
            postcondition_closure,
            operation=operation,
        )
        if postcondition_closure.relation.enabled.value is not enabled:
            raise ConfigError(f"{operation} relation readback does not match request")
        for channel in closure.affected_channels:
            output = self._source_v2_output_target(
                snapshot,
                channel,
                operation=operation,
            )
            if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
                raise ConfigError(f"{operation} postcondition reports an affected output ON")
        expected = self._source_cross_channel_result_from_snapshot(
            postcondition_closure,
            enabled=enabled,
        )
        if result.relation != expected.relation or result.outputs != expected.outputs:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _source_basic_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.BASIC, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_harmonics_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.HARMONICS, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_modulation_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.MODULATION, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_burst_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.BURST, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_fire_v2_fields(
        channel: int,
        feature_field: SourceFieldId,
    ) -> tuple[SourceFieldRef, ...]:
        if feature_field not in {SourceFieldId.BURST, SourceFieldId.SWEEP}:
            raise ValueError("source fire field must be Burst or Sweep")
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.BASIC, target),
            SourceFieldRef(feature_field, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda item: (
                    item.field.value,
                    item.target.scope.value,
                    -1 if item.target.channel is None else item.target.channel,
                    item.target.channels,
                    "" if item.target.input_id is None else item.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_pulse_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.PULSE, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_sweep_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.BASIC, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(SourceFieldId.SWEEP, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_arbitrary_storage_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.ARBITRARY_SELECTION, target),
            SourceFieldRef(SourceFieldId.ARBITRARY_STORAGE, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_arbitrary_select_v2_fields(channel: int) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.ARBITRARY_SELECTION, target),
            SourceFieldRef(SourceFieldId.BASIC, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_arbitrary_volatile_replace_v2_fields(
        channel: int,
    ) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel)
        fields = (
            SourceFieldRef(SourceFieldId.ARBITRARY_SELECTION, target),
            SourceFieldRef(SourceFieldId.ARBITRARY_STORAGE, target),
            SourceFieldRef(SourceFieldId.BASIC, target),
            SourceFieldRef(SourceFieldId.OUTPUT, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @staticmethod
    def _source_counter_v2_fields(input_id: str) -> tuple[SourceFieldRef, ...]:
        target = SourceScopeRef(SourceFacetScope.INPUT, input_id=input_id)
        fields = (
            SourceFieldRef(SourceFieldId.COUNTER, target),
            SourceFieldRef(
                SourceFieldId.IDENTITY,
                SourceScopeRef(SourceFacetScope.INSTRUMENT),
            ),
        )
        return tuple(
            sorted(
                fields,
                key=lambda field: (
                    field.field.value,
                    field.target.scope.value,
                    -1 if field.target.channel is None else field.target.channel,
                    field.target.channels,
                    "" if field.target.input_id is None else field.target.input_id,
                ),
            )
        )

    @classmethod
    def _source_output_v2_fields(
        cls,
        channel: int,
        *,
        include_basic: bool,
    ) -> tuple[SourceFieldRef, ...]:
        if include_basic:
            return cls._source_basic_v2_fields(channel)
        return (
            SourceFieldRef(
                SourceFieldId.OUTPUT,
                SourceScopeRef(SourceFacetScope.CHANNEL, channel=channel),
            ),
        )

    @staticmethod
    def _source_v2_counter_target(
        snapshot: SourceSnapshotV2,
        input_id: str,
        *,
        direction: SourceFeatureDirection,
        operation: str,
    ) -> tuple[SourceCounterInputState, SourceCounterCapabilityProfile]:
        features = tuple(
            feature
            for feature in snapshot.runtime_profile.features
            if (
                feature.feature is SourceFeature.COUNTER
                and feature.scope is SourceFacetScope.INPUT
                and feature.support is SupportState.SUPPORTED
                and direction in feature.directions
                and isinstance(feature.profile, SourceCounterCapabilityProfile)
            )
        )
        if len(features) != 1:
            raise ConfigError(f"{operation} requires a runtime Counter {direction.value} profile")
        profile = features[0].profile
        assert isinstance(profile, SourceCounterCapabilityProfile)
        if input_id not in profile.input_ids:
            raise ConfigError(f"{operation} input_id is unsupported by the runtime profile")
        if (
            snapshot.system.availability is not Availability.VALUE
            or not isinstance(snapshot.system.value, SourceSystemStateV2)
        ):
            raise ConfigError(f"{operation} requires readable Counter system state")
        counter = next(
            (item for item in snapshot.system.value.counters if item.input_id == input_id),
            None,
        )
        if counter is None:
            raise ConfigError(f"{operation} input_id is absent from snapshot")
        return counter, profile

    @staticmethod
    def _source_v2_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[BasicWaveFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.basic.availability is not Availability.VALUE or not isinstance(
            target.basic.value,
            BasicWaveFacet,
        ):
            raise ConfigError(f"{operation} requires readable basic state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.basic.value, target.output.value

    @staticmethod
    def _source_v2_harmonic_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[HarmonicFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.harmonics.availability is not Availability.VALUE or not isinstance(
            target.harmonics.value,
            HarmonicFacet,
        ):
            raise ConfigError(f"{operation} requires readable harmonic state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.harmonics.value, target.output.value

    @staticmethod
    def _source_v2_modulation_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[ModulationFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.modulation.availability is not Availability.VALUE or not isinstance(
            target.modulation.value,
            ModulationFacet,
        ):
            raise ConfigError(f"{operation} requires readable modulation state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.modulation.value, target.output.value

    @staticmethod
    def _source_v2_burst_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[BurstFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.burst.availability is not Availability.VALUE or not isinstance(
            target.burst.value,
            BurstFacet,
        ):
            raise ConfigError(f"{operation} requires readable burst state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.burst.value, target.output.value

    @staticmethod
    def _source_v2_pulse_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[PulseFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.pulse.availability is not Availability.VALUE or not isinstance(
            target.pulse.value,
            PulseFacet,
        ):
            raise ConfigError(f"{operation} requires readable pulse state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.pulse.value, target.output.value

    @staticmethod
    def _source_v2_sweep_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[BasicWaveFacet, SweepFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.basic.availability is not Availability.VALUE or not isinstance(
            target.basic.value,
            BasicWaveFacet,
        ):
            raise ConfigError(f"{operation} requires readable basic state")
        if target.sweep.availability is not Availability.VALUE or not isinstance(
            target.sweep.value,
            SweepFacet,
        ):
            raise ConfigError(f"{operation} requires readable sweep state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.basic.value, target.sweep.value, target.output.value

    def _source_v2_fire_target(
        self,
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        feature: SourceFeature,
        operation: str,
    ) -> tuple[BasicWaveFacet, BurstFacet | SweepFacet, OutputFacet]:
        if feature is SourceFeature.SWEEP:
            return self._source_v2_sweep_target(
                snapshot,
                channel,
                operation=operation,
            )
        if feature is SourceFeature.BURST:
            basic, output = self._source_v2_target(
                snapshot,
                channel,
                operation=operation,
            )
            burst, _ = self._source_v2_burst_target(
                snapshot,
                channel,
                operation=operation,
            )
            return basic, burst, output
        raise ValueError("source fire feature must be Burst or Sweep")

    @staticmethod
    def _source_v2_arbitrary_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[ArbitraryFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.arbitrary.availability is not Availability.VALUE or not isinstance(
            target.arbitrary.value,
            ArbitraryFacet,
        ):
            raise ConfigError(f"{operation} requires readable arbitrary selection state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.arbitrary.value, target.output.value

    @staticmethod
    def _source_v2_arbitrary_select_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[BasicWaveFacet, ArbitraryFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.basic.availability is not Availability.VALUE or not isinstance(
            target.basic.value,
            BasicWaveFacet,
        ):
            raise ConfigError(f"{operation} requires readable basic state")
        arbitrary, output = SourceService._source_v2_arbitrary_target(
            snapshot,
            channel,
            operation=operation,
        )
        return target.basic.value, arbitrary, output

    @staticmethod
    def _source_v2_harmonic_preflight_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[Observed[HarmonicFacet], OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.harmonics.availability is Availability.VALUE and not isinstance(
            target.harmonics.value,
            HarmonicFacet,
        ):
            raise ConfigError(f"{operation} harmonic state has an invalid type")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.harmonics, target.output.value

    @staticmethod
    def _source_v2_modulation_preflight_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[Observed[ModulationFacet], OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.modulation.availability is Availability.VALUE and not isinstance(
            target.modulation.value,
            ModulationFacet,
        ):
            raise ConfigError(f"{operation} modulation state has an invalid type")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.modulation, target.output.value

    @staticmethod
    def _source_v2_burst_preflight_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[Observed[BurstFacet], OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.burst.availability is Availability.VALUE and not isinstance(
            target.burst.value,
            BurstFacet,
        ):
            raise ConfigError(f"{operation} burst state has an invalid type")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.burst, target.output.value

    @staticmethod
    def _source_v2_pulse_preflight_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[Observed[PulseFacet], OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.pulse.availability is Availability.VALUE and not isinstance(
            target.pulse.value,
            PulseFacet,
        ):
            raise ConfigError(f"{operation} pulse state has an invalid type")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.pulse, target.output.value

    @staticmethod
    def _source_v2_sweep_preflight_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> tuple[BasicWaveFacet, Observed[SweepFacet], OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.basic.availability is not Availability.VALUE or not isinstance(
            target.basic.value,
            BasicWaveFacet,
        ):
            raise ConfigError(f"{operation} requires readable basic state")
        if target.sweep.availability is Availability.VALUE and not isinstance(
            target.sweep.value,
            SweepFacet,
        ):
            raise ConfigError(f"{operation} sweep state has an invalid type")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.basic.value, target.sweep, target.output.value

    @staticmethod
    def _source_v2_output_target(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> OutputFacet:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError(f"{operation} target channel is absent from snapshot")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError(f"{operation} requires readable output state")
        return target.output.value

    def _validate_source_basic_v2_preflight(
        self,
        request: SourceBasicConfigureRequest,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        output: OutputFacet,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError("source.basic_configure_v2 requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE:
            raise ConfigError("source.basic_configure_v2 requires target output OFF")
        if output.enabled.value is not False:
            raise _SourceV2BasicRequiresLiveMutation(
                "source.basic_configure_v2 requires target output OFF"
            )
        if not any(
            feature.feature is SourceFeature.BASIC
            and feature.scope is SourceFacetScope.CHANNEL
            and request.channel in feature.channels
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.CONFIGURE in feature.directions
            for feature in snapshot.runtime_profile.features
        ):
            raise ConfigError(
                "source.basic_configure_v2 is not available for the runtime target channel"
            )
        runtime_basic = next(
            feature
            for feature in snapshot.runtime_profile.features
            if feature.feature is SourceFeature.BASIC
            and feature.scope is SourceFacetScope.CHANNEL
            and request.channel in feature.channels
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.CONFIGURE in feature.directions
        )
        assert isinstance(runtime_basic.profile, SourceBasicCapabilityProfile)
        if (
            request.patch.waveform_kind.action is PatchAction.SET
            and request.patch.waveform_kind.value not in runtime_basic.profile.waveform_kinds
        ):
            raise ConfigError(
                "source.basic_configure_v2 waveform_kind is not supported by the runtime profile"
            )
        try:
            current_vpp, current_offset = self._source_v2_amplitude_offset(
                basic,
                operation="source.basic_configure_v2",
            )
        except ConfigError as exc:
            if request.patch.waveform_kind.action is not PatchAction.SET:
                raise
            raise _SourceV2BasicLegacyFallback(
                "source.basic_configure_v2 cannot losslessly represent the current "
                "V1 waveform state"
            ) from exc
        patch = request.patch
        requested_vpp = (
            float(patch.amplitude_vpp.value)
            if patch.amplitude_vpp.action is PatchAction.SET
            else current_vpp
        )
        requested_offset = (
            float(patch.offset_v.value)
            if patch.offset_v.action is PatchAction.SET
            else current_offset
        )
        self._check_source_v2_final_output_limits(
            requested_vpp,
            requested_offset,
            operation="source.basic_configure_v2",
        )

    def _validate_source_basic_live_v2_preflight(
        self,
        request: SourceBasicConfigureRequest,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.basic_live_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires target output ON")
        if output.enabled.value is not True:
            raise _SourceV2BasicRequiresOffMutation(
                f"{operation} requires target output ON"
            )
        runtime_basic = next(
            (
                feature
                for feature in snapshot.runtime_profile.features
                if feature.feature is SourceFeature.BASIC
                and feature.scope is SourceFacetScope.CHANNEL
                and request.channel in feature.channels
                and feature.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in feature.directions
            ),
            None,
        )
        if runtime_basic is None or not isinstance(
            runtime_basic.profile,
            SourceBasicCapabilityProfile,
        ):
            raise ConfigError(
                f"{operation} is not available for the runtime target channel"
            )
        patch = request.patch
        set_fields = tuple(
            name
            for name, value in (
                ("waveform_kind", patch.waveform_kind),
                ("frequency_hz", patch.frequency_hz),
                ("amplitude_vpp", patch.amplitude_vpp),
                ("offset_v", patch.offset_v),
                ("square_duty_cycle_percent", patch.square_duty_cycle_percent),
            )
            if value.action is PatchAction.SET
        )
        if len(set_fields) != 1 or set_fields[0] not in {
            "frequency_hz",
            "amplitude_vpp",
        }:
            raise ConfigError(
                f"{operation} requires exactly one frequency_hz or amplitude_vpp SET"
            )
        profile = runtime_basic.profile
        if set_fields[0] == "frequency_hz" and not profile.live_frequency_configurable:
            raise ConfigError(f"{operation} frequency_hz is not declared live-configurable")
        if (
            set_fields[0] == "amplitude_vpp"
            and not profile.live_amplitude_vpp_configurable
        ):
            raise ConfigError(f"{operation} amplitude_vpp is not declared live-configurable")
        if (
            basic.frequency_mode.availability is not Availability.VALUE
            or basic.frequency_mode.value is not SourceFrequencyMode.FIXED
        ):
            raise ConfigError(f"{operation} requires fixed frequency mode")
        current_vpp, current_offset = self._source_v2_amplitude_offset(
            basic,
            operation=operation,
        )
        requested_vpp = (
            float(patch.amplitude_vpp.value)
            if patch.amplitude_vpp.action is PatchAction.SET
            else current_vpp
        )
        self._check_source_v2_final_output_limits(
            requested_vpp,
            current_offset,
            operation=operation,
        )

    @staticmethod
    def _source_harmonic_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
        direction: SourceFeatureDirection = SourceFeatureDirection.CONFIGURE,
    ) -> SourceHarmonicCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.HARMONICS
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and direction in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceHarmonicCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_harmonic_v2_preflight(
        self,
        request: SourceHarmonicConfigureRequest,
        snapshot: SourceSnapshotV2,
        harmonics: Observed[HarmonicFacet],
        output: OutputFacet,
    ) -> None:
        del harmonics
        operation = "source.harmonics_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_harmonic_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if not profile.minimum_order <= request.order <= profile.maximum_order:
            raise ConfigError(f"{operation} order is not supported by the runtime profile")
        if request.preset not in profile.presets:
            raise ConfigError(f"{operation} preset is not supported by the runtime profile")
        if not profile.configured_order_readable or not profile.preset_readable:
            raise ConfigError(f"{operation} requires configured order and preset readback")

    def _validate_source_harmonic_disable_v2_preflight(
        self,
        request: SourceHarmonicDisableRequest,
        snapshot: SourceSnapshotV2,
        harmonics: Observed[HarmonicFacet],
        output: OutputFacet,
    ) -> HarmonicFacet:
        operation = "source.harmonics_disable_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        self._source_harmonic_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
            direction=SourceFeatureDirection.DISABLE,
        )
        if harmonics.availability is not Availability.VALUE or not isinstance(
            harmonics.value,
            HarmonicFacet,
        ):
            raise ConfigError(f"{operation} requires readable harmonic state")
        if harmonics.value.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable harmonic enabled state")
        return harmonics.value

    @staticmethod
    def _validate_source_harmonic_v2_readback(
        request: SourceHarmonicConfigureRequest,
        harmonics: HarmonicFacet,
        *,
        operation: str,
    ) -> None:
        if harmonics.enabled.availability is not Availability.VALUE or harmonics.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports harmonics disabled")
        if (
            harmonics.configured_order.availability is not Availability.VALUE
            or harmonics.configured_order.value != request.order
        ):
            raise ConfigError(f"{operation} configured order readback does not match request")
        if (
            harmonics.preset.availability is not Availability.VALUE
            or harmonics.preset.value is not request.preset
        ):
            raise ConfigError(f"{operation} preset readback does not match request")

    def _validate_source_harmonic_v2_result(
        self,
        request: SourceHarmonicConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.harmonics_configure_v2"
        if not isinstance(result, SourceHarmonicConfigureResult):
            raise ConfigError(
                "configure_source_harmonics_v2() returned an invalid SourceHarmonicConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_harmonic_v2_readback(
            request,
            result.harmonics,
            operation=operation,
        )

    def _validate_source_harmonic_v2_postcondition(
        self,
        request: SourceHarmonicConfigureRequest,
        result: SourceHarmonicConfigureResult,
        snapshot: SourceSnapshotV2,
        harmonics: HarmonicFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.harmonics_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_harmonic_v2_readback(request, harmonics, operation=operation)
        result_order = result.harmonics.configured_order.value
        result_preset = result.harmonics.preset.value
        assert result_order is not None and result_preset is not None
        if (result_order, result_preset) != (
            harmonics.configured_order.value,
            harmonics.preset.value,
        ):
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _validate_source_harmonic_disable_v2_readback(
        harmonics: HarmonicFacet,
        *,
        operation: str,
    ) -> None:
        if harmonics.enabled.availability is not Availability.VALUE or harmonics.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports harmonics enabled")

    def _validate_source_harmonic_disable_v2_result(
        self,
        request: SourceHarmonicDisableRequest,
        result: object,
    ) -> None:
        operation = "source.harmonics_disable_v2"
        if not isinstance(result, SourceHarmonicDisableResult):
            raise ConfigError(
                "disable_source_harmonics_v2() returned an invalid SourceHarmonicDisableResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_harmonic_disable_v2_readback(
            result.harmonics,
            operation=operation,
        )

    def _validate_source_harmonic_disable_v2_postcondition(
        self,
        request: SourceHarmonicDisableRequest,
        result: SourceHarmonicDisableResult,
        snapshot: SourceSnapshotV2,
        harmonics: HarmonicFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.harmonics_disable_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_harmonic_disable_v2_readback(
            result.harmonics,
            operation=operation,
        )
        self._validate_source_harmonic_disable_v2_readback(
            harmonics,
            operation=operation,
        )

    @staticmethod
    def _source_modulation_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceModulationCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.MODULATION
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceModulationCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_modulation_v2_preflight(
        self,
        request: SourceModulationConfigureRequest,
        snapshot: SourceSnapshotV2,
        modulation: Observed[ModulationFacet],
        output: OutputFacet,
    ) -> None:
        del modulation
        operation = "source.modulation_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_modulation_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceModulationKind.AM not in profile.kinds:
            raise ConfigError(f"{operation} internal AM is not supported by the runtime profile")
        if SourceModulationSource.INTERNAL not in profile.sources:
            raise ConfigError(f"{operation} internal source is not supported by the runtime profile")
        if SourceModulationParameterKind.DEPTH_PERCENT not in profile.parameter_kinds:
            raise ConfigError(f"{operation} AM depth is not supported by the runtime profile")
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} requires configured modulation readback")

    @staticmethod
    def _validate_source_modulation_v2_readback(
        request: SourceModulationConfigureRequest,
        modulation: ModulationFacet,
        *,
        operation: str,
    ) -> None:
        if modulation.enabled.availability is not Availability.VALUE or modulation.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports modulation disabled")
        if (
            modulation.kind.availability is not Availability.VALUE
            or modulation.kind.value is not SourceModulationKind.AM
        ):
            raise ConfigError(f"{operation} postcondition does not report AM")
        if (
            modulation.source.availability is not Availability.VALUE
            or modulation.source.value is not SourceModulationSource.INTERNAL
        ):
            raise ConfigError(f"{operation} postcondition does not report internal modulation")
        expected_parameter = SourceModulationParameter(
            SourceModulationParameterKind.DEPTH_PERCENT,
            request.depth_percent,
        )
        if (
            modulation.parameters.availability is not Availability.VALUE
            or modulation.parameters.value != (expected_parameter,)
        ):
            raise ConfigError(f"{operation} depth readback does not match request")
        if (
            modulation.internal_frequency_hz.availability is not Availability.VALUE
            or modulation.internal_frequency_hz.value != request.internal_frequency_hz
        ):
            raise ConfigError(f"{operation} internal frequency readback does not match request")
        if (
            modulation.internal_waveform_kind.availability is not Availability.VALUE
            or modulation.internal_waveform_kind.value is not SourceWaveformKind.SINE
        ):
            raise ConfigError(f"{operation} internal waveform readback does not match scope")

    def _validate_source_modulation_v2_result(
        self,
        request: SourceModulationConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.modulation_configure_v2"
        if not isinstance(result, SourceModulationConfigureResult):
            raise ConfigError(
                "configure_source_modulation_v2() returned an invalid "
                "SourceModulationConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_modulation_v2_readback(
            request,
            result.modulation,
            operation=operation,
        )

    def _validate_source_modulation_v2_postcondition(
        self,
        request: SourceModulationConfigureRequest,
        result: SourceModulationConfigureResult,
        snapshot: SourceSnapshotV2,
        modulation: ModulationFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.modulation_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_modulation_v2_readback(request, modulation, operation=operation)
        if result.modulation != modulation:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _source_pm_modulation_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceModulationCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.MODULATION
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceModulationCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_pm_modulation_v2_preflight(
        self,
        request: SourcePmModulationConfigureRequest,
        snapshot: SourceSnapshotV2,
        modulation: Observed[ModulationFacet],
        output: OutputFacet,
    ) -> None:
        del modulation
        operation = "source.modulation_pm_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_pm_modulation_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceModulationKind.PM not in profile.kinds:
            raise ConfigError(f"{operation} internal PM is not supported by the runtime profile")
        if SourceModulationSource.INTERNAL not in profile.sources:
            raise ConfigError(f"{operation} internal source is not supported by the runtime profile")
        if SourceModulationParameterKind.PHASE_DEVIATION_DEG not in profile.parameter_kinds:
            raise ConfigError(
                f"{operation} PM phase deviation is not supported by the runtime profile"
            )
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} requires configured modulation readback")

    @staticmethod
    def _validate_source_pm_modulation_v2_readback(
        request: SourcePmModulationConfigureRequest,
        modulation: ModulationFacet,
        *,
        operation: str,
    ) -> None:
        if modulation.enabled.availability is not Availability.VALUE or modulation.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports modulation disabled")
        if (
            modulation.kind.availability is not Availability.VALUE
            or modulation.kind.value is not SourceModulationKind.PM
        ):
            raise ConfigError(f"{operation} postcondition does not report PM")
        if (
            modulation.source.availability is not Availability.VALUE
            or modulation.source.value is not SourceModulationSource.INTERNAL
        ):
            raise ConfigError(f"{operation} postcondition does not report internal modulation")
        expected_parameter = SourceModulationParameter(
            SourceModulationParameterKind.PHASE_DEVIATION_DEG,
            request.phase_deviation_deg,
        )
        if (
            modulation.parameters.availability is not Availability.VALUE
            or modulation.parameters.value != (expected_parameter,)
        ):
            raise ConfigError(f"{operation} phase deviation readback does not match request")
        if (
            modulation.internal_frequency_hz.availability is not Availability.VALUE
            or modulation.internal_frequency_hz.value != request.internal_frequency_hz
        ):
            raise ConfigError(f"{operation} internal frequency readback does not match request")
        if (
            modulation.internal_waveform_kind.availability is not Availability.VALUE
            or modulation.internal_waveform_kind.value is not SourceWaveformKind.SINE
        ):
            raise ConfigError(f"{operation} internal waveform readback does not match scope")

    def _validate_source_pm_modulation_v2_result(
        self,
        request: SourcePmModulationConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.modulation_pm_configure_v2"
        if not isinstance(result, SourcePmModulationConfigureResult):
            raise ConfigError(
                "configure_source_pm_modulation_v2() returned an invalid "
                "SourcePmModulationConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_pm_modulation_v2_readback(
            request,
            result.modulation,
            operation=operation,
        )

    def _validate_source_pm_modulation_v2_postcondition(
        self,
        request: SourcePmModulationConfigureRequest,
        result: SourcePmModulationConfigureResult,
        snapshot: SourceSnapshotV2,
        modulation: ModulationFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.modulation_pm_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_pm_modulation_v2_readback(
            request,
            modulation,
            operation=operation,
        )
        if result.modulation != modulation:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _source_fm_modulation_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceModulationCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.MODULATION
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceModulationCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_fm_modulation_v2_preflight(
        self,
        request: SourceFmModulationConfigureRequest,
        snapshot: SourceSnapshotV2,
        modulation: Observed[ModulationFacet],
        output: OutputFacet,
    ) -> None:
        del modulation
        operation = "source.modulation_fm_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_fm_modulation_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceModulationKind.FM not in profile.kinds:
            raise ConfigError(f"{operation} internal FM is not supported by the runtime profile")
        if SourceModulationSource.INTERNAL not in profile.sources:
            raise ConfigError(f"{operation} internal source is not supported by the runtime profile")
        if SourceModulationParameterKind.FREQUENCY_DEVIATION_HZ not in profile.parameter_kinds:
            raise ConfigError(
                f"{operation} FM frequency deviation is not supported by the runtime profile"
            )
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} requires configured modulation readback")

    @staticmethod
    def _validate_source_fm_modulation_v2_readback(
        request: SourceFmModulationConfigureRequest,
        modulation: ModulationFacet,
        *,
        operation: str,
    ) -> None:
        if modulation.enabled.availability is not Availability.VALUE or modulation.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports modulation disabled")
        if (
            modulation.kind.availability is not Availability.VALUE
            or modulation.kind.value is not SourceModulationKind.FM
        ):
            raise ConfigError(f"{operation} postcondition does not report FM")
        if (
            modulation.source.availability is not Availability.VALUE
            or modulation.source.value is not SourceModulationSource.INTERNAL
        ):
            raise ConfigError(f"{operation} postcondition does not report internal modulation")
        expected_parameter = SourceModulationParameter(
            SourceModulationParameterKind.FREQUENCY_DEVIATION_HZ,
            request.frequency_deviation_hz,
        )
        if (
            modulation.parameters.availability is not Availability.VALUE
            or modulation.parameters.value != (expected_parameter,)
        ):
            raise ConfigError(f"{operation} frequency deviation readback does not match request")
        if (
            modulation.internal_frequency_hz.availability is not Availability.VALUE
            or modulation.internal_frequency_hz.value != request.internal_frequency_hz
        ):
            raise ConfigError(f"{operation} internal frequency readback does not match request")
        if (
            modulation.internal_waveform_kind.availability is not Availability.VALUE
            or modulation.internal_waveform_kind.value is not SourceWaveformKind.SINE
        ):
            raise ConfigError(f"{operation} internal waveform readback does not match scope")

    def _validate_source_fm_modulation_v2_result(
        self,
        request: SourceFmModulationConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.modulation_fm_configure_v2"
        if not isinstance(result, SourceFmModulationConfigureResult):
            raise ConfigError(
                "configure_source_fm_modulation_v2() returned an invalid "
                "SourceFmModulationConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_fm_modulation_v2_readback(
            request,
            result.modulation,
            operation=operation,
        )

    def _validate_source_fm_modulation_v2_postcondition(
        self,
        request: SourceFmModulationConfigureRequest,
        result: SourceFmModulationConfigureResult,
        snapshot: SourceSnapshotV2,
        modulation: ModulationFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.modulation_fm_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_fm_modulation_v2_readback(
            request,
            modulation,
            operation=operation,
        )
        if result.modulation != modulation:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _source_pwm_modulation_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceModulationCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.MODULATION
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceModulationCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_pwm_modulation_v2_preflight(
        self,
        request: SourcePwmModulationConfigureRequest,
        snapshot: SourceSnapshotV2,
        modulation: Observed[ModulationFacet],
        output: OutputFacet,
    ) -> None:
        del modulation
        operation = "source.modulation_pwm_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_pwm_modulation_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceModulationKind.PWM not in profile.kinds:
            raise ConfigError(f"{operation} internal PWM is not supported by the runtime profile")
        if SourceModulationSource.INTERNAL not in profile.sources:
            raise ConfigError(f"{operation} internal source is not supported by the runtime profile")
        if request.deviation_parameter.kind not in profile.parameter_kinds:
            raise ConfigError(
                f"{operation} requested PWM deviation is not supported by the runtime profile"
            )
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} requires configured modulation readback")

    @staticmethod
    def _validate_source_pwm_modulation_v2_readback(
        request: SourcePwmModulationConfigureRequest,
        modulation: ModulationFacet,
        *,
        operation: str,
    ) -> None:
        if modulation.enabled.availability is not Availability.VALUE or modulation.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports modulation disabled")
        if (
            modulation.kind.availability is not Availability.VALUE
            or modulation.kind.value is not SourceModulationKind.PWM
        ):
            raise ConfigError(f"{operation} postcondition does not report PWM")
        if (
            modulation.source.availability is not Availability.VALUE
            or modulation.source.value is not SourceModulationSource.INTERNAL
        ):
            raise ConfigError(f"{operation} postcondition does not report internal modulation")
        if (
            modulation.parameters.availability is not Availability.VALUE
            or modulation.parameters.value != (request.deviation_parameter,)
        ):
            raise ConfigError(f"{operation} deviation readback does not match request")
        if (
            modulation.internal_frequency_hz.availability is not Availability.VALUE
            or modulation.internal_frequency_hz.value != request.internal_frequency_hz
        ):
            raise ConfigError(f"{operation} internal frequency readback does not match request")
        if (
            modulation.internal_waveform_kind.availability is not Availability.VALUE
            or modulation.internal_waveform_kind.value is not SourceWaveformKind.SINE
        ):
            raise ConfigError(f"{operation} internal waveform readback does not match scope")

    def _validate_source_pwm_modulation_v2_result(
        self,
        request: SourcePwmModulationConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.modulation_pwm_configure_v2"
        if not isinstance(result, SourcePwmModulationConfigureResult):
            raise ConfigError(
                "configure_source_pwm_modulation_v2() returned an invalid "
                "SourcePwmModulationConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_pwm_modulation_v2_readback(
            request,
            result.modulation,
            operation=operation,
        )

    def _validate_source_pwm_modulation_v2_postcondition(
        self,
        request: SourcePwmModulationConfigureRequest,
        result: SourcePwmModulationConfigureResult,
        snapshot: SourceSnapshotV2,
        modulation: ModulationFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.modulation_pwm_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_pwm_modulation_v2_readback(
            request,
            modulation,
            operation=operation,
        )
        if result.modulation != modulation:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _source_sweep_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
        direction: SourceFeatureDirection = SourceFeatureDirection.CONFIGURE,
    ) -> SourceSweepCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.SWEEP
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and direction in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceSweepCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    @staticmethod
    def _source_sweep_basic_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceBasicCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.BASIC
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.READ in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceBasicCapabilityProfile):
            raise ConfigError(f"{operation} requires readable basic state at runtime")
        return feature.profile

    def _validate_source_sweep_v2_preflight(
        self,
        request: SourceSweepConfigureRequest,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        sweep: Observed[SweepFacet],
        output: OutputFacet,
    ) -> None:
        del basic, sweep
        operation = "source.sweep_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_sweep_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if request.spacing not in profile.spacing_modes:
            raise ConfigError(f"{operation} spacing is not supported by the runtime profile")
        if request.trigger_source not in profile.trigger_sources:
            raise ConfigError(
                f"{operation} requested trigger source is not supported by the runtime profile"
            )
        if not profile.timing_readable or not profile.marker_readable:
            raise ConfigError(f"{operation} requires sweep timing and marker readback")
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} requires configured internal sweep readback")
        basic_profile = self._source_sweep_basic_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceFrequencyMode.SWEEP not in basic_profile.frequency_modes:
            raise ConfigError(f"{operation} requires sweep frequency mode at runtime")

    @staticmethod
    def _validate_source_sweep_v2_readback(
        request: SourceSweepConfigureRequest,
        basic: BasicWaveFacet,
        sweep: SweepFacet,
        *,
        operation: str,
    ) -> None:
        if (
            basic.frequency_mode.availability is not Availability.VALUE
            or basic.frequency_mode.value is not SourceFrequencyMode.SWEEP
        ):
            raise ConfigError(f"{operation} postcondition does not report sweep frequency mode")
        if sweep.enabled.availability is not Availability.VALUE or sweep.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports sweep disabled")
        for label, observed, expected in (
            ("start frequency", sweep.start_hz, request.start_hz),
            ("stop frequency", sweep.stop_hz, request.stop_hz),
            ("spacing", sweep.spacing, request.spacing),
            ("steps", sweep.steps, request.steps),
            ("sweep time", sweep.sweep_time_s, request.sweep_time_s),
        ):
            if observed.availability is not Availability.VALUE or observed.value != expected:
                raise ConfigError(f"{operation} {label} readback does not match request")
        for label, observed in (
            ("start hold", sweep.start_hold_s),
            ("stop hold", sweep.stop_hold_s),
            ("return time", sweep.return_time_s),
        ):
            if observed.availability is not Availability.VALUE or observed.value != 0.0:
                raise ConfigError(f"{operation} {label} readback does not match scope")
        if sweep.trigger.availability is not Availability.VALUE or not isinstance(
            sweep.trigger.value,
            SourceTriggerState,
        ):
            raise ConfigError(f"{operation} trigger readback does not match scope")
        trigger = sweep.trigger.value
        if (
            trigger.source.availability is not Availability.VALUE
            or trigger.source.value is not request.trigger_source
        ):
            raise ConfigError(f"{operation} trigger source does not match request")
        if (
            trigger.slope.availability is not Availability.VALUE
            or trigger.slope.value is not SourceTriggerSlope.POSITIVE
        ):
            raise ConfigError(f"{operation} trigger slope does not match scope")
        if (
            trigger.output.availability is not Availability.VALUE
            or trigger.output.value is not SourceTriggerOutput.OFF
        ):
            raise ConfigError(f"{operation} trigger output does not match scope")
        if sweep.marker.availability is not Availability.VALUE or not isinstance(
            sweep.marker.value,
            SourceSweepMarker,
        ):
            raise ConfigError(f"{operation} marker readback does not match scope")
        marker = sweep.marker.value
        if marker.enabled.availability is not Availability.VALUE or marker.enabled.value is not False:
            raise ConfigError(f"{operation} marker state does not match scope")

    def _validate_source_sweep_v2_result(
        self,
        request: SourceSweepConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.sweep_configure_v2"
        if not isinstance(result, SourceSweepConfigureResult):
            raise ConfigError(
                "configure_source_sweep_v2() returned an invalid SourceSweepConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_sweep_v2_readback(
            request,
            result.basic,
            result.sweep,
            operation=operation,
        )

    def _validate_source_sweep_v2_postcondition(
        self,
        request: SourceSweepConfigureRequest,
        result: SourceSweepConfigureResult,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        sweep: SweepFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.sweep_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_sweep_v2_readback(request, basic, sweep, operation=operation)
        if result.basic != basic or result.sweep != sweep:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _source_arbitrary_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceArbitraryCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.ARBITRARY
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceArbitraryCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    @staticmethod
    def _source_arbitrary_basic_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceBasicCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.BASIC
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.READ in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceBasicCapabilityProfile):
            raise ConfigError(f"{operation} requires readable basic state at runtime")
        return feature.profile

    @staticmethod
    def _validate_source_arbitrary_storage_payload(
        request: SourceArbitraryStorageRequest,
        payload: object,
    ) -> None:
        if not isinstance(payload, bytes):
            raise ConfigError("source.arbitrary_storage_v2 payload must be bytes")
        if len(payload) != request.payload_size_bytes:
            raise ConfigError(
                "source.arbitrary_storage_v2 payload size does not match the request"
            )
        digest = "sha256:" + sha256(payload).hexdigest()
        if digest != request.payload_sha256:
            raise ConfigError(
                "source.arbitrary_storage_v2 payload SHA-256 does not match the request"
            )

    @staticmethod
    def _read_source_arbitrary_storage_v2_slot(
        source: SourceDriver,
        channel: int,
        slot_id: str,
        *,
        operation: str,
    ) -> SourceArbitraryStorageSlot:
        slot = cast(
            SourceArbitraryStorageV2Driver,
            source,
        ).read_source_arbitrary_storage_v2(channel, slot_id)
        if not isinstance(slot, SourceArbitraryStorageSlot):
            raise ConfigError(
                "read_source_arbitrary_storage_v2() returned an invalid "
                "SourceArbitraryStorageSlot"
            )
        if slot.channel != channel or slot.slot_id != slot_id:
            raise ConfigError(f"{operation} storage slot readback does not match the target")
        return slot

    def _validate_source_arbitrary_storage_v2_preflight(
        self,
        request: SourceArbitraryStorageRequest,
        snapshot: SourceSnapshotV2,
        arbitrary: ArbitraryFacet,
        output: OutputFacet,
        slot: SourceArbitraryStorageSlot,
    ) -> None:
        del arbitrary
        operation = "source.arbitrary_storage_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable output state")
        profile = self._source_arbitrary_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if not profile.selection_readable or not profile.storage_metadata_readable:
            raise ConfigError(f"{operation} requires readable selected ARB metadata")
        if not profile.storage_slot_metadata_readable:
            raise ConfigError(f"{operation} requires readable named storage slots")
        if request.write_mode not in profile.storage_write_modes:
            raise ConfigError(f"{operation} write mode is not supported by the runtime profile")
        maximum = profile.storage_max_payload_bytes
        if maximum is None or request.payload_size_bytes > maximum:
            raise ConfigError(f"{operation} payload size exceeds the runtime profile")
        if request.write_mode is SourceStorageWriteMode.CREATE_ONLY:
            if slot.exists:
                raise ConfigError(f"{operation} create-only target slot is not empty")
            return
        if not slot.exists or slot.payload_sha256 != request.expected_previous_sha256:
            raise ConfigError(f"{operation} expected previous storage digest does not match")

    @staticmethod
    def _validate_source_arbitrary_storage_v2_result(
        request: SourceArbitraryStorageRequest,
        result: object,
    ) -> None:
        operation = "source.arbitrary_storage_v2"
        if not isinstance(result, SourceArbitraryStorageResult):
            raise ConfigError(
                "mutate_source_arbitrary_storage_v2() returned an invalid "
                "SourceArbitraryStorageResult"
            )
        if (
            result.channel != request.channel
            or result.slot_id != request.slot_id
            or result.payload_sha256 != request.payload_sha256
            or result.payload_size_bytes != request.payload_size_bytes
        ):
            raise ConfigError(f"{operation} result does not match the request")
        if not result.write_completed or not result.readback_verified:
            raise ConfigError(f"{operation} result does not prove the write and readback")

    @staticmethod
    def _validate_source_arbitrary_storage_v2_postcondition(
        request: SourceArbitraryStorageRequest,
        result: SourceArbitraryStorageResult,
        preflight_snapshot: SourceSnapshotV2,
        preflight_arbitrary: ArbitraryFacet,
        preflight_output: OutputFacet,
        postcondition_snapshot: SourceSnapshotV2,
        postcondition_arbitrary: ArbitraryFacet,
        postcondition_output: OutputFacet,
        postcondition_slot: SourceArbitraryStorageSlot,
    ) -> None:
        operation = "source.arbitrary_storage_v2"
        if (
            preflight_snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT
            or postcondition_snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT
        ):
            raise ConfigError(f"{operation} requires consistent preflight and postcondition snapshots")
        if preflight_arbitrary != postcondition_arbitrary:
            raise ConfigError(f"{operation} must not change arbitrary selection")
        if preflight_output != postcondition_output:
            raise ConfigError(f"{operation} must not change output state")
        if (
            not postcondition_slot.exists
            or postcondition_slot.payload_sha256 != request.payload_sha256
            or postcondition_slot.payload_size_bytes != request.payload_size_bytes
        ):
            raise ConfigError(f"{operation} storage readback does not match the request")
        if (
            result.payload_sha256 != postcondition_slot.payload_sha256
            or result.payload_size_bytes != postcondition_slot.payload_size_bytes
        ):
            raise ConfigError(f"{operation} result does not match storage readback")

    def _validate_source_arbitrary_select_v2_preflight(
        self,
        request: SourceArbitrarySelectRequest,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        arbitrary: ArbitraryFacet,
        output: OutputFacet,
    ) -> None:
        del basic, arbitrary
        operation = "source.arbitrary_select_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_arbitrary_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if not profile.selection_readable or not profile.storage_metadata_readable:
            raise ConfigError(f"{operation} requires selected waveform and storage digest readback")
        if request.playback_mode not in profile.playback_modes:
            raise ConfigError(f"{operation} playback mode is not supported by the runtime profile")
        if (
            request.playback_mode is SourceArbitraryPlaybackMode.TRUE_ARB
            and not profile.sample_rate_readable
        ):
            raise ConfigError(f"{operation} true-ARB requires readable sample rate")
        basic_profile = self._source_arbitrary_basic_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceWaveformKind.ARBITRARY not in basic_profile.waveform_kinds:
            raise ConfigError(f"{operation} requires arbitrary basic waveform support")

    @staticmethod
    def _validate_source_arbitrary_select_v2_readback(
        request: SourceArbitrarySelectRequest,
        basic: BasicWaveFacet,
        arbitrary: ArbitraryFacet,
        *,
        operation: str,
    ) -> None:
        if (
            basic.waveform_kind.availability is not Availability.VALUE
            or basic.waveform_kind.value is not SourceWaveformKind.ARBITRARY
        ):
            raise ConfigError(f"{operation} basic waveform readback is not arbitrary")
        if (
            arbitrary.selected_waveform_id.availability is not Availability.VALUE
            or arbitrary.selected_waveform_id.value != request.slot_id
        ):
            raise ConfigError(f"{operation} selected waveform readback does not match request")
        if (
            arbitrary.playback_mode.availability is not Availability.VALUE
            or arbitrary.playback_mode.value is not request.playback_mode
        ):
            raise ConfigError(f"{operation} playback mode readback does not match request")
        if arbitrary.storage_digest.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires storage digest readback")
        if request.playback_mode is SourceArbitraryPlaybackMode.DDS:
            if (
                arbitrary.playback_frequency_hz.availability is not Availability.VALUE
                or arbitrary.playback_frequency_hz.value != request.playback_frequency_hz
            ):
                raise ConfigError(
                    f"{operation} DDS playback frequency readback does not match request"
                )
            return
        if (
            arbitrary.sample_rate_hz.availability is not Availability.VALUE
            or arbitrary.sample_rate_hz.value != request.sample_rate_hz
        ):
            raise ConfigError(f"{operation} true-ARB sample rate readback does not match request")

    def _validate_source_arbitrary_select_v2_result(
        self,
        request: SourceArbitrarySelectRequest,
        result: object,
    ) -> None:
        operation = "source.arbitrary_select_v2"
        if not isinstance(result, SourceArbitrarySelectResult):
            raise ConfigError(
                "select_source_arbitrary_v2() returned an invalid SourceArbitrarySelectResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_arbitrary_select_v2_readback(
            request,
            result.basic,
            result.arbitrary,
            operation=operation,
        )

    def _validate_source_arbitrary_select_v2_postcondition(
        self,
        request: SourceArbitrarySelectRequest,
        result: SourceArbitrarySelectResult,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        arbitrary: ArbitraryFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.arbitrary_select_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_arbitrary_select_v2_readback(
            request,
            basic,
            arbitrary,
            operation=operation,
        )
        if result.basic != basic or result.arbitrary != arbitrary:
            raise ConfigError(f"{operation} result readback does not match postcondition")

    @staticmethod
    def _validate_source_arbitrary_volatile_replace_v2_payload(
        request: SourceArbitraryVolatileReplaceRequest,
        payload: object,
    ) -> None:
        if not isinstance(payload, bytes):
            raise ConfigError("source.arbitrary_volatile_replace_v2 payload must be bytes")
        if len(payload) != request.payload_size_bytes:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 payload size does not match the request"
            )
        digest = "sha256:" + sha256(payload).hexdigest()
        if digest != request.payload_sha256:
            raise ConfigError(
                "source.arbitrary_volatile_replace_v2 payload SHA-256 does not match the request"
            )

    def _validate_source_arbitrary_volatile_replace_v2_preflight(
        self,
        request: SourceArbitraryVolatileReplaceRequest,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        arbitrary: ArbitraryFacet,
        output: OutputFacet,
    ) -> None:
        del basic, arbitrary
        operation = "source.arbitrary_volatile_replace_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_arbitrary_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if not profile.selection_readable:
            raise ConfigError(f"{operation} requires readable selected ARB state")
        minimum = profile.volatile_replace_min_points
        maximum = profile.volatile_replace_max_points
        payload_maximum = profile.volatile_replace_max_payload_bytes
        if minimum is None or maximum is None or payload_maximum is None:
            raise ConfigError(f"{operation} requires volatile replace limits in the runtime profile")
        if request.point_count < minimum or request.point_count > maximum:
            raise ConfigError(f"{operation} point count exceeds the runtime profile")
        if request.payload_size_bytes > payload_maximum:
            raise ConfigError(f"{operation} payload size exceeds the runtime profile")
        basic_profile = self._source_arbitrary_basic_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceWaveformKind.ARBITRARY not in basic_profile.waveform_kinds:
            raise ConfigError(f"{operation} requires arbitrary basic waveform support")

    @staticmethod
    def _validate_source_arbitrary_volatile_replace_v2_result(
        request: SourceArbitraryVolatileReplaceRequest,
        result: object,
    ) -> None:
        operation = "source.arbitrary_volatile_replace_v2"
        if not isinstance(result, SourceArbitraryVolatileReplaceResult):
            raise ConfigError(
                "replace_source_arbitrary_volatile_v2() returned an invalid "
                "SourceArbitraryVolatileReplaceResult"
            )
        if (
            result.channel != request.channel
            or result.payload_sha256 != request.payload_sha256
            or result.payload_size_bytes != request.payload_size_bytes
            or result.point_count != request.point_count
        ):
            raise ConfigError(f"{operation} result does not match the request")
        if not result.write_completed:
            raise ConfigError(f"{operation} result does not prove the write")

    @staticmethod
    def _validate_source_arbitrary_volatile_replace_v2_postcondition(
        result: SourceArbitraryVolatileReplaceResult,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        arbitrary: ArbitraryFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.arbitrary_volatile_replace_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        if (
            basic.waveform_kind.availability is not Availability.VALUE
            or basic.waveform_kind.value is not SourceWaveformKind.ARBITRARY
        ):
            raise ConfigError(f"{operation} basic waveform readback is not arbitrary")
        if (
            arbitrary.selected_waveform_id.availability is not Availability.VALUE
            or arbitrary.selected_waveform_id.value != result.selected_waveform_id
        ):
            raise ConfigError(f"{operation} selected waveform readback does not match result")

    @staticmethod
    def _source_counter_configuration_field(
        request: SourceCounterConfigureRequest,
    ) -> SourceCounterConfigurationField:
        fields = tuple(
            field
            for field, patch_value in (
                (SourceCounterConfigurationField.COUPLING, request.patch.coupling),
                (SourceCounterConfigurationField.IMPEDANCE_OHM, request.patch.impedance_ohm),
                (SourceCounterConfigurationField.ATTENUATION, request.patch.attenuation),
                (SourceCounterConfigurationField.TRIGGER_LEVEL_V, request.patch.trigger_level_v),
                (SourceCounterConfigurationField.STATISTICS_ENABLED, request.patch.statistics_enabled),
            )
            if patch_value.action is PatchAction.SET
        )
        if len(fields) != 1:  # pragma: no cover - request model already enforces this.
            raise ConfigError("source.counter_configure_v2 requires exactly one Counter field")
        return fields[0]

    @staticmethod
    def _source_counter_configuration_expected_value(
        request: SourceCounterConfigureRequest,
        field: SourceCounterConfigurationField,
    ) -> object:
        values = {
            SourceCounterConfigurationField.COUPLING: request.patch.coupling.value,
            SourceCounterConfigurationField.IMPEDANCE_OHM: request.patch.impedance_ohm.value,
            SourceCounterConfigurationField.ATTENUATION: request.patch.attenuation.value,
            SourceCounterConfigurationField.TRIGGER_LEVEL_V: request.patch.trigger_level_v.value,
            SourceCounterConfigurationField.STATISTICS_ENABLED: request.patch.statistics_enabled.value,
        }
        value = values[field]
        if value is None:  # pragma: no cover - request model already enforces SET values.
            raise ConfigError("source.counter_configure_v2 Counter value is missing")
        return value

    @staticmethod
    def _source_counter_configuration_observed_value(
        state: SourceCounterInputState,
        field: SourceCounterConfigurationField,
        *,
        operation: str,
    ) -> object:
        observed = {
            SourceCounterConfigurationField.COUPLING: state.coupling,
            SourceCounterConfigurationField.IMPEDANCE_OHM: state.impedance_ohm,
            SourceCounterConfigurationField.ATTENUATION: state.attenuation,
            SourceCounterConfigurationField.TRIGGER_LEVEL_V: state.trigger_level_v,
            SourceCounterConfigurationField.STATISTICS_ENABLED: state.statistics_enabled,
        }[field]
        if observed.availability is not Availability.VALUE:
            raise ConfigError(
                f"{operation} requires readable {field.value} Counter configuration"
            )
        return observed.value

    def _validate_source_counter_configure_v2_preflight(
        self,
        request: SourceCounterConfigureRequest,
        snapshot: SourceSnapshotV2,
        counter: SourceCounterInputState,
        profile: SourceCounterCapabilityProfile,
    ) -> bool:
        operation = "source.counter_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} requires readable Counter configuration")
        field = self._source_counter_configuration_field(request)
        if field not in profile.configurable_fields:
            raise ConfigError(f"{operation} field is not configurable in the runtime profile")
        current = self._source_counter_configuration_observed_value(
            counter,
            field,
            operation=operation,
        )
        return current != self._source_counter_configuration_expected_value(request, field)

    def _validate_source_counter_configure_v2_result(
        self,
        request: SourceCounterConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.counter_configure_v2"
        if not isinstance(result, SourceCounterConfigureResult):
            raise ConfigError(
                "configure_source_counter_v2() returned an invalid SourceCounterConfigureResult"
            )
        if result.input_id != request.input_id:
            raise ConfigError(f"{operation} result input_id does not match request")
        field = self._source_counter_configuration_field(request)
        if self._source_counter_configuration_observed_value(
            result.state,
            field,
            operation=operation,
        ) != self._source_counter_configuration_expected_value(request, field):
            raise ConfigError(f"{operation} result does not match the request")

    def _validate_source_counter_configure_v2_postcondition(
        self,
        request: SourceCounterConfigureRequest,
        result: SourceCounterConfigureResult,
        snapshot: SourceSnapshotV2,
        counter: SourceCounterInputState,
        profile: SourceCounterCapabilityProfile,
    ) -> None:
        operation = "source.counter_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if not profile.configuration_readable:
            raise ConfigError(f"{operation} postcondition lacks readable Counter configuration")
        self._validate_source_counter_configure_v2_result(request, result)
        field = self._source_counter_configuration_field(request)
        if self._source_counter_configuration_observed_value(
            counter,
            field,
            operation=operation,
        ) != self._source_counter_configuration_expected_value(request, field):
            raise ConfigError(f"{operation} readback does not match the request")

    @staticmethod
    def _validate_source_counter_enable_v2_preflight(
        request: SourceCounterEnableRequest,
        snapshot: SourceSnapshotV2,
        counter: SourceCounterInputState,
        profile: SourceCounterCapabilityProfile,
        *,
        operation: str,
    ) -> bool:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if not profile.enabled_configurable:
            raise ConfigError(f"{operation} is not configurable in the runtime profile")
        if counter.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable Counter enabled state")
        return counter.enabled.value is not request.enabled

    @staticmethod
    def _validate_source_counter_enable_v2_result(
        request: SourceCounterEnableRequest,
        result: object,
        *,
        operation: str,
    ) -> None:
        if not isinstance(result, SourceCounterEnableResult):
            raise ConfigError(
                "set_source_counter_enabled_v2() returned an invalid SourceCounterEnableResult"
            )
        if result.input_id != request.input_id or result.enabled is not request.enabled:
            raise ConfigError(f"{operation} result does not match the request")

    def _validate_source_counter_enable_v2_postcondition(
        self,
        request: SourceCounterEnableRequest,
        result: SourceCounterEnableResult,
        snapshot: SourceSnapshotV2,
        counter: SourceCounterInputState,
        profile: SourceCounterCapabilityProfile,
        *,
        operation: str,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if not profile.enabled_configurable:
            raise ConfigError(f"{operation} postcondition lacks runtime enable support")
        self._validate_source_counter_enable_v2_result(request, result, operation=operation)
        if counter.enabled.availability is not Availability.VALUE or (
            counter.enabled.value is not request.enabled
        ):
            raise ConfigError(f"{operation} enabled readback does not match the request")

    @staticmethod
    def _validate_source_counter_measure_v2_preflight(
        snapshot: SourceSnapshotV2,
        counter: SourceCounterInputState,
        profile: SourceCounterCapabilityProfile,
        *,
        operation: str,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if counter.enabled.availability is not Availability.VALUE or counter.enabled.value is not True:
            raise ConfigError(f"{operation} requires Counter enabled")
        if profile.query_effect not in {
            SourceQueryEffect.PURE_READ,
            SourceQueryEffect.STATEFUL_CONSUMING_READ,
        }:
            raise ConfigError(f"{operation} requires a known read-only Counter query effect")

    @staticmethod
    def _validate_source_counter_measure_v2_result(
        request: SourceCounterMeasureRequest,
        result: object,
        profile: SourceCounterCapabilityProfile,
        *,
        operation: str,
    ) -> None:
        if not isinstance(result, SourceCounterMeasureResult):
            raise ConfigError(
                "measure_source_counter_v2() returned an invalid SourceCounterMeasureResult"
            )
        if result.input_id != request.input_id:
            raise ConfigError(f"{operation} result input_id does not match request")
        if not {
            measurement.kind for measurement in result.measurements
        } <= set(profile.measurement_kinds):
            raise ConfigError(f"{operation} result includes an unsupported measurement kind")

    @staticmethod
    def _source_burst_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
        direction: SourceFeatureDirection = SourceFeatureDirection.CONFIGURE,
    ) -> SourceBurstCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.BURST
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and direction in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourceBurstCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_burst_v2_preflight(
        self,
        request: SourceBurstConfigureRequest,
        snapshot: SourceSnapshotV2,
        burst: Observed[BurstFacet],
        output: OutputFacet,
    ) -> None:
        del burst
        operation = "source.burst_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_burst_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourceBurstMode.TRIGGERED not in profile.modes:
            raise ConfigError(f"{operation} triggered mode is not supported by the runtime profile")
        if request.trigger_source not in profile.trigger_sources:
            raise ConfigError(
                f"{operation} requested trigger source is not supported by the runtime profile"
            )
        if not profile.timing_readable:
            raise ConfigError(f"{operation} requires burst timing readback")
        configuration_readable = (
            profile.triggered_internal_configuration_readable
            if request.trigger_source is SourceTriggerSource.INTERNAL
            else profile.triggered_manual_configuration_readable
        )
        if not configuration_readable:
            raise ConfigError(
                f"{operation} requires configured {request.trigger_source.value} "
                "triggered burst readback"
            )

    @staticmethod
    def _validate_source_burst_v2_readback(
        request: SourceBurstConfigureRequest,
        burst: BurstFacet,
        *,
        operation: str,
    ) -> None:
        if burst.enabled.availability is not Availability.VALUE or burst.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports burst disabled")
        if (
            burst.mode.availability is not Availability.VALUE
            or burst.mode.value is not SourceBurstMode.TRIGGERED
        ):
            raise ConfigError(f"{operation} postcondition does not report triggered mode")
        for label, observed, expected in (
            ("cycles", burst.cycles, request.cycles),
            ("phase", burst.phase_deg, request.phase_deg),
            ("internal period", burst.internal_period_s, request.internal_period_s),
            ("delay", burst.delay_s, request.delay_s),
        ):
            if observed.availability is not Availability.VALUE or observed.value != expected:
                raise ConfigError(f"{operation} {label} readback does not match request")
        if burst.trigger.availability is not Availability.VALUE or not isinstance(
            burst.trigger.value,
            SourceTriggerState,
        ):
            raise ConfigError(f"{operation} trigger readback does not match scope")
        trigger = burst.trigger.value
        if (
            trigger.source.availability is not Availability.VALUE
            or trigger.source.value is not request.trigger_source
        ):
            raise ConfigError(f"{operation} trigger source does not match request")
        if (
            trigger.slope.availability is not Availability.VALUE
            or trigger.slope.value is not SourceTriggerSlope.POSITIVE
        ):
            raise ConfigError(f"{operation} trigger slope does not match scope")
        if (
            trigger.output.availability is not Availability.VALUE
            or trigger.output.value is not SourceTriggerOutput.OFF
        ):
            raise ConfigError(f"{operation} trigger output does not match scope")

    def _validate_source_burst_v2_result(
        self,
        request: SourceBurstConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.burst_configure_v2"
        if not isinstance(result, SourceBurstConfigureResult):
            raise ConfigError(
                "configure_source_burst_v2() returned an invalid SourceBurstConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_burst_v2_readback(
            request,
            result.burst,
            operation=operation,
        )

    def _validate_source_burst_v2_postcondition(
        self,
        request: SourceBurstConfigureRequest,
        result: SourceBurstConfigureResult,
        snapshot: SourceSnapshotV2,
        burst: BurstFacet,
        output: OutputFacet,
    ) -> None:
        del result
        operation = "source.burst_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_burst_v2_readback(request, burst, operation=operation)

    def _validate_source_fire_v2_preflight(
        self,
        request: SourceFireRequest,
        *,
        feature: SourceFeature,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        feature_state: BurstFacet | SweepFacet,
        output: OutputFacet,
        configuration_digest: str,
        operation: str,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not True:
            raise ConfigError(f"{operation} requires target output ON")
        if feature is SourceFeature.BURST:
            if not isinstance(feature_state, BurstFacet):
                raise ConfigError(f"{operation} requires readable burst state")
            profile = self._source_burst_runtime_profile(
                snapshot,
                channel=request.channel,
                operation=operation,
                direction=SourceFeatureDirection.FIRE,
            )
            if (
                SourceBurstMode.TRIGGERED not in profile.modes
                or SourceTriggerSource.MANUAL not in profile.trigger_sources
                or not profile.timing_readable
                or not profile.triggered_manual_configuration_readable
            ):
                raise ConfigError(f"{operation} runtime Burst profile cannot prove fire scope")
        elif feature is SourceFeature.SWEEP:
            if not isinstance(feature_state, SweepFacet):
                raise ConfigError(f"{operation} requires readable sweep state")
            profile = self._source_sweep_runtime_profile(
                snapshot,
                channel=request.channel,
                operation=operation,
                direction=SourceFeatureDirection.FIRE,
            )
            if (
                SourceTriggerSource.MANUAL not in profile.trigger_sources
                or not profile.timing_readable
                or not profile.marker_readable
                or not profile.configuration_readable
            ):
                raise ConfigError(f"{operation} runtime Sweep profile cannot prove fire scope")
            if (
                basic.frequency_mode.availability is not Availability.VALUE
                or basic.frequency_mode.value is not SourceFrequencyMode.SWEEP
            ):
                raise ConfigError(f"{operation} requires sweep frequency mode")
        else:  # pragma: no cover - private callers pass one of the two declared features.
            raise ValueError("source fire feature must be Burst or Sweep")
        trigger = feature_state.trigger
        if (
            trigger.availability is not Availability.VALUE
            or not isinstance(trigger.value, SourceTriggerState)
            or trigger.value.source.availability is not Availability.VALUE
            or trigger.value.source.value is not SourceTriggerSource.MANUAL
        ):
            raise ConfigError(f"{operation} requires manual trigger source")
        if source_v2_digest(feature_state) != configuration_digest:
            raise ConfigError(
                f"{operation} configured feature state no longer matches the same-session receipt"
            )
        vpp, offset = self._source_v2_amplitude_offset(basic, operation=operation)
        self._check_source_v2_final_output_limits(vpp, offset, operation=operation)

    @staticmethod
    def _validate_source_fire_v2_result(
        request: SourceFireRequest,
        result: object,
        *,
        operation: str,
    ) -> None:
        if not isinstance(result, SourceFireResult):
            raise ConfigError(f"{operation} driver returned an invalid SourceFireResult")
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")

    def _validate_source_fire_v2_postcondition(
        self,
        request: SourceFireRequest,
        *,
        feature: SourceFeature,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        feature_state: BurstFacet | SweepFacet,
        output: OutputFacet,
        configuration_digest: str,
        operation: str,
    ) -> None:
        self._validate_source_fire_v2_preflight(
            request,
            feature=feature,
            snapshot=snapshot,
            basic=basic,
            feature_state=feature_state,
            output=output,
            configuration_digest=configuration_digest,
            operation=operation,
        )

    @staticmethod
    def _source_pulse_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourcePulseCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.PULSE
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
            ),
            None,
        )
        if feature is None or not isinstance(feature.profile, SourcePulseCapabilityProfile):
            raise ConfigError(f"{operation} is not available for the runtime target channel")
        return feature.profile

    def _validate_source_pulse_v2_preflight(
        self,
        request: SourcePulseConfigureRequest,
        snapshot: SourceSnapshotV2,
        pulse: Observed[PulseFacet],
        output: OutputFacet,
    ) -> None:
        del pulse
        operation = "source.pulse_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} requires target output OFF")
        profile = self._source_pulse_runtime_profile(
            snapshot,
            channel=request.channel,
            operation=operation,
        )
        if SourcePulseHoldBasis.WIDTH not in profile.hold_modes:
            raise ConfigError(f"{operation} WIDTH hold is not supported by the runtime profile")
        if not profile.delay_readable or not profile.transitions_readable:
            raise ConfigError(f"{operation} requires delay and transition readback")
        if not profile.width_configuration_readable:
            raise ConfigError(f"{operation} requires configured WIDTH pulse readback")

    @staticmethod
    def _validate_source_pulse_v2_readback(
        request: SourcePulseConfigureRequest,
        pulse: PulseFacet,
        *,
        operation: str,
    ) -> None:
        if (
            pulse.hold_basis.availability is not Availability.VALUE
            or pulse.hold_basis.value is not SourcePulseHoldBasis.WIDTH
        ):
            raise ConfigError(f"{operation} postcondition does not report WIDTH hold")
        for label, observed, expected in (
            ("width", pulse.width_s, request.width_s),
            ("delay", pulse.delay_s, request.delay_s),
            ("leading transition", pulse.leading_transition_s, request.leading_transition_s),
            ("trailing transition", pulse.trailing_transition_s, request.trailing_transition_s),
        ):
            if observed.availability is not Availability.VALUE or observed.value != expected:
                raise ConfigError(f"{operation} {label} readback does not match request")

    def _validate_source_pulse_v2_result(
        self,
        request: SourcePulseConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.pulse_configure_v2"
        if not isinstance(result, SourcePulseConfigureResult):
            raise ConfigError(
                "configure_source_pulse_v2() returned an invalid SourcePulseConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if result.output_enabled:
            raise ConfigError(f"{operation} result reports output ON")
        self._validate_source_pulse_v2_readback(
            request,
            result.pulse,
            operation=operation,
        )

    def _validate_source_pulse_v2_postcondition(
        self,
        request: SourcePulseConfigureRequest,
        result: SourcePulseConfigureResult,
        snapshot: SourceSnapshotV2,
        pulse: PulseFacet,
        output: OutputFacet,
    ) -> None:
        del result
        operation = "source.pulse_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError(f"{operation} postcondition reports output ON")
        self._validate_source_pulse_v2_readback(request, pulse, operation=operation)

    def _validate_source_basic_v2_result(
        self,
        request: SourceBasicConfigureRequest,
        result: object,
    ) -> None:
        if not isinstance(result, SourceBasicConfigureResult):
            raise ConfigError(
                "configure_source_basic_v2() returned an invalid SourceBasicConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError("source.basic_configure_v2 result channel does not match request")
        if result.output_enabled:
            raise ConfigError("source.basic_configure_v2 result reports output ON")
        vpp, offset = self._source_v2_amplitude_offset(
            result.basic,
            operation="source.basic_configure_v2",
        )
        self._check_source_v2_final_output_limits(
            vpp,
            offset,
            operation="source.basic_configure_v2",
        )
        self._validate_source_basic_v2_patch_readback(request, result.basic)

    def _validate_source_basic_live_v2_result(
        self,
        request: SourceBasicConfigureRequest,
        result: object,
    ) -> None:
        operation = "source.basic_live_configure_v2"
        if not isinstance(result, SourceBasicLiveConfigureResult):
            raise ConfigError(
                "configure_source_basic_live_v2() returned an invalid "
                "SourceBasicLiveConfigureResult"
            )
        if result.channel != request.channel:
            raise ConfigError(f"{operation} result channel does not match request")
        if not result.output_enabled:
            raise ConfigError(f"{operation} result reports output OFF")
        vpp, offset = self._source_v2_amplitude_offset(
            result.basic,
            operation=operation,
        )
        self._check_source_v2_final_output_limits(vpp, offset, operation=operation)
        self._validate_source_basic_v2_patch_readback(
            request,
            result.basic,
            operation=operation,
        )

    def _validate_source_basic_v2_postcondition(
        self,
        request: SourceBasicConfigureRequest,
        result: SourceBasicConfigureResult,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        output: OutputFacet,
    ) -> None:
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError("source.basic_configure_v2 postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError("source.basic_configure_v2 postcondition reports output ON")
        self._validate_source_basic_v2_patch_readback(request, basic)
        result_vpp, result_offset = self._source_v2_amplitude_offset(
            result.basic,
            operation="source.basic_configure_v2",
        )
        post_vpp, post_offset = self._source_v2_amplitude_offset(
            basic,
            operation="source.basic_configure_v2",
        )
        if (result_vpp, result_offset) != (post_vpp, post_offset):
            raise ConfigError(
                "source.basic_configure_v2 final amplitude or offset readback does not match"
            )
        self._check_source_v2_final_output_limits(
            post_vpp,
            post_offset,
            operation="source.basic_configure_v2",
        )

    def _validate_source_basic_live_v2_postcondition(
        self,
        request: SourceBasicConfigureRequest,
        result: SourceBasicLiveConfigureResult,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.basic_live_configure_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports output OFF")
        self._validate_source_basic_v2_patch_readback(
            request,
            basic,
            operation=operation,
        )
        result_vpp, result_offset = self._source_v2_amplitude_offset(
            result.basic,
            operation=operation,
        )
        post_vpp, post_offset = self._source_v2_amplitude_offset(
            basic,
            operation=operation,
        )
        if (result_vpp, result_offset) != (post_vpp, post_offset):
            raise ConfigError(
                f"{operation} final amplitude or offset readback does not match"
            )
        self._check_source_v2_final_output_limits(
            post_vpp,
            post_offset,
            operation=operation,
        )

    @staticmethod
    def _source_v2_amplitude_offset(
        basic: BasicWaveFacet,
        *,
        operation: str,
    ) -> tuple[float, float]:
        amplitude = basic.amplitude
        if amplitude.availability is not Availability.VALUE or not isinstance(
            amplitude.value,
            SourceAmplitude,
        ):
            raise ConfigError(f"{operation} requires a final Vpp amplitude")
        if amplitude.value.unit is not SourceAmplitudeUnit.VPP:
            raise ConfigError(f"{operation} requires a final Vpp amplitude")
        offset = basic.offset_v
        if offset.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires a final offset")
        return float(amplitude.value.value), float(offset.value)

    @staticmethod
    def _validate_source_basic_v2_patch_readback(
        request: SourceBasicConfigureRequest,
        basic: BasicWaveFacet,
        *,
        operation: str = "source.basic_configure_v2",
    ) -> None:
        patch = request.patch
        values = (
            ("waveform_kind", patch.waveform_kind, basic.waveform_kind),
            ("frequency_hz", patch.frequency_hz, basic.frequency_hz),
            ("offset_v", patch.offset_v, basic.offset_v),
            (
                "square_duty_cycle_percent",
                patch.square_duty_cycle_percent,
                basic.square_duty_cycle_percent,
            ),
        )
        for name, patch_value, observed in values:
            if patch_value.action is not PatchAction.SET:
                continue
            if observed.availability is not Availability.VALUE or observed.value != patch_value.value:
                raise ConfigError(
                    f"{operation} {name} readback does not match request"
                )
        if patch.amplitude_vpp.action is PatchAction.SET:
            actual_vpp, _ = SourceService._source_v2_amplitude_offset(
                basic,
                operation=operation,
            )
            if actual_vpp != patch.amplitude_vpp.value:
                raise ConfigError(
                    f"{operation} amplitude_vpp readback does not match request"
                )

    def _check_source_v2_final_output_limits(
        self,
        vpp: float,
        offset: float,
        *,
        operation: str,
    ) -> None:
        self._check_source_vpp(vpp, field=f"{operation} final amplitude")
        limits = self.config.safety_limits
        minimum = limits.min_source_port_voltage_v
        maximum = limits.max_source_port_voltage_v
        if minimum is None or maximum is None:
            return
        low = offset - (vpp / 2.0)
        high = offset + (vpp / 2.0)
        if low < minimum or high > maximum:
            raise ConfigError(
                f"{operation} final port voltage exceeds configured limits"
            )

    @staticmethod
    def _validate_source_output_v2_runtime_direction(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        direction: SourceFeatureDirection,
        operation: str,
    ) -> None:
        if not any(
            feature.feature is SourceFeature.OUTPUT
            and feature.scope is SourceFacetScope.CHANNEL
            and channel in feature.channels
            and feature.support is SupportState.SUPPORTED
            and direction in feature.directions
            for feature in snapshot.runtime_profile.features
        ):
            raise ConfigError(f"{operation} is not available for the runtime target channel")

    def _validate_source_output_enable_preflight(
        self,
        request: SourceOutputRequest,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        output: OutputFacet,
    ) -> bool:
        operation = "source.output_enable_v2"
        self._validate_source_output_v2_runtime_direction(
            snapshot,
            channel=request.channel,
            direction=SourceFeatureDirection.ENABLE,
            operation=operation,
        )
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} requires a fresh consistent snapshot")
        if output.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable output state")
        vpp, offset = self._source_v2_amplitude_offset(basic, operation=operation)
        self._check_source_v2_final_output_limits(vpp, offset, operation=operation)
        if output.enabled.value is True:
            return False
        self._reject_active_cross_channel_relations(snapshot, request.channel, operation=operation)
        return True

    @staticmethod
    def _validate_source_output_disable_preflight(
        request: SourceOutputRequest,
        snapshot: SourceSnapshotV2,
        output: OutputFacet,
    ) -> bool:
        operation = "source.output_disable_v2"
        SourceService._validate_source_output_v2_runtime_direction(
            snapshot,
            channel=request.channel,
            direction=SourceFeatureDirection.DISABLE,
            operation=operation,
        )
        if output.enabled.availability is not Availability.VALUE:
            raise ConfigError(f"{operation} requires readable output state")
        return output.enabled.value is not False

    @staticmethod
    def _reject_active_cross_channel_relations(
        snapshot: SourceSnapshotV2,
        channel: int,
        *,
        operation: str,
    ) -> None:
        cross_channel = snapshot.cross_channel
        if cross_channel.availability is not Availability.VALUE:
            return
        for relation in getattr(cross_channel.value, "relations", ()):
            enabled = getattr(relation, "enabled", None)
            if (
                channel in getattr(relation, "channels", ())
                and getattr(enabled, "availability", None) is Availability.VALUE
                and getattr(enabled, "value", None) is True
            ):
                raise ConfigError(
                    f"{operation} requires M6-C handling for an active cross-channel relation"
                )

    def _validate_source_output_v2_result(
        self,
        request: SourceOutputRequest,
        result: object,
        *,
        operation: str,
    ) -> None:
        if not isinstance(result, SourceOutputResult):
            raise ConfigError(f"set_source_output_v2() returned an invalid result for {operation}")
        if result.channel != request.channel or result.enabled is not request.enabled:
            raise ConfigError(f"{operation} result does not match the requested output state")
        if not request.enabled:
            return
        if result.final_amplitude is None or result.final_offset_v is None:
            raise ConfigError(f"{operation} requires final Vpp and Offset readback")
        self._check_source_v2_final_output_limits(
            result.final_amplitude.value,
            result.final_offset_v,
            operation=operation,
        )

    def _source_output_v2_noop_result(
        self,
        request: SourceOutputRequest,
        snapshot: SourceSnapshotV2,
        *,
        operation: str,
    ) -> SourceOutputResult:
        if not request.enabled:
            return SourceOutputResult(channel=request.channel, enabled=False)
        basic, _ = self._source_v2_target(
            snapshot,
            request.channel,
            operation=operation,
        )
        vpp, offset = self._source_v2_amplitude_offset(basic, operation=operation)
        return SourceOutputResult(
            channel=request.channel,
            enabled=True,
            final_amplitude=SourceAmplitude(vpp, SourceAmplitudeUnit.VPP),
            final_offset_v=offset,
        )

    def _validate_source_output_enable_postcondition(
        self,
        result: SourceOutputResult,
        snapshot: SourceSnapshotV2,
        basic: BasicWaveFacet,
        output: OutputFacet,
    ) -> None:
        operation = "source.output_enable_v2"
        if snapshot.consistency.state is not SnapshotConsistencyState.CONSISTENT:
            raise ConfigError(f"{operation} postcondition snapshot is inconsistent")
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not True:
            raise ConfigError(f"{operation} postcondition reports output OFF")
        self._reject_active_cross_channel_relations(snapshot, result.channel, operation=operation)
        vpp, offset = self._source_v2_amplitude_offset(basic, operation=operation)
        if (
            result.final_amplitude is None
            or result.final_offset_v is None
            or (result.final_amplitude.value, result.final_offset_v) != (vpp, offset)
        ):
            raise ConfigError(f"{operation} final Vpp or Offset readback does not match")
        self._check_source_v2_final_output_limits(vpp, offset, operation=operation)

    @staticmethod
    def _validate_source_output_disable_postcondition(output: OutputFacet) -> None:
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError("source.output_disable_v2 postcondition reports output ON")

    def _recover_source_v2_output_off(
        self,
        context: SourceOperationContextCoordinator,
        source: SourceDriver,
        channel: int,
        extensions: SourceDescriptorExtensions,
        output_field: SourceFieldRef,
        *,
        operation: str,
    ) -> dict[str, object]:
        descriptor = self.descriptor
        session_state = self.session_state
        if session_state is None or session_state.health is SessionHealth.POISONED:
            return {"status": "not_attempted", "reason": "session_poisoned"}
        if descriptor is None or "source.output_v2" not in descriptor.capabilities:
            return {"status": "not_attempted", "reason": "output_capability_unavailable"}
        if not callable(getattr(source, "set_source_output_v2", None)):
            return {"status": "not_attempted", "reason": "output_method_unavailable"}
        try:
            safe_state = context.make_phase_spec(
                SourceOperationPhase.FAILURE_SAFE_STATE,
                allowed_io={"write"},
                fields=(output_field,),
                max_steps=1,
            )
            with context.authorize_phase(safe_state):
                result = cast(SourceOutputV2Driver, source).set_source_output_v2(
                    SourceOutputRequest(channel=channel, enabled=False)
                )
                if (
                    not isinstance(result, SourceOutputResult)
                    or result.channel != channel
                    or result.enabled
                ):
                    raise ConfigError(f"{operation} recovery OFF is not proven")
        except BaseException:
            return {
                "status": "off_failed",
                "session_health": session_state.health.value,
            }
        if session_state.health is SessionHealth.POISONED:
            return {"status": "off_sent_unverified", "reason": "session_poisoned"}
        try:
            verification = context.make_phase_spec(
                SourceOperationPhase.CLEANUP_VERIFICATION,
                allowed_io={"query"},
                fields=(output_field,),
                max_steps=extensions.query_contract.max_queries,
            )
            with context.authorize_phase(verification) as authorization:
                snapshot = self._snapshot_v2_with_open_source(
                    source,
                    correlation_id=context.correlation_id,
                    allow_uncertain_session=True,
                    deadline=authorization.deadline,
                )
                output = self._source_v2_output_target(
                    snapshot,
                    channel,
                    operation=operation,
                )
                if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
                    raise ConfigError(f"{operation} recovery OFF readback is not proven")
                context.mark_safe_state_verified(
                    authorization,
                    io_kind="query",
                    fields=(output_field,),
                )
        except BaseException:
            return {
                "status": "off_sent_unverified",
                "session_health": session_state.health.value,
            }
        return {
            "status": "off_verified",
            "session_health": session_state.health.value,
        }

    def _recover_source_v2_outputs_off(
        self,
        context: SourceOperationContextCoordinator,
        source: SourceDriver,
        channels: tuple[int, ...],
        extensions: SourceDescriptorExtensions,
        output_fields: tuple[SourceFieldRef, ...],
        *,
        operation: str,
    ) -> dict[str, object]:
        """Issue one bounded V2 OFF attempt for each already-frozen affected port."""

        descriptor = self.descriptor
        session_state = self.session_state
        if session_state is None or session_state.health is SessionHealth.POISONED:
            return {"status": "not_attempted", "reason": "session_poisoned"}
        if descriptor is None or "source.output_v2" not in descriptor.capabilities:
            return {"status": "not_attempted", "reason": "output_capability_unavailable"}
        if not callable(getattr(source, "set_source_output_v2", None)):
            return {"status": "not_attempted", "reason": "output_method_unavailable"}
        if len(channels) != len(output_fields) or len(channels) > context.operation_contract.recovery_max_steps:
            return {"status": "not_attempted", "reason": "recovery_range_unavailable"}
        try:
            safe_state = context.make_phase_spec(
                SourceOperationPhase.FAILURE_SAFE_STATE,
                allowed_io={"write"},
                fields=output_fields,
                max_steps=len(channels),
            )
            with context.authorize_phase(safe_state):
                for channel in channels:
                    result = cast(SourceOutputV2Driver, source).set_source_output_v2(
                        SourceOutputRequest(channel=channel, enabled=False)
                    )
                    if (
                        not isinstance(result, SourceOutputResult)
                        or result.channel != channel
                        or result.enabled
                    ):
                        raise ConfigError(f"{operation} recovery OFF is not proven")
        except BaseException:
            return {
                "status": "off_failed",
                "channels": list(channels),
                "session_health": session_state.health.value,
            }
        if session_state.health is SessionHealth.POISONED:
            return {
                "status": "off_sent_unverified",
                "channels": list(channels),
                "reason": "session_poisoned",
            }
        try:
            verification = context.make_phase_spec(
                SourceOperationPhase.CLEANUP_VERIFICATION,
                allowed_io={"query"},
                fields=output_fields,
                max_steps=extensions.query_contract.max_queries,
            )
            with context.authorize_phase(verification) as authorization:
                snapshot = self._snapshot_v2_with_open_source(
                    source,
                    correlation_id=context.correlation_id,
                    allow_uncertain_session=True,
                    deadline=authorization.deadline,
                )
                for channel in channels:
                    output = self._source_v2_output_target(
                        snapshot,
                        channel,
                        operation=operation,
                    )
                    if (
                        output.enabled.availability is not Availability.VALUE
                        or output.enabled.value is not False
                    ):
                        raise ConfigError(f"{operation} recovery OFF readback is not proven")
                context.mark_safe_state_verified(
                    authorization,
                    io_kind="query",
                    fields=output_fields,
                )
        except BaseException:
            return {
                "status": "off_sent_unverified",
                "channels": list(channels),
                "session_health": session_state.health.value,
            }
        return {
            "status": "off_verified",
            "channels": list(channels),
            "session_health": session_state.health.value,
        }

    def _source_basic_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceBasicConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceBasicConfigureResult | SourceBasicLiveConfigureResult | None,
        recovery: dict[str, object] | None = None,
        capability: str = "source.basic_configure_v2",
        output_expected: str = "off",
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": capability,
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": output_expected,
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_harmonic_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceHarmonicConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceHarmonicConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.harmonics_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_harmonic_disable_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceHarmonicDisableRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceHarmonicDisableResult | None,
        wrote_main: bool,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.harmonics_disable_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {
                "status": "written" if wrote_main else "already_at_target",
                "result": source_v2_to_data(result),
            }
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_modulation_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceModulationConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceModulationConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.modulation_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_pm_modulation_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourcePmModulationConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourcePmModulationConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.modulation_pm_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_fm_modulation_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceFmModulationConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceFmModulationConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.modulation_fm_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_pwm_modulation_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourcePwmModulationConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourcePwmModulationConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.modulation_pwm_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_sweep_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceSweepConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceSweepConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.sweep_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        request_data = cast(dict[str, object], source_v2_to_data(request))
        if request.trigger_source is SourceTriggerSource.INTERNAL:
            request_data = dict(request_data)
            request_data.pop("trigger_source", None)
        artifact["request"] = request_data
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_arbitrary_storage_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceArbitraryStorageRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        preflight_slot: SourceArbitraryStorageSlot | None,
        postcondition_slot: SourceArbitraryStorageSlot | None,
        result: SourceArbitraryStorageResult | None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.arbitrary_storage_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
                "storage_slot": (
                    None
                    if preflight_slot is None
                    else source_v2_to_data(preflight_slot)
                ),
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
                "storage_slot": (
                    None
                    if postcondition_slot is None
                    else source_v2_to_data(postcondition_slot)
                ),
            }
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "unchanged",
            "selection_expected": "unchanged",
            "storage_readback_verified": postcondition_slot is not None,
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_arbitrary_select_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceArbitrarySelectRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceArbitrarySelectResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.arbitrary_select_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_arbitrary_volatile_replace_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceArbitraryVolatileReplaceRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceArbitraryVolatileReplaceResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.arbitrary_volatile_replace_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
            "selection_expected": None if result is None else result.selected_waveform_id,
            "content_readback_verified": (
                None if result is None else result.content_readback_verified
            ),
            "previous_content": (
                "restorable"
                if result is not None and result.previous_content_restorable
                else "unrecoverable"
            ),
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_counter_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceCounterConfigureRequest | SourceCounterEnableRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceCounterConfigureResult | SourceCounterEnableResult | None,
        wrote_main: bool,
        capability: str,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": capability,
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_input_id": request.input_id,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {
                "status": "written" if wrote_main else "already_at_target",
                "result": source_v2_to_data(result),
            }
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "counter_input_id": request.input_id,
            "automatic_rollback": "not_available",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_burst_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceBurstConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceBurstConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.burst_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        request_data = cast(dict[str, object], source_v2_to_data(request))
        if request.trigger_source is SourceTriggerSource.INTERNAL:
            request_data = dict(request_data)
            request_data.pop("trigger_source", None)
        artifact["request"] = request_data
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_fire_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceFireRequest,
        feature: SourceFeature,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceFireResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        capability = (
            "source.burst_fire_v2"
            if feature is SourceFeature.BURST
            else "source.sweep_fire_v2"
        )
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": capability,
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        artifact["persistent_session_verified"] = True
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {
                "result": source_v2_to_data(result),
                "command_completed": True,
            }
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
                "emission_verified": False,
                "external_measurement_required": True,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off" if recovery is not None else "on",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for declared_feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in declared_feature.evidence_refs
            }
        )
        return artifact

    def _source_pulse_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourcePulseConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourcePulseConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.pulse_configure_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {"result": source_v2_to_data(result)}
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_cross_channel_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: object,
        operation: str,
        closure: _SourceCrossChannelClosure,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceCrossChannelConfigureResult | None,
        wrote_main: bool,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": operation,
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "channels": list(closure.relation.channels),
                "affected_channels": list(closure.affected_channels),
                "relation_graph_digest": source_v2_digest(closure.relation_graph),
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {
                "status": "written" if wrote_main else "already_at_target",
                "result": source_v2_to_data(result),
            }
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "affected_outputs_expected": "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _source_output_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceOutputRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceOutputResult | None,
        wrote_main: bool,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.output_v2",
            "contract_version": SOURCE_CONTRACT_VERSION,
            "descriptor_digest": descriptor_digest,
        }
        artifact["request"] = source_v2_to_data(request)
        if preflight_snapshot is not None:
            artifact["preflight"] = {
                "target_channel": request.channel,
                "snapshot_digest": source_v2_digest(preflight_snapshot),
                "consistency": preflight_snapshot.consistency.state.value,
            }
        if result is not None:
            artifact["mutation"] = {
                "status": "written" if wrote_main else "already_at_target",
                "result": source_v2_to_data(result),
            }
        if postcondition_snapshot is not None:
            artifact["postcondition"] = {
                "snapshot_digest": source_v2_digest(postcondition_snapshot),
                "consistency": postcondition_snapshot.consistency.state.value,
            }
        if recovery is not None:
            artifact["recovery"] = dict(recovery)
        artifact["final_state"] = {
            "session_health": context.session_state.health.value,
            "output_expected": "on" if request.enabled else "off",
        }
        artifact["evidence_refs"] = sorted(
            {
                evidence_ref
                for feature in (
                    ()
                    if preflight_snapshot is None
                    else preflight_snapshot.runtime_profile.features
                )
                for evidence_ref in feature.evidence_refs
            }
        )
        return artifact

    def _attach_source_basic_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_basic_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_harmonic_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_harmonic_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_harmonic_disable_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_harmonic_disable_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_modulation_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_modulation_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_pm_modulation_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_pm_modulation_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_fm_modulation_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_fm_modulation_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_pwm_modulation_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_pwm_modulation_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_sweep_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_sweep_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_arbitrary_storage_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_arbitrary_storage_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_arbitrary_select_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_arbitrary_select_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_arbitrary_volatile_replace_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_arbitrary_volatile_replace_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_counter_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_counter_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_burst_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_burst_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_fire_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_fire_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_pulse_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_pulse_v2_artifact(**kwargs))
        except Exception:
            pass

    def _attach_source_cross_channel_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(
                exc,
                "source_operation_artifact",
                self._source_cross_channel_v2_artifact(**kwargs),
            )
        except Exception:
            pass

    def _attach_source_output_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_output_v2_artifact(**kwargs))
        except Exception:
            pass

    @staticmethod
    def _source_v2_waveform_from_v1(function: str) -> SourceWaveformKind:
        aliases = {
            "SIN": SourceWaveformKind.SINE,
            "SINE": SourceWaveformKind.SINE,
            "SQU": SourceWaveformKind.SQUARE,
            "SQUARE": SourceWaveformKind.SQUARE,
            "RAMP": SourceWaveformKind.RAMP,
            "TRI": SourceWaveformKind.RAMP,
            "TRIANGLE": SourceWaveformKind.RAMP,
            "PULS": SourceWaveformKind.PULSE,
            "PULSE": SourceWaveformKind.PULSE,
            "NOIS": SourceWaveformKind.NOISE,
            "NOISE": SourceWaveformKind.NOISE,
            "DC": SourceWaveformKind.DC,
        }
        normalized = function.strip().upper()
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ConfigError(
                "source.set_function cannot map this waveform to source.basic_configure_v2"
            ) from exc

    @staticmethod
    def _source_status_from_v2_snapshot(
        snapshot: SourceSnapshotV2,
        channel: int,
    ) -> SourceStatus:
        """Flatten a V2 readback only for a legacy V1 return value."""

        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError("Source V2 postcondition does not contain the target channel")

        basic = (
            target.basic.value
            if target.basic.availability is Availability.VALUE
            and isinstance(target.basic.value, BasicWaveFacet)
            else None
        )
        output = (
            target.output.value
            if target.output.availability is Availability.VALUE
            and isinstance(target.output.value, OutputFacet)
            else None
        )

        def observed_value(value: object) -> object | None:
            return (
                getattr(value, "value", None)
                if getattr(value, "availability", None) is Availability.VALUE
                else None
            )

        waveform_codes = {
            SourceWaveformKind.SINE: "SIN",
            SourceWaveformKind.SQUARE: "SQU",
            SourceWaveformKind.RAMP: "RAMP",
            SourceWaveformKind.PULSE: "PULS",
            SourceWaveformKind.NOISE: "NOIS",
            SourceWaveformKind.DC: "DC",
            SourceWaveformKind.ARBITRARY: "ARB",
            SourceWaveformKind.OTHER: "OTHER",
        }
        waveform = None if basic is None else observed_value(basic.waveform_kind)
        function = waveform_codes.get(waveform, "UNKNOWN")
        frequency = None if basic is None else observed_value(basic.frequency_hz)
        offset = None if basic is None else observed_value(basic.offset_v)
        phase = None if basic is None else observed_value(basic.phase_deg)
        duty = None if basic is None else observed_value(basic.square_duty_cycle_percent)
        amplitude = None if basic is None else observed_value(basic.amplitude)
        amplitude_value = amplitude.value if isinstance(amplitude, SourceAmplitude) else None
        amplitude_unit = amplitude.unit.value.upper() if isinstance(amplitude, SourceAmplitude) else None
        frequency_mode_value = None if basic is None else observed_value(basic.frequency_mode)
        frequency_mode = {
            "fixed": "FIX",
            "sweep": "SWE",
            "list": "LIST",
        }.get(getattr(frequency_mode_value, "value", None), "UNKNOWN")
        enabled = None if output is None else observed_value(output.enabled)

        return SourceStatus(
            channel=channel,
            output="ON" if enabled is True else "OFF" if enabled is False else "UNKNOWN",
            function=function,
            frequency_hz=float(frequency) if isinstance(frequency, (int, float)) else None,
            amplitude=float(amplitude_value) if isinstance(amplitude_value, (int, float)) else None,
            amplitude_unit=amplitude_unit,
            offset_v=float(offset) if isinstance(offset, (int, float)) else None,
            phase_deg=float(phase) if isinstance(phase, (int, float)) else None,
            frequency_mode=frequency_mode,
            sweep_enabled=(
                "ON" if frequency_mode == "SWE" else "OFF" if frequency_mode != "UNKNOWN" else "UNKNOWN"
            ),
            apply_raw=None,
            square_duty_cycle_percent=float(duty) if isinstance(duty, (int, float)) else None,
        )

    def channel_profile(self, channel: int | None = None) -> SourceChannelProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.channel_profile", "source.channel_profile")
        with self._source_session() as source:
            return cast(SourceChannelProfileDriver, source).get_channel_profile(channel)

    def coupling_profile(self) -> SourceCouplingProfile:
        self._require("source.coupling_profile", "source.coupling_profile")
        with self._source_session() as source:
            return cast(SourceCouplingDriver, source).get_coupling_profile()

    def configure_coupling(
        self,
        configuration: SourceCouplingConfiguration,
    ) -> SourceCouplingProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_COUPLING,
            "source.coupling_configure_v2",
        )
        if not isinstance(configuration, SourceCouplingConfiguration):
            raise ConfigError("source coupling configuration must be SourceCouplingConfiguration")
        source_cfg = self._source_config()
        required = ["source.coupling_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.coupling_configure", *required)
        with self._source_session() as source:
            return cast(SourceCouplingDriver, source).configure_coupling(
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def harmonic_profile(self, channel: int) -> SourceHarmonicProfile:
        self._require("source.harmonic_profile", "source.harmonic_profile")
        with self._source_session() as source:
            return cast(SourceHarmonicProfileDriver, source).get_harmonic_profile(channel)

    def configure_harmonics(
        self,
        channel: int,
        configuration: SourceHarmonicConfiguration,
        *,
        check_errors: bool = True,
    ) -> SourceHarmonicProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_HARMONICS,
            "source.harmonics_configure_v2",
            "source.harmonics_disable_v2",
        )
        if not isinstance(configuration, SourceHarmonicConfiguration):
            raise ConfigError("source harmonic configuration must be SourceHarmonicConfiguration")
        required = ["source.harmonic_configure"]
        if check_errors:
            required.append("source.errors")
        self._require("source.harmonic_configure", *required)
        with self._source_session() as source:
            return cast(SourceHarmonicControlDriver, source).configure_harmonics(
                channel,
                configuration,
                check_errors=check_errors,
            )

    def am_modulation_profile(self, channel: int | None = None) -> SourceAmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_am_profile", "source.modulation_am_profile")
        with self._source_session() as source:
            return cast(SourceAmModulationProfileDriver, source).get_am_modulation_profile(channel)

    def configure_am_modulation(
        self,
        configuration: SourceAmModulationConfiguration,
        channel: int | None = None,
    ) -> SourceAmModulationProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_AM,
            "source.modulation_configure_v2",
        )
        if not isinstance(configuration, SourceAmModulationConfiguration):
            raise ConfigError(
                "source AM modulation configuration must be SourceAmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_am_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_am_configure", *required)
        with self._source_session() as source:
            return cast(SourceAmModulationControlDriver, source).configure_am_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def fm_modulation_profile(self, channel: int | None = None) -> SourceFmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_fm_profile", "source.modulation_fm_profile")
        with self._source_session() as source:
            return cast(SourceFmModulationProfileDriver, source).get_fm_modulation_profile(channel)

    def configure_fm_modulation(
        self,
        configuration: SourceFmModulationConfiguration,
        channel: int | None = None,
    ) -> SourceFmModulationProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_FM,
            "source.modulation_fm_configure_v2",
        )
        if not isinstance(configuration, SourceFmModulationConfiguration):
            raise ConfigError(
                "source FM modulation configuration must be SourceFmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_fm_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_fm_configure", *required)
        with self._source_session() as source:
            return cast(SourceFmModulationControlDriver, source).configure_fm_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def pm_modulation_profile(self, channel: int | None = None) -> SourcePmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_pm_profile", "source.modulation_pm_profile")
        with self._source_session() as source:
            return cast(SourcePmModulationProfileDriver, source).get_pm_modulation_profile(channel)

    def configure_pm_modulation(
        self,
        configuration: SourcePmModulationConfiguration,
        channel: int | None = None,
    ) -> SourcePmModulationProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_PM,
            "source.modulation_pm_configure_v2",
        )
        if not isinstance(configuration, SourcePmModulationConfiguration):
            raise ConfigError(
                "source PM modulation configuration must be SourcePmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_pm_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_pm_configure", *required)
        with self._source_session() as source:
            return cast(SourcePmModulationControlDriver, source).configure_pm_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def pwm_modulation_profile(self, channel: int | None = None) -> SourcePwmModulationProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.modulation_pwm_profile", "source.modulation_pwm_profile")
        with self._source_session() as source:
            return cast(SourcePwmModulationProfileDriver, source).get_pwm_modulation_profile(
                channel
            )

    def configure_pwm_modulation(
        self,
        configuration: SourcePwmModulationConfiguration,
        channel: int | None = None,
    ) -> SourcePwmModulationProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_PWM,
            "source.modulation_pwm_configure_v2",
        )
        if not isinstance(configuration, SourcePwmModulationConfiguration):
            raise ConfigError(
                "source PWM modulation configuration must be SourcePwmModulationConfiguration"
            )
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.modulation_pwm_configure"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.modulation_pwm_configure", *required)
        with self._source_session() as source:
            return cast(SourcePwmModulationControlDriver, source).configure_pwm_modulation(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def pulse_profile(self, channel: int | None = None) -> SourcePulseProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.pulse_profile", "source.pulse_profile")
        with self._source_session() as source:
            return cast(SourcePulseProfileDriver, source).get_pulse_profile(channel)

    def burst_profile(self, channel: int | None = None) -> SourceBurstProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.burst_profile", "source.burst_profile")
        with self._source_session() as source:
            return cast(SourceBurstProfileDriver, source).get_burst_profile(channel)

    def sweep_profile(self, channel: int | None = None) -> SourceSweepProfile:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.sweep_profile", "source.sweep_profile")
        with self._source_session() as source:
            return cast(SourceSweepProfileDriver, source).get_sweep_profile(channel)

    def counter_profile(self) -> SourceCounterProfile:
        self._require("source.counter_profile", "source.counter_profile")
        with self._source_session() as source:
            return cast(SourceCounterProfileDriver, source).get_counter_profile()

    def configure_pulse(
        self,
        configuration: SourcePulseConfiguration,
        channel: int | None = None,
    ) -> SourcePulseProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_PULSE,
            "source.pulse_configure_v2",
        )
        if not isinstance(configuration, SourcePulseConfiguration):
            raise ConfigError("source pulse configuration must be SourcePulseConfiguration")
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.pulse_configure", "source.status"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.pulse_configure", *required)
        with self._source_session() as source:
            if source.get_status(channel).output != "OFF":
                raise ConfigError(
                    "controlled source pulse configuration requires output OFF / "
                    "受控信号源脉冲配置要求输出为 OFF"
                )
            return cast(SourcePulseControlDriver, source).configure_pulse(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def configure_burst(
        self,
        configuration: SourceBurstConfiguration,
        channel: int | None = None,
    ) -> SourceBurstProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_BURST,
            "source.burst_configure_v2",
        )
        if not isinstance(configuration, SourceBurstConfiguration):
            raise ConfigError("source burst configuration must be SourceBurstConfiguration")
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.burst_configure", "source.status"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.burst_configure", *required)
        if configuration.trigger_source == "MANUAL" and self.session is None:
            raise ConfigError(
                "manual source burst configuration requires a persistent source session / "
                "手动触发突发配置要求持久信号源会话"
            )
        with self._source_session() as source:
            if source.get_status(channel).output != "OFF":
                raise ConfigError(
                    "controlled source burst configuration requires output OFF / "
                    "受控信号源突发配置要求输出为 OFF"
                )
            return cast(SourceBurstControlDriver, source).configure_burst(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def trigger_burst(self, channel: int | None = None) -> None:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.burst_fire_v2"):
            self.fire_burst_v2(SourceFireRequest(channel=channel))
            return
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.TRIGGER_BURST,
            "source.output_v2",
            "source.burst_configure_v2",
        )
        required = ["source.burst_trigger"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.burst_trigger", *required)
        if self.session is None:
            raise ConfigError(
                "manual source burst trigger requires the persistent source session that "
                "configured it / 手动突发触发必须复用执行配置的持久信号源会话"
            )
        cast(SourceBurstControlDriver, self.session).trigger_burst(
            channel,
            check_errors=source_cfg.check_errors,
        )

    def configure_sweep(
        self,
        configuration: SourceSweepConfiguration,
        channel: int | None = None,
    ) -> SourceSweepProfile:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.CONFIGURE_SWEEP,
            "source.sweep_configure_v2",
        )
        if not isinstance(configuration, SourceSweepConfiguration):
            raise ConfigError("source sweep configuration must be SourceSweepConfiguration")
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        required = ["source.sweep_configure", "source.status"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.sweep_configure", *required)
        if configuration.trigger_source == "MANUAL" and self.session is None:
            raise ConfigError(
                "manual source sweep configuration requires a persistent source session / "
                "手动触发扫频配置要求持久信号源会话"
            )
        with self._source_session() as source:
            status = source.get_status(channel)
            if status.output != "OFF":
                raise ConfigError(
                    "controlled source sweep configuration requires output OFF / "
                    "受控信号源扫频配置要求输出为 OFF"
                )
            if status.amplitude is None or status.amplitude_unit != "VPP":
                raise ConfigError(
                    "controlled source sweep requires a readable VPP amplitude / "
                    "受控信号源扫频要求可读的 VPP 幅度"
                )
            self._check_source_vpp(
                status.amplitude,
                field="source sweep amplitude / 信号源扫频幅度",
            )
            return cast(SourceSweepControlDriver, source).configure_sweep(
                channel,
                configuration,
                check_errors=source_cfg.check_errors,
            )

    def trigger_sweep(self, channel: int | None = None) -> None:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.sweep_fire_v2"):
            self.fire_sweep_v2(SourceFireRequest(channel=channel))
            return
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.TRIGGER_SWEEP,
            "source.output_v2",
            "source.sweep_configure_v2",
        )
        required = ["source.sweep_trigger"]
        if source_cfg.check_errors:
            required.append("source.errors")
        self._require("source.sweep_trigger", *required)
        if self.session is None:
            raise ConfigError(
                "manual source sweep trigger requires the persistent source session that "
                "configured it / 手动扫频触发必须复用执行配置的持久信号源会话"
            )
        cast(SourceSweepControlDriver, self.session).trigger_sweep(
            channel,
            check_errors=source_cfg.check_errors,
        )

    def snapshot_restorable_state(self, channel: int | None = None) -> RestorableSourceState:
        if self._declares_source_v2_basic_restore():
            source_cfg = self._source_config()
            target_channel = source_cfg.default_channel if channel is None else channel
            status = self._source_status_from_v2_snapshot(
                self.snapshot_v2(),
                target_channel,
            )
            if self.state_guard is not None:
                self.state_guard.observe(status)
            return RestorableSourceState.from_status(status)
        return RestorableSourceState.from_status(self.status(channel=channel))

    def restore_restorable_state(self, state: RestorableSourceState) -> SourceStatus:
        if self._declares_source_v2_basic_restore():
            # Basic V2 MAIN phases permit exactly one bounded driver write.
            # Build every request before turning output OFF, then preserve the
            # legacy restore order with separate transactions.
            requests = (
                SourceBasicConfigureRequest(
                    channel=state.channel,
                    patch=SourceBasicPatch(
                        waveform_kind=PatchValue(
                            PatchAction.SET,
                            self._source_v2_waveform_from_v1(state.function),
                        ),
                    ),
                ),
                SourceBasicConfigureRequest(
                    channel=state.channel,
                    patch=SourceBasicPatch(
                        amplitude_vpp=PatchValue(
                            PatchAction.SET,
                            state.amplitude_vpp,
                        ),
                    ),
                ),
                SourceBasicConfigureRequest(
                    channel=state.channel,
                    patch=SourceBasicPatch(
                        frequency_hz=PatchValue(
                            PatchAction.SET,
                            state.frequency_hz,
                        ),
                    ),
                ),
                *(
                    (
                        SourceBasicConfigureRequest(
                            channel=state.channel,
                            patch=SourceBasicPatch(
                                square_duty_cycle_percent=PatchValue(
                                    PatchAction.SET,
                                    state.square_duty_cycle_percent,
                                ),
                            ),
                        ),
                    )
                    if state.square_duty_cycle_percent is not None
                    else ()
                ),
            )
            self._set_output_v2_transaction(
                SourceOutputRequest(channel=state.channel, enabled=False),
            )
            final_snapshot = None
            for request in requests:
                final_snapshot = self._configure_basic_v2_transaction(request).snapshot
            assert final_snapshot is not None
            if state.output == "ON":
                output = self._set_output_v2_transaction(
                    SourceOutputRequest(channel=state.channel, enabled=True),
                )
                final_snapshot = output.snapshot
            status = self._source_status_from_v2_snapshot(final_snapshot, state.channel)
            self._state_guard_after_write(status)
            return status
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.RESTORE,
            "source.basic_configure_v2",
            "source.harmonics_configure_v2",
            "source.harmonics_disable_v2",
            "source.modulation_configure_v2",
            "source.modulation_pm_configure_v2",
            "source.modulation_fm_configure_v2",
            "source.modulation_pwm_configure_v2",
            "source.sweep_configure_v2",
            "source.burst_configure_v2",
            "source.pulse_configure_v2",
            "source.combine_configure_v2",
            "source.coupling_configure_v2",
            "source.tracking_configure_v2",
            "source.phase_relation_configure_v2",
            "source.output_v2",
        )
        self.set_output(channel=state.channel, enabled=False)
        self.set_function(channel=state.channel, function=state.function)
        self.set_amplitude_vpp(channel=state.channel, value_vpp=state.amplitude_vpp)
        self.set_frequency(channel=state.channel, value_hz=state.frequency_hz)
        if state.square_duty_cycle_percent is not None:
            self.set_square_duty_cycle(
                channel=state.channel,
                duty_percent=state.square_duty_cycle_percent,
            )
        return self.set_output(channel=state.channel, enabled=state.output == "ON")

    def set_frequency(self, channel: int | None, value_hz: float) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.basic_configure_v2"):
            request = SourceBasicConfigureRequest(
                channel=channel,
                patch=SourceBasicPatch(
                    frequency_hz=PatchValue(PatchAction.SET, value_hz),
                ),
            )
            if self._declares_source_v2_capability("source.basic_live_configure_v2"):
                try:
                    transaction = self._configure_basic_live_v2_transaction(request)
                except _SourceV2BasicRequiresOffMutation:
                    transaction = self._configure_basic_v2_transaction(request)
            else:
                transaction = self._configure_basic_v2_transaction(request)
            status = self._source_status_from_v2_snapshot(transaction.snapshot, channel)
            self._state_guard_after_write(status)
            return status
        required = ["source.set_frequency"]
        if source_cfg.settle_ms_after_set_frequency:
            required.append("source.status")
        if source_cfg.check_errors:
            required.append("source.errors")
        if self.state_guard is not None:
            required.append("source.status")
        self._require("source.set_frequency", *required)
        with self._source_session() as source:
            self._state_guard_before_write(source, channel)
            status = source.set_frequency(
                channel,
                value_hz,
                ensure_fix_mode=source_cfg.ensure_fix_mode_on_set_frequency,
                check_errors=False,
            )
            if source_cfg.settle_ms_after_set_frequency:
                time.sleep(source_cfg.settle_ms_after_set_frequency / 1000.0)
                status = source.get_status(channel)
            if source_cfg.check_errors:
                source.assert_no_errors()
            self._state_guard_after_write(status)
            return status

    def set_output(self, channel: int | None, enabled: bool) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.output_v2"):
            transaction = self._set_output_v2_transaction(
                SourceOutputRequest(channel=channel, enabled=enabled),
            )
            status = self._source_status_from_v2_snapshot(transaction.snapshot, channel)
            self._state_guard_after_write(status)
            return status
        required = ["source.output"]
        if enabled:
            required.append("source.status")
        if self.state_guard is not None and "source.status" not in required:
            required.append("source.status")
        self._require("source.output", *required)
        with self._source_session() as source:
            current = None
            if enabled or self.state_guard is not None:
                current = source.get_status(channel)
            if enabled:
                status = current
                assert status is not None
                if status.amplitude_unit != "VPP":
                    raise ConfigError(
                        "source output requires a readable VPP amplitude / "
                        "信号源输出要求可读的 VPP 幅度"
                    )
                self._check_source_vpp(status.amplitude, field="source output amplitude / 信号源输出幅度")
            if self.state_guard is not None:
                assert current is not None
                self.state_guard.before_write(current, force_off=not enabled)
            result = source.set_output(channel, enabled, check_errors=source_cfg.check_errors)
            self._state_guard_after_write(result)
            return result

    def _source_v2_basic_declares_waveform(
        self,
        *,
        channel: int,
        waveform: SourceWaveformKind,
    ) -> bool:
        descriptor = self.descriptor
        extensions = None if descriptor is None else descriptor.source_extensions
        if not isinstance(extensions, SourceDescriptorExtensions):
            return False
        return any(
            feature.feature is SourceFeature.BASIC
            and feature.scope is SourceFacetScope.CHANNEL
            and channel in feature.channels
            and feature.support is SupportState.SUPPORTED
            and SourceFeatureDirection.CONFIGURE in feature.directions
            and isinstance(feature.profile, SourceBasicCapabilityProfile)
            and waveform in feature.profile.waveform_kinds
            for feature in extensions.features
        )

    def _set_function_v1(
        self,
        *,
        channel: int,
        function: str,
        source_cfg: SourceConfig,
    ) -> SourceStatus:
        required = ["source.set_function"]
        if self.state_guard is not None:
            required.append("source.status")
        self._require("source.set_function", *required)
        with self._source_session() as source:
            self._state_guard_before_write(source, channel)
            result = source.set_function(channel, function, check_errors=source_cfg.check_errors)
            self._state_guard_after_write(result)
            return result

    def set_function(self, channel: int | None, function: str) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.basic_configure_v2"):
            waveform = self._source_v2_waveform_from_v1(function)
            if self._source_v2_basic_declares_waveform(
                channel=channel,
                waveform=waveform,
            ):
                try:
                    transaction = self._configure_basic_v2_transaction(
                        SourceBasicConfigureRequest(
                            channel=channel,
                            patch=SourceBasicPatch(
                                waveform_kind=PatchValue(
                                    PatchAction.SET,
                                    waveform,
                                ),
                            ),
                        ),
                    )
                except _SourceV2BasicLegacyFallback:
                    pass
                else:
                    status = self._source_status_from_v2_snapshot(transaction.snapshot, channel)
                    self._state_guard_after_write(status)
                    return status
        return self._set_function_v1(
            channel=channel,
            function=function,
            source_cfg=source_cfg,
        )

    def set_square_duty_cycle(self, channel: int | None, duty_percent: float) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.basic_configure_v2"):
            transaction = self._configure_basic_v2_transaction(
                SourceBasicConfigureRequest(
                    channel=channel,
                    patch=SourceBasicPatch(
                        square_duty_cycle_percent=PatchValue(PatchAction.SET, duty_percent),
                    ),
                )
            )
            status = self._source_status_from_v2_snapshot(transaction.snapshot, channel)
            self._state_guard_after_write(status)
            return status
        required = ["source.set_square_duty_cycle"]
        if self.state_guard is not None:
            required.append("source.status")
        self._require("source.set_square_duty_cycle", *required)
        with self._source_session() as source:
            self._state_guard_before_write(source, channel)
            result = source.set_square_duty_cycle(
                channel,
                duty_percent,
                check_errors=source_cfg.check_errors,
            )
            self._state_guard_after_write(result)
            return result

    def set_amplitude_vpp(self, channel: int | None, value_vpp: float) -> SourceStatus:
        source_cfg = self._source_config()
        self._check_source_vpp(value_vpp, field="source amplitude / 信号源幅度")
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.basic_configure_v2"):
            request = SourceBasicConfigureRequest(
                channel=channel,
                patch=SourceBasicPatch(
                    amplitude_vpp=PatchValue(PatchAction.SET, value_vpp),
                ),
            )
            if self._declares_source_v2_capability("source.basic_live_configure_v2"):
                try:
                    transaction = self._configure_basic_live_v2_transaction(request)
                except _SourceV2BasicRequiresOffMutation:
                    transaction = self._configure_basic_v2_transaction(request)
            else:
                transaction = self._configure_basic_v2_transaction(request)
            status = self._source_status_from_v2_snapshot(transaction.snapshot, channel)
            self._state_guard_after_write(status)
            return status
        required = ["source.set_amplitude_vpp"]
        if self.state_guard is not None:
            required.append("source.status")
        self._require("source.set_amplitude_vpp", *required)
        with self._source_session() as source:
            self._state_guard_before_write(source, channel)
            result = source.set_amplitude_vpp(
                channel,
                value_vpp,
                check_errors=source_cfg.check_errors,
            )
            self._state_guard_after_write(result)
            return result


    def upload_arbitrary_waveform(
        self,
        *,
        channel: int | None,
        file_path: str,
        playback_frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
        sample_rate_hz: float | None = None,
        max_points: int = 16384,
        byte_order: str = "little",
        output_on: bool = False,
    ) -> SourceStatus:
        source_cfg = self._source_config()
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.UPLOAD_ARBITRARY,
            "source.basic_configure_v2",
            "source.output_v2",
            "source.arbitrary_storage_v2",
            "source.arbitrary_select_v2",
            "source.arbitrary_volatile_replace_v2",
        )
        self._require_finite(
            playback_frequency_hz,
            field="arbitrary waveform playback frequency / 任意波播放频率",
        )
        self._require_finite(offset_v, field="arbitrary waveform offset / 任意波偏置")
        self._check_source_vpp(amplitude_vpp, field="arbitrary waveform amplitude / 任意波幅度")
        channel = source_cfg.default_channel if channel is None else channel
        waveform = load_arbitrary_waveform(
            file_path,
            sample_rate_hz=sample_rate_hz,
            max_points=max_points,
        )
        block = build_dg4000_dac14_binary_block(waveform, byte_order=byte_order)
        required = ["source.arbitrary_upload"]
        if self.state_guard is not None:
            required.append("source.status")
        self._require("source.arbitrary_upload", *required)
        with self._source_session() as source:
            self._state_guard_before_write(source, channel)
            result = source.upload_dg4000_dac14_block(
                channel=channel,
                block=block,
                playback_frequency_hz=playback_frequency_hz,
                amplitude_vpp=amplitude_vpp,
                offset_v=offset_v,
                output_on=output_on,
                check_errors=source_cfg.check_errors,
            )
            self._state_guard_after_write(result)
            return result

    def _check_source_vpp(self, value_vpp: object, *, field: str) -> None:
        self._require_finite(value_vpp, field=field)
        assert isinstance(value_vpp, (int, float)) and not isinstance(value_vpp, bool)
        if value_vpp < 0:
            raise ConfigError(
                f"non-negative Vpp required / Vpp 必须为非负数: {field}"
            )
        limit = self.config.safety_limits.max_source_vpp
        if limit is not None and value_vpp > limit:
            raise ConfigError(
                f"safety limit exceeded / 安全上限已超出: {field} {value_vpp:.12g} Vpp "
                f"> max_source_vpp {limit:.12g} Vpp"
            )

    @staticmethod
    def _require_finite(value: object, *, field: str) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
        ):
            raise ConfigError(
                f"finite value required / 必须为有限数: {field}"
            )

    def probe_arbitrary_queries(self, channel: int | None = None) -> list[ArbitraryQueryProbeResult]:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        self._require("source.arbitrary_probe", "source.arbitrary_probe")
        with self._source_session() as source:
            return source.probe_arbitrary_queries(channel)
