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
from wavebench.errors import ConfigError


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
            auto_range=None,
            impedance="10M",
        )

    def trigger_status(self):
        self.events.append("trigger_status")
        return SimpleNamespace(source="AUTO")

    def calculation_status(self):
        self.events.append("calculation_status")
        return SimpleNamespace(function="none")

    def calculation_statistics(self, expected_function: str):
        self.events.append(f"calculation_statistics:{expected_function}")
        return SimpleNamespace(function=expected_function, value=1.0, count=2)

    def system_interface_status(self):
        self.events.append("system_interface_status")
        return SimpleNamespace(language="ENGLISH")

    def set_voltage_range(self, function: str, range_code: int):
        self.events.append(f"set_voltage_range:{function}:{range_code}")
        return SimpleNamespace(function=function, previous_range_code=2, range_code=range_code)

    def set_dcv_impedance(self, impedance: str):
        self.events.append(f"set_dcv_impedance:{impedance}")
        return SimpleNamespace(previous_impedance="10M", impedance=impedance, range_code=2)

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

    def test_trigger_status_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())
        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            status = service.trigger_status()
        self.assertEqual(status.source, "AUTO")
        self.assertEqual(events, ["trigger_status", "close"])

    def test_calculation_status_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())
        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            status = service.calculation_status()
        self.assertEqual(status.function, "none")
        self.assertEqual(events, ["calculation_status", "close"])

    def test_calculation_statistics_requires_explicit_confirmation_before_transport(self):
        service = DmmService(config=make_config(0), logger=CommandLogger())
        with patch.object(service, "_open_dmm") as open_dmm:
            with self.assertRaisesRegex(ConfigError, "explicit confirmation"):
                service.calculation_statistics(
                    "average", calculation_active_confirmed=False
                )
        open_dmm.assert_not_called()

    def test_calculation_statistics_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())
        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            result = service.calculation_statistics(
                "max", calculation_active_confirmed=True
            )
        self.assertEqual(result.function, "max")
        self.assertEqual(events, ["calculation_statistics:max", "close"])

    def test_system_interface_status_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())
        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            result = service.system_interface_status()
        self.assertEqual(result.language, "ENGLISH")
        self.assertEqual(events, ["system_interface_status", "close"])

    def test_system_interface_status_missing_capability_fails_before_transport(self):
        service = DmmService(config=make_config(0), logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.dmm",
            capabilities=("dmm.read",),
        )
        with patch.object(service, "_open_dmm") as open_dmm:
            with self.assertRaisesRegex(ConfigError, "dmm.system_interface_status"):
                service.system_interface_status()
        open_dmm.assert_not_called()

    def test_voltage_range_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())

        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            result = service.set_voltage_range("dcv", 1)

        self.assertEqual(result.range_code, 1)
        self.assertEqual(events, ["set_voltage_range:dcv:1", "close"])

    def test_dcv_impedance_closes_transport(self):
        events: list[str] = []
        service = DmmService(config=make_config(0), logger=CommandLogger())

        with patch.object(service, "_require"), patch.object(
            service, "_open_dmm", return_value=FakeDmm(events)
        ):
            result = service.set_dcv_impedance("10g")

        self.assertEqual(result.impedance, "10G")
        self.assertEqual(events, ["set_dcv_impedance:10G", "close"])

    def test_voltage_range_missing_capability_fails_before_transport(self):
        service = DmmService(config=make_config(0), logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.dmm",
            capabilities=("dmm.read",),
        )

        with patch.object(service, "_open_dmm") as open_dmm:
            with self.assertRaisesRegex(ConfigError, "dmm.set_voltage_range"):
                service.set_voltage_range("dcv", 1)

        open_dmm.assert_not_called()

    def test_dcv_impedance_missing_capability_fails_before_transport(self):
        service = DmmService(config=make_config(0), logger=CommandLogger())
        service.descriptor = SimpleNamespace(
            driver_id="legacy.dmm",
            capabilities=("dmm.read",),
        )

        with patch.object(service, "_open_dmm") as open_dmm:
            with self.assertRaisesRegex(ConfigError, "dmm.set_dcv_impedance"):
                service.set_dcv_impedance("10G")

        open_dmm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
