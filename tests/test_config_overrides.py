import unittest
import tempfile
from math import inf, nan
from pathlib import Path

from wavebench.config import AutoscaleConfig, ConnectionConfig, DmmConfig, OutputConfig, SafetyLimitsConfig, ScopeConfig, WaveBenchConfig, WaveformConfig, load_config
from wavebench.errors import ConfigError
from wavebench.services.source_safety import require_source_v2_energy_safety_limits


class ConfigOverrideTests(unittest.TestCase):
    def test_loads_explicit_rsinstrument_scope_backends(self):
        for backend in (
            "rsinstrument-socket",
            "rsinstrument",
            "rsinstrument-rsvisa",
            "rsinstrument-pyvisa-py",
        ):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "wavebench.toml"
                path.write_text(
                    f'''\
[connection]
backend = "{backend}"
resource = "TCPIP::192.0.2.40::INSTR"
[scope]
''',
                    encoding="utf-8",
                )

                config = load_config(path)

                self.assertEqual(config.connection.backend, backend)

    def test_rejects_unknown_scope_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text(
                '''
[connection]
backend = "socket"
resource = "TCPIP::192.0.2.40::INSTR"
[scope]
''',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "connection.backend must be one of"):
                load_config(path)

    def test_output_overrides_disable_csv_only(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_output_overrides(save_csv=False)
        self.assertFalse(updated.output.save_csv)
        self.assertTrue(updated.output.save_npy)

    def test_waveform_overrides_points_only(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_waveform_overrides(points="def")
        self.assertEqual(updated.waveform.points, "DEF")
        self.assertEqual(updated.waveform.format, "real")

    def test_waveform_overrides_reject_invalid_points(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        with self.assertRaises(Exception):
            config.with_waveform_overrides(points="10000")

    def test_waveform_overrides_time_range(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_waveform_overrides(time_range_s=0.01)
        self.assertEqual(updated.waveform.points, "dmax")
        self.assertEqual(updated.waveform.time_range_s, 0.01)

    def test_waveform_overrides_expected_frequency(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_waveform_overrides(expected_frequency_hz=500.0, frequency_tolerance_ratio=0.1)
        self.assertEqual(updated.waveform.expected_frequency_hz, 500.0)
        self.assertEqual(updated.waveform.frequency_tolerance_ratio, 0.1)

    def test_safety_limits_defaults_and_are_preserved_by_overrides(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
            safety_limits=SafetyLimitsConfig(
                max_source_vpp=2.5,
                min_source_port_voltage_v=-2.0,
                max_source_port_voltage_v=3.0,
            ),
        )
        self.assertEqual(config.safety_limits.max_source_vpp, 2.5)
        updated = config.with_waveform_overrides(points="def")
        self.assertEqual(updated.safety_limits.max_source_vpp, 2.5)
        self.assertEqual(updated.safety_limits.min_source_port_voltage_v, -2.0)
        self.assertEqual(updated.safety_limits.max_source_port_voltage_v, 3.0)

    def test_quality_config_defaults_and_is_preserved_by_overrides(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        self.assertEqual(config.quality.auto_recover_attempts, 2)
        updated = config.with_waveform_overrides(points="def")
        self.assertEqual(updated.quality.auto_recover_attempts, 2)

    def test_waveform_overrides_target_cycles(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_waveform_overrides(window_frequency_hz=1000.0, target_cycles=10.0)
        self.assertEqual(updated.waveform.window_frequency_hz, 1000.0)
        self.assertEqual(updated.waveform.target_cycles, 10.0)

    def test_waveform_overrides_min_signal_vpp(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        self.assertEqual(config.waveform.min_signal_vpp, 0.02)
        updated = config.with_waveform_overrides(min_signal_vpp=0.005)
        self.assertEqual(updated.waveform.min_signal_vpp, 0.005)

    def test_waveform_overrides_vertical_scale_and_target_vpp(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_waveform_overrides(vertical_scale_v_per_div=0.2, target_vpp=1.0)
        self.assertEqual(updated.waveform.vertical_scale_v_per_div, 0.2)
        self.assertEqual(updated.waveform.target_vpp, 1.0)


if __name__ == "__main__":
    unittest.main()


class SafetyLimitsConfigTests(unittest.TestCase):
    def test_loads_safety_limits_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
resource = "TCPIP::127.0.0.1::INSTR"

[scope]

[safety_limits]
max_source_vpp = 2.5
max_power_voltage_v = 5.0
max_power_current_limit_a = 0.2
min_source_port_voltage_v = -3.0
max_source_port_voltage_v = 4.0
""", encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.safety_limits.max_source_vpp, 2.5)
            self.assertEqual(config.safety_limits.max_power_voltage_v, 5.0)
            self.assertEqual(config.safety_limits.max_power_current_limit_a, 0.2)
            self.assertEqual(config.safety_limits.min_source_port_voltage_v, -3.0)
            self.assertEqual(config.safety_limits.max_source_port_voltage_v, 4.0)

    def test_old_safety_limits_positional_layout_is_compatible(self):
        limits = SafetyLimitsConfig(2.5, 5.0, 0.2)

        self.assertEqual(limits.max_source_vpp, 2.5)
        self.assertEqual(limits.max_power_voltage_v, 5.0)
        self.assertEqual(limits.max_power_current_limit_a, 0.2)
        self.assertIsNone(limits.min_source_port_voltage_v)
        self.assertIsNone(limits.max_source_port_voltage_v)

    def test_source_port_voltage_limits_must_be_configured_together(self):
        for body in (
            "min_source_port_voltage_v = -2.0",
            "max_source_port_voltage_v = 2.0",
        ):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "wavebench.toml"
                path.write_text(
                    "[connection]\nresource = \"TCPIP::127.0.0.1::INSTR\"\n"
                    "[scope]\n[safety_limits]\n"
                    f"{body}\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ConfigError, "configured together"):
                    load_config(path)

    def test_source_port_voltage_limits_require_finite_signed_interval(self):
        for minimum, maximum, message in (
            (2.0, -2.0, "must be <"),
            (2.0, 2.0, "must be <"),
            (True, 2.0, "finite number"),
            (-2.0, True, "finite number"),
            (nan, 2.0, "finite number"),
            (-2.0, inf, "finite number"),
        ):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaisesRegex(ConfigError, message):
                    require_source_v2_energy_safety_limits(
                        SafetyLimitsConfig(
                            max_source_vpp=2.0,
                            min_source_port_voltage_v=minimum,
                            max_source_port_voltage_v=maximum,
                        )
                    )

    def test_source_v2_energy_limits_fail_closed_when_an_axis_is_missing(self):
        cases = (
            (
                SafetyLimitsConfig(),
                ["max_source_port_voltage_v", "max_source_vpp", "min_source_port_voltage_v"],
            ),
            (
                SafetyLimitsConfig(max_source_vpp=2.0),
                ["max_source_port_voltage_v", "min_source_port_voltage_v"],
            ),
            (
                SafetyLimitsConfig(max_source_vpp=2.0, min_source_port_voltage_v=-2.0),
                ["max_source_port_voltage_v"],
            ),
        )
        for limits, expected_missing in cases:
            with self.subTest(limits=limits):
                with self.assertRaisesRegex(ConfigError, "explicit safety limits") as raised:
                    require_source_v2_energy_safety_limits(limits)
                self.assertEqual(raised.exception.missing_fields, tuple(expected_missing))
                self.assertEqual(
                    raised.exception.to_envelope().as_dict()["code"],
                    "source_safety_limits_required",
                )

    def test_source_v2_energy_limits_keep_vpp_and_absolute_limits_independent(self):
        result = require_source_v2_energy_safety_limits(
            SafetyLimitsConfig(
                max_source_vpp=1.0,
                min_source_port_voltage_v=-3.0,
                max_source_port_voltage_v=4.0,
            )
        )
        self.assertEqual(result.max_source_vpp, 1.0)
        self.assertEqual(result.min_source_port_voltage_v, -3.0)
        self.assertEqual(result.max_source_port_voltage_v, 4.0)

    def test_load_config_rejects_invalid_source_port_voltage_values(self):
        for minimum, maximum, message in (
            ("true", "2.0", "finite number"),
            ("-2.0", "true", "finite number"),
            ("nan", "2.0", "finite number"),
            ("-2.0", "inf", "finite number"),
            ("2.0", "-2.0", "must be <"),
        ):
            with self.subTest(minimum=minimum, maximum=maximum), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "wavebench.toml"
                path.write_text(
                    "[connection]\nresource = \"TCPIP::127.0.0.1::INSTR\"\n"
                    "[scope]\n[safety_limits]\n"
                    f"min_source_port_voltage_v = {minimum}\n"
                    f"max_source_port_voltage_v = {maximum}\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_all_config_overrides_preserve_source_port_voltage_limits(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
            safety_limits=SafetyLimitsConfig(
                max_source_vpp=2.0,
                min_source_port_voltage_v=-2.5,
                max_source_port_voltage_v=3.5,
            ),
        )
        overrides = (
            config.with_connection_timeout_ms(200),
            config.with_resource("TCPIP::127.0.0.2::INSTR"),
            config.with_output_overrides(save_csv=False),
            config.with_waveform_overrides(points="def"),
            config.with_source_resource("TCPIP::127.0.0.3::INSTR"),
            config.with_power_resource("TCPIP::127.0.0.4::INSTR"),
            config.with_dmm_resource("TCPIP::127.0.0.5::INSTR"),
        )
        for updated in overrides:
            with self.subTest(updated=updated):
                self.assertEqual(updated.safety_limits, config.safety_limits)

    def test_rejects_non_positive_safety_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
resource = "TCPIP::127.0.0.1::INSTR"

[scope]

[safety_limits]
max_source_vpp = 0
""", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "safety_limits.max_source_vpp"):
                load_config(path)


class QualityConfigTests(unittest.TestCase):
    def test_loads_quality_recovery_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
backend = "lan"
resource = "TCPIP::127.0.0.1::INSTR"
timeout_ms = 1000
opc_timeout_ms = 1000

[scope]
driver = "rtm2032"
default_channel = 1
reset_before_run = false
check_errors = true

[autoscale]
wait_opc = true
check_errors = true

[waveform]
format = "real"
byte_order = "lsbf"
points = "def"

[output]
directory = "data/raw"
package_naming = "timestamp_label"
save_csv = true
save_npy = true
save_json = true
save_commands_log = true
save_screenshot = false

[quality]
auto_recover_attempts = 4
consistency_required_captures = 3
frequency_consistency_ratio = 0.01
voltage_vpp_consistency_ratio = 0.03
voltage_mean_consistency_v = 0.02
duty_consistency = 0.01
""", encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.quality.auto_recover_attempts, 4)
            self.assertEqual(config.quality.consistency_required_captures, 3)
            self.assertEqual(config.quality.frequency_consistency_ratio, 0.01)
            self.assertEqual(config.quality.voltage_vpp_consistency_ratio, 0.03)
            self.assertEqual(config.quality.voltage_mean_consistency_v, 0.02)
            self.assertEqual(config.quality.duty_consistency, 0.01)

    def test_rejects_invalid_quality_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
resource = "TCPIP::127.0.0.1::INSTR"

[scope]

[quality]
auto_recover_attempts = -1
""", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "auto_recover_attempts"):
                load_config(path)


class RuntimeRobustnessConfigTests(unittest.TestCase):
    def test_loads_connection_retry_and_tui_log_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
backend = "lan"
resource = "TCPIP::127.0.0.1::INSTR"
timeout_ms = 1000
opc_timeout_ms = 1000
read_retry_attempts = 2
read_retry_delay_ms = 250

[scope]
driver = "rtm2032"
default_channel = 1
reset_before_run = false
check_errors = true

[autoscale]
wait_opc = true
check_errors = true

[waveform]
format = "real"
byte_order = "lsbf"
points = "def"

[output]
directory = "data/raw"
package_naming = "timestamp_label"
save_csv = true
save_npy = true
save_json = true
save_commands_log = true
save_screenshot = false

[tui]
log_max_lines = 1234
log_keep_lines_after_trim = 123
""", encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.connection.read_retry_attempts, 2)
            self.assertEqual(config.connection.read_retry_delay_ms, 250)
            self.assertEqual(config.tui.log_max_lines, 1234)
            self.assertEqual(config.tui.log_keep_lines_after_trim, 123)

    def test_rejects_invalid_tui_log_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
resource = "TCPIP::127.0.0.1::INSTR"

[scope]

[tui]
log_max_lines = 100
log_keep_lines_after_trim = 101
""", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "log_keep_lines_after_trim"):
                load_config(path)


class SourceConfigTests(unittest.TestCase):
    def test_loads_source_settle_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
backend = "lan"
resource = "TCPIP::127.0.0.1::INSTR"
timeout_ms = 1000
opc_timeout_ms = 1000

[scope]
driver = "rtm2032"
default_channel = 1
reset_before_run = false
check_errors = true

[autoscale]
wait_opc = true
check_errors = true

[waveform]
format = "real"
byte_order = "lsbf"
points = "def"

[output]
directory = "data/raw"
package_naming = "timestamp_label"
save_csv = true
save_npy = true
save_json = true
save_commands_log = true
save_screenshot = false

[source]
driver = "dg4202"
resource = "TCPIP::198.51.100.3::INSTR"
default_channel = 2
settle_ms_after_set_frequency = 500
""", encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.source)
            self.assertEqual(config.source.default_channel, 2)
            self.assertEqual(config.source.settle_ms_after_set_frequency, 500)


class PowerConfigTests(unittest.TestCase):
    def test_loads_power_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text("""
[connection]
backend = "lan"
resource = "TCPIP::127.0.0.1::INSTR"
timeout_ms = 1000
opc_timeout_ms = 1000

[scope]
driver = "rtm2032"
default_channel = 1
reset_before_run = false
check_errors = true

[autoscale]
wait_opc = true
check_errors = true

[waveform]
format = "real"
byte_order = "lsbf"
points = "def"

[output]
directory = "data/raw"
package_naming = "timestamp_label"
save_csv = true
save_npy = true
save_json = true
save_commands_log = true
save_screenshot = false

[power]
driver = "dp800"
resource = "TCPIP::198.51.100.4::INSTR"
default_channel = 1
check_errors = true
settle_ms_after_set = 2000
settle_ms_after_output = 1000
""", encoding="utf-8")
            config = load_config(path)
            self.assertIsNotNone(config.power)
            self.assertEqual(config.power.resource, "TCPIP::198.51.100.4::INSTR")
            self.assertEqual(config.power.default_channel, 1)
            self.assertEqual(config.power.settle_ms_after_set, 2000)
            self.assertEqual(config.power.settle_ms_after_output, 1000)
            updated = config.with_power_resource("TCPIP::192.0.2.50::INSTR")
            self.assertEqual(updated.power.resource, "TCPIP::192.0.2.50::INSTR")

class DmmConfigTests(unittest.TestCase):
    def test_loads_dm3058_serial_line_endings_and_flow_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text('''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
[dmm]
driver = "dm3058"
backend = "serial"
resource = "/dev/serial/by-id/usb-test"
baudrate = 9600
bytesize = 8
parity = "N"
stopbits = 1
write_termination = "crlf"
read_termination = "lf"
xonxoff = false
rtscts = false
dsrdtr = false
''', encoding="utf-8")

            config = load_config(path)

            self.assertEqual(config.dmm.write_termination, "crlf")
            self.assertEqual(config.dmm.read_termination, "lf")
            self.assertFalse(config.dmm.xonxoff)
            self.assertFalse(config.dmm.rtscts)
            self.assertFalse(config.dmm.dsrdtr)

    def test_rejects_unknown_dmm_serial_termination(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text('''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
[dmm]
driver = "dm3058"
backend = "serial"
resource = "/dev/ttyUSB0"
write_termination = "nul"
''', encoding="utf-8")

            with self.assertRaisesRegex(Exception, "dmm.write_termination"):
                load_config(path)

    def test_rejects_non_boolean_dmm_flow_control(self):
        for field in ("xonxoff", "rtscts", "dsrdtr"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "wavebench.toml"
                path.write_text(f'''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
[dmm]
driver = "dm3058"
backend = "serial"
resource = "/dev/serial/by-id/usb-test"
{field} = "false"
''', encoding="utf-8")

                with self.assertRaisesRegex(Exception, f"dmm.{field} must be a boolean"):
                    load_config(path)

    def test_loads_dm3058_lan_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text('''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
[dmm]
driver = "dm3058"
backend = "lan"
resource = "TCPIP::198.51.100.5::INSTR"
timeout_ms = 3000
settle_ms_before_read = 250
settle_ms_after_function_change = 750
''', encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.dmm.driver, "dm3058")
            self.assertEqual(config.dmm.backend, "lan")
            self.assertEqual(config.dmm.resource, "TCPIP::198.51.100.5::INSTR")
            self.assertEqual(config.dmm.settle_ms_before_read, 250)
            self.assertEqual(config.dmm.settle_ms_after_function_change, 750)

    def test_rejects_negative_dmm_read_settle_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text('''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
[dmm]
driver = "dm3058"
backend = "lan"
resource = "TCPIP::198.51.100.5::INSTR"
settle_ms_before_read = -1
''', encoding="utf-8")
            with self.assertRaisesRegex(Exception, "dmm.settle_ms_before_read"):
                load_config(path)

    def test_rejects_negative_dmm_function_change_settle_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text('''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
[dmm]
driver = "dm3058"
backend = "lan"
resource = "TCPIP::198.51.100.5::INSTR"
settle_ms_after_function_change = -1
''', encoding="utf-8")
            with self.assertRaisesRegex(Exception, "dmm.settle_ms_after_function_change"):
                load_config(path)

    def test_dmm_resource_override_infers_lan_for_tcpip(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
        )
        updated = config.with_dmm_resource("TCPIP::198.51.100.5::INSTR")
        self.assertEqual(updated.dmm.driver, "dm3058")
        self.assertEqual(updated.dmm.backend, "lan")

    def test_dmm_resource_override_tcpip_switches_existing_serial_config_to_lan(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
            dmm=DmmConfig("dm3000", "/dev/ttyUSB0", "serial", 9600, 8, "N", 1, 1000),
        )
        updated = config.with_dmm_resource("TCPIP::198.51.100.5::INSTR")
        self.assertEqual(updated.dmm.driver, "dm3058")
        self.assertEqual(updated.dmm.backend, "lan")

    def test_dmm_resource_override_preserves_settle_delays(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
            dmm=DmmConfig("dm3058", "TCPIP::old::INSTR", "lan", 9600, 8, "N", 1, 1000, 500, 750),
        )
        updated = config.with_dmm_resource("TCPIP::198.51.100.5::INSTR")
        self.assertEqual(updated.dmm.settle_ms_before_read, 500)
        self.assertEqual(updated.dmm.settle_ms_after_function_change, 750)

    def test_dmm_resource_override_preserves_serial_framing(self):
        config = WaveBenchConfig(
            connection=ConnectionConfig("lan", "TCPIP::127.0.0.1::INSTR", 100, 100),
            scope=ScopeConfig("rtm2032", None, 1, False, True),
            autoscale=AutoscaleConfig(True, True),
            waveform=WaveformConfig("real", "lsbf", "dmax"),
            output=OutputConfig(Path("data/raw"), "timestamp_label", True, True, True, True, False),
            source_path=Path("test.toml"),
            dmm=DmmConfig(
                "dm3058",
                "/dev/serial/by-id/old",
                "serial",
                9600,
                8,
                "N",
                1,
                3000,
                write_termination="crlf",
                read_termination="lf",
                rtscts=False,
            ),
        )

        updated = config.with_dmm_resource("/dev/serial/by-id/new")

        self.assertEqual(updated.dmm.backend, "serial")
        self.assertEqual(updated.dmm.write_termination, "crlf")
        self.assertEqual(updated.dmm.read_termination, "lf")
        self.assertFalse(updated.dmm.rtscts)


class InstrumentPluginConfigTests(unittest.TestCase):
    def test_accepts_canonical_driver_id_and_plugin_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text(
                '''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
driver = "rohde-schwarz.rtm2032"
[scope.options]
example_flag = true
''',
                encoding="utf-8",
            )

            config = load_config(path)

            self.assertEqual(config.scope.driver, "rohde-schwarz.rtm2032")
            self.assertEqual(config.scope.options, {"example_flag": True})

    def test_rejects_missing_executable_plugin_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text(
                '''
[connection]
resource = "TCPIP::scope::INSTR"
[scope]
driver = "missing.scope"
''',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(Exception, "scope.driver.*is not installed"):
                load_config(path)
