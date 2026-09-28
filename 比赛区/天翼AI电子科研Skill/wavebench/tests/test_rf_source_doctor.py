from __future__ import annotations

from pathlib import Path

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    RfSourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.doctor import doctor_records


def _config(*, rf_resource: str | None) -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1_000, 1_000),
        scope=ScopeConfig("rtm2032", "RTM2032", 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        rf_source=RfSourceConfig(driver="example.rf1", resource=rf_resource),
    )


def test_doctor_adds_rf_source_as_identity_only_target() -> None:
    records = doctor_records(
        _config(rf_resource="TCPIP::rf::INSTR"),
        idn_probe=lambda resource, timeout_ms: {
            "TCPIP::scope::INSTR": "Rohde&Schwarz,RTM2032,0,0",
            "TCPIP::rf::INSTR": "Example,RF1,0,0",
        }.get(resource),
    )

    assert [(record.target, record.severity) for record in records] == [
        ("scope", "ok"),
        ("rf_source", "ok"),
    ]


def test_doctor_reports_unconfigured_rf_source_resource_without_querying() -> None:
    queried: list[str] = []
    records = doctor_records(
        _config(rf_resource=None),
        idn_probe=lambda resource, timeout_ms: queried.append(resource) or "unused",
    )

    rf_source = next(record for record in records if record.target == "rf_source")
    assert rf_source.severity == "warning"
    assert rf_source.idn is None
    assert "" not in queried
