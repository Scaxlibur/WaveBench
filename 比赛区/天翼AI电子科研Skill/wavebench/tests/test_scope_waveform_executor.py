from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pytest

from wavebench.errors import ConfigError, DataError, InstrumentError, TransportIOError
from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.instruments.api import InstrumentDescriptor
from wavebench.instruments.models import WaveformData, WaveformHeader
from wavebench.instruments.scope_extensions import (
    ErrorDrainResult,
    ScopeDescriptorExtensions,
    ScopeWaveformBinaryOperationProfile,
    ScopeWaveformBinaryProfile,
    ScopeWaveformTransferRestoreResult,
    ScopeWaveformTransferStateSnapshot,
)
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService
from wavebench.services.scope_waveform_executor import BoundedWaveformExecutor
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


_FETCH_FIELDS = ("scope.waveform_source",)
_CAPTURE_FIELDS = (
    "scope.run_state",
    "scope.acquisition",
    "scope.trigger",
    "scope.timebase",
    "scope.channel_display",
    "scope.channel_vertical",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
)
_TOKEN_ATTRS = {
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
}


def _fields_for(operation_kind: str) -> tuple[str, ...]:
    return _FETCH_FIELDS if operation_kind == "fetch" else _CAPTURE_FIELDS


def _snapshot(fields: tuple[str, ...]) -> ScopeWaveformTransferStateSnapshot:
    return ScopeWaveformTransferStateSnapshot(
        captured_fields=fields,
        **{_TOKEN_ATTRS[field]: f"token-{index}" for index, field in enumerate(fields)},
    )


class _Backend:
    _wavebench_binary_budget_parameters = True
    resource = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.binary_trailing: list[bytes] = []
        self.fail_binary_sync = False
        self.closed = 0

    def record_event(self, direction: str, text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append(command)
        return "EXAMPLE,EX1" if command == "*IDN?" else "ok"

    def query_float_list(self, command: str, *, timeout_ms=None, replay=ReplayPolicy.NO_REPLAY):
        self.queries.append(command)
        return [1.0]

    def query_bin_block(self, command: str, *, replay=ReplayPolicy.NO_REPLAY) -> bytes:
        self.queries.append(command)
        return b"legacy"

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
        self.queries.append(command)
        self.binary_trailing.append(_transport_trailing)
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
        assert framing is BinaryResponseFraming.DEFINITE_BLOCK
        assert len(payload) <= max_bytes
        return BinaryQueryResult(
            data=payload,
            framing=framing,
            declared_length=len(payload),
            framing_header_bytes=3,
            consumed_bytes=3 + len(payload) + len(_transport_trailing),
            transport_trailing_bytes=_transport_trailing,
        )

    def query_opc(self, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append("*OPC?")
        return "1"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def write_bytes(self, command: bytes) -> None:
        self.writes.append("BINARY_WRITE")

    def close(self) -> None:
        self.closed += 1


def _waveform(channel: int = 1) -> WaveformData:
    return WaveformData(
        channel=channel,
        header=WaveformHeader(0.0, 1e-3, 2),
        voltages_v=np.array([0.0, 1.0]),
    )


def _bounded_transport(
    backend: _Backend,
    state: InstrumentSessionState,
) -> GuardedAuditedTransport:
    transport = GuardedAuditedTransport(backend, session_state=state)
    transport._mark_bounded_binary_backend_verified()
    return transport


class _Driver:
    def __init__(self, transport: GuardedAuditedTransport) -> None:
        self.transport = transport
        self.fail_after_binary = False
        self.use_legacy_binary = False
        self.skip_binary = False
        self.use_safe_binary_replay = False
        self.use_message_binary_framing = False
        self.restore_calls = 0
        self.restore_fail = False
        self.verify_calls = 0
        self.drain_calls = 0
        self.legacy_fetch_calls = 0
        self.fail_channel: int | None = None
        self.return_channel_override: int | None = None
        self.multi_result_override: dict[int, WaveformData] | None = None
        self.callback_waveform_override: WaveformData | None = None
        self.duplicate_waveform_callback = False
        self.skip_waveform_callbacks = False

    def idn(self) -> str:
        return self.transport.query("*IDN?", replay=ReplayPolicy.NO_REPLAY)

    def close(self) -> None:
        self.transport.close()

    def snapshot_waveform_transfer_state(self, fields):
        self.transport.query("SNAP?", replay=ReplayPolicy.NO_REPLAY)
        return _snapshot(tuple(fields))

    def restore_waveform_transfer_state(self, baseline):
        self.restore_calls += 1
        self.transport.write("RESTORE")
        if self.restore_fail:
            return ScopeWaveformTransferRestoreResult("failed", (), ())
        return ScopeWaveformTransferRestoreResult(
            "completed",
            baseline.restore_order,
            baseline.restore_order,
        )

    def verify_waveform_transfer_state_restored(self, baseline):
        self.verify_calls += 1
        self.transport.query("VERIFY?", replay=ReplayPolicy.NO_REPLAY)
        return _snapshot(tuple(baseline.snapshot.captured_fields))

    def fetch_waveform_bounded(self, channel, points="dmax", *, baseline):
        self.transport.query("PRE?", replay=ReplayPolicy.NO_REPLAY)
        if self.use_legacy_binary:
            self.transport.query_bin_block("DATA?", replay=ReplayPolicy.NO_REPLAY)
            raise AssertionError("legacy binary query unexpectedly returned")
        if not self.skip_binary:
            self.transport.query_binary(
                "DATA?",
                framing=(
                    BinaryResponseFraming.MESSAGE
                    if self.use_message_binary_framing
                    else BinaryResponseFraming.DEFINITE_BLOCK
                ),
                max_bytes=2,
                replay=(
                    ReplayPolicy.SAFE_TO_REPLAY
                    if self.use_safe_binary_replay
                    else ReplayPolicy.NO_REPLAY
                ),
            )
        if self.fail_after_binary:
            raise DataError("conversion failed")
        return _waveform(
            self.return_channel_override
            if self.return_channel_override is not None
            else channel
        )

    def capture_waveform_bounded(
        self,
        channel,
        points="dmax",
        *,
        time_range_s=None,
        vertical_scale_v_per_div=None,
        baseline,
    ):
        return self.fetch_waveform_bounded(channel, points, baseline=baseline)

    def capture_waveforms_bounded(
        self,
        channels,
        points="dmax",
        *,
        time_range_s=None,
        vertical_scale_v_per_div=None,
        on_channel_start=None,
        on_waveform=None,
        baseline,
    ):
        if self.multi_result_override is not None:
            self.transport.query_binary(
                "DATA?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=2,
                replay=ReplayPolicy.NO_REPLAY,
            )
            return self.multi_result_override
        result: dict[int, WaveformData] = {}
        for channel in channels:
            if on_channel_start is not None:
                on_channel_start(channel)
            if self.fail_channel == channel:
                raise DataError(f"CH{channel} bounded read failed")
            waveform = self.fetch_waveform_bounded(channel, points, baseline=baseline)
            result[channel] = waveform
            if on_waveform is not None and not self.skip_waveform_callbacks:
                callback_waveform = (
                    self.callback_waveform_override
                    if self.callback_waveform_override is not None
                    and self.callback_waveform_override.channel == channel
                    else waveform
                )
                on_waveform(channel, callback_waveform)
                if self.duplicate_waveform_callback:
                    on_waveform(channel, waveform)
        return result

    def drain_errors(self, *, max_records: int) -> ErrorDrainResult:
        self.drain_calls += 1
        self.transport.query("ERR?", replay=ReplayPolicy.NO_REPLAY)
        return ErrorDrainResult(records=(), terminated=True, query_count=1)

    def fetch_waveform(self, *args, **kwargs):
        self.legacy_fetch_calls += 1
        raise AssertionError("legacy waveform route must not be used by an opt-in descriptor")


def _profile(*, operation_kind: str = "fetch", trailing: str = "0d0a") -> ScopeWaveformBinaryProfile:
    fields = _fields_for(operation_kind)
    return ScopeWaveformBinaryProfile(
        operations=(
            ScopeWaveformBinaryOperationProfile(
                operation_kind=operation_kind,  # type: ignore[arg-type]
                response_max_bytes=1_024,
                operation_max_bytes=4_096,
                query_max_count=4,
                resynchronization_max_bytes=0,
                restore_order=fields,
                snapshot_max_steps=len(fields),
                restore_max_steps=len(fields),
                verify_max_steps=len(fields),
            ),
        ),
        transport_trailing_hex=trailing,
    )


def _descriptor(
    *,
    operation_kind: str = "fetch",
    error_drain: bool = False,
) -> InstrumentDescriptor:
    capability = {
        "fetch": "scope.fetch_waveform",
        "capture_single": "scope.capture_waveform",
        "capture_multiple": "scope.capture_waveforms",
    }[operation_kind]
    capabilities = ("scope.idn", capability) + (("scope.error_drain_v1",) if error_drain else ())
    return InstrumentDescriptor(
        driver_id="example.waveform",
        kind="scope",
        display_name="Example",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities,
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: object(),
        wavebench_min_version="0.8.24",
        scope_extensions=ScopeDescriptorExtensions(
            waveform_binary_profile=_profile(operation_kind=operation_kind)
        ),
    )


def _executor(
    *,
    operation_kind: str = "fetch",
    error_drain: bool = False,
) -> tuple[BoundedWaveformExecutor, _Driver, _Backend, InstrumentSessionState]:
    backend = _Backend()
    state = InstrumentSessionState(epoch_id="bounded-epoch")
    transport = _bounded_transport(backend, state)
    driver = _Driver(transport)
    return (
        BoundedWaveformExecutor(
            driver=driver,
            descriptor=_descriptor(operation_kind=operation_kind, error_drain=error_drain),
            session_state=state,
            connection_timeout_ms=5_000,
            transport=transport,
        ),
        driver,
        backend,
        state,
    )


def test_bounded_fetch_uses_profile_trailing_one_ledger_and_core_owned_recovery() -> None:
    executor, driver, backend, state = _executor()

    result = executor.fetch(channel=1, points="DEF", check_errors=False)

    assert isinstance(result.value, WaveformData)
    assert result.identity == "EXAMPLE,EX1"
    assert backend.binary_trailing == [b"\r\n"]
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert backend.writes == ["RESTORE"]
    assert state.health is SessionHealth.HEALTHY
    assert result.diagnostics["scope_operation"]["binary_budget"]["remaining_query_count"] == 3


def test_bounded_executor_rejects_unverified_transport_before_any_io() -> None:
    backend = _Backend()
    state = InstrumentSessionState(epoch_id="unverified-bounded-epoch")
    transport = GuardedAuditedTransport(backend, session_state=state)
    driver = _Driver(transport)

    with pytest.raises(ConfigError, match="factory-validated"):
        BoundedWaveformExecutor(
            driver=driver,
            descriptor=_descriptor(),
            session_state=state,
            connection_timeout_ms=5_000,
            transport=transport,
        )

    assert backend.queries == []
    assert backend.writes == []


def test_proven_data_failure_restores_but_lost_sync_never_attempts_cleanup() -> None:
    executor, driver, backend, state = _executor()
    driver.fail_after_binary = True

    with pytest.raises(DataError, match="conversion failed"):
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY

    executor, driver, backend, state = _executor()
    backend.fail_binary_sync = True

    with pytest.raises(TransportIOError) as raised:
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert raised.value.synchronization is Synchronization.LOST
    assert driver.restore_calls == 0
    assert driver.verify_calls == 0
    assert backend.writes == []
    assert state.health is SessionHealth.POISONED


def test_bounded_driver_cannot_fall_back_to_legacy_binary_entry() -> None:
    executor, driver, backend, state = _executor()
    driver.use_legacy_binary = True

    with pytest.raises(TransportIOError) as raised:
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert raised.value.reason_code == "binary_legacy_entry_unsupported"
    assert backend.binary_trailing == []
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_driver_cannot_opt_into_binary_replay() -> None:
    executor, driver, backend, state = _executor()
    driver.use_safe_binary_replay = True

    with pytest.raises(TransportIOError) as raised:
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert raised.value.reason_code == "binary_replay_unsupported"
    assert backend.binary_trailing == []
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_driver_cannot_change_the_profile_binary_framing() -> None:
    executor, driver, backend, state = _executor()
    driver.use_message_binary_framing = True

    with pytest.raises(TransportIOError) as raised:
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert raised.value.reason_code == "binary_framing_profile_unsupported"
    assert backend.binary_trailing == []
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_driver_must_issue_at_least_one_binary_query() -> None:
    executor, driver, _, state = _executor()
    driver.skip_binary = True

    with pytest.raises(DataError, match="did not issue a binary query"):
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_restore_failure_prevents_success_and_poison_latches_the_session() -> None:
    executor, driver, _, state = _executor()
    driver.restore_fail = True

    with pytest.raises(InstrumentError, match="restore did not complete"):
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.POISONED


def test_bounded_single_waveform_requires_the_requested_channel() -> None:
    executor, driver, _, state = _executor()
    driver.return_channel_override = 2

    with pytest.raises(DataError, match="mismatched channel"):
        executor.fetch(channel=1, points="DEF", check_errors=False)

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_path_uses_typed_error_drain_and_service_dispatches_before_legacy_gate() -> None:
    executor, driver, backend, _ = _executor(error_drain=True)

    result = executor.fetch(channel=1, points="DEF", check_errors=True)

    assert isinstance(result.value, WaveformData)
    assert driver.drain_calls == 2
    assert driver.legacy_fetch_calls == 0

    executor, _, backend, _ = _executor(error_drain=False)
    with pytest.raises(ConfigError, match="scope.error_drain_v1"):
        executor.fetch(channel=1, points="DEF", check_errors=True)
    assert backend.queries == []

    backend = _Backend()
    state = InstrumentSessionState(epoch_id="service-bounded")
    driver = _Driver(_bounded_transport(backend, state))
    descriptor = _descriptor()
    service = ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.waveform", access="read_write", check_errors=False),
            connection=SimpleNamespace(timeout_ms=5_000),
            waveform=SimpleNamespace(format="real", byte_order="lsbf", points="DEF"),
        ),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor,
        transport=driver.transport,
        session_state=state,
    )

    waveform = service.fetch_waveform(1)

    assert isinstance(waveform, WaveformData)
    assert driver.legacy_fetch_calls == 0


def test_bounded_multi_capture_preserves_callbacks_and_one_cleanup_transaction() -> None:
    executor, driver, _, _ = _executor(operation_kind="capture_multiple")
    events: list[tuple[str, int]] = []

    result = executor.capture_multiple(
        channels=[1, 2],
        points="DEF",
        time_range_s=None,
        vertical_scale_v_per_div=None,
        check_errors=False,
        on_channel_start=lambda channel: events.append(("start", int(channel))),
        on_waveform=lambda channel, waveform: events.append(("waveform", channel)),
    )

    assert set(result.value) == {1, 2}
    assert events == [("start", 1), ("waveform", 1), ("start", 2), ("waveform", 2)]
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1


def test_bounded_single_capture_uses_complete_recovery_closure() -> None:
    executor, driver, _, state = _executor(operation_kind="capture_single")

    result = executor.capture_single(
        channel=1,
        points="DEF",
        time_range_s=0.001,
        vertical_scale_v_per_div=0.5,
        check_errors=False,
    )

    assert isinstance(result.value, WaveformData)
    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


@pytest.mark.parametrize(
    ("returned", "message"),
    (
        ({1: _waveform(1)}, "channel set"),
        ({1: _waveform(2), 2: _waveform(2)}, "mismatched channel"),
    ),
)
def test_bounded_multi_capture_rejects_incomplete_or_mismatched_return_maps(
    returned: dict[int, WaveformData],
    message: str,
) -> None:
    executor, driver, _, state = _executor(operation_kind="capture_multiple")
    driver.multi_result_override = returned

    with pytest.raises(DataError, match=message):
        executor.capture_multiple(
            channels=[1, 2],
            points="DEF",
            time_range_s=None,
            vertical_scale_v_per_div=None,
            check_errors=False,
            on_channel_start=None,
            on_waveform=None,
        )

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_multi_capture_reconciles_callbacks_with_the_return_map() -> None:
    executor, driver, _, state = _executor(operation_kind="capture_multiple")
    driver.callback_waveform_override = WaveformData(
        channel=1,
        header=WaveformHeader(0.0, 1e-3, 2),
        voltages_v=np.array([9.0, 10.0]),
    )

    with pytest.raises(DataError, match="does not match its callback"):
        executor.capture_multiple(
            channels=[1, 2],
            points="DEF",
            time_range_s=None,
            vertical_scale_v_per_div=None,
            check_errors=False,
            on_channel_start=lambda channel: None,
            on_waveform=lambda channel, waveform: None,
        )

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_multi_capture_rejects_duplicate_waveform_callbacks() -> None:
    executor, driver, _, state = _executor(operation_kind="capture_multiple")
    driver.duplicate_waveform_callback = True

    with pytest.raises(DataError, match="more than once"):
        executor.capture_multiple(
            channels=[1, 2],
            points="DEF",
            time_range_s=None,
            vertical_scale_v_per_div=None,
            check_errors=False,
            on_channel_start=lambda channel: None,
            on_waveform=lambda channel, waveform: None,
        )

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_multi_capture_requires_callbacks_when_the_caller_provides_one() -> None:
    executor, driver, _, state = _executor(operation_kind="capture_multiple")
    driver.skip_waveform_callbacks = True

    with pytest.raises(DataError, match="did not emit exactly one"):
        executor.capture_multiple(
            channels=[1, 2],
            points="DEF",
            time_range_s=None,
            vertical_scale_v_per_div=None,
            check_errors=False,
            on_channel_start=lambda channel: None,
            on_waveform=lambda channel, waveform: None,
        )

    assert driver.restore_calls == 1
    assert driver.verify_calls == 1
    assert state.health is SessionHealth.HEALTHY


def test_bounded_scope_service_preserves_partial_multichannel_artifacts() -> None:
    with TemporaryDirectory() as tmp:
        backend = _Backend()
        state = InstrumentSessionState(epoch_id="service-bounded-multi")
        driver = _Driver(_bounded_transport(backend, state))
        driver.fail_channel = 2
        config = WaveBenchConfig(
            connection=ConnectionConfig(
                backend="lan",
                resource="TCPIP::fake::INSTR",
                timeout_ms=5_000,
                opc_timeout_ms=5_000,
            ),
            scope=ScopeConfig(
                driver="example.waveform",
                model_hint=None,
                default_channel=1,
                reset_before_run=False,
                check_errors=False,
            ),
            autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
            waveform=WaveformConfig(
                format="real",
                byte_order="lsbf",
                points="DEF",
                time_range_s=0.001,
            ),
            output=OutputConfig(
                directory=Path(tmp),
                package_naming="timestamp_label",
                save_csv=False,
                save_npy=True,
                save_json=True,
                save_commands_log=False,
                save_screenshot=False,
            ),
            source_path=Path(tmp) / "wavebench.toml",
        )
        service = ScopeService(
            config=config,
            logger=CommandLogger(),
            session=driver,
            descriptor=_descriptor(operation_kind="capture_multiple"),
            transport=driver.transport,
            session_state=state,
        )

        with pytest.raises(DataError, match="CH2 bounded read failed"):
            service.capture_waveforms(channels=[1, 2], label="bounded-partial")

        [failed_dir] = Path(tmp).glob("*bounded-partial_failed")
        assert (failed_dir / "ch1.npy").exists()
        assert not (failed_dir / "ch2.npy").exists()
        metadata = json.loads((failed_dir / "metadata.partial.json").read_text("utf-8"))
        assert metadata["completed_channels"] == [1]
        assert metadata["failed_channel"] == 2
        assert metadata["stage"] == "read_waveform"
        assert "scope_operation_diagnostics" in metadata
