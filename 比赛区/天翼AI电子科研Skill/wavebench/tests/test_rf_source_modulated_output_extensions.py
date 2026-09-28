from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.capabilities import CAPABILITY_METHODS
from wavebench.instruments.rf_source_capabilities import validate_rf_source_descriptor
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfModulatedOutputProfile,
    RfModulatedOutputRequest,
    RfModulatedOutputResult,
    RfModulationKind,
    RfModulationModeProfile,
    RfModulationProfile,
    RfModulationRequest,
    RfModulationResult,
    RfModulationSnapshot,
    RfModulationSource,
    RfModulationState,
    RfModulationStateSnapshot,
    RfModulationValueUnit,
    RfModulationWaveform,
    RfObserved,
    RfOutputPortProfile,
    RfOutputProfile,
    RfOutputRequest,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
    rf_source_modulated_output_operation_artifact,
)


def _am_profile(*, maximum: float = 100.0) -> RfModulationModeProfile:
    return RfModulationModeProfile(
        kind=RfModulationKind.AM,
        value_unit=RfModulationValueUnit.PERCENT,
        value_min=0.0,
        value_max=maximum,
        internal_frequency_min_hz=10.0,
        internal_frequency_max_hz=100_000.0,
    )


def _modulation_profile() -> RfModulationProfile:
    return RfModulationProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(_am_profile(),),
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


def _extensions(
    *,
    modulated_profile: RfModulatedOutputProfile | None = None,
) -> RfSourceDescriptorExtensions:
    return RfSourceDescriptorExtensions(
        contract_version=RF_SOURCE_CONTRACT_VERSION,
        topology=_topology(),
        features=(
            RfFeatureCapability(
                feature=RfFeature.MODULATED_OUTPUT,
                directions=(RfFeatureDirection.ENABLE,),
                port_ids=("rf_out",),
                profile=modulated_profile
                or RfModulatedOutputProfile(
                    maximum_power_dbm=-30.0,
                    mode_profiles=(_am_profile(maximum=50.0),),
                ),
            ),
            RfFeatureCapability(
                feature=RfFeature.MODULATION,
                directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                port_ids=("rf_out",),
                profile=_modulation_profile(),
            ),
            RfFeatureCapability(
                feature=RfFeature.OUTPUT,
                directions=(RfFeatureDirection.DISABLE, RfFeatureDirection.ENABLE),
                port_ids=("rf_out",),
                profile=RfOutputProfile(output_readable=True),
            ),
        ),
    )


def _descriptor(
    *capabilities: str,
    extensions: RfSourceDescriptorExtensions | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.modulated-output",
        kind="rf_source",
        models=("RF-MODULATED-OUTPUT",),
        capabilities=capabilities,
        wavebench_min_version="0.8.25",
        wavebench_max_version="0.9.0",
        rf_source_extensions=extensions or _extensions(),
    )


def _request() -> RfModulatedOutputRequest:
    return RfModulatedOutputRequest(
        modulation=RfModulationRequest(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
        )
    )


def _snapshot(*, output_enabled: bool) -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-50.0),
                output_enabled=RfObserved.value_of(output_enabled),
                modulation=RfObserved.value_of(RfModulationState.ENABLED),
                pulse=RfObserved.value_of(RfPulseState.DISABLED),
                sweep=RfObserved.value_of(RfSweepState.DISABLED),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=())),
    )


def _modulation_snapshot() -> RfModulationSnapshot:
    return RfModulationSnapshot(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        source=RfModulationSource.INTERNAL,
        waveform=RfModulationWaveform.SINE,
        internal_frequency_hz=1_000.0,
        depth_percent=50.0,
        enabled_modes=(RfModulationKind.AM,),
        global_enabled=True,
    )


class _Driver:
    def close(self) -> None:
        return None

    def idn(self) -> str:
        return "EXAMPLE,RF-MODULATED-OUTPUT,0,1"

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        return _snapshot(output_enabled=False)

    def get_rf_modulation_state(self, port_id: str) -> RfModulationStateSnapshot:
        return RfModulationStateSnapshot(port_id=port_id)

    def get_rf_modulation_snapshot(
        self,
        port_id: str,
        kind: RfModulationKind,
    ) -> RfModulationSnapshot:
        assert port_id == "rf_out"
        assert kind is RfModulationKind.AM
        return _modulation_snapshot()

    def configure_rf_modulation(self, request: RfModulationRequest) -> None:
        del request

    def set_rf_output(self, request: RfOutputRequest) -> None:
        del request


def test_modulated_output_contract_is_explicit_and_typed() -> None:
    request = _request()
    result = RfModulatedOutputResult(
        modulation=RfModulationResult(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
        ),
        write_completed=True,
    )

    assert request.port_id == "rf_out"
    assert request.kind is RfModulationKind.AM
    assert result.port_id == "rf_out"
    assert result.kind is RfModulationKind.AM
    with pytest.raises(ValueError, match="requires RfModulationRequest"):
        RfModulatedOutputRequest(modulation=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must complete one RF-ON write"):
        RfModulatedOutputResult(modulation=result.modulation, write_completed=False)


def test_modulated_output_profile_requires_nonempty_sorted_modes() -> None:
    with pytest.raises(ValueError, match="invalid type"):
        RfModulatedOutputProfile(maximum_power_dbm=-30.0, mode_profiles=())
    with pytest.raises(ValueError, match="finite"):
        RfModulatedOutputProfile(
            maximum_power_dbm=float("nan"),
            mode_profiles=(_am_profile(),),
        )


def test_modulated_output_descriptor_requires_base_capabilities_and_subset_profile() -> None:
    capabilities = (
        "rf_source.idn",
        "rf_source.snapshot",
        "rf_source.modulation_configure",
        "rf_source.output",
        "rf_source.modulated_output_enable",
    )
    descriptor = _descriptor(*capabilities)

    assert CAPABILITY_METHODS["rf_source.modulated_output_enable"] == (
        "get_rf_modulation_snapshot",
        "set_rf_output",
    )
    validate_rf_source_descriptor(descriptor, _Driver())

    missing_output = _descriptor(
        "rf_source.idn",
        "rf_source.snapshot",
        "rf_source.modulation_configure",
        "rf_source.modulated_output_enable",
    )
    with pytest.raises(ConfigError, match="requires the rf_source.output capability"):
        validate_rf_source_descriptor(missing_output)

    too_wide = _descriptor(
        *capabilities,
        extensions=_extensions(
            modulated_profile=RfModulatedOutputProfile(
                maximum_power_dbm=-30.0,
                mode_profiles=(_am_profile(maximum=101.0),),
            )
        ),
    )
    with pytest.raises(ConfigError, match="must be a subset of modulation"):
        validate_rf_source_descriptor(too_wide)

    excessive_power = _descriptor(
        *capabilities,
        extensions=_extensions(
            modulated_profile=RfModulatedOutputProfile(
                maximum_power_dbm=21.0,
                mode_profiles=(_am_profile(maximum=50.0),),
            )
        ),
    )
    with pytest.raises(ConfigError, match="maximum_power_dbm"):
        validate_rf_source_descriptor(excessive_power)


def test_modulated_output_artifact_keeps_exact_active_profile_before_and_after_on() -> None:
    request = _request()
    result = RfModulatedOutputResult(
        modulation=RfModulationResult(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
        ),
        write_completed=True,
    )
    artifact = rf_source_modulated_output_operation_artifact(
        request,
        result,
        preflight_snapshot=_snapshot(output_enabled=False),
        preflight_modulation_snapshot=_modulation_snapshot(),
        postcondition_snapshot=_snapshot(output_enabled=True),
        postcondition_modulation_snapshot=_modulation_snapshot(),
    )

    assert artifact["operation"] == "rf_source.modulated_output_enable"
    assert artifact["request"]["modulation"]["kind"] == "am"
    assert artifact["postcondition_snapshot"]["ports"][0]["output_enabled"]["value"] is True
    assert artifact["preflight_modulation_snapshot"]["enabled_modes"] == ["am"]
