from __future__ import annotations

import numpy as np
import pytest
import zlib

import wavebench.services as public_services
from wavebench.errors import DataError, InstrumentError
from wavebench.instruments import InstrumentDescriptor
from wavebench.instruments.scope_extensions import (
    DriverErrorRecord,
    ErrorCheckSpec,
    ErrorDrainResult,
    ScopeAcquisitionCompletion,
    ScopeAcquisitionControlProfile,
    ScopeAcquisitionControlSnapshot,
    ScopeAcquisitionRunState,
    ScopeAxisMetadata,
    ScopeBaselineRestoreResult,
    ScopeContinuousAcquisitionRequest,
    ScopeDescriptorExtensions,
    ScopeScreenshot,
    ScopeScreenshotProfile,
    ScopeScreenshotRequest,
    ScopeScreenshotRestoreResult,
    ScopeScreenshotStateSnapshot,
    ScopeScreenshotVariant,
    ScopeTraceData,
    ScopeTraceMetadata,
    ScopeTraceProfile,
    ScopeTraceRef,
    ScopeTraceTransferRestoreResult,
    ScopeTraceTransferStateSnapshot,
)
from wavebench.services.scope_extension_service import (
    ExperimentalScopeExtensionService,
    ScopeExtensionService,
)
from wavebench.transport.contracts import (
    BinaryQueryResult,
    BinaryResponseFraming,
    ReplayPolicy,
)
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.session import SessionHealth


def _png(width: int = 2, height: int = 3) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return len(payload).to_bytes(4, "big") + kind + payload + crc.to_bytes(4, "big")

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    rows = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class _Backend:
    resource = "fake"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []
        self.binary_queries = 0

    def record_event(self, direction: str, text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append(command)
        return "ok"

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]:
        self.queries.append(command)
        return [1.0]

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        self.queries.append(command)
        return b"data"

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
        self.binary_queries += 1
        data = _png() if "SCREEN" in command else b"\x01\x02"
        assert len(data) <= max_bytes
        if framing is BinaryResponseFraming.MESSAGE:
            return BinaryQueryResult(data, framing, None, 0, len(data))
        header_bytes = 2 + len(str(len(data)))
        return BinaryQueryResult(
            data,
            framing,
            len(data),
            header_bytes,
            header_bytes + len(data) + len(_transport_trailing),
            _transport_trailing,
        )

    def query_opc(self, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append("*OPC?")
        return "1"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def write_bytes(self, command: bytes) -> None:
        self.writes.append("BINARY_WRITE")

    def close(self) -> None:
        pass


TRACE_FIELDS = (
    "scope.run_state",
    "scope.waveform_source",
    "scope.waveform_mode",
    "scope.query_response_header",
    "scope.waveform_format",
    "scope.waveform_byte_order",
    "scope.waveform_points",
    "scope.waveform_transfer_window",
)


class _Driver:
    def __init__(self, transport: GuardedAuditedTransport) -> None:
        self.transport = transport
        self.fail_screenshot = False
        self.trace_verify_mismatch = False
        self.fail_single_completion = False
        self.fail_stop_once = False
        self.restore_calls = 0
        self.error_records: tuple[DriverErrorRecord, ...] = ()
        self.error_queries = 0
        self.extra_error_query = False
        self.screenshot_profile = ScopeScreenshotProfile(
            (
                ScopeScreenshotVariant(
                    request=ScopeScreenshotRequest(
                        menu_mode="exclude",
                        color_mode="color",
                    ),
                    media_type="image/png",
                    framing=BinaryResponseFraming.DEFINITE_BLOCK,
                    response_max_bytes=262_144,
                    operation_max_bytes=262_144,
                    resynchronization_max_bytes=0,
                    changed_fields=("scope.display_menu", "scope.display_color"),
                    restore_order=("scope.display_menu", "scope.display_color"),
                    snapshot_max_steps=2,
                    restore_max_steps=2,
                    verify_max_steps=2,
                    width_px=(2, 2),
                    height_px=(3, 3),
                ),
            )
        )
        self.acquisition_profile = ScopeAcquisitionControlProfile(
            supported_continuous_modes=("auto", "normal"),
            single_arm_semantics="configure_then_arm",
            arm_resets_acquisition_count=False,
            failure_restore_order=("scope.trigger", "scope.acquisition"),
            snapshot_max_steps=3,
            restore_max_steps=3,
            verify_max_steps=3,
            identity_semantics="unique_within_session_epoch",
        )
        self.trace_profile = ScopeTraceProfile(
            fetchable_kinds=("analog",),
            max_points=1_000,
            restore_order=TRACE_FIELDS,
            snapshot_max_steps=8,
            restore_max_steps=8,
            verify_max_steps=8,
        )
        self.screenshot_snapshot = ScopeScreenshotStateSnapshot(
            captured_fields=("scope.display_menu", "scope.display_color"),
            menu_state_token="MENU_ON",
            color_state_token="COLOR",
        )
        self.run_state = ScopeAcquisitionRunState(
            "stopped",
            "normal",
            "STOP",
            acquisition_identity="old",
        )
        self.trigger_token = "TRIGGER_NORMAL"
        self.acquisition_token = "ACQ_NORMAL"
        self.trace_snapshot = ScopeTraceTransferStateSnapshot(
            captured_fields=TRACE_FIELDS,
            run_state_token="STOP",
            waveform_source_token="C1",
            waveform_mode_token="NORMAL",
            query_response_header_token="OFF",
            waveform_format_token="BYTE",
            waveform_byte_order_token="LSB",
            waveform_points_token="DMAX",
            waveform_transfer_window_token="ALL",
        )

    def close(self) -> None:
        pass

    def idn(self) -> str:
        self.transport.query("*IDN?")
        return "EXAMPLE,SCOPE"

    def get_screenshot_profile(self) -> ScopeScreenshotProfile:
        return self.screenshot_profile

    def snapshot_screenshot_state(self, fields):
        for field_name in fields:
            self.transport.query(f"SNAP:{field_name}?")
        return self.screenshot_snapshot

    def capture_screenshot(self, request, *, baseline):
        self.transport.write("MENU EXCLUDE")
        self.transport.write("COLOR COLOR")
        binary = self.transport.query_binary(
            "SCREEN?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=262_144,
        )
        if self.fail_screenshot:
            raise DataError("screenshot parser failed")
        return ScopeScreenshot(
            data=binary.data,
            media_type="image/png",
            width_px=2,
            height_px=3,
            requested=request,
            effective=request,
            framing=binary.framing,
        )

    def restore_screenshot_state(self, baseline):
        self.restore_calls += 1
        for field_name in baseline.restore_order:
            self.transport.write(f"RESTORE:{field_name}")
        return ScopeScreenshotRestoreResult(
            "completed",
            baseline.restore_order,
            baseline.restore_order,
        )

    def verify_screenshot_state_restored(self, fields, baseline):
        for field_name in fields:
            self.transport.query(f"VERIFY:{field_name}?")
        return baseline.snapshot

    def get_acquisition_run_state(self):
        self.transport.query("RUN_STATE?")
        return self.run_state

    def snapshot_acquisition_control(self):
        self.transport.query("RUN_STATE?")
        self.transport.query("TRIGGER?")
        self.transport.query("ACQUISITION?")
        return ScopeAcquisitionControlSnapshot(
            self.run_state,
            self.trigger_token,
            self.acquisition_token,
        )

    def start_continuous(self, *, trigger_mode, baseline):
        self.transport.write(f"TRIGGER {trigger_mode}")
        self.transport.write("RUN")
        self.transport.query("RUN_STATE?")
        self.run_state = ScopeAcquisitionRunState("acquiring", trigger_mode, "RUN")
        return self.run_state

    def stop_acquisition(self):
        if self.fail_stop_once:
            self.fail_stop_once = False
            raise DataError("stop result invalid")
        self.transport.write("STOP")
        self.transport.query("RUN_STATE?")
        self.run_state = ScopeAcquisitionRunState("stopped", "normal", "STOP")
        return self.run_state

    def acquire_single(self, *, baseline, deadline):
        self.transport.write("TRIGGER SINGLE")
        self.transport.query("READY?")
        proof_baseline = ScopeAcquisitionRunState(
            "ready",
            "single",
            "READY",
            acquisition_identity="old",
        )
        self.transport.write("SINGLE")
        self.transport.query("ARMING?")
        armed = ScopeAcquisitionRunState(
            "arming",
            "single",
            "ARM",
            acquisition_identity="old",
        )
        self.transport.query("COMPLETE?")
        completed = ScopeAcquisitionRunState(
            "stopped",
            "single",
            "STOP",
            acquisition_identity=("old" if self.fail_single_completion else "new"),
        )
        return ScopeAcquisitionCompletion(
            state=completed,
            original_state=baseline.snapshot.run_state,
            proof_baseline_state=proof_baseline,
            proof_baseline_stage="configured_pre_arm",
            proof="identity_delta",
            baseline_identity="old",
            completed_identity=completed.acquisition_identity,
            observed_states=(armed, completed),
        )

    def restore_acquisition_control(self, baseline):
        self.restore_calls += 1
        for field_name in baseline.restore_order:
            self.transport.write(f"RESTORE:{field_name}")
        self.run_state = ScopeAcquisitionRunState("stopped", "normal", "STOP")
        self.trigger_token = baseline.snapshot.trigger_state_token
        self.acquisition_token = baseline.snapshot.acquisition_state_token
        return ScopeBaselineRestoreResult(
            "completed",
            baseline.restore_order,
            baseline.restore_order,
        )

    def verify_acquisition_control_restored(self, baseline):
        self.transport.query("VERIFY:RUN?")
        self.transport.query("VERIFY:TRIGGER?")
        self.transport.query("VERIFY:ACQUISITION?")
        return ScopeAcquisitionControlSnapshot(
            self.run_state,
            self.trigger_token,
            self.acquisition_token,
        )

    def get_trace_metadata(self, source):
        self.transport.query("TRACE:METADATA?")
        return ScopeTraceMetadata(
            source=source,
            x_axis=ScopeAxisMetadata("time", "s", 0.0, 1e-9, 2),
            y_unit="v",
            y_semantics="linear",
            value_encoding="real",
            operation="identity",
            fetchable=True,
        )

    def snapshot_trace_transfer_state(self, fields):
        for field_name in fields:
            self.transport.query(f"SNAP:{field_name}?")
        return self.trace_snapshot

    def fetch_trace(self, source, *, points="dmax", baseline=None):
        self.transport.write("TRACE:SOURCE C1")
        self.transport.query_binary(
            "TRACE:DATA?",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            max_bytes=8_388_608,
        )
        return ScopeTraceData(self.get_trace_metadata(source), np.array([1.0, 2.0]))

    def restore_trace_transfer_state(self, baseline):
        self.restore_calls += 1
        for field_name in baseline.restore_order:
            self.transport.write(f"RESTORE:{field_name}")
        return ScopeTraceTransferRestoreResult(
            "completed",
            baseline.restore_order,
            baseline.restore_order,
        )

    def verify_trace_transfer_state_restored(self, baseline):
        for field_name in baseline.restore_order:
            self.transport.query(f"VERIFY:{field_name}?")
        if not self.trace_verify_mismatch:
            return baseline.snapshot
        return ScopeTraceTransferStateSnapshot(
            captured_fields=TRACE_FIELDS,
            run_state_token="STOP",
            waveform_source_token="C1",
            waveform_mode_token="NORMAL",
            query_response_header_token="OFF",
            waveform_format_token="BYTE",
            waveform_byte_order_token="MSB",
            waveform_points_token="DMAX",
            waveform_transfer_window_token="ALL",
        )

    def drain_errors(self, *, max_records):
        for _ in range(len(self.error_records) + 1):
            self.transport.query("ERROR?")
            self.error_queries += 1
        if self.extra_error_query:
            self.transport.query("ERROR:EXTRA?")
            self.error_queries += 1
        return ErrorDrainResult(
            records=self.error_records,
            terminated=True,
            query_count=len(self.error_records) + 1,
        )


def _service(*, error_capability: bool = False):
    backend = _Backend()
    transport = GuardedAuditedTransport(backend)
    driver = _Driver(transport)
    capabilities = [
        "scope.idn",
        "scope.screenshot_profile",
        "scope.screenshot_v2",
        "scope.acquisition_run_state",
        "scope.acquisition_control",
        "scope.trace_metadata",
        "scope.fetch_trace",
    ]
    if error_capability:
        capabilities.append("scope.error_drain_v1")
    descriptor = InstrumentDescriptor(
        driver_id="example.scope",
        kind="scope",
        display_name="Example",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=tuple(capabilities),
        idn_patterns=("EXAMPLE",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda context: driver,
        wavebench_min_version="0.8.23",
        scope_extensions=ScopeDescriptorExtensions(
            screenshot_profile=driver.screenshot_profile,
            acquisition_control_profile=driver.acquisition_profile,
            trace_profile=driver.trace_profile,
        ),
    )
    service = ExperimentalScopeExtensionService(
        driver=driver,
        descriptor=descriptor,
        session_state=transport.session_state,
        connection_timeout_ms=1_000,
        enabled=True,
    )
    return service, driver, transport, backend


def test_screenshot_success_restores_and_verifies_before_return() -> None:
    service, driver, transport, backend = _service()
    request = ScopeScreenshotRequest(menu_mode="exclude", color_mode="color")

    result = service.screenshot_v2(request)

    assert isinstance(result.value, ScopeScreenshot)
    assert transport.session_state.health is SessionHealth.HEALTHY
    assert driver.restore_calls == 1
    assert backend.binary_queries == 1
    assert [phase["phase"] for phase in result.diagnostics["phases"]] == [
        "preflight",
        "main",
        "success_restore",
        "cleanup_verification",
    ]


def test_public_service_needs_no_experimental_enable_and_freezes_artifact_schema() -> None:
    internal, driver, transport, _ = _service()
    service = ScopeExtensionService(
        driver=driver,
        descriptor=internal.descriptor,
        session_state=transport.session_state,
        connection_timeout_ms=1_000,
    )

    result = service.screenshot_v2(
        ScopeScreenshotRequest(menu_mode="exclude", color_mode="color")
    )
    payload = result.as_dict()

    assert payload["schema"] == "wavebench.scope.result.v1"
    assert payload["diagnostics"]["schema"] == "wavebench.scope.operation.v1"
    assert payload["result"]["payload_bytes"] == len(result.value.data)
    assert "data" not in payload["result"]
    assert driver.restore_calls == 1
    assert public_services.ScopeExtensionService is ScopeExtensionService


def test_screenshot_main_failure_is_primary_but_cleanup_can_restore_health() -> None:
    service, driver, transport, _ = _service()
    driver.fail_screenshot = True

    with pytest.raises(DataError, match="parser failed") as raised:
        service.screenshot_v2(
            ScopeScreenshotRequest(menu_mode="exclude", color_mode="color")
        )

    assert transport.session_state.health is SessionHealth.HEALTHY
    assert driver.restore_calls == 1
    diagnostics = raised.value.scope_operation_diagnostics
    assert diagnostics["cleanup_error"] is None
    assert diagnostics["screenshot"]["verification"]["status"] == "verified"


def test_trace_success_and_verification_mismatch_paths_are_fail_closed() -> None:
    service, driver, transport, _ = _service()
    source = ScopeTraceRef("analog", index=1)
    result = service.fetch_trace(source, points=2)
    assert isinstance(result.value, ScopeTraceData)
    assert transport.session_state.health is SessionHealth.HEALTHY
    assert result.diagnostics["trace_cleanup"]["verification"]["status"] == "verified"

    service, driver, transport, _ = _service()
    driver.trace_verify_mismatch = True
    with pytest.raises(ValueError, match="mismatched"):
        service.fetch_trace(source, points=2)
    assert transport.session_state.health is SessionHealth.POISONED


def test_acquisition_success_keeps_postcondition_and_failure_restores_baseline() -> None:
    service, driver, transport, _ = _service()
    started = service.start_acquisition(
        ScopeContinuousAcquisitionRequest("normal")
    )
    assert started.value.phase == "acquiring"
    assert driver.restore_calls == 0
    assert transport.session_state.health is SessionHealth.HEALTHY
    assert "scope.identity" in transport.session_state.verified_fields
    assert "scope.run_state" not in transport.session_state.verified_fields
    assert "scope.trigger" not in transport.session_state.verified_fields

    service, driver, transport, _ = _service()
    driver.fail_single_completion = True
    with pytest.raises(ValueError, match="identity proof") as raised:
        service.acquire_single()
    assert driver.restore_calls == 1
    assert transport.session_state.health is SessionHealth.HEALTHY
    assert raised.value.scope_operation_diagnostics["cleanup"]["verification"]["status"] == "verified"


def test_failed_normal_stop_uses_bounded_recovery_stop_and_preserves_primary() -> None:
    service, driver, transport, _ = _service()
    driver.run_state = ScopeAcquisitionRunState("acquiring", "normal", "RUN")
    driver.fail_stop_once = True

    with pytest.raises(DataError, match="stop result invalid"):
        service.stop_acquisition()

    assert transport.session_state.health is SessionHealth.HEALTHY
    assert driver.run_state.phase == "stopped"


def test_required_error_check_stops_before_main_and_disabled_is_zero_io() -> None:
    service, driver, transport, backend = _service(error_capability=True)
    driver.error_records = (
        DriverErrorRecord(1, "TCPIP::private::INSTR failed", "error", "queue"),
    )
    with pytest.raises(InstrumentError, match="prevent") as raised:
        service.screenshot_v2(
            ScopeScreenshotRequest(menu_mode="exclude", color_mode="color"),
            error_check=ErrorCheckSpec("required", timing="before"),
        )
    assert backend.binary_queries == 0
    assert not any(command.startswith("MENU ") for command in backend.writes)
    error_artifact = raised.value.scope_operation_diagnostics["error_check"]
    assert error_artifact["status"] == "failed"
    assert error_artifact["checks"][0]["records"][0]["message_redacted"] is True
    assert transport.session_state.health is SessionHealth.HEALTHY

    service, driver, _, backend = _service(error_capability=True)
    service.screenshot_v2(
        ScopeScreenshotRequest(menu_mode="exclude", color_mode="color"),
        error_check=ErrorCheckSpec("disabled"),
    )
    assert driver.error_queries == 0
    assert backend.binary_queries == 1


def test_experimental_service_gate_is_closed_by_default() -> None:
    service, driver, transport, _ = _service()
    with pytest.raises(Exception, match="disabled"):
        ExperimentalScopeExtensionService(
            driver=driver,
            descriptor=service.descriptor,
            session_state=transport.session_state,
            connection_timeout_ms=1_000,
        )


def test_error_drain_query_count_must_match_guarded_transport_evidence() -> None:
    service, driver, _, backend = _service(error_capability=True)
    driver.extra_error_query = True
    with pytest.raises(ValueError, match="query_count"):
        service.screenshot_v2(
            ScopeScreenshotRequest(menu_mode="exclude", color_mode="color"),
            error_check=ErrorCheckSpec("required", timing="before"),
        )
    assert backend.binary_queries == 0
