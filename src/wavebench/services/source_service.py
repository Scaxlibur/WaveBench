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
    OutputFacet,
    PatchAction,
    SOURCE_BASIC_CONFIGURE_V2_OPERATION_CONTRACT,
    SOURCE_CONTRACT_VERSION,
    SnapshotConsistencyState,
    SourceDescriptorExtensions,
    SourceAmplitude,
    SourceAmplitudeUnit,
    SourceBasicCapabilityProfile,
    SourceBasicConfigureRequest,
    SourceBasicConfigureResult,
    SourceBasicConfigureV2Driver,
    SourceFacetScope,
    SourceFieldId,
    SourceFieldRef,
    SourceFeature,
    SourceFeatureDirection,
    SourceOutputRequest,
    SourceOutputResult,
    SourceOutputV2Driver,
    SourceScopeRef,
    SourceSnapshotV2,
    SourceSnapshotV2Driver,
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
    """Internal M5-B result; public Source V2 write entry points come later."""

    result: SourceBasicConfigureResult
    artifact: dict[str, object]


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
                    preflight_basic, preflight_output = self._source_basic_v2_target(
                        preflight_snapshot,
                        request.channel,
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
                                self._source_basic_v2_target(
                                    postcondition_snapshot,
                                    request.channel,
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
                            recovery = self._recover_source_basic_v2_off(
                                context,
                                source,
                                request.channel,
                                extensions,
                                output_field,
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
    def _source_basic_v2_target(
        snapshot: SourceSnapshotV2,
        channel: int,
    ) -> tuple[BasicWaveFacet, OutputFacet]:
        target = next((item for item in snapshot.channels if item.channel == channel), None)
        if target is None:
            raise ConfigError("source.basic_configure_v2 target channel is absent from snapshot")
        if target.basic.availability is not Availability.VALUE or not isinstance(
            target.basic.value,
            BasicWaveFacet,
        ):
            raise ConfigError("source.basic_configure_v2 requires readable basic state")
        if target.output.availability is not Availability.VALUE or not isinstance(
            target.output.value,
            OutputFacet,
        ):
            raise ConfigError("source.basic_configure_v2 requires readable output state")
        return target.basic.value, target.output.value

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
        current_vpp, current_offset = self._source_basic_v2_amplitude_offset(basic)
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
        self._check_source_basic_v2_limits(requested_vpp, requested_offset)

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
        vpp, offset = self._source_basic_v2_amplitude_offset(result.basic)
        self._check_source_basic_v2_limits(vpp, offset)
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
        result_vpp, result_offset = self._source_basic_v2_amplitude_offset(result.basic)
        post_vpp, post_offset = self._source_basic_v2_amplitude_offset(basic)
        if (result_vpp, result_offset) != (post_vpp, post_offset):
            raise ConfigError(
                "source.basic_configure_v2 final amplitude or offset readback does not match"
            )
        self._check_source_basic_v2_limits(post_vpp, post_offset)

    @staticmethod
    def _source_basic_v2_amplitude_offset(basic: BasicWaveFacet) -> tuple[float, float]:
        amplitude = basic.amplitude
        if amplitude.availability is not Availability.VALUE or not isinstance(
            amplitude.value,
            SourceAmplitude,
        ):
            raise ConfigError("source.basic_configure_v2 requires a final Vpp amplitude")
        if amplitude.value.unit is not SourceAmplitudeUnit.VPP:
            raise ConfigError("source.basic_configure_v2 requires a final Vpp amplitude")
        offset = basic.offset_v
        if offset.availability is not Availability.VALUE:
            raise ConfigError("source.basic_configure_v2 requires a final offset")
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
            actual_vpp, _ = SourceService._source_basic_v2_amplitude_offset(basic)
            if actual_vpp != patch.amplitude_vpp.value:
                raise ConfigError(
                    "source.basic_configure_v2 amplitude_vpp readback does not match request"
                )

    def _check_source_basic_v2_limits(self, vpp: float, offset: float) -> None:
        self._check_source_vpp(vpp, field="source.basic_configure_v2 final amplitude")
        limits = self.config.safety_limits
        minimum = limits.min_source_port_voltage_v
        maximum = limits.max_source_port_voltage_v
        if minimum is None or maximum is None:
            return
        low = offset - (vpp / 2.0)
        high = offset + (vpp / 2.0)
        if low < minimum or high > maximum:
            raise ConfigError(
                "source.basic_configure_v2 final port voltage exceeds configured limits"
            )

    def _recover_source_basic_v2_off(
        self,
        context: SourceOperationContextCoordinator,
        source: SourceDriver,
        channel: int,
        extensions: SourceDescriptorExtensions,
        output_field: SourceFieldRef,
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
                    raise ConfigError("source.basic_configure_v2 recovery OFF is not proven")
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
                _, output = self._source_basic_v2_target(snapshot, channel)
                if output.enabled.availability is not Availability.VALUE or output.enabled.value is not False:
                    raise ConfigError("source.basic_configure_v2 recovery OFF readback is not proven")
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

    def _attach_source_basic_v2_diagnostics(
        self,
        exc: BaseException,
        **kwargs: object,
    ) -> None:
        try:
            setattr(exc, "source_operation_artifact", self._source_basic_v2_artifact(**kwargs))
        except Exception:
            pass

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
