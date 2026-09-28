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
from wavebench.errors import ConfigError
from wavebench.instruments.rf_source_extensions import (
    RF_SOURCE_CONTRACT_VERSION,
    RfExternalGatePolarity,
    RfExternalTriggerEdge,
    RfFeature,
    RfFeatureCapability,
    RfFeatureDirection,
    RfOutputPortProfile,
    RfPulseTriggerMode,
    RfSourceDescriptorExtensions,
    RfSourceTopology,
    RfSweepMode,
    RfSweepTriggerMode,
    RfTriggerProfile,
    RfTriggerSnapshot,
)
from wavebench.logging import CommandLogger
from wavebench.services.rf_source_service import RfSourceService
from wavebench.transport.session import InstrumentSessionState, SessionHealth


def _config(*, access: str = "read_only") -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        rf_source=RfSourceConfig(
            driver="example.rf.trigger",
            resource="TCPIP::rf::INSTR",
            access=access,  # type: ignore[arg-type]
        ),
    )


def _profile(*, state_readable: bool = True) -> RfTriggerProfile:
    if not state_readable:
        return RfTriggerProfile(state_readable=False)
    return RfTriggerProfile(
        state_readable=True,
        pulse_trigger_modes=(
            RfPulseTriggerMode.AUTOMATIC,
            RfPulseTriggerMode.BUS,
            RfPulseTriggerMode.EXTERNAL,
            RfPulseTriggerMode.EXTERNAL_GATE,
            RfPulseTriggerMode.KEY,
        ),
        pulse_external_trigger_edges=(
            RfExternalTriggerEdge.NEGATIVE,
            RfExternalTriggerEdge.POSITIVE,
        ),
        pulse_external_gate_polarities=(
            RfExternalGatePolarity.INVERTED,
            RfExternalGatePolarity.NORMAL,
        ),
        sweep_modes=(RfSweepMode.CONTINUOUS, RfSweepMode.SINGLE),
        sweep_period_trigger_modes=(
            RfSweepTriggerMode.AUTOMATIC,
            RfSweepTriggerMode.BUS,
            RfSweepTriggerMode.EXTERNAL,
            RfSweepTriggerMode.KEY,
        ),
        sweep_point_trigger_modes=(
            RfSweepTriggerMode.AUTOMATIC,
            RfSweepTriggerMode.BUS,
            RfSweepTriggerMode.EXTERNAL,
            RfSweepTriggerMode.KEY,
        ),
    )


def _descriptor(
    *capabilities: str,
    profile: RfTriggerProfile | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id="example.rf.trigger",
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
                    feature=RfFeature.TRIGGER,
                    directions=(RfFeatureDirection.READ,),
                    port_ids=("rf_out",),
                    profile=profile or _profile(),
                ),
            ),
        ),
    )


def _snapshot(
    *,
    port_id: str = "rf_out",
    pulse_trigger_mode: RfPulseTriggerMode = RfPulseTriggerMode.AUTOMATIC,
) -> RfTriggerSnapshot:
    return RfTriggerSnapshot(
        port_id=port_id,
        pulse_trigger_mode=pulse_trigger_mode,
        pulse_external_trigger_edge=RfExternalTriggerEdge.POSITIVE,
        pulse_external_gate_polarity=RfExternalGatePolarity.NORMAL,
        sweep_mode=RfSweepMode.CONTINUOUS,
        sweep_period_trigger_mode=RfSweepTriggerMode.AUTOMATIC,
        sweep_point_trigger_mode=RfSweepTriggerMode.AUTOMATIC,
    )


class _Driver:
    def __init__(self, snapshot: RfTriggerSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []
        self.write_calls: list[str] = []

    def close(self) -> None:
        self.calls.append("close")

    def get_rf_trigger_snapshot(self, port_id: str) -> RfTriggerSnapshot:
        self.calls.append("trigger_snapshot")
        assert port_id == "rf_out"
        return self.snapshot


def _service(
    snapshot: RfTriggerSnapshot,
    *,
    access: str = "read_only",
    descriptor: SimpleNamespace | None = None,
) -> tuple[RfSourceService, _Driver]:
    driver = _Driver(snapshot)
    service = RfSourceService(
        config=_config(access=access),
        logger=CommandLogger(),
        session=driver,
        descriptor=descriptor
        or _descriptor("rf_source.idn", "rf_source.trigger_snapshot"),
        session_state=InstrumentSessionState(),
    )
    return service, driver


def test_trigger_snapshot_reads_declared_state_without_writes() -> None:
    service, driver = _service(_snapshot())

    result, artifact = service.trigger_snapshot_with_artifact("rf_out")

    assert result.pulse_trigger_mode is RfPulseTriggerMode.AUTOMATIC
    assert driver.calls == ["trigger_snapshot"]
    assert driver.write_calls == []
    assert artifact["operation"] == "rf_source.trigger_snapshot"
    assert artifact["trigger_snapshot"]["port_id"] == "rf_out"
    assert service.session_state is not None
    assert service.session_state.health is SessionHealth.HEALTHY


def test_trigger_snapshot_rejects_missing_capability_or_profile_before_driver_io() -> None:
    missing_capability, missing_capability_driver = _service(
        _snapshot(),
        descriptor=_descriptor("rf_source.idn"),
    )
    with pytest.raises(ConfigError, match="rf_source.trigger_snapshot"):
        missing_capability.trigger_snapshot("rf_out")
    assert missing_capability_driver.calls == []
    assert missing_capability_driver.write_calls == []

    unreadable_profile, unreadable_profile_driver = _service(
        _snapshot(),
        descriptor=_descriptor(
            "rf_source.idn",
            "rf_source.trigger_snapshot",
            profile=_profile(state_readable=False),
        ),
    )
    with pytest.raises(ConfigError, match="readable trigger profile"):
        unreadable_profile.trigger_snapshot("rf_out")
    assert unreadable_profile_driver.calls == []
    assert unreadable_profile_driver.write_calls == []

    wrong_port, wrong_port_driver = _service(_snapshot())
    with pytest.raises(ConfigError, match="undeclared RF port"):
        wrong_port.trigger_snapshot("not_a_port")
    assert wrong_port_driver.calls == []
    assert wrong_port_driver.write_calls == []


@pytest.mark.parametrize(
    ("snapshot", "message"),
    (
        (_snapshot(port_id="other"), "does not match the requested port"),
        (
            _snapshot(pulse_trigger_mode=RfPulseTriggerMode.BUS),
            "pulse trigger mode is outside the descriptor profile",
        ),
    ),
)
def test_trigger_snapshot_rejects_unexpected_readback_without_writes(
    snapshot: RfTriggerSnapshot,
    message: str,
) -> None:
    profile = _profile()
    if snapshot.pulse_trigger_mode is RfPulseTriggerMode.BUS:
        profile = RfTriggerProfile(
            state_readable=True,
            pulse_trigger_modes=(RfPulseTriggerMode.AUTOMATIC,),
            pulse_external_trigger_edges=(RfExternalTriggerEdge.POSITIVE,),
            pulse_external_gate_polarities=(RfExternalGatePolarity.NORMAL,),
            sweep_modes=(RfSweepMode.CONTINUOUS,),
            sweep_period_trigger_modes=(RfSweepTriggerMode.AUTOMATIC,),
            sweep_point_trigger_modes=(RfSweepTriggerMode.AUTOMATIC,),
        )
    service, driver = _service(
        snapshot,
        descriptor=_descriptor("rf_source.idn", "rf_source.trigger_snapshot", profile=profile),
    )

    with pytest.raises(ConfigError, match=message):
        service.trigger_snapshot("rf_out")

    assert driver.calls == ["trigger_snapshot"]
    assert driver.write_calls == []
