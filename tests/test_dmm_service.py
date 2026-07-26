from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    DmmConfig,
    OutputConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.logging import CommandLogger
from wavebench.services.dmm_service import DmmService


def make_config(settle_ms_before_read: int) -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1000, 1000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "dmax"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("test.toml"),
        dmm=DmmConfig(
            driver="dm3058",
            resource="TCPIP::dmm::INSTR",
            backend="lan",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_ms=1000,
            settle_ms_before_read=settle_ms_before_read,
        ),
    )


class FakeDmm:
    def __init__(self, events: list[str]):
        self.events = events
        self.active_function = "dcv"

    def function_status(self) -> str:
        self.events.append("function_status")
        return self.active_function

    def set_function(self, function: str) -> str:
        self.events.append(f"set_function:{function}")
        self.active_function = function
        return self.active_function

    def measurement_profile(self):
        self.events.append("measurement_profile")
        return SimpleNamespace(
            function=self.active_function,
            range_code=0,
            auto_range=True,
            impedance="10M",
        )

    def read(self, function: str = "dcv"):
        self.events.append(f"read:{function}")
        return SimpleNamespace(function=function, value=1.0, unit="V", raw="1.000000E+00")

    def close(self) -> None:
        self.events.append("close")


class DmmServiceTests(unittest.TestCase):
    def test_read_waits_before_dmm_read_when_configured(self):
        events: list[str] = []
        service = DmmService(config=make_config(250), logger=CommandLogger())

        with patch.object(service, "_open_dmm", return_value=FakeDmm(events)), patch(
            "wavebench.services.dmm_service.time.sleep", side_effect=lambda seconds: events.append(f"sleep:{seconds}")
        ) as sleep:
            reading = service.read(function="acv")

        sleep.assert_called_once_with(0.25)
        self.assertEqual(reading.function, "acv")
        self.assertEqual(events, ["sleep:0.25", "read:acv", "close"])

    def test_read_does_not_wait_when_delay_is_zero(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())

        with patch.object(service, "_open_dmm", return_value=FakeDmm(events)), patch(
            "wavebench.services.dmm_service.time.sleep"
        ) as sleep:
            service.read(function="dcv")

        sleep.assert_not_called()
        self.assertEqual(events, ["read:dcv", "close"])

    def test_function_status_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())

        with patch.object(service, "_open_dmm", return_value=FakeDmm(events)):
            result = service.function_status()

        self.assertEqual(result, "dcv")
        self.assertEqual(events, ["function_status", "close"])

    def test_set_function_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())

        with patch.object(service, "_open_dmm", return_value=FakeDmm(events)):
            result = service.set_function("acv")

        self.assertEqual(result, "acv")
        self.assertEqual(events, ["set_function:acv", "close"])

    def test_measurement_profile_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())

        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            profile = service.measurement_profile()

        self.assertEqual(profile.function, "dcv")
        self.assertEqual(events, ["measurement_profile", "close"])


if __name__ == "__main__":
    unittest.main()
