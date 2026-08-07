from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    PowerConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dg4202 import SourceStatus
from wavebench.drivers.dp800 import PowerStatus
from wavebench.errors import StateDriftError, error_envelope
from wavebench.logging import CommandLogger
from wavebench.services.power_service import PowerService
from wavebench.services.state_guard import PowerStateGuard, SourceStateGuard
from wavebench.services.source_service import SourceService


def _source(**changes):
    value = {
        "channel": 1,
        "output": "OFF",
        "function": "SIN",
        "frequency_hz": 1000.0,
        "amplitude": 1.0,
        "amplitude_unit": "VPP",
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "frequency_mode": "FIX",
        "sweep_enabled": "OFF",
        "apply_raw": None,
        "square_duty_cycle_percent": None,
        "measured_voltage_v": 99.0,
    }
    value.update(changes)
    return SimpleNamespace(as_dict=lambda: value)


def _power(**changes):
    value = {
        "channel": 1,
        "output": "OFF",
        "mode": "CV",
        "rating": "30V/3A",
        "set_voltage_v": 3.3,
        "set_current_a": 0.2,
        "measured_voltage_v": 1.0,
        "measured_current_a": 0.1,
        "measured_power_w": 0.1,
    }
    value.update(changes)
    return SimpleNamespace(as_dict=lambda: value)


def _config() -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1000, 1000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DMAX"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("wavebench.toml"),
        source=SourceConfig("dg4202", "TCPIP::source::INSTR", 1, False, True, 0),
        power=PowerConfig("dp800", "TCPIP::power::INSTR", 1, False, 0, 0),
    )


def _source_status(**changes) -> SourceStatus:
    values = {
        "channel": 1,
        "output": "OFF",
        "function": "SIN",
        "frequency_hz": 1000.0,
        "amplitude": 1.0,
        "amplitude_unit": "VPP",
        "offset_v": 0.0,
        "phase_deg": 0.0,
        "frequency_mode": "FIX",
        "sweep_enabled": "OFF",
        "apply_raw": None,
        "square_duty_cycle_percent": 50.0,
    }
    values.update(changes)
    return SourceStatus(**values)


def _power_status(**changes) -> PowerStatus:
    values = {
        "channel": 1,
        "output": "OFF",
        "mode": "CV",
        "rating": None,
        "set_voltage_v": 3.3,
        "set_current_a": 0.2,
        "measured_voltage_v": 0.0,
        "measured_current_a": 0.0,
        "measured_power_w": 0.0,
    }
    values.update(changes)
    return PowerStatus(**values)


def test_source_guard_ignores_measurements_and_updates_after_write() -> None:
    guard = SourceStateGuard()
    initial = _source()
    guard.observe(initial)
    guard.before_write(_source(measured_voltage_v=2.0))
    updated = _source(frequency_hz=2000.0)
    guard.after_write(updated)
    guard.before_write(_source(frequency_hz=2000.0000005))
    assert guard.snapshot()["1"]["frequency_hz"] == 2000.0


def test_source_guard_rejects_external_state_drift_with_details() -> None:
    guard = SourceStateGuard()
    guard.observe(_source())
    with pytest.raises(StateDriftError, match="changed outside") as raised:
        guard.before_write(_source(amplitude=2.0))
    assert raised.value.code == "state_drift"
    envelope = error_envelope(raised.value, operation="source.set_frequency")
    assert envelope["details"]["diff"]["amplitude"]["expected"] == 1.0
    assert envelope["details"]["diff"]["amplitude"]["actual"] == 2.0


def test_authorized_off_can_converge_after_drift() -> None:
    guard = SourceStateGuard()
    guard.observe(_source(output="ON"))
    guard.before_write(_source(output="OFF", amplitude=9.0), force_off=True)
    assert guard.snapshot()["1"]["amplitude"] == 9.0


def test_power_guard_tracks_setpoints_but_not_measurements() -> None:
    guard = PowerStateGuard()
    guard.observe(_power())
    guard.before_write(_power(measured_voltage_v=8.0))
    with pytest.raises(StateDriftError):
        guard.before_write(_power(set_voltage_v=5.0))


def test_source_service_refuses_setter_after_state_drift() -> None:
    session = Mock()
    baseline = _source_status()
    session.get_status.return_value = baseline
    session.set_frequency.return_value = _source_status(frequency_hz=2000.0)
    service = SourceService(
        config=_config(),
        logger=CommandLogger(),
        session=session,
        state_guard=SourceStateGuard(),
    )

    service.status(channel=1)
    session.get_status.return_value = _source_status(amplitude=2.0)
    with pytest.raises(StateDriftError):
        service.set_frequency(channel=1, value_hz=2000.0)
    session.set_frequency.assert_not_called()


def test_power_service_refuses_setter_after_state_drift() -> None:
    session = Mock()
    session.get_status.return_value = _power_status()
    session.set_voltage_current_limit.return_value = _power_status(set_voltage_v=5.0)
    service = PowerService(
        config=_config(),
        logger=CommandLogger(),
        session=session,
        state_guard=PowerStateGuard(),
    )

    service.status(channel=1)
    session.get_status.return_value = _power_status(set_voltage_v=4.0)
    with pytest.raises(StateDriftError):
        service.set_voltage_current_limit(channel=1, voltage_v=5.0, current_limit_a=0.2)
    session.set_voltage_current_limit.assert_not_called()


def test_service_state_guard_baseline_uses_write_readback() -> None:
    session = Mock()
    initial = _source_status()
    written = _source_status(frequency_hz=2000.0)
    session.get_status.return_value = initial
    session.set_frequency.return_value = written
    service = SourceService(
        config=_config(),
        logger=CommandLogger(),
        session=session,
        state_guard=SourceStateGuard(),
    )

    service.set_frequency(channel=1, value_hz=2000.0)
    session.get_status.return_value = written
    session.set_amplitude_vpp.return_value = _source_status(
        frequency_hz=2000.0,
        amplitude=2.0,
    )
    service.set_amplitude_vpp(channel=1, value_vpp=2.0)
    assert session.set_amplitude_vpp.call_count == 1
