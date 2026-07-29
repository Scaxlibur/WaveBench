from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
    load_config,
)
from wavebench.drivers.ds1104 import DS1104Scope, parse_rigol_waveform_preamble
from wavebench.errors import DataError
from wavebench.logging import CommandLogger
from wavebench.services.scope_service import ScopeService, assert_scope_high_impedance


class FakeTransport:
    def __init__(self, *, responses=None, binary_reader=None):
        self.responses = responses or {}
        self.binary_reader = binary_reader
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.events: list[tuple[str, str]] = []
        self.closed = False

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        return self.responses[command]

    def query_bin_block(self, command):
        self.queries.append(command)
        if self.binary_reader is not None:
            return self.binary_reader(self, command)
        return self.responses[command]

    def query_opc(self):
        self.queries.append("*OPC?")
        return "1"

    def record_event(self, direction, text):
        self.events.append((direction, text))

    def close(self):
        self.closed = True


def test_parse_rigol_waveform_preamble():
    preamble = parse_rigol_waveform_preamble(
        "0,0,3,1,5.0e-1,-1.0,1,2.5e-1,100,10"
    )

    assert preamble.points == 3
    assert preamble.x_increment == 0.5
    assert preamble.x_origin == -1.0
    assert preamble.y_increment == 0.25
    assert preamble.y_reference == 10.0


@pytest.mark.parametrize(
    "response",
    [
        "0,0,3",
        "1,0,3,1,1,0,0,1,0,0",
        "0,0,0,1,1,0,0,1,0,0",
        "bad,0,3,1,1,0,0,1,0,0",
    ],
)
def test_parse_rigol_waveform_preamble_rejects_invalid_data(response):
    with pytest.raises(DataError):
        parse_rigol_waveform_preamble(response)


def test_fetch_normal_waveform_converts_byte_samples_and_time_axis():
    transport = FakeTransport(
        responses={
            ":WAVeform:PREamble?": "0,0,3,1,0.5,-1,1,0.25,100,10",
            ":WAVeform:DATA?": bytes([109, 110, 111]),
        }
    )
    scope = DS1104Scope(transport=transport)

    waveform = scope.fetch_waveform(channel=2, points="DEF", check_errors=False)

    np.testing.assert_allclose(waveform.voltages_v, [-0.25, 0.0, 0.25])
    np.testing.assert_allclose(waveform.times_s, [-1.5, -1.0, -0.5])
    assert waveform.channel == 2
    assert transport.writes == [
        ":CHANnel2:DISPlay ON",
        ":WAVeform:SOURce CHANnel2",
        ":WAVeform:MODE NORMal",
        ":WAVeform:FORMat BYTE",
        ":WAVeform:STARt 1",
        ":WAVeform:STOP 3",
    ]


def test_fetch_normal_resets_window_after_a_previous_raw_transfer():
    transport = FakeTransport(
        responses={
            ":WAVeform:PREamble?": "0,0,3,1,1e-6,0,0,0.1,0,127",
            ":WAVeform:DATA?": bytes([127, 128, 129]),
        }
    )
    transport.writes.extend(
        [":WAVeform:STARt 2750001", ":WAVeform:STOP 3000000"]
    )
    scope = DS1104Scope(transport=transport)

    waveform = scope.fetch_waveform(channel=1, points="DEF", check_errors=False)

    assert waveform.sample_count == 3
    assert transport.writes[-2:] == [":WAVeform:STARt 1", ":WAVeform:STOP 3"]


def test_fetch_raw_waveform_stops_and_reads_in_250k_chunks():
    points = 250_002

    def binary_reader(transport, command):
        assert command == ":WAVeform:DATA?"
        start = int(transport.writes[-2].split()[-1])
        stop = int(transport.writes[-1].split()[-1])
        return bytes([127]) * (stop - start + 1)

    transport = FakeTransport(
        responses={
            ":WAVeform:PREamble?": f"0,0,{points},1,1e-9,0,0,0.01,0,127",
        },
        binary_reader=binary_reader,
    )
    scope = DS1104Scope(transport=transport)

    waveform = scope.fetch_waveform(channel=1, points="DMAX", check_errors=False)

    assert waveform.sample_count == points
    assert transport.writes[0] == ":STOP"
    assert ":WAVeform:STARt 1" in transport.writes
    assert ":WAVeform:STOP 250000" in transport.writes
    assert ":WAVeform:STARt 250001" in transport.writes
    assert ":WAVeform:STOP 250002" in transport.writes
    assert transport.queries.count(":WAVeform:DATA?") == 2
    assert any(
        direction == "telemetry"
        and "stage=waveform_chunk" in text
        and "range=1-250000" in text
        and "throughput_mib_s=" in text
        for direction, text in transport.events
    )
    assert any(
        direction == "telemetry"
        and "stage=waveform_transfer" in text
        and f"points={points}" in text
        and "chunks=2" in text
        for direction, text in transport.events
    )
    assert any(
        direction == "telemetry"
        and "stage=waveform_convert" in text
        and f"points={points}" in text
        for direction, text in transport.events
    )


def test_failed_raw_chunk_records_range_and_failure_before_raising():
    points = 250_002

    def binary_reader(transport, command):
        assert command == ":WAVeform:DATA?"
        start = int(transport.writes[-2].split()[-1])
        stop = int(transport.writes[-1].split()[-1])
        if start == 250_001:
            raise TimeoutError("interrupted")
        return bytes([127]) * (stop - start + 1)

    transport = FakeTransport(
        responses={
            ":WAVeform:PREamble?": f"0,0,{points},1,1e-9,0,0,0.01,0,127",
        },
        binary_reader=binary_reader,
    )

    with pytest.raises(TimeoutError, match="interrupted"):
        DS1104Scope(transport=transport).fetch_waveform(
            channel=1,
            points="DMAX",
            check_errors=False,
        )

    assert any(
        direction == "telemetry"
        and "stage=waveform_chunk" in text
        and "status=failed" in text
        and "range=250001-250002" in text
        for direction, text in transport.events
    )


def test_capture_translates_total_time_range_to_12_divisions():
    transport = FakeTransport(
        responses={
            ":WAVeform:PREamble?": "0,0,2,1,1e-3,0,0,0.1,0,127",
            ":WAVeform:DATA?": bytes([127, 128]),
        }
    )
    scope = DS1104Scope(transport=transport)

    scope.capture_waveform(
        channel=3,
        points="DEF",
        check_errors=False,
        time_range_s=0.012,
        vertical_scale_v_per_div=0.2,
    )

    assert ":TIMebase:MODE MAIN" in transport.writes
    assert ":TIMebase:MAIN:SCALe 0.001" in transport.writes
    assert ":CHANnel3:SCALe 0.2" in transport.writes
    assert ":CHANnel3:OFFSet 0" in transport.writes
    assert transport.writes.index(":SINGle") < transport.writes.index(
        ":WAVeform:MODE NORMal"
    )
    assert "*OPC?" in transport.queries


def test_multichannel_capture_uses_one_single_and_one_opc():
    current_channel = {"value": 1}

    def binary_reader(transport, command):
        assert command == ":WAVeform:DATA?"
        return bytes([127 + current_channel["value"], 128 + current_channel["value"]])

    transport = FakeTransport(
        responses={
            ":WAVeform:PREamble?": "0,0,2,1,1e-3,0,0,0.1,0,127",
        },
        binary_reader=binary_reader,
    )
    completed = []

    waveforms = DS1104Scope(transport=transport).capture_waveforms(
        channels=[1, 2],
        points="DEF",
        check_errors=False,
        on_channel_start=lambda channel: current_channel.__setitem__("value", channel),
        on_waveform=lambda channel, waveform: completed.append(channel),
    )

    assert list(waveforms) == [1, 2]
    assert completed == [1, 2]
    assert transport.writes.count(":SINGle") == 1
    assert transport.queries.count("*OPC?") == 1


def test_ds1104_rejects_channels_outside_one_to_four():
    scope = DS1104Scope(transport=FakeTransport())

    with pytest.raises(DataError, match="between 1 and 4"):
        scope.channel_coupling(5)


def test_ds1104_screenshot_uses_display_data_png_query():
    png = b"\x89PNG\r\n\x1a\nrest"
    transport = FakeTransport(responses={":DISPlay:DATA? ON,OFF,PNG": png})
    scope = DS1104Scope(transport=transport)

    assert scope.screenshot_png() == png
    assert transport.queries == [":DISPlay:DATA? ON,OFF,PNG"]


@pytest.mark.parametrize("coupling", ["AC", "DC", "GND"])
def test_ds1000z_couplings_pass_fixed_high_impedance_guard(coupling):
    assert (
        assert_scope_high_impedance(coupling, channel=1, driver="ds1104") == coupling
    )


def _service_config(tmp: str, driver: str = "ds1104") -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::fake::INSTR", 1000, 30000),
        scope=ScopeConfig(driver, None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DEF"),
        output=OutputConfig(Path(tmp), "timestamp_label", False, False, True, False, False),
        source_path=Path(tmp) / "wavebench.toml",
    )


def test_scope_service_routes_ds1104_driver():
    with TemporaryDirectory() as tmp:
        transport = FakeTransport()
        service = ScopeService(config=_service_config(tmp), logger=CommandLogger())
        with patch(
            "wavebench.instruments.factory.PyVisaTransport.open",
            return_value=transport,
        ):
            scope = service.open_session()

        assert isinstance(scope, DS1104Scope)
        scope.close()
        assert transport.closed


def test_load_config_accepts_ds1104_driver():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "wavebench.toml"
        path.write_text(
            """
[connection]
backend = "lan"
resource = "TCPIP::192.0.2.20::INSTR"

[scope]
driver = "ds1104"
""",
            encoding="utf-8",
        )

        config = load_config(path)

        assert config.scope.driver == "ds1104"
