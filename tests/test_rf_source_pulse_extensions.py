from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.instruments.capabilities import CAPABILITY_METHODS
from wavebench.instruments.rf_source_capabilities import validate_rf_source_descriptor
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_PULSE_SNAPSHOT_SCHEMA,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulationState,
    RfObserved,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseConfigureRequest,
    RfPulseConfigureResult,
    RfPulseMode,
    RfPulseModeProfile,
    RfPulsePolarity,
    RfPulseProfile,
    RfPulseSnapshot,
    RfPulseSource,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
    rf_pulse_snapshot_document,
    rf_source_pulse_operation_artifact,
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


def _pulse_mode() -> RfPulseModeProfile:
    return RfPulseModeProfile(
        source=RfPulseSource.INTERNAL,
        mode=RfPulseMode.SINGLE,
        polarities=(RfPulsePolarity.INVERTED, RfPulsePolarity.NORMAL),
        period_min_s=40e-9,
        period_max_s=170.0,
        width_min_s=10e-9,
        width_max_s=170.0 - 10e-9,
        minimum_off_time_s=10e-9,
    )


def _pulse_profile() -> RfPulseProfile:
    return RfPulseProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(_pulse_mode(),),
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


def _pulse_snapshot() -> RfPulseSnapshot:
    return RfPulseSnapshot(
        port_id="rf_out",
        source=RfPulseSource.INTERNAL,
        mode=RfPulseMode.SINGLE,
        period_s=1e-3,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
        state=RfPulseState.DISABLED,
    )


def _descriptor() -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.pulse",
        kind="rf_source",
        models=("RF-PULSE",),
        capabilities=(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.pulse_configure",
        ),
        wavebench_min_version="0.8.25",
        wavebench_max_version="0.9.0",
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=_topology(),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.PULSE,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=_pulse_profile(),
                ),
            ),
        ),
    )


class _Driver:
    def close(self) -> None:
        return None

    def idn(self) -> str:
        return "EXAMPLE,RF-PULSE,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        return _snapshot()

    def get_rf_pulse_snapshot(self, port_id: str) -> RfPulseSnapshot:
        assert port_id == "rf_out"
        return _pulse_snapshot()

    def configure_rf_pulse(self, request: RfPulseConfigureRequest) -> None:
        assert request.port_id == "rf_out"


def test_pulse_contract_rejects_unsafe_profiles_and_requests() -> None:
    with pytest.raises(ValueError, match="internal source"):
        RfPulseModeProfile(
            source=RfPulseSource.EXTERNAL,
            mode=RfPulseMode.SINGLE,
            polarities=(RfPulsePolarity.NORMAL,),
            period_min_s=40e-9,
            period_max_s=170.0,
            width_min_s=10e-9,
            width_max_s=1.0,
            minimum_off_time_s=10e-9,
        )
    with pytest.raises(ValueError, match="single mode"):
        RfPulseModeProfile(
            source=RfPulseSource.INTERNAL,
            mode=RfPulseMode.TRAIN,
            polarities=(RfPulsePolarity.NORMAL,),
            period_min_s=40e-9,
            period_max_s=170.0,
            width_min_s=10e-9,
            width_max_s=1.0,
            minimum_off_time_s=10e-9,
        )
    with pytest.raises(ValueError, match="sorted by value"):
        RfPulseModeProfile(
            source=RfPulseSource.INTERNAL,
            mode=RfPulseMode.SINGLE,
            polarities=(RfPulsePolarity.NORMAL, RfPulsePolarity.INVERTED),
            period_min_s=40e-9,
            period_max_s=170.0,
            width_min_s=10e-9,
            width_max_s=1.0,
            minimum_off_time_s=10e-9,
        )
    with pytest.raises(ValueError, match="less than period"):
        RfPulseConfigureRequest(
            port_id="rf_out",
            period_s=1e-3,
            width_s=1e-3,
            polarity=RfPulsePolarity.NORMAL,
        )
    with pytest.raises(ValueError, match="finite"):
        RfPulseConfigureRequest(
            port_id="rf_out",
            period_s=float("nan"),
            width_s=1e-6,
            polarity=RfPulsePolarity.NORMAL,
        )


def test_pulse_descriptor_requires_readable_bounded_profile_and_methods() -> None:
    descriptor = _descriptor()

    assert CAPABILITY_METHODS["rf_source.pulse_configure"] == (
        "get_rf_pulse_snapshot",
        "configure_rf_pulse",
    )
    validate_rf_source_descriptor(descriptor, _Driver())

    invalid = _descriptor()
    invalid.rf_source_extensions = RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=_topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.PULSE,
                directions=(RfFeatureDirection.CONFIGURE,),
                port_ids=("rf_out",),
                profile=_pulse_profile(),
            ),
        ),
    )
    with pytest.raises(Exception, match="configure and read"):
        validate_rf_source_descriptor(invalid)


def test_pulse_snapshot_document_and_artifact_keep_typed_safe_evidence() -> None:
    request = RfPulseConfigureRequest(
        port_id="rf_out",
        period_s=1e-3,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
    )
    result = RfPulseConfigureResult(
        port_id="rf_out",
        period_s=1e-3,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
    )
    pulse_snapshot = _pulse_snapshot()

    document = rf_pulse_snapshot_document(pulse_snapshot)
    artifact = rf_source_pulse_operation_artifact(
        request,
        result,
        preflight_snapshot=_snapshot(),
        postcondition_snapshot=_snapshot(),
        postcondition_pulse_snapshot=pulse_snapshot,
    )

    assert document["schema"] == RF_SOURCE_PULSE_SNAPSHOT_SCHEMA
    assert document["source"] == "internal"
    assert document["state"] == "disabled"
    assert artifact["operation"] == "rf_source.pulse_configure"
    assert artifact["request"]["period_s"] == 1e-3
    assert artifact["postcondition_pulse_snapshot"]["width_s"] == 100e-6
    assert "resource" not in str(artifact)
