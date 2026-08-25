from __future__ import annotations

import numpy as np
import pytest

from wavebench.instruments.models import WaveformData, WaveformHeader
from wavebench.instruments.scope_extensions import (
    SCOPE_AVERAGE_CAPTURE_FIELD_ORDER,
    ScopeAcquisitionCompletion,
    ScopeAcquisitionControlBaseline,
    ScopeAcquisitionControlSnapshot,
    ScopeAcquisitionRunState,
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
)


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
