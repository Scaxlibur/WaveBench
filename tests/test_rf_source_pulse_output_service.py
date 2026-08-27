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
    RfPulseMode,
    RfPulseModeProfile,
    RfPulseOutputDirection,
    RfPulseOutputProfile,
    RfPulseOutputRequest,
    RfPulseOutputSnapshot,
    RfPulsePolarity,
    RfPulseProfile,
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
            driver="example.rf.pulse-output",
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


def _descriptor(*capabilities: str) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.pulse-output",
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
                    profile=_pulse_profile(),
                ),
                RfFeatureCapability(
                    feature=RfFeature.PULSE_OUTPUT,
                    directions=(
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


def _pulse_output_snapshot(
    *,
    enabled: bool = False,
    period_s: float = 1e-3,
) -> RfPulseOutputSnapshot:
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
        period_s=period_s,
        width_s=100e-6,
        polarity=RfPulsePolarity.NORMAL,
        pulse_state=RfPulseState.DISABLED,
    )


class _Driver:
    def __init__(
        self,
        rf_snapshots: list[RfSourceSnapshot],
        pulse_output_snapshots: list[RfPulseOutputSnapshot],
        *,
        raise_after_write: bool = False,
    ) -> None:
        self.rf_snapshots = list(rf_snapshots)
        self.pulse_output_snapshots = list(pulse_output_snapshots)
        self.raise_after_write = raise_after_write
        self.calls: list[str] = []
        self.requests: list[RfPulseOutputRequest] = []

    def close(self) -> None:
        self.calls.append("close")

    def get_rf_snapshot(self) -> RfSourceSnapshot:
        self.calls.append("snapshot")
        if not self.rf_snapshots:
            raise AssertionError("unexpected RF snapshot")
        return self.rf_snapshots.pop(0)

    def get_rf_pulse_output_snapshot(
        self,
        port_id: str,
        interface_id: str,
    ) -> RfPulseOutputSnapshot:
        self.calls.append("pulse_output_snapshot")
        assert (port_id, interface_id) == ("rf_out", "pulse_in_out")
        if not self.pulse_output_snapshots:
            raise AssertionError("unexpected Pulse-output snapshot")
        return self.pulse_output_snapshots.pop(0)

    def set_rf_pulse_output(self, request: RfPulseOutputRequest) -> None:
        self.calls.append("set_pulse_output")
        self.requests.append(request)
        if self.raise_after_write:
            raise ConfigError("fake Pulse-output write failed after transmission")


def _request(*, enabled: bool = True) -> RfPulseOutputRequest:
    return RfPulseOutputRequest(
        port_id="rf_out",
        interface_id="pulse_in_out",
        enabled=enabled,
    )


def _service(
    rf_snapshots: list[RfSourceSnapshot],
    pulse_output_snapshots: list[RfPulseOutputSnapshot],
    *,
    access: str = "read_write",
    descriptor: SimpleNamespace | None = None,
    raise_after_write: bool = False,
) -> tuple[RfSourceService, _Driver]:
    driver = _Driver(
        rf_snapshots,
        pulse_output_snapshots,
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
            "rf_source.pulse_output",
        ),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def test_pulse_output_enable_uses_one_write_and_independent_readbacks() -> None:
    request = _request()
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot()],
        [_pulse_output_snapshot(), _pulse_output_snapshot(enabled=True)],
    )

    result, artifact = service.set_pulse_output_with_artifact(request)

    assert result.enabled is True
    assert result.write_completed is True
    assert driver.requests == [request]
    assert driver.calls == [
        "snapshot",
        "pulse_output_snapshot",
        "set_pulse_output",
        "snapshot",
        "pulse_output_snapshot",
    ]
    assert artifact["operation"] == "rf_source.pulse_output_enable"
    assert artifact["postcondition_pulse_output_snapshot"]["enabled"] is True
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
def test_pulse_output_enable_rejects_unsafe_rf_preflight_without_write(
    snapshot: RfSourceSnapshot,
    message: str,
) -> None:
    service, driver = _service([snapshot], [_pulse_output_snapshot()])

    with pytest.raises(ConfigError, match=message):
        service.set_pulse_output(_request())

    assert driver.requests == []
    assert driver.calls == ["snapshot", "pulse_output_snapshot"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_pulse_output_enable_rejects_profile_mismatch_without_write() -> None:
    service, driver = _service([_rf_snapshot()], [_pulse_output_snapshot(period_s=2e-3)])

    with pytest.raises(ConfigError, match="does not match the descriptor profile"):
        service.set_pulse_output(_request())

    assert driver.requests == []
    assert driver.calls == ["snapshot", "pulse_output_snapshot"]


def test_pulse_output_disable_remains_available_for_a_drifted_profile() -> None:
    request = _request(enabled=False)
    service, driver = _service(
        [_rf_snapshot(output_enabled=True), _rf_snapshot(output_enabled=True)],
        [_pulse_output_snapshot(enabled=True, period_s=2e-3), _pulse_output_snapshot(enabled=False, period_s=2e-3)],
    )

    result = service.set_pulse_output(request)

    assert result.enabled is False
    assert result.write_completed is True
    assert driver.requests == [request]
    assert driver.calls == [
        "snapshot",
        "pulse_output_snapshot",
        "set_pulse_output",
        "snapshot",
        "pulse_output_snapshot",
    ]


def test_pulse_output_idempotence_preserves_the_readback_evidence_without_a_write() -> None:
    request = _request()
    service, driver = _service([_rf_snapshot()], [_pulse_output_snapshot(enabled=True)])

    result, artifact = service.set_pulse_output_with_artifact(request)

    assert result.write_completed is False
    assert driver.requests == []
    assert driver.calls == ["snapshot", "pulse_output_snapshot"]
    assert artifact["preflight_pulse_output_snapshot"] == artifact[
        "postcondition_pulse_output_snapshot"
    ]


def test_pulse_output_checks_capability_and_access_before_driver_io() -> None:
    missing, missing_driver = _service(
        [],
        [],
        descriptor=_descriptor("rf_source.idn", "rf_source.snapshot", "rf_source.pulse_configure"),
    )
    with pytest.raises(ConfigError, match="rf_source.pulse_output"):
        missing.set_pulse_output(_request())
    assert missing_driver.calls == []

    read_only, read_only_driver = _service([], [], access="read_only")
    with pytest.raises(AccessDeniedError, match="rf_source.pulse_output_enable"):
        read_only.set_pulse_output(_request())
    assert read_only_driver.calls == []


def test_pulse_output_write_failure_is_not_retried_and_degrades_session() -> None:
    service, driver = _service(
        [_rf_snapshot()],
        [_pulse_output_snapshot()],
        raise_after_write=True,
    )

    with pytest.raises(ConfigError, match="failed after transmission"):
        service.set_pulse_output(_request())

    assert driver.requests == [_request()]
    assert driver.calls == ["snapshot", "pulse_output_snapshot", "set_pulse_output"]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN


def test_pulse_output_postcondition_mismatch_is_not_retried_and_degrades_session() -> None:
    service, driver = _service(
        [_rf_snapshot(), _rf_snapshot()],
        [_pulse_output_snapshot(), _pulse_output_snapshot(enabled=False)],
    )

    with pytest.raises(ConfigError, match="does not match requested"):
        service.set_pulse_output(_request())

    assert driver.requests == [_request()]
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.UNCERTAIN
