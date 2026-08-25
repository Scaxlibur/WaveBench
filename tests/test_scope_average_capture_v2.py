from __future__ import annotations

from dataclasses import replace
import numpy as np
import pytest

from wavebench.errors import ConfigError, DataError, TransportIOError
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.models import (
    ScopeChannelInputStateV2,
    WaveformData,
    WaveformHeader,
)
from wavebench.instruments.scope_extensions import (
    ErrorDrainResult,
    SCOPE_AVERAGE_CAPTURE_FIELD_ORDER,
    ScopeAcquisitionCompletion,
    ScopeAcquisitionControlBaseline,
    ScopeAcquisitionControlProfile,
    ScopeAcquisitionControlSnapshot,
    ScopeAcquisitionRunState,
    ScopeAcquisitionStatusProfileV2,
    ScopeAverageCaptureBaseline,
    ScopeAverageCaptureBinaryProfile,
    ScopeAverageCaptureProfileV2,
    ScopeAverageCaptureRequestV2,
    ScopeAverageCaptureRestoreResult,
    ScopeAverageCaptureResultV2,
    ScopeAverageCaptureStateSnapshot,
    ScopeAverageCaptureVerification,
    ScopeAverageCompletionProofV2,
    ScopeAverageConfigurationV2,
    ScopeDescriptorExtensions,
)
from wavebench.services.scope_average_capture_executor import ScopeAverageCaptureExecutor
from wavebench.services.scope_service import ScopeService
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import (
    BinaryQueryResult,
    BinaryResponseFraming,
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import InstrumentSessionState, SessionHealth


def _stopped_state() -> ScopeAcquisitionRunState:
    return ScopeAcquisitionRunState(
        phase="stopped",
        trigger_mode="single",
        raw_state="STOP",
        acquisition_count=3,
        counter_epoch="epoch-1",
        acquisition_identity="identity-1",
    )


def _completion() -> ScopeAcquisitionCompletion:
    stopped = _stopped_state()
    arming = ScopeAcquisitionRunState(
        phase="arming",
        trigger_mode="single",
        raw_state="ARM",
        acquisition_count=3,
        counter_epoch="epoch-1",
        acquisition_identity="identity-1",
    )
    completed = ScopeAcquisitionRunState(
        phase="complete",
        trigger_mode="single",
        raw_state="COMPLETE",
        acquisition_count=4,
        counter_epoch="epoch-1",
        acquisition_identity="identity-2",
    )
    return ScopeAcquisitionCompletion(
        state=completed,
        original_state=stopped,
        proof_baseline_state=stopped,
        proof_baseline_stage="configured_pre_arm",
        proof="identity_delta",
        baseline_identity="identity-1",
        completed_identity="identity-2",
        observed_states=(arming, completed),
    )


def _snapshot(
    *,
    configuration: ScopeAverageConfigurationV2 | None = None,
) -> ScopeAverageCaptureStateSnapshot:
    return ScopeAverageCaptureStateSnapshot(
        captured_fields=SCOPE_AVERAGE_CAPTURE_FIELD_ORDER,
        configuration=configuration
        or ScopeAverageConfigurationV2(
            mechanism="global_acquisition",
            acquisition_type="normal",
            average_count=1,
        ),
        run_state=_stopped_state(),
        **{
            {
                "scope.run_state": "run_state_token",
                "scope.acquisition": "acquisition_token",
                "scope.trigger": "trigger_token",
                "scope.timebase": "timebase_token",
                "scope.channel_display": "channel_display_token",
                "scope.channel_vertical": "channel_vertical_token",
                "scope.waveform_source": "waveform_source_token",
                "scope.waveform_mode": "waveform_mode_token",
                "scope.query_response_header": "query_response_header_token",
                "scope.waveform_format": "waveform_format_token",
                "scope.waveform_byte_order": "waveform_byte_order_token",
                "scope.waveform_points": "waveform_points_token",
                "scope.waveform_transfer_window": "waveform_transfer_window_token",
            }[field_name]: f"token-{index}"
            for index, field_name in enumerate(SCOPE_AVERAGE_CAPTURE_FIELD_ORDER)
        },
    )


def _baseline() -> ScopeAverageCaptureBaseline:
    snapshot = _snapshot()
    acquisition_baseline = ScopeAcquisitionControlBaseline(
        context_id="context-1",
        session_epoch="epoch-1",
        baseline_nonce="child-nonce",
        snapshot=ScopeAcquisitionControlSnapshot(
            run_state=snapshot.run_state,
            trigger_state_token=snapshot.trigger_token or "missing",
            acquisition_state_token=snapshot.acquisition_token or "missing",
        ),
        restore_order=("scope.run_state", "scope.trigger", "scope.acquisition"),
    )
    return ScopeAverageCaptureBaseline(
        context_id="context-1",
        session_epoch="epoch-1",
        baseline_nonce="parent-nonce",
        snapshot=snapshot,
        restore_order=SCOPE_AVERAGE_CAPTURE_FIELD_ORDER,
        acquisition_baseline=acquisition_baseline,
    )


def _profile() -> ScopeAverageCaptureProfileV2:
    return ScopeAverageCaptureProfileV2(
        global_acquisition_type="average",
        completion_contract_id="device-average-complete-v1",
        channel_range=(1, 4),
        supported_points=("def", "dmax"),
        average_count_min=2,
        average_count_max=64,
        requires_power_of_two=True,
        binary=ScopeAverageCaptureBinaryProfile(
            response_max_bytes=1_024,
            operation_max_bytes=4_096,
            query_max_count=4,
            resynchronization_max_bytes=0,
            transport_trailing_hex="0a",
        ),
        restore_order=SCOPE_AVERAGE_CAPTURE_FIELD_ORDER,
        snapshot_max_steps=len(SCOPE_AVERAGE_CAPTURE_FIELD_ORDER),
        main_max_steps=8,
        restore_max_steps=len(SCOPE_AVERAGE_CAPTURE_FIELD_ORDER),
        verify_max_steps=len(SCOPE_AVERAGE_CAPTURE_FIELD_ORDER),
    )


def _waveform() -> WaveformData:
    return WaveformData(
        channel=1,
        header=WaveformHeader(0.0, 1e-3, 2),
        voltages_v=np.array([0.0, 1.0]),
    )


def test_average_capture_v2_profile_validates_the_single_channel_r1_contract() -> None:
    request = ScopeAverageCaptureRequestV2(
        channels=(1,),
        average_count=4,
        mechanism="global_acquisition",
        acquisition_stopped=True,
        points="dmax",
    )
    profile = _profile()

    profile.validate_request(request)
    profile.validate_configuration(
        ScopeAverageConfigurationV2("global_acquisition", "average", 4),
        request=request,
    )
    assert profile.binary.transport_trailing == b"\n"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {
                "channels": (1, 2),
                "average_count": 4,
                "mechanism": "global_acquisition",
                "acquisition_stopped": True,
            },
            "exactly one channel",
        ),
        (
            {
                "channels": (1,),
                "average_count": 3,
                "mechanism": "global_acquisition",
                "acquisition_stopped": True,
            },
            "power of two",
        ),
    ],
)
def test_average_capture_v2_request_and_profile_reject_outside_r1(
    kwargs: dict[str, object],
    message: str,
) -> None:
    if len(kwargs["channels"]) != 1:  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=message):
            ScopeAverageCaptureRequestV2(**kwargs)  # type: ignore[arg-type]
        return
    request = ScopeAverageCaptureRequestV2(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        _profile().validate_request(request)


def test_average_capture_v2_baseline_binds_the_child_acquisition_baseline() -> None:
    baseline = _baseline()

    assert baseline.acquisition_baseline.context_id == baseline.context_id
    assert baseline.acquisition_baseline.baseline_nonce != baseline.baseline_nonce

    with pytest.raises(ValueError, match="child baseline trigger token"):
        ScopeAverageCaptureBaseline(
            context_id=baseline.context_id,
            session_epoch=baseline.session_epoch,
            baseline_nonce=baseline.baseline_nonce,
            snapshot=baseline.snapshot,
            restore_order=baseline.restore_order,
            acquisition_baseline=ScopeAcquisitionControlBaseline(
                context_id=baseline.context_id,
                session_epoch=baseline.session_epoch,
                baseline_nonce="other-child",
                snapshot=ScopeAcquisitionControlSnapshot(
                    run_state=baseline.snapshot.run_state,
                    trigger_state_token="wrong",
                    acquisition_state_token=baseline.snapshot.acquisition_token or "missing",
                ),
                restore_order=("scope.run_state", "scope.trigger", "scope.acquisition"),
            ),
        )


def test_average_capture_v2_result_requires_full_restore_and_fresh_verification() -> None:
    baseline = _baseline()
    request = ScopeAverageCaptureRequestV2(
        channels=(1,),
        average_count=4,
        mechanism="global_acquisition",
        acquisition_stopped=True,
    )
    configured = ScopeAverageConfigurationV2("global_acquisition", "average", 4)
    completion = ScopeAverageCompletionProofV2(
        evidence="device_average_complete",
        mechanism="global_acquisition",
        configured_average_count=4,
        configuration_readback=configured,
        acquisition_completion=_completion(),
        device_average_complete=True,
        contract_id="device-average-complete-v1",
        context_id=baseline.context_id,
        session_epoch=baseline.session_epoch,
        acquisition_baseline_nonce_digest="0123456789abcdef",
    )
    restore = ScopeAverageCaptureRestoreResult(
        "completed",
        baseline.restore_order,
        baseline.restore_order,
    )
    restore.validate_for(baseline)
    verification = ScopeAverageCaptureVerification(
        "verified",
        baseline.restore_order,
        (),
    )
    verification.validate_for(baseline)

    result = ScopeAverageCaptureResultV2(
        request=request,
        waveforms=(_waveform(),),
        configuration_before=baseline.snapshot.configuration,
        configuration_after=baseline.snapshot.configuration,
        run_state_before=baseline.snapshot.run_state,
        run_state_after=baseline.snapshot.run_state,
        completion=completion,
        restore=restore,
        verification=verification,
    )

    assert result.waveforms[0].channel == 1


class _Backend:
    _wavebench_binary_budget_parameters = True
    resource = "fake"

    def __init__(self) -> None:
        self.events: list[str] = []
        self.fail_binary_sync = False

    def record_event(self, _direction: str, _text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.events.append(command)
        return "EXAMPLE,AVG-1,SN-1,1.0"

    def query_binary(
        self,
        command: str,
        *,
        framing: BinaryResponseFraming,
        max_bytes: int,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
        _transport_trailing: bytes = b"",
        _resynchronization_max_bytes: int = 0,
    ) -> BinaryQueryResult:
        assert framing is BinaryResponseFraming.DEFINITE_BLOCK
        assert replay is ReplayPolicy.NO_REPLAY
        self.events.append(command)
        if self.fail_binary_sync:
            raise TransportIOError(
                "truncated binary response",
                operation="query_binary",
                phase=TransportPhase.READING,
                replay_policy=replay,
                command_transmission=CommandTransmission.SENT,
                response_progress=ResponseProgress.PARTIAL,
                synchronization=Synchronization.LOST,
                attempts=1,
                reason_code="binary_truncated",
                consumed_bytes=1,
            )
        payload = b"\x00\x01"
        assert len(payload) <= max_bytes
        return BinaryQueryResult(
            data=payload,
            framing=framing,
            declared_length=len(payload),
            framing_header_bytes=3,
            consumed_bytes=3 + len(payload) + len(_transport_trailing),
            transport_trailing_bytes=_transport_trailing,
        )

    def write(self, command: str) -> None:
        self.events.append(command)

    def close(self) -> None:
        pass


class _Driver:
    def __init__(self, transport: GuardedAuditedTransport) -> None:
        self.transport = transport
        self.events: list[str] = []
        self.configuration = ScopeAverageConfigurationV2(
            "global_acquisition",
            "normal",
            1,
        )
        self.complete = True
        self.fail_after_binary = False
        self.skip_binary = False
        self.restore_calls = 0
        self.verify_calls = 0
        self.drain_calls = 0
        self.legacy_average_calls = 0
        self.input_termination = "high_z"

    def close(self) -> None:
        pass

    def idn(self) -> str:
        self.events.append("idn")
        return self.transport.query("*IDN?", replay=ReplayPolicy.NO_REPLAY)

    def get_channel_input_state_v2(self, channel: int) -> ScopeChannelInputStateV2:
        self.events.append("input")
        self.transport.query("INPUT?", replay=ReplayPolicy.NO_REPLAY)
        return ScopeChannelInputStateV2(
            channel,
            "dc",
            self.input_termination,
            unavailable_fields=("impedance_ohm",),
        )

    def get_acquisition_status_v2(self, *, fields):
        raise AssertionError("average capture V2 must not call acquisition-status V2")

    def get_acquisition_run_state(self) -> ScopeAcquisitionRunState:
        self.events.append("stopped-recheck")
        self.transport.query("RUN?", replay=ReplayPolicy.NO_REPLAY)
        return _stopped_state()

    def start_continuous(self, **_kwargs):
        raise AssertionError("average capture V2 must not start continuous acquisition")

    def stop_acquisition(self):
        raise AssertionError("average capture V2 must not send STOP")

    def acquire_single(self, **_kwargs):
        raise AssertionError("average capture V2 must not use the public single Service path")

    def snapshot_acquisition_control(self):
        raise AssertionError("average capture V2 owns its child baseline")

    def restore_acquisition_control(self, _baseline):
        raise AssertionError("average capture V2 must restore its parent baseline once")

    def verify_acquisition_control_restored(self, _baseline):
        raise AssertionError("average capture V2 must verify its parent baseline once")

    def snapshot_average_capture_state(self, fields):
        self.events.append("snapshot")
        self.transport.query("SNAP?", replay=ReplayPolicy.NO_REPLAY)
        assert tuple(fields) == SCOPE_AVERAGE_CAPTURE_FIELD_ORDER
        return _snapshot(configuration=self.configuration)

    def set_average_acquisition_type_v2(self, acquisition_type: str, *, baseline) -> None:
        self.events.append("set-type")
        self.transport.write(f"TYPE {acquisition_type}")
        self.configuration = ScopeAverageConfigurationV2(
            "global_acquisition",
            acquisition_type,
            self.configuration.average_count,
        )

    def get_average_configuration_v2(self, *, baseline) -> ScopeAverageConfigurationV2:
        self.events.append("read-config")
        self.transport.query("CONFIG?", replay=ReplayPolicy.NO_REPLAY)
        return self.configuration

    def set_average_count_v2(self, average_count: int, *, baseline) -> None:
        self.events.append("set-count")
        self.transport.write(f"COUNT {average_count}")
        self.configuration = ScopeAverageConfigurationV2(
            "global_acquisition",
            self.configuration.acquisition_type,
            average_count,
        )

    def acquire_average_single_v2(self, *, baseline, deadline: float) -> ScopeAcquisitionCompletion:
        self.events.append("single")
        self.transport.write("SINGLE")
        return _completion()

    def get_device_average_complete_v2(self, *, baseline) -> bool:
        self.events.append("complete")
        self.transport.query("AVERAGE:COMPLETE?", replay=ReplayPolicy.NO_REPLAY)
        return self.complete

    def fetch_average_waveform_bounded(self, channel: int, *, points: str, baseline) -> WaveformData:
        self.events.append("fetch")
        assert points == "DMAX"
        if not self.skip_binary:
            self.transport.query_binary(
                "DATA?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=2,
                replay=ReplayPolicy.NO_REPLAY,
            )
        if self.fail_after_binary:
            raise DataError("conversion failed")
        return _waveform()

    def restore_average_capture_state(self, baseline) -> ScopeAverageCaptureRestoreResult:
        self.events.append("restore")
        self.restore_calls += 1
        self.transport.write("RESTORE")
        self.configuration = baseline.snapshot.configuration
        return ScopeAverageCaptureRestoreResult(
            "completed",
            baseline.restore_order,
            baseline.restore_order,
        )

    def verify_average_capture_state_restored(self, baseline) -> ScopeAverageCaptureStateSnapshot:
        self.events.append("verify")
        self.verify_calls += 1
        self.transport.query("VERIFY?", replay=ReplayPolicy.NO_REPLAY)
        return baseline.snapshot

    def drain_errors(self, *, max_records: int) -> ErrorDrainResult:
        self.events.append("drain")
        self.drain_calls += 1
        self.transport.query("ERR?", replay=ReplayPolicy.NO_REPLAY)
        return ErrorDrainResult(records=(), terminated=True, query_count=1)

    def capture_average(self, *_args, **_kwargs):
        self.legacy_average_calls += 1
        raise AssertionError("average capture V2 must not use legacy capture_average")


def _acquisition_profile() -> ScopeAcquisitionControlProfile:
    return ScopeAcquisitionControlProfile(
        supported_continuous_modes=("normal",),
        single_arm_semantics="configure_then_arm",
        arm_resets_acquisition_count=False,
        failure_restore_order=("scope.trigger", "scope.acquisition"),
        snapshot_max_steps=3,
        restore_max_steps=3,
        verify_max_steps=3,
        identity_semantics="unique_within_session_epoch",
    )


def _descriptor(*, error_drain: bool = True) -> InstrumentDescriptor:
    capabilities = (
        "scope.idn",
        "scope.capture_average_v2",
        "scope.acquisition_status_v2",
        "scope.acquisition_run_state",
        "scope.acquisition_control",
        "scope.channel_input_state_v2",
    ) + (("scope.error_drain_v1",) if error_drain else ())
    return InstrumentDescriptor(
        driver_id="example.average-capture-v2",
        kind="scope",
        display_name="Example average scope",
        manufacturer="Example",
        models=("AVG-1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE,AVG-1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version="0.8.24",
        scope_extensions=ScopeDescriptorExtensions(
            acquisition_control_profile=_acquisition_profile(),
            acquisition_status_profile_v2=ScopeAcquisitionStatusProfileV2(
                readable_fields=("acquisition_type",),
                max_queries=1,
            ),
            average_capture_profile_v2=_profile(),
        ),
    )


def _executor(
    backend: _Backend,
    driver: _Driver,
) -> ScopeAverageCaptureExecutor:
    transport = driver.transport
    return ScopeAverageCaptureExecutor(
        driver=driver,
        descriptor=_descriptor(),
        session_state=transport.session_state,
        connection_timeout_ms=10_000,
        transport=transport,
    )


def _bounded_transport(backend: _Backend) -> GuardedAuditedTransport:
    state = InstrumentSessionState(epoch_id="epoch-1")
    transport = GuardedAuditedTransport(backend, session_state=state)
    transport._mark_bounded_binary_backend_verified()
    return transport


def _request(*, allow_50ohm: bool = False) -> ScopeAverageCaptureRequestV2:
    return ScopeAverageCaptureRequestV2(
        channels=(1,),
        average_count=4,
        mechanism="global_acquisition",
        acquisition_stopped=True,
        allow_50ohm=allow_50ohm,
    )


def test_average_capture_v2_executes_one_core_owned_single_channel_transaction() -> None:
    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))

    result = _executor(backend, driver).execute(_request(), check_errors=True)

    assert result.value.waveforms[0].channel == 1
    assert np.array_equal(result.value.waveforms[0].voltages_v, np.array([0.0, 1.0]))
    assert driver.events == [
        "idn",
        "input",
        "snapshot",
        "drain",
        "set-type",
        "read-config",
        "set-count",
        "read-config",
        "stopped-recheck",
        "single",
        "complete",
        "fetch",
        "drain",
        "restore",
        "verify",
    ]
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert driver.drain_calls == 2
    assert driver.legacy_average_calls == 0
    assert driver.transport.session_state.health is SessionHealth.HEALTHY


def test_average_capture_v2_completion_failure_restores_without_binary_fetch() -> None:
    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))
    driver.complete = False

    with pytest.raises(DataError, match="completion_unproven"):
        _executor(backend, driver).execute(_request(), check_errors=False)

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert "fetch" not in driver.events
    assert driver.drain_calls == 0


def test_average_capture_v2_data_failure_after_proven_boundary_restores() -> None:
    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))
    driver.fail_after_binary = True

    with pytest.raises(DataError, match="conversion failed"):
        _executor(backend, driver).execute(_request(), check_errors=False)

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert driver.transport.session_state.health is SessionHealth.HEALTHY


def test_average_capture_v2_lost_binary_sync_poisoned_session_skips_cleanup() -> None:
    backend = _Backend()
    backend.fail_binary_sync = True
    driver = _Driver(_bounded_transport(backend))

    with pytest.raises(TransportIOError):
        _executor(backend, driver).execute(_request(), check_errors=False)

    assert driver.restore_calls == 0
    assert driver.verify_calls == 0
    assert driver.transport.session_state.health is SessionHealth.POISONED


def test_average_capture_v2_input_safety_requires_explicit_50_ohm_authorization() -> None:
    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))
    driver.input_termination = "50_ohm"

    with pytest.raises(ConfigError, match="allow_50ohm"):
        _executor(backend, driver).execute(_request(), check_errors=False)
    assert driver.events == ["idn", "input"]

    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))
    driver.input_termination = "50_ohm"
    _executor(backend, driver).execute(_request(allow_50ohm=True), check_errors=False)
    assert driver.restore_calls == 1


def _service(driver: _Driver, *, check_errors: bool = False) -> ScopeService:
    return ScopeService(
        config=type(
            "Config",
            (),
            {
                "scope": type(
                    "Scope",
                    (),
                    {
                        "driver": "example.average-capture-v2",
                        "access": "read_write",
                        "check_errors": check_errors,
                    },
                )(),
                "connection": type("Connection", (), {"timeout_ms": 10_000})(),
            },
        )(),
        logger=CommandLogger(),
        session=driver,
        descriptor=_descriptor(error_drain=check_errors),
        transport=driver.transport,
        session_state=driver.transport.session_state,
    )


def test_average_capture_v2_service_uses_only_the_v2_executor_route() -> None:
    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))

    result = _service(driver).capture_average_v2(_request())

    assert result.waveforms[0].channel == 1
    assert driver.legacy_average_calls == 0


def test_average_capture_v2_service_requires_typed_error_drain_before_opening_session() -> None:
    backend = _Backend()
    driver = _Driver(_bounded_transport(backend))
    service = _service(driver, check_errors=True)
    service.descriptor = _descriptor(error_drain=False)
    service._open_scope = lambda: pytest.fail("missing error drain must fail before opening scope")

    with pytest.raises(ConfigError, match="scope.error_drain_v1"):
        service.capture_average_v2(_request())
    assert driver.events == []


def _open_factory_descriptor() -> object:
    return open_instrument_driver(
        driver_reference="example.average-capture-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_average_capture_v2_factory_latch_blocks_construction_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    errors: list[TransportIOError] = []

    def factory(context):
        transport = context.open_transport()
        with pytest.raises(TransportIOError) as raised:
            transport.query("*IDN?")
        errors.append(raised.value)
        return _Driver(transport)

    descriptor = replace(_descriptor(), factory=factory)
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory._open_transport",
        lambda **_kwargs: backend,
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory._validate_bounded_binary_transport",
        lambda **_kwargs: None,
    )

    opened = _open_factory_descriptor()

    assert [error.reason_code for error in errors] == ["factory_construction_pending"]
    assert errors[0].attempts == 0
    assert backend.events == []
    assert opened.transport is not None
    assert opened.transport._has_verified_bounded_binary_backend()
