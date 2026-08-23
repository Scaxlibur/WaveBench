from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
import time
from typing import cast

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
    BasicWaveFacet,
    BurstFacet,
    HarmonicFacet,
    ModulationFacet,
    Observed,
    OutputFacet,
    PulseFacet,
    PatchAction,
    PatchValue,
    SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_BURST_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_CONTRACT_VERSION,
    SOURCE_FM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_HARMONICS_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_OUTPUT_DISABLE_V2_OPERATION_CONTRACT,
    SOURCE_OUTPUT_ENABLE_V2_OPERATION_CONTRACT,
    SOURCE_PM_MODULATION_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_PULSE_CONFIGURE_V2_OPERATION_CONTRACT,
    SnapshotConsistencyState,
    SourceDescriptorExtensions,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceBasicCapabilityProfile,
    SourceBasicConfigureRequest,
    SourceBasicConfigureResult,
    SourceBasicConfigureV2Driver,
    SourceBasicPatch,
    SourceBurstCapabilityProfile,
    SourceBurstConfigureRequest,
    SourceBurstConfigureResult,
    SourceBurstConfigureV2Driver,
    SourceBurstMode,
    SourceFacetScope,
    SourceFieldId,
    SourceFieldRef,
    SourceFeature,
    SourceFeatureDirection,
    SourceFmModulationConfigureRequest,
    SourceFmModulationConfigureResult,
    SourceFmModulationConfigureV2Driver,
    SourceHarmonicCapabilityProfile,
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
    SourceOutputRequest,
    SourceOutputResult,
    SourceOutputV2Driver,
    SourcePulseCapabilityProfile,
    SourcePulseConfigureRequest,
    SourcePulseConfigureResult,
    SourcePulseConfigureV2Driver,
    SourcePulseHoldBasis,
    SourcePmModulationConfigureRequest,
    SourcePmModulationConfigureResult,
    SourcePmModulationConfigureV2Driver,
    SourceScopeRef,
    SourceSnapshotV2,
    SourceSnapshotV2Driver,
    SourceTriggerOutput,
    SourceTriggerSlope,
    SourceTriggerSource,
    SourceTriggerState,
    SourceV1WriteRouteId,
    SourceWaveformKind,
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
from wavebench.transport.session import InstrumentSessionState
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
from wavebench.transport.session import SessionHealth


@dataclass(frozen=True, slots=True)
class _SourceBasicConfigureV2Transaction:
    """Core transaction result shared by public and V1-adapter routes."""

    result: SourceBasicConfigureResult
    artifact: dict[str, object]
    snapshot: SourceSnapshotV2


@dataclass(frozen=True, slots=True)
class _SourceHarmonicConfigureV2Transaction:
    """Core transaction result shared by the Harmonic public route."""

    result: SourceHarmonicConfigureResult
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
class _SourceBurstConfigureV2Transaction:
    """Core transaction result shared by the internal Triggered Burst route."""

    result: SourceBurstConfigureResult
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

    def configure_burst_v2(
        self,
        request: SourceBurstConfigureRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[SourceBurstConfigureResult, dict[str, object]]:
        """Configure one OFF source channel with the internal Triggered Burst scope."""

        transaction = self._configure_burst_v2_transaction(
            request,
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
    ) -> _SourceBasicConfigureV2Transaction:
        """Execute the private M5-B basic-write transaction.

        This method deliberately remains private until M5-D owns the public
        Service, CLI, run-plan and V1 dual-contract routes.  It is the single
        core path that M5-B tests use to prove the write/recovery contract.
        """

        if not isinstance(request, SourceBasicConfigureRequest):
            raise ConfigError("source.basic_configure_v2 requires SourceBasicConfigureRequest")
        self._require(
            "source.basic_configure_v2",
            "source.snapshot_v2",
            "source.basic_configure_v2",
        )
        with self._source_session() as source:
            descriptor = self.descriptor
            extensions = None if descriptor is None else descriptor.source_extensions
            session_state = self.session_state
            if not isinstance(extensions, SourceDescriptorExtensions):
                raise ConfigError(
                    "source.basic_configure_v2 requires validated source_extensions"
                )
            if session_state is None:
                raise ConfigError(
                    "source.basic_configure_v2 requires a connection-bound session state"
                )
            fields = self._source_basic_v2_fields(request.channel)
            output_field = next(
                field for field in fields if field.field is SourceFieldId.OUTPUT
            )
            context = SourceOperationContextCoordinator(
                session_state=session_state,
                operation_spec=require_operation_spec("source.basic_configure_v2"),
                operation_contract=SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT,
                connection_timeout_ms=self.config.connection.timeout_ms,
                baseline_snapshot_digest=None,
                fields=fields,
                required_off_outputs=(
                    SourceScopeRef(SourceFacetScope.CHANNEL, channel=request.channel),
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
            result: SourceBasicConfigureResult | None = None
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
                        operation="source.basic_configure_v2",
                    )
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
                    max_steps=SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT.main_max_steps,
                )
                try:
                    with context.authorize_phase(main):
                        main_entered = True
                        result = cast(SourceBasicConfigureV2Driver, source).configure_source_basic_v2(
                            request
                        )
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
                                    operation="source.basic_configure_v2",
                                )
                            )
                            assert result is not None
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
                                operation="source.basic_configure_v2",
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
                    ),
                    snapshot=postcondition_snapshot,
                )
            except BaseException:
                if not context.terminal:
                    context.complete()
                raise

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
        if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
            raise ConfigError("source.basic_configure_v2 requires target output OFF")
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
        current_vpp, current_offset = self._source_v2_amplitude_offset(
            basic,
            operation="source.basic_configure_v2",
        )
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

    @staticmethod
    def _source_harmonic_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceHarmonicCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.HARMONICS
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
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
    def _source_burst_runtime_profile(
        snapshot: SourceSnapshotV2,
        *,
        channel: int,
        operation: str,
    ) -> SourceBurstCapabilityProfile:
        feature = next(
            (
                candidate
                for candidate in snapshot.runtime_profile.features
                if candidate.feature is SourceFeature.BURST
                and candidate.scope is SourceFacetScope.CHANNEL
                and channel in candidate.channels
                and candidate.support is SupportState.SUPPORTED
                and SourceFeatureDirection.CONFIGURE in candidate.directions
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
        if SourceTriggerSource.INTERNAL not in profile.trigger_sources:
            raise ConfigError(f"{operation} internal trigger is not supported by the runtime profile")
        if not profile.timing_readable:
            raise ConfigError(f"{operation} requires burst timing readback")
        if not profile.triggered_internal_configuration_readable:
            raise ConfigError(f"{operation} requires configured internal triggered burst readback")

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
            or trigger.source.value is not SourceTriggerSource.INTERNAL
        ):
            raise ConfigError(f"{operation} trigger source does not match scope")
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
                    f"source.basic_configure_v2 {name} readback does not match request"
                )
        if patch.amplitude_vpp.action is PatchAction.SET:
            actual_vpp, _ = SourceService._source_v2_amplitude_offset(
                basic,
                operation="source.basic_configure_v2",
            )
            if actual_vpp != patch.amplitude_vpp.value:
                raise ConfigError(
                    "source.basic_configure_v2 amplitude_vpp readback does not match request"
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

    def _source_basic_v2_artifact(
        self,
        *,
        context: SourceOperationContextCoordinator,
        request: SourceBasicConfigureRequest,
        preflight_snapshot: SourceSnapshotV2 | None,
        postcondition_snapshot: SourceSnapshotV2 | None,
        result: SourceBasicConfigureResult | None,
        recovery: dict[str, object] | None = None,
    ) -> dict[str, object]:
        artifact = context.artifact()
        descriptor_digest = (
            None
            if preflight_snapshot is None
            else preflight_snapshot.runtime_profile.descriptor_digest
        )
        artifact["capability_decision"] = {
            "capability": "source.basic_configure_v2",
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

    def _attach_source_burst_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_burst_v2_artifact(**kwargs))
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
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.TRIGGER_SWEEP,
            "source.output_v2",
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
        return RestorableSourceState.from_status(self.status(channel=channel))

    def restore_restorable_state(self, state: RestorableSourceState) -> SourceStatus:
        self._reject_v1_route_for_source_v2(
            SourceV1WriteRouteId.RESTORE,
            "source.basic_configure_v2",
            "source.harmonics_configure_v2",
            "source.modulation_configure_v2",
            "source.modulation_pm_configure_v2",
            "source.modulation_fm_configure_v2",
            "source.burst_configure_v2",
            "source.pulse_configure_v2",
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
            transaction = self._configure_basic_v2_transaction(
                SourceBasicConfigureRequest(
                    channel=channel,
                    patch=SourceBasicPatch(
                        frequency_hz=PatchValue(PatchAction.SET, value_hz),
                    ),
                )
            )
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

    def set_function(self, channel: int | None, function: str) -> SourceStatus:
        source_cfg = self._source_config()
        channel = source_cfg.default_channel if channel is None else channel
        if self._declares_source_v2_capability("source.basic_configure_v2"):
            transaction = self._configure_basic_v2_transaction(
                SourceBasicConfigureRequest(
                    channel=channel,
                    patch=SourceBasicPatch(
                        waveform_kind=PatchValue(
                            PatchAction.SET,
                            self._source_v2_waveform_from_v1(function),
                        ),
                    ),
                )
            )
            status = self._source_status_from_v2_snapshot(transaction.snapshot, channel)
            self._state_guard_after_write(status)
            return status
        required = ["source.set_function"]
        if self.state_guard is not None:
            required.append("source.status")
        self._require("source.set_function", *required)
        with self._source_session() as source:
            self._state_guard_before_write(source, channel)
            result = source.set_function(channel, function, check_errors=source_cfg.check_errors)
            self._state_guard_after_write(result)
            return result

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
            transaction = self._configure_basic_v2_transaction(
                SourceBasicConfigureRequest(
                    channel=channel,
                    patch=SourceBasicPatch(
                        amplitude_vpp=PatchValue(PatchAction.SET, value_vpp),
                    ),
                )
            )
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
