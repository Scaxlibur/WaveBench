from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    RfPortSafetyConfig,
    RfSourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.errors import AccessDeniedError, ConfigError
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
    RfModulationValueUnit,
    RfModulationWaveform,
    RfObserved,
    RfOutputPortProfile,
    RfOutputProfile,
    RfOutputRequest,
    RfPortSnapshot,
    RfProtectionConditionPolicy,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepState,
)
from wavebench.logging import CommandLogger
from wavebench.services.rf_source_service import RfSourceService
from wavebench.transport.session import InstrumentSessionState, SessionHealth


def _config(*, access: str = "read_write") -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        rf_source=RfSourceConfig(
            driver="example.rf.modulated-output",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
            safety_ports=(
                RfPortSafetyConfig(
                    port_id="rf_out",
                    minimum_frequency_hz=9_000.0,
                    maximum_frequency_hz=3_000_000_000.0,
                    maximum_power_dbm=-30.0,
                    actual_termination_ohm=50.0,
                ),
            ),
        ),
    )


def _mode_profile(*, value_max: float = 100.0) -> RfModulationModeProfile:
    return RfModulationModeProfile(
        kind=RfModulationKind.AM,
        value_unit=RfModulationValueUnit.PERCENT,
        value_min=0.0,
        value_max=value_max,
        internal_frequency_min_hz=10.0,
        internal_frequency_max_hz=100_000.0,
    )


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.modulated-output",
        capabilities=capabilities,
        rf_source_extensions=RfSourceDescriptorExtensions(
            contract_version=RF_SOURCE_CONTRACT_VERSION,
            topology=RfSourceTopology(
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
            ),
            features=(
                RfFeatureCapability(
                    feature=RfFeature.MODULATED_OUTPUT,
                    directions=(RfFeatureDirection.ENABLE,),
                    port_ids=("rf_out",),
                    profile=RfModulatedOutputProfile(
                        maximum_power_dbm=-30.0,
                        mode_profiles=(_mode_profile(value_max=50.0),),
                    ),
                ),
                RfFeatureCapability(
                    feature=RfFeature.MODULATION,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=RfModulationProfile(
                        state_readable=True,
                        configuration_readable=True,
                        mode_profiles=(_mode_profile(),),
                    ),
                ),
                RfFeatureCapability(
                    feature=RfFeature.OUTPUT,
                    directions=(RfFeatureDirection.DISABLE, RfFeatureDirection.ENABLE),
                    port_ids=("rf_out",),
                    profile=RfOutputProfile(output_readable=True),
                ),
            ),
            protection_conditions=(
                RfProtectionConditionPolicy("overtemperature", True),
                RfProtectionConditionPolicy("status_notice", False),
            ),
        ),
    )


def _request(*, depth_percent: float = 50.0) -> RfModulatedOutputRequest:
    return RfModulatedOutputRequest(
        modulation=RfModulationRequest(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=depth_percent,
        )
    )


def _rf_snapshot(
    *,
    output_enabled: bool = False,
    modulation: RfModulationState = RfModulationState.ENABLED,
    pulse: RfPulseState = RfPulseState.DISABLED,
    sweep: RfSweepState = RfSweepState.DISABLED,
    power_dbm: float = -50.0,
    protection_codes: tuple[str, ...] = (),
) -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(power_dbm),
                output_enabled=RfObserved.value_of(output_enabled),
                modulation=RfObserved.value_of(modulation),
                pulse=RfObserved.value_of(pulse),
                sweep=RfObserved.value_of(sweep),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=protection_codes)),
    )


def _modulation_snapshot(
    *,
    enabled_modes: tuple[RfModulationKind, ...] = (RfModulationKind.AM,),
    global_enabled: bool = True,
    depth_percent: float = 50.0,
    fault_codes: tuple[str, ...] = (),
) -> RfModulationSnapshot:
    return RfModulationSnapshot(
        port_id="rf_out",
        kind=RfModulationKind.AM,
        source=RfModulationSource.INTERNAL,
        waveform=RfModulationWaveform.SINE,
        internal_frequency_hz=1_000.0,
        depth_percent=depth_percent,
        enabled_modes=enabled_modes,
        global_enabled=global_enabled,
        fault_codes=fault_codes,
    )


class _Driver:
    def __init__(
        self,
        rf_snapshots: list[RfSourceSnapshot],
        modulation_snapshots: list[RfModulationSnapshot],
        *,
        raise_after_enable: bool = False,
    ) -> None:
        self.rf_snapshots = list(rf_snapshots)
        self.modulation_snapshots = list(modulation_snapshots)
        self.raise_after_enable = raise_after_enable
        self.calls: list[str] = []
        self.output_requests: list[RfOutputRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.rf_snapshots:
            raise AssertionError("unexpected RF snapshot")
        return self.rf_snapshots.pop(0)

    def get_rf_modulation_snapshot(
        self,
        port_id: str,
        kind: RfModulationKind,
    ) -> RfModulationSnapshot:
        self.calls.append("modulation_snapshot")
        assert port_id == "rf_out"
        assert kind is RfModulationKind.AM
        if not self.modulation_snapshots:
            raise AssertionError("unexpected modulation snapshot")
        return self.modulation_snapshots.pop(0)

    def set_rf_output(self, request: RfOutputRequest) -> None:
        self.calls.append("set_rf_output")
        self.output_requests.append(request)
        if request.enabled and self.raise_after_enable:
            raise ConfigError("RF ON failed after transmission")


def _service(
    rf_snapshots: list[RfSourceSnapshot],
    modulation_snapshots: list[RfModulationSnapshot],
    *,
    access: str = "read_write",
    descriptor: SimpleNamespace | None = None,
    raise_after_enable: bool = False,
) -> tuple[RfSourceService, _Driver]:
    driver = _Driver(
        rf_snapshots,
        modulation_snapshots,
        raise_after_enable=raise_after_enable,
    )
    service = RfSourceService(
        config=_config(access=access),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor
        or _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.modulation_configure",
            "rf_source.output",
            "rf_source.modulated_output_enable",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def test_modulated_output_enable_uses_one_on_write_and_exact_pre_post_readback() -> None:
    request = _request()
    service, driver = _service(
        [_rf_snapshot(output_enabled=False), _rf_snapshot(output_enabled=True)],
        [_modulation_snapshot(), _modulation_snapshot()],
    )

    result, artifact = service.enable_modulated_output_with_artifact(request)

    assert result == RfModulatedOutputResult(
        modulation=RfModulationResult(
            port_id="rf_out",
            kind=RfModulationKind.AM,
            internal_frequency_hz=1_000.0,
            depth_percent=50.0,
        ),
        write_completed=True,
    )
    assert driver.output_requests == [RfOutputRequest(port_id="rf_out", enabled=True)]
    assert driver.calls == [
        "snapshot",
        "modulation_snapshot",
        "set_rf_output",
        "snapshot",
        "modulation_snapshot",
    ]
    assert artifact["operation"] == "rf_source.modulated_output_enable"
    assert artifact["postcondition_snapshot"]["ports"][0]["output_enabled"]["value"] is True
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


@pytest.mark.parametrize(
    ("rf_snapshot", "modulation_snapshot", "message"),
    (
        (_rf_snapshot(output_enabled=True), _modulation_snapshot(), "target RF output OFF"),
        (_rf_snapshot(), _modulation_snapshot(enabled_modes=()), "only the requested modulation mode"),
        (_rf_snapshot(), _modulation_snapshot(global_enabled=False), "global modulation enabled"),
        (_rf_snapshot(), _modulation_snapshot(fault_codes=("am_fault",)), "modulation fault"),
        (_rf_snapshot(pulse=RfPulseState.ENABLED), _modulation_snapshot(), "Pulse disabled"),
        (_rf_snapshot(sweep=RfSweepState.ENABLED), _modulation_snapshot(), "Sweep disabled"),
        (_rf_snapshot(power_dbm=-20.0), _modulation_snapshot(), "modulated-output"),
    ),
)
def test_modulated_output_enable_rejects_unsafe_preflight_without_write(
    rf_snapshot: RfSourceSnapshot,
    modulation_snapshot: RfModulationSnapshot,
    message: str,
) -> None:
    service, driver = _service([rf_snapshot], [modulation_snapshot])

    with pytest.raises(ConfigError, match=message):
        service.enable_modulated_output(_request())

    assert driver.output_requests == []
    assert driver.calls == ["snapshot", "modulation_snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_modulated_output_enable_checks_capability_access_and_profile_before_driver_io() -> None:
    missing, missing_driver = _service(
        [],
        [],
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot", "rf_source.output"),
    )
    with pytest.raises(ConfigError, match="rf_source.modulation_configure"):
        missing.enable_modulated_output(_request())
    assert missing_driver.calls == []

    read_only, read_only_driver = _service([], [], access="read_only")
    with pytest.raises(AccessDeniedError, match="rf_source.modulated_output_enable"):
        read_only.enable_modulated_output(_request())
    assert read_only_driver.calls == []

    out_of_profile, out_of_profile_driver = _service([], [])
    with pytest.raises(ConfigError, match="outside the descriptor range"):
        out_of_profile.enable_modulated_output(_request(depth_percent=51.0))
    assert out_of_profile_driver.calls == []


def test_modulated_output_enable_mismatch_or_write_failure_runs_one_off_recovery() -> None:
    request = _request()
    mismatch_service, mismatch_driver = _service(
        [
            _rf_snapshot(output_enabled=False),
            _rf_snapshot(output_enabled=False),
            _rf_snapshot(output_enabled=False),
        ],
        [_modulation_snapshot(), _modulation_snapshot()],
    )

    with pytest.raises(ConfigError, match="target RF output ON") as mismatch:
        mismatch_service.enable_modulated_output(request)

    assert mismatch_driver.output_requests == [
        RfOutputRequest(port_id="rf_out", enabled=True),
        RfOutputRequest(port_id="rf_out", enabled=False),
    ]
    assert mismatch.value.rf_source_recovery == {
        "status": "off_verified",
        "session_health": "uncertain",
    }
    assert mismatch_service.session_state is not None
    assert mismatch_service.session_state.health is SessionHealth.UNCERTAIN

    failed_service, failed_driver = _service(
        [_rf_snapshot(output_enabled=False), _rf_snapshot(output_enabled=False)],
        [_modulation_snapshot()],
        raise_after_enable=True,
    )

    with pytest.raises(ConfigError, match="failed after transmission") as failed:
        failed_service.enable_modulated_output(request)

    assert failed_driver.output_requests == [
        RfOutputRequest(port_id="rf_out", enabled=True),
        RfOutputRequest(port_id="rf_out", enabled=False),
    ]
    assert failed.value.rf_source_recovery["status"] == "off_verified"
    assert failed_service.session_state is not None
    assert failed_service.session_state.health is SessionHealth.UNCERTAIN
