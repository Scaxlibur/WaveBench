from __future__ import annotations

from dataclasses import fields, replace

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
    ScopeWaveformBinaryOperationProfile,
    ScopeWaveformBinaryProfile,
    ScopeWaveformTransferStateSnapshot,
    validate_acquisition_completion,
)
from wavebench.scope_extension_constants import (
    SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
    SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
)
from wavebench.transport.contracts import BinaryResponseFraming


def _png(width: int = 2, height: int = 3, *, private_chunk_bytes: int = 0) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return len(payload).to_bytes(4, "big") + kind + payload + crc.to_bytes(4, "big")

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    rows = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows))
    if private_chunk_bytes:
        payload += chunk(b"raNd", b"x" * private_chunk_bytes)
    return payload + chunk(b"IEND", b"")


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
        response_max_bytes=SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
        operation_max_bytes=SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
        resynchronization_max_bytes=0,
        changed_fields=(),
        restore_order=(),
        snapshot_max_steps=0,
        restore_max_steps=0,
        verify_max_steps=0,
    )
    profile = ScopeScreenshotProfile((variant,))

    assert (
        SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES,
        SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES,
    ) == (8_388_608, 8_388_608)
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
    with pytest.raises(ValueError, match="response_max_bytes"):
        replace(
            variant,
            response_max_bytes=SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES + 1,
            operation_max_bytes=SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES + 1,
        )
    with pytest.raises(ValueError, match="operation_max_bytes"):
        replace(
            variant,
            operation_max_bytes=SCOPE_SCREENSHOT_BINARY_OPERATION_MAX_BYTES + 1,
        )
    with pytest.raises(ValueError, match="resynchronization_max_bytes"):
        replace(variant, resynchronization_max_bytes=1)
    with pytest.raises(ValueError, match="exactly 1"):
        replace(variant, query_max_count=2)


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
    base_png = _png()
    documented_payload = _png(
        private_chunk_bytes=387_356 - len(base_png) - 12,
    )
    assert len(documented_payload) == 387_356
    assert len(documented_payload) <= SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES
    assert ScopeScreenshot(
        data=documented_payload,
        media_type="image/png",
        width_px=2,
        height_px=3,
        requested=ScopeScreenshotRequest(),
        effective=ScopeScreenshotRequest(),
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
    ).width_px == 2
    oversized_payload = _png(
        private_chunk_bytes=(
            SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES + 1 - len(base_png) - 12
        ),
    )
    assert len(oversized_payload) == SCOPE_SCREENSHOT_BINARY_RESPONSE_MAX_BYTES + 1
    with pytest.raises(ValueError, match="not a PNG"):
        ScopeScreenshot(
            data=oversized_payload,
            media_type="image/png",
            width_px=2,
            height_px=3,
            requested=ScopeScreenshotRequest(),
            effective=ScopeScreenshotRequest(),
            framing=BinaryResponseFraming.DEFINITE_BLOCK,
        )
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
    single_arm_semantics: str = "configure_then_arm",
    single_mode_readback_allows_terminal_stop: bool = False,
) -> ScopeAcquisitionControlProfile:
    return ScopeAcquisitionControlProfile(
        supported_continuous_modes=("auto", "normal"),
        single_arm_semantics=single_arm_semantics,  # type: ignore[arg-type]
        arm_resets_acquisition_count=False,
        failure_restore_order=("scope.trigger", "scope.acquisition"),
        snapshot_max_steps=3,
        restore_max_steps=3,
        verify_max_steps=3,
        identity_semantics=identity_semantics,  # type: ignore[arg-type]
        single_mode_readback_allows_terminal_stop=single_mode_readback_allows_terminal_stop,
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


def test_acquisition_terminal_stop_proof_requires_explicit_mode_readback_contract() -> None:
    original = ScopeAcquisitionRunState("stopped", "normal", "STOP")
    completed = ScopeAcquisitionRunState("stopped", "single", "STOP")
    baseline = ScopeAcquisitionControlBaseline(
        "ctx",
        "epoch",
        "nonce",
        ScopeAcquisitionControlSnapshot(original, "TRIG", "ACQ"),
        ("scope.run_state", "scope.trigger", "scope.acquisition"),
    )
    completion = ScopeAcquisitionCompletion(
        state=completed,
        original_state=original,
        proof_baseline_state=original,
        proof_baseline_stage="original_atomic_arm",
        proof="single_mode_readback_then_stopped",
        observed_states=(completed,),
        post_arm_trigger_mode="single",
    )
    profile = _acquisition_profile(
        single_arm_semantics="atomic_configure_and_arm",
        single_mode_readback_allows_terminal_stop=True,
    )

    validate_acquisition_completion(completion, baseline=baseline, profile=profile)

    with pytest.raises(ValueError, match="not enabled"):
        validate_acquisition_completion(
            completion,
            baseline=baseline,
            profile=_acquisition_profile(single_arm_semantics="atomic_configure_and_arm"),
        )
    with pytest.raises(ValueError, match="mode readback"):
        validate_acquisition_completion(
            replace(completion, post_arm_trigger_mode="normal"),
            baseline=baseline,
            profile=profile,
        )
    for invalid_state in (
        ScopeAcquisitionRunState("complete", "single", "COMPLETE"),
        ScopeAcquisitionRunState("stopped", "normal", "STOP"),
    ):
        with pytest.raises(ValueError, match="stopped single-mode"):
            validate_acquisition_completion(
                replace(
                    completion,
                    state=invalid_state,
                    observed_states=(invalid_state,),
                ),
                baseline=baseline,
                profile=profile,
            )
    with pytest.raises(ValueError, match="exactly one"):
        validate_acquisition_completion(
            replace(
                completion,
                observed_states=(
                    ScopeAcquisitionRunState("waiting", "single", "WAIT"),
                    completed,
                ),
            ),
            baseline=baseline,
            profile=profile,
        )
    counted_completed = replace(completed, acquisition_count=0)
    with pytest.raises(ValueError, match="count or identity"):
        validate_acquisition_completion(
            replace(
                completion,
                state=counted_completed,
                observed_states=(counted_completed,),
                completed_count=0,
            ),
            baseline=baseline,
            profile=profile,
        )
    identified_completed = replace(completed, acquisition_identity="completed")
    with pytest.raises(ValueError, match="count or identity"):
        validate_acquisition_completion(
            replace(
                completion,
                state=identified_completed,
                observed_states=(identified_completed,),
                completed_identity="completed",
            ),
            baseline=baseline,
            profile=profile,
        )
    with pytest.raises(TypeError, match="must be bool"):
        _acquisition_profile(single_mode_readback_allows_terminal_stop=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="post-arm trigger mode"):
        replace(completion, post_arm_trigger_mode="invalid")


def test_acquisition_terminal_stop_fields_are_append_only_with_legacy_defaults() -> None:
    profile = ScopeAcquisitionControlProfile(
        ("auto", "normal"),
        "configure_then_arm",
        False,
        ("scope.trigger", "scope.acquisition"),
        3,
        3,
        3,
        "unknown",
        False,
    )
    original = ScopeAcquisitionRunState("stopped", "normal", "STOP")
    stopped = ScopeAcquisitionRunState("stopped", "single", "STOP")
    completion = ScopeAcquisitionCompletion(
        stopped,
        original,
        original,
        "original_atomic_arm",
        "state_transition",
        None,
        None,
        None,
        None,
        (ScopeAcquisitionRunState("waiting", "single", "WAIT"), stopped),
    )

    assert [field.name for field in fields(ScopeAcquisitionControlProfile)][-2:] == [
        "atomic_arm_preserves_count_mode_semantics",
        "single_mode_readback_allows_terminal_stop",
    ]
    assert profile.single_mode_readback_allows_terminal_stop is False
    assert [field.name for field in fields(ScopeAcquisitionCompletion)][-2:] == [
        "observed_states",
        "post_arm_trigger_mode",
    ]
    assert completion.post_arm_trigger_mode is None


def test_existing_acquisition_count_and_state_transition_proofs_remain_valid() -> None:
    original = ScopeAcquisitionRunState(
        "stopped",
        "normal",
        "STOP",
        acquisition_count=3,
        counter_epoch="epoch",
    )
    proof_baseline = ScopeAcquisitionRunState(
        "ready",
        "single",
        "READY",
        acquisition_count=3,
        counter_epoch="epoch",
    )
    arming = ScopeAcquisitionRunState(
        "arming",
        "single",
        "ARM",
        acquisition_count=3,
        counter_epoch="epoch",
    )
    stopped = ScopeAcquisitionRunState(
        "stopped",
        "single",
        "STOP",
        acquisition_count=4,
        counter_epoch="epoch",
    )
    baseline = ScopeAcquisitionControlBaseline(
        "ctx",
        "epoch",
        "nonce",
        ScopeAcquisitionControlSnapshot(original, "TRIG", "ACQ"),
        ("scope.run_state", "scope.trigger", "scope.acquisition"),
    )
    profile = _acquisition_profile(identity_semantics="unknown")
    state_transition = ScopeAcquisitionCompletion(
        state=stopped,
        original_state=original,
        proof_baseline_state=proof_baseline,
        proof_baseline_stage="configured_pre_arm",
        proof="state_transition",
        observed_states=(arming, stopped),
    )
    count_delta = ScopeAcquisitionCompletion(
        state=stopped,
        original_state=original,
        proof_baseline_state=proof_baseline,
        proof_baseline_stage="configured_pre_arm",
        proof="count_delta_with_epoch",
        baseline_count=3,
        completed_count=4,
        observed_states=(arming, stopped),
    )

    validate_acquisition_completion(state_transition, baseline=baseline, profile=profile)
    validate_acquisition_completion(count_delta, baseline=baseline, profile=profile)


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


def test_waveform_binary_profile_is_bounded_and_uses_capture_complete_transfer_models() -> None:
    fields = ("scope.waveform_source", "scope.waveform_format")
    operation = ScopeWaveformBinaryOperationProfile(
        operation_kind="fetch",
        response_max_bytes=8_388_608,
        operation_max_bytes=67_108_864,
        query_max_count=256,
        resynchronization_max_bytes=65_536,
        restore_order=fields,
        snapshot_max_steps=2,
        restore_max_steps=2,
        verify_max_steps=2,
    )
    profile = ScopeWaveformBinaryProfile(
        operations=(operation,),
        transport_trailing_hex="0d0a",
    )

    assert profile.transport_trailing == b"\r\n"
    assert profile.operation_for("fetch") is operation
    assert ScopeWaveformTransferStateSnapshot is not ScopeTraceTransferStateSnapshot
    ScopeWaveformTransferStateSnapshot(
        captured_fields=("scope.timebase", "scope.channel_vertical"),
        timebase_token="TIMEBASE",
        channel_vertical_token="CHANNEL_VERTICAL",
    )
    assert public_instruments.ScopeWaveformBinaryProfile is ScopeWaveformBinaryProfile
    assert ScopeWaveformBinaryProfile(operations=(operation,)).transport_trailing == b""
    assert ScopeWaveformBinaryProfile(
        operations=(operation,), transport_trailing_hex="0a"
    ).transport_trailing == b"\n"

    with pytest.raises(ValueError, match="definite-block"):
        ScopeWaveformBinaryProfile(
            operations=(operation,),
            framing=BinaryResponseFraming.MESSAGE,
        )
    with pytest.raises(ValueError, match="lowercase"):
        ScopeWaveformBinaryProfile(operations=(operation,), transport_trailing_hex="0A")
    with pytest.raises(ValueError, match="operation limit"):
        ScopeWaveformBinaryOperationProfile(
            operation_kind="fetch",
            response_max_bytes=2,
            operation_max_bytes=1,
            query_max_count=1,
            resynchronization_max_bytes=0,
            restore_order=("scope.waveform_source",),
            snapshot_max_steps=1,
            restore_max_steps=1,
            verify_max_steps=1,
        )
    with pytest.raises(ValueError, match="capture restore order"):
        ScopeWaveformBinaryOperationProfile(
            operation_kind="capture_single",
            response_max_bytes=1,
            operation_max_bytes=1,
            query_max_count=1,
            resynchronization_max_bytes=0,
            restore_order=("scope.waveform_source",),
            snapshot_max_steps=1,
            restore_max_steps=1,
            verify_max_steps=1,
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
