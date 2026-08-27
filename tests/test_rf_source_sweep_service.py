from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
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
    RfModulationState,
    RfObserved,
    RfOutputPortProfile,
    RfPortSnapshot,
    RfProtectionStatus,
    RfPulseState,
    RfSourceDescriptorExtensions,
    RfSourceSnapshot,
    RfSourceTopology,
    RfSweepConfigureRequest,
    RfSweepDirection,
    RfSweepModeProfile,
    RfSweepProfile,
    RfSweepShape,
    RfSweepSnapshot,
    RfSweepSpacing,
    RfSweepState,
    RfSweepType,
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
            driver="example.rf.sweep",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _sweep_profile() -> RfSweepProfile:
    return RfSweepProfile(
        state_readable=True,
        configuration_readable=True,
        mode_profiles=(
            RfSweepModeProfile(
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
            ),
        ),
    )


def _descriptor(*capabilities: str, profile: RfSweepProfile | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.sweep",
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
                    feature=RfFeature.SWEEP,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=profile or _sweep_profile(),
                ),
            ),
        ),
    )


def _rf_snapshot(
    *,
    output_enabled: bool = False,
    modulation: RfModulationState = RfModulationState.DISABLED,
    pulse: RfPulseState = RfPulseState.DISABLED,
    sweep: RfSweepState = RfSweepState.DISABLED,
    protection_codes: tuple[str, ...] = (),
) -> RfSourceSnapshot:
    return RfSourceSnapshot(
        ports=(
            RfPortSnapshot(
                port_id="rf_out",
                frequency_hz=RfObserved.value_of(1_000_000.0),
                power_dbm=RfObserved.value_of(-50.0),
                output_enabled=RfObserved.value_of(output_enabled),
                modulation=RfObserved.value_of(modulation),
                pulse=RfObserved.value_of(pulse),
                sweep=RfObserved.value_of(sweep),
            ),
        ),
        protection=RfObserved.value_of(RfProtectionStatus(active_codes=protection_codes)),
    )


def _sweep_snapshot(
    *,
    start_frequency_hz: float = 1_000_000.0,
    stop_frequency_hz: float = 2_000_000.0,
    points: int = 11,
    dwell_s: float = 20e-3,
    state: RfSweepState = RfSweepState.DISABLED,
) -> RfSweepSnapshot:
    return RfSweepSnapshot(
        port_id="rf_out",
        sweep_type=RfSweepType.STEP,
        direction=RfSweepDirection.FORWARD,
        shape=RfSweepShape.RAMP,
        spacing=RfSweepSpacing.LINEAR,
        start_frequency_hz=start_frequency_hz,
        stop_frequency_hz=stop_frequency_hz,
        points=points,
        dwell_s=dwell_s,
        state=state,
    )


class _Driver:
    def __init__(
        self,
        rf_snapshots: list[RfSourceSnapshot],
        sweep_snapshots: list[RfSweepSnapshot],
        *,
        raise_after_write: bool = False,
    ) -> None:
        self.rf_snapshots = list(rf_snapshots)
        self.sweep_snapshots = list(sweep_snapshots)
        self.raise_after_write = raise_after_write
        self.calls: list[str] = []
        self.requests: list[RfSweepConfigureRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.rf_snapshots:
            raise AssertionError("unexpected RF snapshot")
        return self.rf_snapshots.pop(0)

    def get_rf_sweep_snapshot(self, port_id: str) -> RfSweepSnapshot:
        self.calls.append("sweep_snapshot")
        assert port_id == "rf_out"
        if not self.sweep_snapshots:
            raise AssertionError("unexpected Sweep snapshot")
        return self.sweep_snapshots.pop(0)

    def configure_rf_sweep(self, request: RfSweepConfigureRequest) -> None:
        self.calls.append("configure_sweep")
        self.requests.append(request)
        if self.raise_after_write:
            raise ConfigError("fake Sweep write failed after transmission")


def _request(*, dwell_s: float = 20e-3) -> RfSweepConfigureRequest:
    return RfSweepConfigureRequest(
        port_id="rf_out",
        start_frequency_hz=1_000_000.0,
        stop_frequency_hz=2_000_000.0,
        points=11,
        dwell_s=dwell_s,
    )


def _service(
    rf_snapshots: list[RfSourceSnapshot],
    sweep_snapshots: list[RfSweepSnapshot],
    *,
    access: str = "read_write",
    descriptor: SimpleNamespace | None = None,
    raise_after_write: bool = False,
) -> tuple[RfSourceService, _Driver]:
    driver = _Driver(
        rf_snapshots,
        sweep_snapshots,
        raise_after_write=raise_after_write,
    )
    service = RfSourceService(
        config=_config(access=access),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor
        or _descriptor(
            "rf_source.idn",
            "rf_source.snapshot",
            "rf_source.sweep_configure",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def test_sweep_configuration_uses_one_write_and_independent_readbacks() -> None:
    request = _request()
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot()],
        [_sweep_snapshot()],
    )

    result, artifact = service.configure_sweep_with_artifact(request)

    assert result.start_frequency_hz == request.start_frequency_hz
    assert result.stop_frequency_hz == request.stop_frequency_hz
    assert result.points == request.points
    assert result.dwell_s == request.dwell_s
    assert driver.requests == [request]
    assert driver.calls == ["snapshot", "configure_sweep", "snapshot", "sweep_snapshot"]
    assert artifact["operation"] == "rf_source.sweep_configure"
    assert artifact["postcondition_sweep_snapshot"]["state"] == "disabled"
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


@pytest.mark.parametrize(
    ("snapshot", "message"),
    (
        (_rf_snapshot(output_enabled=True), "target RF output OFF"),
        (_rf_snapshot(modulation=RfModulationState.ENABLED), "modulation disabled"),
        (_rf_snapshot(pulse=RfPulseState.ENABLED), "Pulse disabled"),
        (_rf_snapshot(sweep=RfSweepState.ENABLED), "Sweep disabled"),
        (_rf_snapshot(protection_codes=("overtemperature",)), "active protection"),
    ),
)
def test_sweep_configuration_rejects_unsafe_preflight_without_write(
    snapshot: RfSourceSnapshot,
    message: str,
) -> None:
    service, driver = _service([snapshot], [])

    with pytest.raises(ConfigError, match=message):
        service.configure_sweep(_request())

    assert driver.requests == []
    assert driver.calls == ["snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_sweep_configuration_checks_capability_access_and_static_profile_before_driver_io() -> None:
    missing, missing_driver = _service(
        [],
        [],
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot"),
    )
    with pytest.raises(ConfigError, match="rf_source.sweep_configure"):
        missing.configure_sweep(_request())
    assert missing_driver.calls == []

    read_only, read_only_driver = _service([], [], access="read_only")
    with pytest.raises(AccessDeniedError, match="rf_source.sweep_configure"):
        read_only.configure_sweep(_request())
    assert read_only_driver.calls == []

    range_service, range_driver = _service([], [])
    with pytest.raises(ConfigError, match="outside the descriptor range"):
        range_service.configure_sweep(
            RfSweepConfigureRequest(
                port_id="rf_out",
                start_frequency_hz=1_000_000.0,
                stop_frequency_hz=2_000_000.0,
                points=65_536,
                dwell_s=20e-3,
            )
        )
    assert range_driver.calls == []

    dwell_service, dwell_driver = _service([], [])
    with pytest.raises(ConfigError, match="dwell_s is outside the descriptor range"):
        dwell_service.configure_sweep(_request(dwell_s=10e-3))
    assert dwell_driver.calls == []


@pytest.mark.parametrize(
    "sweep_snapshot",
    (
        _sweep_snapshot(stop_frequency_hz=2_100_000.0),
        _sweep_snapshot(points=12),
        _sweep_snapshot(dwell_s=30e-3),
        _sweep_snapshot(state=RfSweepState.ENABLED),
    ),
)
def test_sweep_configuration_mismatch_is_not_retried_and_degrades_session(
    sweep_snapshot: RfSweepSnapshot,
) -> None:
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot()],
        [sweep_snapshot],
    )

    with pytest.raises(ConfigError):
        service.configure_sweep(_request())

    assert driver.requests == [_request()]
    assert driver.calls == ["snapshot", "configure_sweep", "snapshot", "sweep_snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_sweep_configuration_write_failure_is_not_retried_and_degrades_session() -> None:
    service, driver = _service(
        [_rf_snapshot()],
        [],
        raise_after_write=True,
    )

    with pytest.raises(ConfigError, match="failed after transmission"):
        service.configure_sweep(_request())

    assert driver.requests == [_request()]
    assert driver.calls == ["snapshot", "configure_sweep"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN
