from __future__ import annotations

from pathlib import Path

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
    load_config,
)
from wavebench.errors import ConfigError


def _config_text(port_block: str) -> str:
    return f'''\
[connection]
resource = "TCPIP::scope::INSTR"

[scope]

[rf_source]
driver = "example.rf1"
resource = "TCPIP::rf::INSTR"
access = "read_only"

[[rf_source.safety.ports]]
{port_block}
'''


def _accept_references(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def accept(driver: str, *, expected_kind: str) -> None:
        calls.append((driver, expected_kind))

    monkeypatch.setattr(
        "wavebench.instruments.registry.validate_instrument_reference",
        accept,
    )
    return calls


def test_loads_isolated_rf_source_config_and_safety_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "wavebench.toml"
    path.write_text(
        _config_text(
            '''\
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50
'''
        ),
        encoding="utf-8",
    )
    calls = _accept_references(monkeypatch)

    config = load_config(path)

    assert config.rf_source == RfSourceConfig(
        driver="example.rf1",
        resource="TCPIP::rf::INSTR",
        access="read_only",
        safety_ports=(
            RfPortSafetyConfig(
                port_id="rf_out",
                minimum_frequency_hz=9_000.0,
                maximum_frequency_hz=3_000_000_000.0,
                maximum_power_dbm=-20.0,
                actual_termination_ohm=50.0,
            ),
        ),
    )
    assert ("example.rf1", "rf_source") in calls


@pytest.mark.parametrize(
    ("port_block", "message"),
    (
        (
            '''\
port_id = "rf out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50
''',
            "port_id",
        ),
        (
            '''\
port_id = "rf_out"
minimum_frequency_hz = 3000000000
maximum_frequency_hz = 9000
maximum_power_dbm = -20
actual_termination_ohm = 50
''',
            "maximum_frequency_hz",
        ),
        (
            '''\
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = nan
''',
            "actual_termination_ohm",
        ),
    ),
)
def test_rejects_invalid_rf_port_safety_declarations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    port_block: str,
    message: str,
) -> None:
    path = tmp_path / "wavebench.toml"
    path.write_text(_config_text(port_block), encoding="utf-8")
    _accept_references(monkeypatch)

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_rejects_duplicate_rf_port_safety_declarations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "wavebench.toml"
    path.write_text(
        _config_text(
            '''\
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50

[[rf_source.safety.ports]]
port_id = "rf_out"
minimum_frequency_hz = 9000
maximum_frequency_hz = 3000000000
maximum_power_dbm = -20
actual_termination_ohm = 50
'''
        ),
        encoding="utf-8",
    )
    _accept_references(monkeypatch)

    with pytest.raises(ConfigError, match="must be unique"):
        load_config(path)


def test_resource_override_preserves_rf_source_safety_declarations() -> None:
    safety_port = RfPortSafetyConfig("rf_out", 9_000.0, 3_000_000_000.0, -20.0, 50.0)
    config = WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        rf_source=RfSourceConfig(
            driver="example.rf1",
            resource="TCPIP::old-rf::INSTR",
            access="read_only",
            safety_ports=(safety_port,),
        ),
    )

    updated = config.with_rf_source_resource("TCPIP::new-rf::INSTR")

    assert updated.rf_source is not None
    assert updated.rf_source.resource == "TCPIP::new-rf::INSTR"
    assert updated.rf_source.access == "read_only"
    assert updated.rf_source.safety_ports == (safety_port,)
