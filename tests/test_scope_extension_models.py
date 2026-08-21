from __future__ import annotations

import numpy as np
import pytest
import zlib

import wavebench.instruments as public_instruments
from wavebench.instruments.scope_extensions import (
    DriverErrorRecord,
    ErrorDrainResult,
    ScopeAcquisitionCompletion,
    ScopeAcquisitionControlBaseline,
    ScopeAcquisitionControlProfile,
    ScopeAcquisitionControlSnapshot,
    ScopeAcquisitionRunState,
    ScopeAxisMetadata,
    ScopeDescriptorExtensions,
    ScopeScreenshot,
    ScopeScreenshotProfile,
    ScopeScreenshotRequest,
    ScopeScreenshotStateSnapshot,
    ScopeScreenshotVariant,
    ScopeTraceData,
    ScopeTraceMetadata,
    ScopeTraceProfile,
    ScopeTraceRef,
    ScopeTraceTransferStateSnapshot,
    validate_acquisition_completion,
)
from wavebench.transport.contracts import BinaryResponseFraming


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


def test_scope_extension_types_are_exported_from_stable_instrument_api() -> None:
    assert public_instruments.ScopeTraceData is ScopeTraceData
    assert public_instruments.ScopeScreenshotProfile is ScopeScreenshotProfile
    assert public_instruments.DriverErrorRecord is DriverErrorRecord


def test_screenshot_profile_uses_exact_request_tuples_and_fixed_limits() -> None:
    request = ScopeScreenshotRequest(menu_mode="device", color_mode="device")
    variant = ScopeScreenshotVariant(
        request=request,
        media_type="image/png",
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
        response_max_bytes=262_144,
        operation_max_bytes=262_144,
        resynchronization_max_bytes=0,
        changed_fields=(),
        restore_order=(),
        snapshot_max_steps=0,
        restore_max_steps=0,
        verify_max_steps=0,
    )
    profile = ScopeScreenshotProfile((variant,))

    assert profile.select(request) is variant
    with pytest.raises(ValueError, match="exactly one"):
        profile.select(ScopeScreenshotRequest(menu_mode="exclude"))
    with pytest.raises(ValueError, match="equal"):
        ScopeScreenshotVariant(
            request=request,
            media_type="image/png",
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
            response_max_bytes=100,
            operation_max_bytes=101,
            resynchronization_max_bytes=0,
            changed_fields=(),
            restore_order=(),
            snapshot_max_steps=0,
            restore_max_steps=0,
            verify_max_steps=0,
        )


def test_screenshot_state_tokens_and_png_dimensions_are_verified() -> None:
    ScopeScreenshotStateSnapshot(
        captured_fields=("scope.display_menu",),
        menu_state_token="MENU_OFF",
    )
    with pytest.raises(ValueError, match="menu token"):
        ScopeScreenshotStateSnapshot(captured_fields=("scope.display_menu",))

    screenshot = ScopeScreenshot(
        data=_png(),
        media_type="image/png",
        width_px=2,
        height_px=3,
        requested=ScopeScreenshotRequest(),
        effective=ScopeScreenshotRequest(),
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
    )
    assert screenshot.width_px == 2
    with pytest.raises(ValueError, match="dimensions"):
        ScopeScreenshot(
            data=_png(),
            media_type="image/png",
            width_px=9,
            height_px=3,
            requested=ScopeScreenshotRequest(),
            effective=ScopeScreenshotRequest(),
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
        )
    corrupted = bytearray(_png())
    corrupted[29] ^= 0x01
    with pytest.raises(ValueError, match="CRC"):
        ScopeScreenshot(
            data=bytes(corrupted),
            media_type="image/png",
            width_px=2,
            height_px=3,
            requested=ScopeScreenshotRequest(),
            effective=ScopeScreenshotRequest(),
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
        )


def _acquisition_profile(
    *,
    identity_semantics: str = "unique_within_session_epoch",
) -> ScopeAcquisitionControlProfile:
    return ScopeAcquisitionControlProfile(
        supported_continuous_modes=("auto", "normal"),
        single_arm_semantics="configure_then_arm",
        arm_resets_acquisition_count=False,
        failure_restore_order=("scope.trigger", "scope.acquisition"),
        snapshot_max_steps=3,
        restore_max_steps=3,
        verify_max_steps=3,
        identity_semantics=identity_semantics,  # type: ignore[arg-type]
    )


def test_acquisition_identity_proof_depends_on_descriptor_semantics() -> None:
    original = ScopeAcquisitionRunState("stopped", "normal", "STOP", acquisition_identity="old")
    proof_baseline = ScopeAcquisitionRunState("ready", "single", "READY", acquisition_identity="old")
    armed = ScopeAcquisitionRunState("arming", "single", "ARM", acquisition_identity="old")
    completed = ScopeAcquisitionRunState(
        "stopped",
        "single",
        "STOP",
        acquisition_identity="new",
    )
    snapshot = ScopeAcquisitionControlSnapshot(original, "TRIG", "ACQ")
    baseline = ScopeAcquisitionControlBaseline(
        "ctx",
        "epoch",
        "nonce",
        snapshot,
        ("scope.run_state", "scope.trigger", "scope.acquisition"),
    )
    result = ScopeAcquisitionCompletion(
        state=completed,
        original_state=original,
        proof_baseline_state=proof_baseline,
        proof_baseline_stage="configured_pre_arm",
        proof="identity_delta",
        baseline_identity="old",
        completed_identity="new",
        observed_states=(armed, completed),
    )

    validate_acquisition_completion(result, baseline=baseline, profile=_acquisition_profile())
    with pytest.raises(ValueError, match="identity proof"):
        validate_acquisition_completion(
            result,
            baseline=baseline,
            profile=_acquisition_profile(identity_semantics="unknown"),
        )


def test_trace_models_copy_arrays_and_enforce_r1_3_fetch_scope() -> None:
    metadata = ScopeTraceMetadata(
        source=ScopeTraceRef("analog", index=1),
        x_axis=ScopeAxisMetadata("time", "s", 0.0, 1e-9, 2),
        y_unit="v",
        y_semantics="linear",
        value_encoding="real",
        operation="identity",
        fetchable=True,
    )
    source = np.array([1.0, 2.0])
    trace = ScopeTraceData(metadata, source)
    source[0] = 99
    assert trace.values.tolist() == [1.0, 2.0]
    assert trace.values.dtype == np.float64
    assert trace.values.flags.writeable is False

    with pytest.raises(ValueError, match="R1.3 fetchable"):
        ScopeTraceMetadata(
            source=ScopeTraceRef("math", index=1),
            x_axis=ScopeAxisMetadata("time", "s", 0.0, 1e-9, 2),
            y_unit="v",
            y_semantics="linear",
            value_encoding="real",
            operation="device_other",
            fetchable=True,
        )


def test_digital_trace_is_single_line_bitmask_only() -> None:
    metadata = ScopeTraceMetadata(
        source=ScopeTraceRef("digital", index=3),
        x_axis=ScopeAxisMetadata("time", "s", 0.0, 1e-9, 3),
        y_unit="1",
        y_semantics="unknown",
        value_encoding="digital_bitmask",
        operation="identity",
        digital_channels=(3,),
        fetchable=True,
    )
    trace = ScopeTraceData(metadata, np.array([0, 8, 0], dtype=np.uint8))
    assert trace.values.dtype == np.uint16
    with pytest.raises(ValueError, match="single-bit"):
        ScopeTraceData(metadata, np.array([0, 1, 0], dtype=np.uint8))


def test_trace_profile_and_snapshot_close_each_transfer_field() -> None:
    fields = (
        "scope.query_response_header",
        "scope.waveform_byte_order",
        "scope.waveform_transfer_window",
    )
    profile = ScopeTraceProfile(
        fetchable_kinds=("analog", "reference"),
        max_points=1_000,
        restore_order=fields,
        snapshot_max_steps=3,
        restore_max_steps=3,
        verify_max_steps=3,
    )
    assert profile.restore_order == fields
    ScopeTraceTransferStateSnapshot(
        captured_fields=fields,
        query_response_header_token="HEADER_OFF",
        waveform_byte_order_token="LSB",
        waveform_transfer_window_token="ALL",
    )
    with pytest.raises(ValueError, match="presence"):
        ScopeTraceTransferStateSnapshot(
            captured_fields=fields,
            query_response_header_token="HEADER_OFF",
            waveform_byte_order_token="LSB",
        )


def test_descriptor_extension_rejects_queried_only_profile() -> None:
    variant = ScopeScreenshotVariant(
        request=ScopeScreenshotRequest(),
        media_type="image/png",
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
        response_max_bytes=100,
        operation_max_bytes=100,
        resynchronization_max_bytes=0,
        changed_fields=(),
        restore_order=(),
        snapshot_max_steps=0,
        restore_max_steps=0,
        verify_max_steps=0,
    )
    with pytest.raises(ValueError, match="queried-only"):
        ScopeDescriptorExtensions(
            screenshot_profile=ScopeScreenshotProfile((variant,), source="queried")
        )


def test_error_drain_requires_terminator_or_overflow_evidence() -> None:
    record = DriverErrorRecord(1, "error", "error", "queue")
    ErrorDrainResult(records=(record,), terminated=True, query_count=2).validate_for(
        max_records=16
    )
    with pytest.raises(ValueError, match="overflow"):
        ErrorDrainResult(records=(record,), terminated=False, query_count=2).validate_for(
            max_records=1
        )
