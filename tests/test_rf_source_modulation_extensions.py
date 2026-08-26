from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.instruments.capabilities import CAPABILITY_METHODS
from wavebench.instruments.rf_source_capabilities import validate_rf_source_descriptor
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RF_SOURCE_MODULATION_SNAPSHOT_SCHEMA,
    RfCwRequest,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulationKind,
    RfModulationModeProfile,
    RfModulationProfile,
    RfModulationRequest,
    RfModulationResult,
    RfModulationSnapshot,
    RfModulationSource,
    RfModulationValueUnit,
    RfModulationWaveform,
    RfObserved,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
    RfModulationState,
    rf_modulation_snapshot_document,
    rf_source_modulation_operation_artifact,
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


def _profile() -> RfModulationProfile:
    return RfModulationProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(
            RfModulationModeProfile(
                kind=RfModulationKind.AM,
                value_unit=RfModulationValueUnit.PERCENT,
                value_min=0.0,
                value_max=100.0,
                internal_frequency_min_hz=10.0,
                internal_frequency_max_hz=100_000.0,
            ),
            RfModulationModeProfile(
                kind=RfModulationKind.FM,
                value_unit=RfModulationValueUnit.HZ,
                value_min=0.1,
                value_max=1_000_000.0,
                internal_frequency_min_hz=10.0,
                internal_frequency_max_hz=100_000.0,
            ),
            RfModulationModeProfile(
                kind=RfModulationKind.PM,
                value_unit=RfModulationValueUnit.RAD,
                value_min=0.0,
                value_max=5.0,
                internal_frequency_min_hz=10.0,
                internal_frequency_max_hz=100_000.0,
            ),
        ),
    )


def _rf_snapshot() -> RfSourceSnapshot:
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


def _modulation_snapshot(
    *,
    enabled: bool,
) -> RfModulationSnapshot:
    return RfModulationSnapshot(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        source=RfModulationSource.INTERNAL,
        waveform=RfModulationWaveform.SINE,
        internal_frequency_hz=1_000.0,
        depth_percent=50.0,
        enabled_modes=(RfModulationKind.AM,) if enabled else (),
        global_enabled=enabled,
    )


def _descriptor() -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.modulation",
        kind="rf_source",
        models=("RF-MOD",),
        capabilities=(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.modulation_configure",
        ),
        wavebench_min_version="0.8.25",
        wavebench_max_version="0.9.0",
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=_topology(),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.MODULATION,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=_profile(),
                ),
            ),
        ),
    )


class _Driver:
    def close(self) -> None:
        return None

    def idn(self) -> str:
        return "EXAMPLE,RF-MOD,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        return _rf_snapshot()

    def configure_cw(self, request: RfCwRequest) -> None:
        del request

    def get_rf_modulation_snapshot(
        self,
        port_id: str,
        kind: RfModulationKind,
    ) -> RfModulationSnapshot:
        assert port_id == "rf_out"
        assert kind is RfModulationKind.AM
        return _modulation_snapshot(enabled=False)

    def configure_rf_modulation(self, request: RfModulationRequest) -> None:
        del request


def test_modulation_contract_binds_request_value_to_kind() -> None:
    am = RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        internal_frequency_hz=1_000.0,
        depth_percent=50.0,
    )
    fm = RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.FM,
        internal_frequency_hz=1_000.0,
        frequency_deviation_hz=10_000.0,
    )
    pm = RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.PM,
        internal_frequency_hz=1_000.0,
        phase_deviation_rad=2.0,
    )

    assert am.value == 50.0
    assert am.value_unit is RfModulationValueUnit.PERCENT
    assert fm.value == 10_000.0
    assert fm.value_unit is RfModulationValueUnit.HZ
    assert pm.value == 2.0
    assert pm.value_unit is RfModulationValueUnit.RAD

    with pytest.raises(ValueError, match="exactly the parameter"):
        RfModulationRequest(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            frequency_deviation_hz=1.0,
        )
    with pytest.raises(ValueError, match="finite"):
        RfModulationRequest(
            port_id="rf_out",
            kind=RfModulationKind.PM,
            internal_frequency_hz=float("nan"),
            phase_deviation_rad=1.0,
        )


def test_modulation_profile_and_snapshot_are_strict() -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        RfModulationProfile(
            state_readable=True,
            configuration_readable=True,
            mode_profiles=(
                RfModulationModeProfile(
                    kind=RfModulationKind.PM,
                    value_unit=RfModulationValueUnit.RAD,
                    value_min=0.0,
                    value_max=5.0,
                    internal_frequency_min_hz=10.0,
                    internal_frequency_max_hz=100_000.0,
                ),
                RfModulationModeProfile(
                    kind=RfModulationKind.AM,
                    value_unit=RfModulationValueUnit.PERCENT,
                    value_min=0.0,
                    value_max=100.0,
                    internal_frequency_min_hz=10.0,
                    internal_frequency_max_hz=100_000.0,
                ),
            ),
        )
    with pytest.raises(ValueError, match="configuration readback"):
        RfModulationProfile(state_readable=False, configuration_readable=True)
    with pytest.raises(ValueError, match="sorted by value"):
        RfModulationSnapshot(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            source=RfModulationSource.INTERNAL,
            waveform=RfModulationWaveform.SINE,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
            enabled_modes=(RfModulationKind.PM, RfModulationKind.AM),
            global_enabled=True,
        )


def test_modulation_descriptor_requires_readable_bounded_feature_and_methods() -> None:
    descriptor = _descriptor()

    assert CAPABILITY_METHODS["rf_source.modulation_configure"] == (
        "get_rf_modulation_snapshot",
        "configure_rf_modulation",
    )
    validate_rf_source_descriptor(descriptor, _Driver())

    invalid = _descriptor()
    invalid.rf_source_extensions = RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=_topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.MODULATION,
                directions=(RfFeatureDirection.CONFIGURE,),
                port_ids=("rf_out",),
                profile=_profile(),
            ),
        ),
    )
    with pytest.raises(Exception, match="configure and read"):
        validate_rf_source_descriptor(invalid)


def test_modulation_artifact_keeps_typed_pre_and_postcondition_evidence() -> None:
    request = RfModulationRequest(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        internal_frequency_hz=1_000.0,
        depth_percent=50.0,
    )
    result = RfModulationResult(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        internal_frequency_hz=1_000.0,
        depth_percent=50.0,
    )
    preflight = _modulation_snapshot(enabled=False)
    postcondition = _modulation_snapshot(enabled=True)

    document = rf_modulation_snapshot_document(postcondition)
    artifact = rf_source_modulation_operation_artifact(
        request,
        result,
        preflight_snapshot=_rf_snapshot(),
        preflight_modulation_snapshot=preflight,
        postcondition_snapshot=_rf_snapshot(),
        postcondition_modulation_snapshot=postcondition,
    )

    assert document["schema"] == RF_SOURCE_MODULATION_SNAPSHOT_SCHEMA
    assert artifact["operation"] == "rf_source.modulation_configure"
    assert artifact["postcondition_modulation_snapshot"]["global_enabled"] is True
