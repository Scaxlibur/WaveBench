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
    RfPulseConfigureRequest,
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
            driver="example.rf.pulse",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
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


def _descriptor(*capabilities: str, profile: RfPulseProfile | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.pulse",
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
                    feature=RfFeature.PULSE,
                    directions=(RfFeatureDirection.CONFIGURE, RfFeatureDirection.READ),
                    port_ids=("rf_out",),
                    profile=profile or _pulse_profile(),
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


def _pulse_snapshot(
    *,
    period_s: float = 1e-3,
    width_s: float = 100e-6,
    polarity: RfPulsePolarity = RfPulsePolarity.NORMAL,
    source: RfPulseSource = RfPulseSource.INTERNAL,
    mode: RfPulseMode = RfPulseMode.SINGLE,
    state: RfPulseState = RfPulseState.DISABLED,
) -> RfPulseSnapshot:
    return RfPulseSnapshot(
        port_id="rf_out",
        source=source,
        mode=mode,
        period_s=period_s,
        width_s=width_s,
        polarity=polarity,
        state=state,
    )


class _Driver:
    def __init__(
        self,
        rf_snapshots: list[RfSourceSnapshot],
        pulse_snapshots: list[RfPulseSnapshot],
        *,
        raise_after_write: bool = False,
    ) -> None:
        self.rf_snapshots = list(rf_snapshots)
        self.pulse_snapshots = list(pulse_snapshots)
        self.raise_after_write = raise_after_write
        self.calls: list[str] = []
        self.requests: list[RfPulseConfigureRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.rf_snapshots:
            raise AssertionError("unexpected RF snapshot")
        return self.rf_snapshots.pop(0)

    def get_rf_pulse_snapshot(self, port_id: str) -> RfPulseSnapshot:
        self.calls.append("pulse_snapshot")
        assert port_id == "rf_out"
        if not self.pulse_snapshots:
            raise AssertionError("unexpected pulse snapshot")
        return self.pulse_snapshots.pop(0)

    def configure_rf_pulse(self, request: RfPulseConfigureRequest) -> None:
        self.calls.append("configure_pulse")
        self.requests.append(request)
        if self.raise_after_write:
            raise ConfigError("fake pulse write failed after transmission")


def _request(*, width_s: float = 100e-6) -> RfPulseConfigureRequest:
    return RfPulseConfigureRequest(
        port_id="rf_out",
        period_s=1e-3,
        width_s=width_s,
        polarity=RfPulsePolarity.NORMAL,
    )


def _service(
    rf_snapshots: list[RfSourceSnapshot],
    pulse_snapshots: list[RfPulseSnapshot],
    *,
    access: str = "read_write",
    descriptor: SimpleNamespace | None = None,
    raise_after_write: bool = False,
) -> tuple[RfSourceService, _Driver]:
    driver = _Driver(
        rf_snapshots,
        pulse_snapshots,
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
            "rf_source.pulse_configure",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def test_pulse_configuration_uses_one_write_and_independent_readbacks() -> None:
    request = _request()
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot()],
        [_pulse_snapshot()],
    )

    result, artifact = service.configure_pulse_with_artifact(request)

    assert result.period_s == request.period_s
    assert result.width_s == request.width_s
    assert result.polarity is request.polarity
    assert driver.requests == [request]
    assert driver.calls == ["snapshot", "configure_pulse", "snapshot", "pulse_snapshot"]
    assert artifact["operation"] == "rf_source.pulse_configure"
    assert artifact["postcondition_pulse_snapshot"]["state"] == "disabled"
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
def test_pulse_configuration_rejects_unsafe_preflight_without_write(
    snapshot: RfSourceSnapshot,
    message: str,
) -> None:
    service, driver = _service([snapshot], [])

    with pytest.raises(ConfigError, match=message):
        service.configure_pulse(_request())

    assert driver.requests == []
    assert driver.calls == ["snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_pulse_configuration_checks_capability_access_and_static_profile_before_driver_io() -> None:
    missing, missing_driver = _service(
        [],
        [],
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot"),
    )
    with pytest.raises(ConfigError, match="rf_source.pulse_configure"):
        missing.configure_pulse(_request())
    assert missing_driver.calls == []

    read_only, read_only_driver = _service([], [], access="read_only")
    with pytest.raises(AccessDeniedError, match="rf_source.pulse_configure"):
        read_only.configure_pulse(_request())
    assert read_only_driver.calls == []

    range_service, range_driver = _service([], [])
    with pytest.raises(ConfigError, match="outside the descriptor range"):
        range_service.configure_pulse(_request(width_s=1e-9))
    assert range_driver.calls == []

    off_time_service, off_time_driver = _service([], [])
    with pytest.raises(ConfigError, match="minimum off time"):
        off_time_service.configure_pulse(
            RfPulseConfigureRequest(
                port_id="rf_out",
                period_s=40e-9,
                width_s=35e-9,
                polarity=RfPulsePolarity.NORMAL,
            )
        )
    assert off_time_driver.calls == []


@pytest.mark.parametrize(
    "pulse_snapshot",
    (
        _pulse_snapshot(width_s=99e-6),
        _pulse_snapshot(source=RfPulseSource.EXTERNAL),
        _pulse_snapshot(mode=RfPulseMode.TRAIN),
        _pulse_snapshot(state=RfPulseState.ENABLED),
    ),
)
def test_pulse_configuration_mismatch_is_not_retried_and_degrades_session(
    pulse_snapshot: RfPulseSnapshot,
) -> None:
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot()],
        [pulse_snapshot],
    )

    with pytest.raises(ConfigError):
        service.configure_pulse(_request())

    assert driver.requests == [_request()]
    assert driver.calls == ["snapshot", "configure_pulse", "snapshot", "pulse_snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_pulse_configuration_write_failure_is_not_retried_and_degrades_session() -> None:
    service, driver = _service(
        [_rf_snapshot()],
        [],
        raise_after_write=True,
    )

    with pytest.raises(ConfigError, match="failed after transmission"):
        service.configure_pulse(_request())

    assert driver.requests == [_request()]
    assert driver.calls == ["snapshot", "configure_pulse"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN

