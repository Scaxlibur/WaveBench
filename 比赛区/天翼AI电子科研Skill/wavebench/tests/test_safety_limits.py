from dataclasses import replace
from pathlib import Path
from math import inf, nan
from unittest.mock import patch
import unittest

from wavebench.config import (
    AutoscaleConfig,
    ConnectionConfig,
    OutputConfig,
    PowerConfig,
    SafetyLimitsConfig,
    SourceConfig,
    ScopeConfig,
    WaveBenchConfig,
    WaveformConfig,
)
from wavebench.drivers.dg4202 import SourceStatus
from wavebench.drivers.dp800 import PowerProtectionStatus, PowerStatus
from wavebench.errors import ConfigError
from wavebench.logging import CommandLogger
from wavebench.services.power_service import PowerService
from wavebench.services.source_service import SourceService


def make_config() -> WaveBenchConfig:
    return WaveBenchConfig(
        connection=ConnectionConfig("lan", "TCPIP::scope::INSTR", 1000, 1000),
        scope=ScopeConfig("rtm2032", None, 1, False, True),
        autoscale=AutoscaleConfig(True, True),
        waveform=WaveformConfig("real", "lsbf", "DMAX"),
        output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
        source_path=Path("wavebench.toml"),
        source=SourceConfig("dg4202", "TCPIP::source::INSTR", 2, True, True, 0),
        power=PowerConfig("dp800", "TCPIP::power::INSTR", 1, True, 0, 0),
        safety_limits=SafetyLimitsConfig(
            max_source_vpp=2.0,
            max_power_voltage_v=5.0,
            max_power_current_limit_a=0.2,
        ),
    )


def source_status(amplitude: float) -> SourceStatus:
    return SourceStatus(
        channel=2,
        output="OFF",
        function="SIN",
        frequency_hz=1000.0,
        amplitude=amplitude,
        amplitude_unit="VPP",
        offset_v=0.0,
        phase_deg=0.0,
        frequency_mode="FIX",
        sweep_enabled="OFF",
        apply_raw=None,
    )


def power_status(voltage: float, current_limit: float) -> PowerStatus:
    return PowerStatus(
        channel=1,
        output="OFF",
        mode="CV",
        rating=None,
        set_voltage_v=voltage,
        set_current_a=current_limit,
        measured_voltage_v=0.0,
        measured_current_a=0.0,
        measured_power_w=0.0,
    )


def protection_status(ovp_threshold: float, ocp_threshold: float) -> PowerProtectionStatus:
    return PowerProtectionStatus(
        channel=1,
        ovp_enabled="ON",
        ovp_threshold_v=ovp_threshold,
        ovp_tripped="NO",
        ocp_enabled="ON",
        ocp_threshold_a=ocp_threshold,
        ocp_tripped="NO",
    )


class FakeSource:
    def __init__(self, status: SourceStatus):
        self.status = status
        self.output_calls = []
        self.closed = False

    def get_status(self, channel: int) -> SourceStatus:
        return self.status

    def set_output(self, channel: int, enabled: bool, *, check_errors: bool = True) -> SourceStatus:
        self.output_calls.append((channel, enabled, check_errors))
        return self.status

    def close(self) -> None:
        self.closed = True


class FakePower:
    def __init__(self, status: PowerStatus, protection: PowerProtectionStatus | None = None):
        self.status = status
        self.protection = protection or protection_status(6.0, 0.3)
        self.output_calls = []
        self.protection_calls = []
        self.closed = False

    def get_status(self, channel: int) -> PowerStatus:
        return self.status

    def get_protection_status(self, channel: int) -> PowerProtectionStatus:
        return self.protection

    def set_protection(
        self,
        channel: int,
        *,
        ovp_threshold_v: float | None = None,
        ovp_enabled: bool | None = None,
        ocp_threshold_a: float | None = None,
        ocp_enabled: bool | None = None,
        check_errors: bool = True,
    ) -> PowerProtectionStatus:
        self.protection_calls.append((
            channel,
            ovp_threshold_v,
            ovp_enabled,
            ocp_threshold_a,
            ocp_enabled,
            check_errors,
        ))
        return self.protection

    def set_output(
        self,
        channel: int,
        enabled: bool,
        *,
        check_errors: bool = True,
        settle_ms_after_output: int = 0,
    ) -> PowerStatus:
        self.output_calls.append((channel, enabled, check_errors, settle_ms_after_output))
        return self.status

    def close(self) -> None:
        self.closed = True


class SafetyLimitsOutputTests(unittest.TestCase):
    def test_source_amplitude_rejects_nonfinite_values(self):
        service = SourceService(config=make_config(), logger=CommandLogger())

        for value in (nan, inf, -inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "必须为有限数"):
                    service.set_amplitude_vpp(channel=2, value_vpp=value)

    def test_arbitrary_parameters_reject_nonfinite_values_before_opening_source(self):
        service = SourceService(config=make_config(), logger=CommandLogger())

        for field, value in (
            ("playback_frequency_hz", nan),
            ("amplitude_vpp", inf),
            ("offset_v", -inf),
        ):
            values = {
                "playback_frequency_hz": 1000.0,
                "amplitude_vpp": 1.0,
                "offset_v": 0.0,
            }
            values[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ConfigError, "必须为有限数"):
                    service.upload_arbitrary_waveform(
                        channel=2,
                        file_path="unused.npy",
                        **values,
                    )

    def test_source_output_on_rejects_current_amplitude_over_limit(self):
        fake = FakeSource(source_status(5.0))
        service = SourceService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_source", return_value=fake):
            with self.assertRaisesRegex(ConfigError, "安全上限已超出"):
                service.set_output(channel=2, enabled=True)

        self.assertEqual(fake.output_calls, [])
        self.assertTrue(fake.closed)

    def test_source_output_on_rejects_invalid_or_non_vpp_amplitude_before_write(self):
        cases = (
            (replace(source_status(1.0), amplitude=None), "必须为有限数"),
            (replace(source_status(1.0), amplitude="1.0"), "必须为有限数"),
            (replace(source_status(1.0), amplitude=True), "必须为有限数"),
            (replace(source_status(1.0), amplitude=nan), "必须为有限数"),
            (replace(source_status(1.0), amplitude=inf), "必须为有限数"),
            (replace(source_status(1.0), amplitude=-1.0), "必须为非负数"),
            (replace(source_status(1.0), amplitude_unit="VRMS"), "可读的 VPP 幅度"),
        )
        for status, message in cases:
            with self.subTest(status=status):
                fake = FakeSource(status)
                service = SourceService(config=make_config(), logger=CommandLogger())

                with patch.object(service, "_open_source", return_value=fake):
                    with self.assertRaisesRegex(ConfigError, message):
                        service.set_output(channel=2, enabled=True)

                self.assertEqual(fake.output_calls, [])
                self.assertTrue(fake.closed)

    def test_source_set_vpp_rejects_invalid_values_before_opening_source(self):
        service = SourceService(config=make_config(), logger=CommandLogger())
        for value, message in (
            (None, "必须为有限数"),
            ("1.0", "必须为有限数"),
            (True, "必须为有限数"),
            (nan, "必须为有限数"),
            (inf, "必须为有限数"),
            (-1.0, "必须为非负数"),
        ):
            with self.subTest(value=value):
                with patch.object(service, "_open_source") as open_source:
                    with self.assertRaisesRegex(ConfigError, message):
                        service.set_amplitude_vpp(channel=2, value_vpp=value)  # type: ignore[arg-type]
                open_source.assert_not_called()

    def test_source_output_off_does_not_check_amplitude(self):
        fake = FakeSource(source_status(5.0))
        service = SourceService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_source", return_value=fake):
            service.set_output(channel=2, enabled=False)

        self.assertEqual(fake.output_calls, [(2, False, True)])

    def test_source_output_off_allows_missing_amplitude(self):
        fake = FakeSource(replace(source_status(1.0), amplitude=None))
        service = SourceService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_source", return_value=fake):
            service.set_output(channel=2, enabled=False)

        self.assertEqual(fake.output_calls, [(2, False, True)])

    def test_power_output_on_rejects_current_setpoints_over_limit(self):
        fake = FakePower(power_status(12.0, 0.1))
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            with self.assertRaisesRegex(ConfigError, "安全上限已超出"):
                service.set_output(channel=1, enabled=True)

        self.assertEqual(fake.output_calls, [])
        self.assertTrue(fake.closed)

    def test_power_output_off_does_not_check_setpoints(self):
        fake = FakePower(power_status(12.0, 0.5))
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            service.set_output(channel=1, enabled=False)

        self.assertEqual(fake.output_calls, [(1, False, True, 0)])

    def test_power_output_on_rejects_protection_threshold_below_setpoint(self):
        fake = FakePower(
            power_status(3.3, 0.1),
            protection=protection_status(3.2, 0.2),
        )
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            with self.assertRaisesRegex(ConfigError, "保护阈值不安全"):
                service.set_output(channel=1, enabled=True)

        self.assertEqual(fake.output_calls, [])

    def test_power_output_on_rejects_existing_trip(self):
        protection = protection_status(4.0, 0.2)
        protection = PowerProtectionStatus(
            channel=protection.channel,
            ovp_enabled=protection.ovp_enabled,
            ovp_threshold_v=protection.ovp_threshold_v,
            ovp_tripped="YES",
            ocp_enabled=protection.ocp_enabled,
            ocp_threshold_a=protection.ocp_threshold_a,
            ocp_tripped=protection.ocp_tripped,
        )
        fake = FakePower(power_status(3.3, 0.1), protection=protection)
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            with self.assertRaisesRegex(ConfigError, "保护已触发"):
                service.set_output(channel=1, enabled=True)

        self.assertEqual(fake.output_calls, [])

    def test_power_protection_rejects_ovp_below_current_set_voltage_before_write(self):
        fake = FakePower(power_status(5.0, 0.1))
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            with self.assertRaisesRegex(ConfigError, "保护阈值不安全"):
                service.set_protection(channel=1, ovp_threshold_v=4.9)

        self.assertEqual(fake.protection_calls, [])
        self.assertTrue(fake.closed)

    def test_power_protection_rejects_ocp_below_current_limit_before_write(self):
        fake = FakePower(power_status(3.3, 0.2))
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            with self.assertRaisesRegex(ConfigError, "保护阈值不安全"):
                service.set_protection(channel=1, ocp_threshold_a=0.1)

        self.assertEqual(fake.protection_calls, [])

    def test_power_protection_rejects_threshold_over_safety_limit_before_open(self):
        fake = FakePower(power_status(3.3, 0.1))
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake) as open_power:
            with self.assertRaisesRegex(ConfigError, "安全上限已超出"):
                service.set_protection(channel=1, ovp_threshold_v=12.0)

        open_power.assert_not_called()
        self.assertEqual(fake.protection_calls, [])

    def test_power_setpoints_reject_nonfinite_values_before_open(self):
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power") as open_power:
            with self.assertRaisesRegex(ConfigError, "电源电压必须是有限数"):
                service.set_voltage_current_limit(1, float("nan"), 0.1)
            with self.assertRaisesRegex(ConfigError, "电源限流必须是有限数"):
                service.set_voltage_current_limit(1, 3.3, float("inf"))

        open_power.assert_not_called()

    def test_power_protection_rejects_nonfinite_thresholds_before_open(self):
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power") as open_power:
            with self.assertRaisesRegex(ConfigError, "电源电压必须是有限数"):
                service.set_protection(1, ovp_threshold_v=float("nan"))
            with self.assertRaisesRegex(ConfigError, "电源限流必须是有限数"):
                service.set_protection(1, ocp_threshold_a=float("inf"))

        open_power.assert_not_called()

    def test_power_protection_allows_safe_write(self):
        fake = FakePower(power_status(3.3, 0.1))
        service = PowerService(config=make_config(), logger=CommandLogger())

        with patch.object(service, "_open_power", return_value=fake):
            service.set_protection(channel=1, ovp_threshold_v=4.0, ovp_enabled=True, ocp_threshold_a=0.2)

        self.assertEqual(fake.protection_calls, [(1, 4.0, True, 0.2, None, True)])


if __name__ == "__main__":
    unittest.main()
