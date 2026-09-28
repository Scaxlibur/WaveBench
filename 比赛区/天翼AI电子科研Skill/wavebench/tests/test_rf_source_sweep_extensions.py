from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.instruments.capabilities import CAPABILITY_METHODS
from wavebench.instruments.rf_source_capabilities import validate_rf_source_descriptor
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_SWEEP_SNAPSHOT_SCHEMA,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulationState,
    RfObserved,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSweepConfigureRequest,
    RfSweepConfigureResult,
    RfSweepDirection,
    RfSweepModeProfile,
    RfSweepProfile,
    RfSweepShape,
    RfSweepSnapshot,
    RfSweepSpacing,
    RfSweepState,
    RfSweepType,
    RfSourceTopology,
    rf_source_sweep_operation_artifact,
    rf_sweep_snapshot_document,
)


def _topology() -> RfSourceTopology:
    return RfSourceTopology(
        (
            RfOutputPortProfile(
                port_id="rf_out",
                frequency_min_hz=9_000.0,
                frequency_max_hz=3_000_000_000.0,
                power_min_dbm=-110.0,
                power_max_dbm=20.0,
                power_reference_impedance_ohm=50.0,
            ),
        )
    )


def _sweep_mode() -> RfSweepModeProfile:
    return RfSweepModeProfile(
        sweep_type=RfSweepType.STEP,
        direction=RfSweepDirection.FORWARD,
        shape=RfSweepShape.RAMP,
        spacing=RfSweepSpacing.LINEAR,
        frequency_min_hz=9_000.0,
        frequency_max_hz=3_000_000_000.0,
        points_min=2,
        points_max=65_535,
        dwell_min_s=20e-3,
        dwell_max_s=100.0,
    )


def _sweep_profile() -> RfSweepProfile:
    return RfSweepProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(_sweep_mode(),),
    )


def _snapshot() -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-50.0),
                output_enabled=RfObserved.value_of(False),
                modulation=RfObserved.value_of(RfModulationState.DISABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def _sweep_snapshot() -> RfSweepSnapshot:
    return RfSweepSnapshot(
        port_id="rf_out",
        sweep_type=RfSweepType.STEP,
        direction=RfSweepDirection.FORWARD,
        shape=RfSweepShape.RAMP,
        spacing=RfSweepSpacing.LINEAR,
        start_frequency_hz=1_000_000.0,
        stop_frequency_hz=2_000_000.0,
        points=11,
        dwell_s=20e-3,
        state=RfSweepState.DISABLED,
    )


def _descriptor() -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.sweep",
        kind="rf_source",
        models=("RF-SWEEP",),
        capabilities=(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.sweep_configure",
        ),
        wavebench_min_version="0.8.25",
        wavebench_max_version="0.9.0",
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=_topology(),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.SWEEP,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=_sweep_profile(),
                ),
            ),
        ),
    )


class _Driver:
    def close(self) -> None:
        return None

    def idn(self) -> str:
        return "EXAMPLE,RF-SWEEP,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        return _snapshot()

    def get_rf_sweep_snapshot(self, port_id: str) -> RfSweepSnapshot:
        assert port_id == "rf_out"
        return _sweep_snapshot()

    def configure_rf_sweep(self, request: RfSweepConfigureRequest) -> None:
        assert request.port_id == "rf_out"


def test_sweep_contract_rejects_unsafe_profiles_and_requests() -> None:
    with pytest.raises(ValueError, match=">= 2"):
        RfSweepModeProfile(
            sweep_type=RfSweepType.STEP,
            direction=RfSweepDirection.FORWARD,
            shape=RfSweepShape.RAMP,
            spacing=RfSweepSpacing.LINEAR,
            frequency_min_hz=9_000.0,
            frequency_max_hz=3_000_000_000.0,
            points_min=1,
            points_max=65_535,
            dwell_min_s=20e-3,
            dwell_max_s=100.0,
        )
    with pytest.raises(ValueError, match="less than stop"):
        RfSweepConfigureRequest(
            port_id="rf_out",
            start_frequency_hz=1_000_000.0,
            stop_frequency_hz=1_000_000.0,
            points=11,
            dwell_s=20e-3,
        )
    with pytest.raises(ValueError, match="integer"):
        RfSweepConfigureRequest(
            port_id="rf_out",
            start_frequency_hz=1_000_000.0,
            stop_frequency_hz=2_000_000.0,
            points=True,
            dwell_s=20e-3,
        )


def test_sweep_descriptor_requires_readable_bounded_profile_and_methods() -> None:
    descriptor = _descriptor()

    assert CAPABILITY_METHODS["rf_source.sweep_configure"] == (
        "get_rf_sweep_snapshot",
        "configure_rf_sweep",
    )
    validate_rf_source_descriptor(descriptor, _Driver())

    invalid = _descriptor()
    invalid.rf_source_extensions = RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=_topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.SWEEP,
                directions=(RfFeatureDirection.CONFIGURE,),
                port_ids=("rf_out",),
                profile=_sweep_profile(),
            ),
        ),
    )
    with pytest.raises(Exception, match="configure and read"):
        validate_rf_source_descriptor(invalid)


def test_sweep_snapshot_document_and_artifact_keep_typed_safe_evidence() -> None:
    request = RfSweepConfigureRequest(
        port_id="rf_out",
        start_frequency_hz=1_000_000.0,
        stop_frequency_hz=2_000_000.0,
        points=11,
        dwell_s=20e-3,
    )
    result = RfSweepConfigureResult(
        port_id="rf_out",
        start_frequency_hz=1_000_000.0,
        stop_frequency_hz=2_000_000.0,
        points=11,
        dwell_s=20e-3,
    )
    sweep_snapshot = _sweep_snapshot()

    document = rf_sweep_snapshot_document(sweep_snapshot)
    artifact = rf_source_sweep_operation_artifact(
        request,
        result,
        preflight_snapshot=_snapshot(),
        postcondition_snapshot=_snapshot(),
        postcondition_sweep_snapshot=sweep_snapshot,
    )

    assert document["schema"] == RF_SOURCE_SWEEP_SNAPSHOT_SCHEMA
    assert document["sweep_type"] == "step"
    assert document["state"] == "disabled"
    assert artifact["operation"] == "rf_source.sweep_configure"
    assert artifact["request"]["points"] == 11
    assert artifact["postcondition_sweep_snapshot"]["dwell_s"] == 20e-3
    assert "resource" not in str(artifact)
