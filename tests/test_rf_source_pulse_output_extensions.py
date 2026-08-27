from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.instruments.capabilities import CAPABILITY_METHODS
from wavebench.instruments.rf_source_capabilities import validate_rf_source_descriptor
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_PULSE_OUTPUT_SNAPSHOT_SCHEMA,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulationState,
    RfObserved,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseConfigureRequest,
    RfPulseMode,
    RfPulseModeProfile,
    RfPulseOutputDirection,
    RfPulseOutputProfile,
    RfPulseOutputRequest,
    RfPulseOutputResult,
    RfPulseOutputSnapshot,
    RfPulsePolarity,
    RfPulseProfile,
    RfPulseSource,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
    rf_pulse_output_snapshot_document,
    rf_source_pulse_output_operation_artifact,
)
from wavebench.services.operation_specs import require_operation_spec


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


def _pulse_profile() -> RfPulseProfile:
    return RfPulseProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(
            RfPulseModeProfile(
                source=RfPulseSource.INTERNAL,
                mode=RfPulseMode.SINGLE,
                polarities=(RfPulsePolarity.INVERTED, RfPulsePolarity.NORMAL),
                period_min_s=40e-9,
                period_max_s=170.0,
                width_min_s=10e-9,
                width_max_s=170.0 - 10e-9,
                minimum_off_time_s=10e-9,
            ),
        ),
    )


def _pulse_output_profile() -> RfPulseOutputProfile:
    return RfPulseOutputProfile(
        interface_id="pulse_in_out",
        direction=RfPulseOutputDirection.OUTPUT,
        output_readable=True,
        low_level_v=0.0,
        high_level_v=3.3,
        output_impedance_ohm=600.0,
        source=RfPulseSource.INTERNAL,
        mode=RfPulseMode.SINGLE,
        period_s=1e-3,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
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


def _pulse_output_snapshot(*, enabled: bool = False) -> RfPulseOutputSnapshot:
    return RfPulseOutputSnapshot(
        port_id="rf_out",
        interface_id="pulse_in_out",
        direction=RfPulseOutputDirection.OUTPUT,
        enabled=enabled,
        low_level_v=0.0,
        high_level_v=3.3,
        output_impedance_ohm=600.0,
        source=RfPulseSource.INTERNAL,
        mode=RfPulseMode.SINGLE,
        period_s=1e-3,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
        pulse_state=RfPulseState.DISABLED,
    )


def _descriptor(*, directions: tuple[RfFeatureDirection, ...] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.pulse-output",
        kind="rf_source",
        models=("RF-PULSE-OUTPUT",),
        capabilities=(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.pulse_configure",
            "rf_source.pulse_output",
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
                RfFeatureCapability(
                    feature=RfFeature.PULSE_OUTPUT,
                    directions=directions
                    or (
                        RfFeatureDirection.DISABLE,
                        RfFeatureDirection.ENABLE,
                        RfFeatureDirection.READ,
                    ),
                    port_ids=("rf_out",),
                    profile=_pulse_output_profile(),
                ),
            ),
        ),
    )


class _Driver:
    def close(self) -> None:
        return None

    def idn(self) -> str:
        return "EXAMPLE,RF-PULSE-OUTPUT,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        return _snapshot()

    def get_rf_pulse_snapshot(self, port_id: str):
        assert port_id == "rf_out"
        return None

    def configure_rf_pulse(self, request: RfPulseConfigureRequest) -> None:
        assert request.port_id == "rf_out"

    def get_rf_pulse_output_snapshot(self, port_id: str, interface_id: str) -> RfPulseOutputSnapshot:
        assert (port_id, interface_id) == ("rf_out", "pulse_in_out")
        return _pulse_output_snapshot()

    def set_rf_pulse_output(self, request: RfPulseOutputRequest) -> None:
        assert request.port_id == "rf_out"


def test_pulse_output_contract_requires_a_bounded_output_only_profile() -> None:
    with pytest.raises(ValueError, match="high_level_v must exceed"):
        RfPulseOutputProfile(
            interface_id="pulse_in_out",
            direction=RfPulseOutputDirection.OUTPUT,
            output_readable=True,
            low_level_v=3.3,
            high_level_v=3.3,
            output_impedance_ohm=600.0,
            source=RfPulseSource.INTERNAL,
            mode=RfPulseMode.SINGLE,
            period_s=1e-3,
            width_s=100e-6,
            polarity=RfPulsePolarity.NORMAL,
        )
    with pytest.raises(ValueError, match="Pulse modulation disabled"):
        RfPulseOutputProfile(
            interface_id="pulse_in_out",
            direction=RfPulseOutputDirection.OUTPUT,
            output_readable=True,
            low_level_v=0.0,
            high_level_v=3.3,
            output_impedance_ohm=600.0,
            source=RfPulseSource.INTERNAL,
            mode=RfPulseMode.SINGLE,
            period_s=1e-3,
            width_s=100e-6,
            polarity=RfPulsePolarity.NORMAL,
            pulse_state=RfPulseState.ENABLED,
        )


def test_pulse_output_descriptor_requires_read_enable_disable_and_driver_methods() -> None:
    descriptor = _descriptor()

    assert CAPABILITY_METHODS["rf_source.pulse_output"] == (
        "get_rf_pulse_output_snapshot",
        "set_rf_pulse_output",
    )
    validate_rf_source_descriptor(descriptor, _Driver())

    invalid = _descriptor(directions=(RfFeatureDirection.ENABLE, RfFeatureDirection.READ))
    with pytest.raises(Exception, match="enable and disable"):
        validate_rf_source_descriptor(invalid)


def test_pulse_output_snapshot_document_and_artifact_keep_typed_evidence() -> None:
    request = RfPulseOutputRequest(
        port_id="rf_out",
        interface_id="pulse_in_out",
        enabled=True,
    )
    result = RfPulseOutputResult(
        port_id="rf_out",
        interface_id="pulse_in_out",
        enabled=True,
        write_completed=True,
    )
    snapshot = _pulse_output_snapshot(enabled=True)

    document = rf_pulse_output_snapshot_document(snapshot)
    artifact = rf_source_pulse_output_operation_artifact(
        request,
        result,
        preflight_snapshot=_snapshot(),
        preflight_pulse_output_snapshot=_pulse_output_snapshot(),
        postcondition_snapshot=_snapshot(),
        postcondition_pulse_output_snapshot=snapshot,
    )

    assert document["schema"] == RF_SOURCE_PULSE_OUTPUT_SNAPSHOT_SCHEMA
    assert document["direction"] == "output"
    assert document["high_level_v"] == 3.3
    assert artifact["operation"] == "rf_source.pulse_output_enable"
    assert artifact["postcondition_pulse_output_snapshot"]["enabled"] is True
    assert "resource" not in str(artifact)


def test_pulse_output_operation_specs_keep_enable_and_disable_separate() -> None:
    enable = require_operation_spec("rf_source.pulse_output_enable")
    disable = require_operation_spec("rf_source.pulse_output_disable")

    assert enable.required_capabilities == (
        "rf_source.snapshot",
        "rf_source.pulse_configure",
        "rf_source.pulse_output",
    )
    assert enable.changed_fields == ("rf_source.physical_interface.pulse_output.enabled",)
    assert enable.effect == "write"
    assert "rf_output_must_be_off" in enable.risk_flags
    assert "rf_output_must_be_off" not in disable.risk_flags
    assert disable.changed_fields == enable.changed_fields
