from pathlib import Path

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    DmmConfig,
    OutputConfig,
    ScopeConfig,
    SourceConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.discovery import DiscoveryResult
from wavebench.doctor import _resource_suggestion, doctor_records, has_doctor_errors


def make_config(*, source_resource="TCPIP::192.0.2.127::INSTR"):
    return WaveBenchConfig(
        connection=ConnectionConfig(
            backend="lan",
            resource="TCPIP::192.0.2.115::INSTR",
            timeout_ms=1000,
            opc_timeout_ms=30000,
        ),
        scope=ScopeConfig(
            driver="rtm2032",
            model_hint="RTM2032",
            default_channel=1,
            reset_before_run=False,
            check_errors=True,
        ),
        autoscale=AutoscaleConfig(wait_opc=True, check_errors=True),
        waveform=WaveformConfig(format="real", byte_order="lsbf", points="DEF"),
        output=OutputConfig(
            directory=Path("data/raw"),
            package_naming="timestamp_label",
            save_csv=True,
            save_npy=True,
            save_json=True,
            save_commands_log=True,
            save_screenshot=False,
        ),
        source_path=Path("wavebench.toml"),
        source=SourceConfig(
            driver="dg4202",
            resource=source_resource,
            default_channel=1,
            check_errors=True,
            ensure_fix_mode_on_set_frequency=True,
            settle_ms_after_set_frequency=0,
        ),
    )


def test_doctor_reports_configured_instruments_ok():
    idns = {
        "TCPIP::192.0.2.115::INSTR": "Rohde&Schwarz,RTM2032,123,06.010",
        "TCPIP::192.0.2.127::INSTR": "Rigol Technologies,DG4202,DG4E,00.01.14",
    }

    records = doctor_records(make_config(), idn_probe=lambda resource, timeout_ms: idns.get(resource))

    assert [record.severity for record in records] == ["ok", "ok"]
    assert not has_doctor_errors(records)


def test_doctor_reports_missing_resource_as_warning():
    records = doctor_records(make_config(source_resource=None), idn_probe=lambda resource, timeout_ms: "unused")

    source = next(record for record in records if record.target == "source")
    assert source.severity == "warning"
    assert "未配置" in source.message
    assert not has_doctor_errors(records)


def test_doctor_reports_unreachable_resource_as_error():
    records = doctor_records(make_config(), idn_probe=lambda resource, timeout_ms: None)

    assert has_doctor_errors(records)
    assert all(record.severity == "error" for record in records)


def test_doctor_reports_idn_mismatch_as_warning():
    records = doctor_records(make_config(), idn_probe=lambda resource, timeout_ms: "UNKNOWN,MODEL,0")

    assert [record.severity for record in records] == ["warning", "warning"]
    assert not has_doctor_errors(records)


def test_doctor_suggests_windows_serial_resources():
    for resource in ("COM3", r"\\.\COM10"):
        assert "串口" in _resource_suggestion(resource)


def test_doctor_appends_candidate_for_unreachable_matching_idn():
    def discoverer(**kwargs):
        assert kwargs["subnet"] == "192.0.2.0/24"
        return [
            DiscoveryResult(
                address="192.0.2.225",
                port=None,
                protocol="visa",
                resource="TCPIP::192.0.2.225::INSTR",
                source="network",
                status="idn",
                idn="Rigol Technologies,DG4202,DG4E,00.01.14",
            ),
            DiscoveryResult(
                address="192.0.2.226",
                port=None,
                protocol="visa",
                resource="TCPIP::192.0.2.226::INSTR",
                source="network",
                status="idn",
                idn="Rohde&Schwarz,RTM2032,123,06.010",
            ),
        ]

    records = doctor_records(
        make_config(),
        idn_probe=lambda resource, timeout_ms: None,
        discover_subnet="192.0.2.0/24",
        discoverer=discoverer,
    )

    candidates = [record for record in records if record.severity == "candidate"]
    assert [(record.target, record.resource) for record in candidates] == [
        ("scope", "TCPIP::192.0.2.226::INSTR"),
        ("source", "TCPIP::192.0.2.225::INSTR"),
    ]


def test_doctor_does_not_suggest_candidate_for_healthy_target():
    records = doctor_records(
        make_config(),
        idn_probe=lambda resource, timeout_ms: "Rigol Technologies,DG4202,DG4E,00.01.14",
        discover_subnet="192.0.2.0/24",
        discoverer=lambda **kwargs: [
            DiscoveryResult(
                address="192.0.2.225",
                port=None,
                protocol="visa",
                resource="TCPIP::192.0.2.225::INSTR",
                source="network",
                status="idn",
                idn="Rigol Technologies,DG4202,DG4E,00.01.14",
            )
        ],
    )

    assert not [record for record in records if record.severity == "candidate"]


def test_doctor_uses_serial_transport_for_serial_dmm(monkeypatch):
    config = make_config()
    object.__setattr__(
        config,
        "dmm",
        DmmConfig(
            driver="dm3058",
            resource="/dev/serial/by-id/usb-test",
            backend="serial",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_ms=3000,
            write_termination="crlf",
            read_termination="lf",
        ),
    )
    events = []

    class FakeSerialTransport:
        def query(self, command):
            events.append(("query", command))
            return "Rigol Technologies,DM3058,serial,firmware"

        def close(self):
            events.append(("close", None))

    def fake_open(serial_config, logger=None):
        events.append(("open", serial_config))
        return FakeSerialTransport()

    monkeypatch.setattr("wavebench.doctor.SerialTransport.open", fake_open)
    monkeypatch.setattr(
        "wavebench.doctor.query_resource_idn",
        lambda resource, timeout_ms: "Rohde&Schwarz,RTM2032,123,06.010"
        if "115" in resource
        else "Rigol Technologies,DG4202,DG4E,00.01.14",
    )
    records = doctor_records(config)

    dmm = next(record for record in records if record.target == "dmm")
    assert dmm.severity == "ok"
    assert events[0][0] == "open"
    assert events[0][1].write_termination == "crlf"
    assert events[0][1].timeout_ms == 1000
    assert events[1:] == [("query", "*IDN?"), ("close", None)]


def test_doctor_injected_probe_takes_precedence_for_serial_dmm(monkeypatch):
    config = make_config()
    object.__setattr__(
        config,
        "dmm",
        DmmConfig("dm3058", "/dev/serial/by-id/usb-test", "serial", 9600, 8, "N", 1, 3000),
    )
    monkeypatch.setattr(
        "wavebench.doctor.SerialTransport.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("serial should not open")),
    )

    records = doctor_records(
        config,
        idn_probe=lambda resource, timeout_ms: "Rigol Technologies,DM3058,serial,firmware",
    )

    assert next(record for record in records if record.target == "dmm").severity == "ok"
