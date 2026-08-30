import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np

from wavebench.cli import build_parser, main
from wavebench.plugins.api import InstrumentPlugin
from wavebench.plugins.lifecycle import InstalledPlugin, LifecycleResult


class FakePluginEntryPoint:
    def __init__(self, name, loaded, *, group="wavebench.drivers"):
        self.name = name
        self.group = group
        self._loaded = loaded
        self.dist = None

    def load(self):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


class FakePluginEntryPoints(list):
    def select(self, *, group):
        return [entry_point for entry_point in self if entry_point.group == group]


def make_cli_plugin(driver_id="example.scope"):
    return InstrumentPlugin(
        driver_id=driver_id,
        kind="scope",
        display_name="Example Scope",
        manufacturer="Example",
        models=("EX1",),
        capabilities=("scope.idn",),
        summary="Example plugin.",
    )


class CliTests(unittest.TestCase):
    def test_run_calibrate_builds_offline_artifacts_without_loading_instruments(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            response_rows = ["index,requested_frequency_hz,requested_vpp,gain_db,status"]
            index = 0
            for amplitude in (0.05, 0.1):
                for frequency in (100, 1000, 10000, 100000):
                    response_rows.append(f"{index},{frequency},{amplitude},{6 - index * 0.05},ok")
                    index += 1
            (run_dir / "frequency_response.csv").write_text("\n".join(response_rows), encoding="utf-8")
            calibration = Path(tmp) / "calibration.toml"
            calibration.write_text("[calibration]\ntarget_mode = 'unity_gain'\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch("wavebench.cli._load_run_service") as load_service, redirect_stdout(stdout):
                code = main(["run", "calibrate", str(run_dir), "--config", str(calibration)])

            self.assertEqual(code, 0)
            load_service.assert_not_called()
            self.assertTrue((run_dir / "frequency_response_calibration.csv").exists())
            self.assertTrue((run_dir / "frequency_response_calibration.json").exists())
            self.assertIn("calibration_csv=", stdout.getvalue())

    def test_run_calibrate_requires_response_selector_for_manifest_with_multiple_responses(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            entries = []
            for label in ("a", "b"):
                directory = run_dir / "frequency_response" / label
                directory.mkdir(parents=True)
                rows = ["index,requested_frequency_hz,requested_vpp,gain_db,status"]
                index = 0
                for amplitude in (0.05, 0.1):
                    for frequency in (100, 1000, 10000, 100000):
                        rows.append(f"{index},{frequency},{amplitude},1,ok")
                        index += 1
                (directory / "frequency_response.csv").write_text("\n".join(rows), encoding="utf-8")
                entries.append({"step_index": len(entries), "label": label, "directory": f"frequency_response/{label}"})
            (run_dir / "frequency_responses.json").write_text(
                json.dumps({"schema_version": 1, "responses": entries}), encoding="utf-8"
            )
            calibration = Path(tmp) / "calibration.toml"
            calibration.write_text("[calibration]\ntarget_mode = 'unity_gain'\n", encoding="utf-8")

            self.assertEqual(main(["run", "calibrate", str(run_dir), "--config", str(calibration)]), 2)
            self.assertEqual(
                main(["run", "calibrate", str(run_dir), "--config", str(calibration), "--response", "b"]),
                0,
            )
            self.assertTrue((run_dir / "frequency_response" / "b" / "frequency_response_calibration.json").exists())

    def test_capture_accepts_points_and_output_flags(self):
        args = build_parser().parse_args([
            "scope", "capture", "--points", "def", "--time-range", "0.01", "--window-frequency", "500", "--target-cycles", "10", "--expect-frequency", "500", "--frequency-tolerance", "0.1", "--no-csv", "--label", "x"
        ])
        self.assertEqual(args.command, "capture")
        self.assertEqual(args.points, "def")
        self.assertEqual(args.time_range, 0.01)
        self.assertEqual(args.window_frequency, 500.0)
        self.assertEqual(args.expect_frequency, 500.0)
        self.assertEqual(args.target_cycles, 10.0)
        self.assertEqual(args.frequency_tolerance, 0.1)
        self.assertTrue(args.no_csv)

    def test_capture_accepts_target_vpp_and_vertical_scale(self):
        args = build_parser().parse_args([
            "scope", "capture", "--target-vpp", "1.0", "--vertical-scale", "0.25"
        ])
        self.assertEqual(args.target_vpp, 1.0)
        self.assertEqual(args.vertical_scale, 0.25)

    def test_capture_accepts_screenshot_flag(self):
        args = build_parser().parse_args(["scope", "capture", "--screenshot"])
        self.assertEqual(args.command, "capture")
        self.assertTrue(args.screenshot)

    def test_capture_accepts_repeated_channels(self):
        args = build_parser().parse_args(["scope", "capture", "--channel", "1", "--channel", "2"])
        self.assertEqual(args.command, "capture")
        self.assertEqual(args.channel, [1, 2])

    def test_power_status_accepts_channel(self):
        args = build_parser().parse_args(["power", "status", "--channel", "1"])
        self.assertEqual(args.domain, "power")
        self.assertEqual(args.command, "status")
        self.assertEqual(args.channel, 1)

    def test_dmm_read_accepts_function(self):
        args = build_parser().parse_args(["dmm", "read", "dcv"])
        self.assertEqual(args.domain, "dmm")
        self.assertEqual(args.command, "read")
        self.assertEqual(args.function, "dcv")

    def test_dmm_profile_accepts_subcommand(self):
        args = build_parser().parse_args(["dmm", "profile"])
        self.assertEqual(args.domain, "dmm")
        self.assertEqual(args.command, "profile")

    def test_dmm_profile_prints_stable_fields(self):
        class StubDmmService:
            def measurement_profile(self):
                from wavebench.instruments import DmmMeasurementProfile

                return DmmMeasurementProfile("dcv", 0, None, "10M")

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "profile"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            ["function=dcv", "range_code=0", "auto_range=n/a", "impedance=10M"],
        )

    def test_dmm_system_interface_status_prints_redacted_stable_fields(self):
        class StubDmmService:
            def system_interface_status(self):
                from wavebench.instruments import DmmSystemInterfaceStatus

                return DmmSystemInterfaceStatus(
                    True,
                    "ENGLISH",
                    "DOT",
                    "NONE",
                    128,
                    False,
                    True,
                    True,
                    22,
                    9600,
                    "NONE8BITS",
                )

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "system-interface", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            [
                "beeper_enabled=true",
                "language=english",
                "decimal_format=dot",
                "separator_format=none",
                "display_brightness=128",
                "scan_board_installed=false",
                "lan_interface_installed=true",
                "dhcp_enabled=true",
                "gpib_address=22",
                "rs232_baud=9600",
                "rs232_parity=none8bits",
            ],
        )
        output = stdout.getvalue().lower()
        for sensitive in ("idn", "mac", "ip=", "hostname", "domain"):
            self.assertNotIn(sensitive, output)

    def test_dmm_trigger_status_prints_stable_fields(self):
        class StubDmmService:
            def trigger_status(self):
                from wavebench.instruments import DmmTriggerStatus

                return DmmTriggerStatus("AUTO", 0.4, False, 1, 1, "RISE", "POS", 0.007)

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "trigger", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            [
                "source=AUTO",
                "auto_interval_s=0.4",
                "auto_hold=false",
                "auto_hold_sensitivity=1",
                "single_count=1",
                "external_slope=RISE",
                "vmc_polarity=POS",
                "vmc_pulse_width_s=0.007",
            ],
        )

    def test_dmm_calculation_status_prints_stable_fields(self):
        class StubDmmService:
            def calculation_status(self):
                from wavebench.instruments import DmmCalculationStatus

                return DmmCalculationStatus("none", 0, 0.0, 600.0)

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "calculation", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            [
                "function=none",
                "statistic_count=0",
                "db_reference=0",
                "dbm_reference_ohm=600",
            ],
        )

    def test_dmm_calculation_statistics_forwards_confirmation(self):
        class StubDmmService:
            def calculation_statistics(self, function, *, calculation_active_confirmed):
                from wavebench.instruments import DmmCalculationStatistics

                self.call = (function, calculation_active_confirmed)
                return DmmCalculationStatistics(function, 1.25, 3)

        service = StubDmmService()
        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=service):
            with redirect_stdout(stdout):
                code = main(
                    [
                        "dmm",
                        "calculation",
                        "statistics",
                        "average",
                        "--calculation-active-confirmed",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(service.call, ("average", True))
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            ["function=average", "value=1.25", "count=3"],
        )

    def test_dmm_calculation_statistics_requires_explicit_confirmation(self):
        args = build_parser().parse_args(["dmm", "calculation", "statistics", "average"])

        self.assertFalse(args.calculation_active_confirmed)

    def test_dmm_voltage_range_set_prints_stable_fields(self):
        class StubDmmService:
            def set_voltage_range(self, function, range_code):
                from wavebench.instruments import DmmVoltageRangeConfiguration

                return DmmVoltageRangeConfiguration(function, 2, range_code)

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "range", "set", "dcv", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            ["function=dcv", "previous_range_code=2", "range_code=1", "changed=true"],
        )

    def test_dmm_impedance_set_prints_stable_fields(self):
        class StubDmmService:
            def set_dcv_impedance(self, impedance):
                from wavebench.instruments import DmmDcvImpedanceConfiguration

                return DmmDcvImpedanceConfiguration("10M", impedance, 2)

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "impedance", "set", "10g"])
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue().strip().splitlines(),
            ["previous_impedance=10M", "impedance=10G", "range_code=2", "changed=true"],
        )

    def test_dmm_function_status_accepts_subcommand(self):
        args = build_parser().parse_args(["dmm", "function", "status"])
        self.assertEqual(args.domain, "dmm")
        self.assertEqual(args.command, "function")
        self.assertEqual(args.dmm_function_command, "status")

    def test_dmm_function_set_accepts_function(self):
        args = build_parser().parse_args(["dmm", "function", "set", "acv"])
        self.assertEqual(args.domain, "dmm")
        self.assertEqual(args.command, "function")
        self.assertEqual(args.dmm_function_command, "set")
        self.assertEqual(args.function, "acv")

    def test_dmm_function_status_prints_bilingual_output(self):
        class StubDmmService:
            def function_status(self):
                return "res"

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "function", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "功能 / Function: res")

    def test_dmm_function_set_prints_bilingual_output(self):
        class StubDmmService:
            def set_function(self, function):
                return function

        stdout = io.StringIO()
        with patch("wavebench.cli._load_dmm_service", return_value=StubDmmService()):
            with redirect_stdout(stdout):
                code = main(["dmm", "function", "set", "freq"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "功能已切换 / Function set: freq")

    def test_power_set_accepts_voltage_and_current_limit(self):
        args = build_parser().parse_args([
            "power", "set", "--channel", "1", "--voltage", "3.3", "--current-limit", "0.2"
        ])
        self.assertEqual(args.domain, "power")
        self.assertEqual(args.command, "set")
        self.assertEqual(args.channel, 1)
        self.assertEqual(args.voltage, 3.3)
        self.assertEqual(args.current_limit, 0.2)

    def test_doctor_accepts_config_and_timeout(self):
        args = build_parser().parse_args([
            "doctor",
            "--config",
            "bench.toml",
            "--timeout-ms",
            "500",
            "--discover-subnet",
            "192.0.2.0/24",
            "--discover-ports",
            "5025,111",
            "--discover-timeout-ms",
            "300",
            "--discover-workers",
            "8",
            "--discover-max-hosts",
            "32",
            "--no-visa",
        ])
        self.assertEqual(args.domain, "doctor")
        self.assertEqual(args.config, "bench.toml")
        self.assertEqual(args.timeout_ms, 500)
        self.assertEqual(args.discover_subnet, "192.0.2.0/24")
        self.assertEqual(args.discover_ports, "5025,111")
        self.assertEqual(args.discover_timeout_ms, 300)
        self.assertEqual(args.discover_workers, 8)
        self.assertEqual(args.discover_max_hosts, 32)
        self.assertTrue(args.no_visa)

    def test_doctor_prints_records_and_returns_error_status(self):
        class StubRecord:
            severity = "error"
            target = "scope"
            driver = "rtm2032"
            resource = "TCPIP::192.0.2.115::INSTR"
            idn = None
            message = "no *IDN? response / 没有 *IDN? 响应"
            suggestion = "check cable / 检查网线"

        stdout = io.StringIO()
        with patch("wavebench.cli.load_config", return_value=object()):
            with patch("wavebench.cli.doctor_records", return_value=[StubRecord()]):
                with redirect_stdout(stdout):
                    code = main(["doctor", "--config", "bench.toml"])
        self.assertEqual(code, 2)
        output = stdout.getvalue()
        self.assertIn("severity\ttarget\tdriver", output)
        self.assertIn("scope\trtm2032", output)

    def test_run_template_accepts_list_output_print_and_force(self):
        args = build_parser().parse_args([
            "run",
            "template",
            "source-scope-sine",
            "--output",
            "plans/new.toml",
            "--print",
            "--force",
            "--frequency",
            "10000",
            "--frequencies",
            "100,1000,10000",
            "--vpp",
            "3.3",
            "--source-channel",
            "2",
            "--scope-channel",
            "1",
            "--power-channel",
            "3",
            "--voltage",
            "5",
            "--current-limit",
            "0.2",
        ])

        self.assertEqual(args.domain, "run")
        self.assertEqual(args.command, "template")
        self.assertEqual(args.template, "source-scope-sine")
        self.assertEqual(args.output, "plans/new.toml")
        self.assertTrue(args.print_template)
        self.assertTrue(args.force)
        self.assertEqual(args.frequency, 10000.0)
        self.assertEqual(args.frequencies, "100,1000,10000")
        self.assertEqual(args.vpp, 3.3)
        self.assertEqual(args.source_channel, 2)
        self.assertEqual(args.scope_channel, 1)
        self.assertEqual(args.power_channel, 3)
        self.assertEqual(args.voltage, 5.0)
        self.assertEqual(args.current_limit, 0.2)

    def test_run_template_list_prints_available_templates(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["run", "template", "--list"])

        self.assertEqual(code, 0)
        self.assertIn("source-scope-sine", stdout.getvalue())

    def test_power_output_accepts_on_off(self):
        args = build_parser().parse_args(["power", "output", "--channel", "1", "off"])
        self.assertEqual(args.domain, "power")
        self.assertEqual(args.command, "output")
        self.assertEqual(args.channel, 1)
        self.assertEqual(args.state, "off")

    def test_power_protection_status_accepts_channel(self):
        args = build_parser().parse_args(["power", "protection", "status", "--channel", "1"])
        self.assertEqual(args.domain, "power")
        self.assertEqual(args.command, "protection")
        self.assertEqual(args.protection_command, "status")
        self.assertEqual(args.channel, 1)

    def test_power_protection_set_accepts_thresholds_and_states(self):
        args = build_parser().parse_args([
            "power", "protection", "set",
            "--channel", "1",
            "--ovp-threshold", "6.0",
            "--ovp", "on",
            "--ocp-threshold", "0.3",
            "--ocp", "off",
        ])
        self.assertEqual(args.domain, "power")
        self.assertEqual(args.command, "protection")
        self.assertEqual(args.protection_command, "set")
        self.assertEqual(args.channel, 1)
        self.assertEqual(args.ovp_threshold, 6.0)
        self.assertEqual(args.ovp, "on")
        self.assertEqual(args.ocp_threshold, 0.3)
        self.assertEqual(args.ocp, "off")

    def test_source_status_accepts_channel(self):
        args = build_parser().parse_args(["source", "status", "--channel", "2"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "status")
        self.assertEqual(args.channel, 2)

    def test_source_profile_accepts_channel(self):
        args = build_parser().parse_args(["source", "profile", "--channel", "1"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "profile")
        self.assertEqual(args.channel, 1)

    def test_source_sweep_profile_accepts_channel(self):
        args = build_parser().parse_args(["source", "sweep-profile", "--channel", "2"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "sweep-profile")
        self.assertEqual(args.channel, 2)

    def test_source_pulse_and_burst_profiles_accept_channel(self):
        pulse = build_parser().parse_args(["source", "pulse-profile", "--channel", "1"])
        burst = build_parser().parse_args(["source", "burst-profile", "--channel", "2"])
        self.assertEqual((pulse.command, pulse.channel), ("pulse-profile", 1))
        self.assertEqual((burst.command, burst.channel), ("burst-profile", 2))

    def test_source_counter_profile_has_no_channel_argument(self):
        args = build_parser().parse_args(["source", "counter-profile"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "counter-profile")
        self.assertFalse(hasattr(args, "channel"))

    def test_source_counter_profile_uses_counter_formatter(self):
        profile = object()

        class StubSourceService:
            def counter_profile(self):
                return profile

        with patch("wavebench.cli._load_source_service", return_value=StubSourceService()), patch(
            "wavebench.cli._print_source_counter_profile"
        ) as print_profile:
            code = main(["source", "counter-profile"])

        self.assertEqual(code, 0)
        print_profile.assert_called_once_with(profile)

    def test_source_sweep_profile_uses_sweep_formatter(self):
        profile = object()

        class StubSourceService:
            def sweep_profile(self, channel):
                assert channel == 2
                return profile

        with patch("wavebench.cli._load_source_service", return_value=StubSourceService()), patch(
            "wavebench.cli._print_source_sweep_profile"
        ) as print_profile:
            code = main(["source", "sweep-profile", "--channel", "2"])

        self.assertEqual(code, 0)
        print_profile.assert_called_once_with(profile)

    def test_source_pulse_profile_uses_pulse_formatter(self):
        profile = object()

        class StubSourceService:
            def pulse_profile(self, channel):
                assert channel == 1
                return profile

        with patch("wavebench.cli._load_source_service", return_value=StubSourceService()), patch(
            "wavebench.cli._print_source_pulse_profile"
        ) as print_profile:
            code = main(["source", "pulse-profile", "--channel", "1"])

        self.assertEqual(code, 0)
        print_profile.assert_called_once_with(profile)

    def test_source_burst_profile_uses_burst_formatter(self):
        profile = object()

        class StubSourceService:
            def burst_profile(self, channel):
                assert channel == 2
                return profile

        with patch("wavebench.cli._load_source_service", return_value=StubSourceService()), patch(
            "wavebench.cli._print_source_burst_profile"
        ) as print_profile:
            code = main(["source", "burst-profile", "--channel", "2"])

        self.assertEqual(code, 0)
        print_profile.assert_called_once_with(profile)

    def test_source_profile_uses_profile_formatter(self):
        profile = object()

        class StubSourceService:
            def channel_profile(self, channel):
                assert channel == 1
                return profile

        with patch("wavebench.cli._load_source_service", return_value=StubSourceService()), patch(
            "wavebench.cli._print_source_channel_profile"
        ) as print_profile:
            code = main(["source", "profile", "--channel", "1"])

        self.assertEqual(code, 0)
        print_profile.assert_called_once_with(profile)

    def test_source_status_still_uses_source_formatter(self):
        class StubStatus:
            channel = 2

        class StubSourceService:
            def status(self, channel):
                assert channel == 2
                return StubStatus()

        with patch("wavebench.cli._load_source_service", return_value=StubSourceService()), patch(
            "wavebench.cli._print_source_status"
        ) as print_status:
            code = main(["source", "status", "--channel", "2"])

        self.assertEqual(code, 0)
        print_status.assert_called_once()
        self.assertEqual(print_status.call_args.args[0].channel, 2)

    def test_source_set_freq_accepts_value(self):
        args = build_parser().parse_args(["source", "set-freq", "--channel", "2", "1000"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "set-freq")
        self.assertEqual(args.channel, 2)
        self.assertEqual(args.value_hz, 1000.0)

    def test_source_v2_commands_accept_explicit_channels(self):
        basic = build_parser().parse_args(
            [
                "source",
                "basic-configure-v2",
                "--channel",
                "2",
                "--waveform",
                "square",
                "--frequency-hz",
                "1000",
                "--amplitude-vpp",
                "1.5",
            ]
        )
        live = build_parser().parse_args(
            [
                "source",
                "basic-live-configure-v2",
                "--channel",
                "2",
                "--frequency-hz",
                "2000",
            ]
        )
        output = build_parser().parse_args(["source", "output-v2", "--channel", "2", "on"])
        harmonics = build_parser().parse_args(
            [
                "source",
                "harmonics-configure-v2",
                "--channel",
                "2",
                "--order",
                "8",
                "--preset",
                "odd",
            ]
        )
        harmonic_disable = build_parser().parse_args(
            ["source", "harmonics-disable-v2", "--channel", "2"]
        )
        modulation = build_parser().parse_args(
            [
                "source",
                "modulation-configure-v2",
                "--channel",
                "2",
                "--depth-percent",
                "80",
                "--internal-frequency-hz",
                "25",
            ]
        )
        pulse = build_parser().parse_args(
            [
                "source",
                "pulse-configure-v2",
                "--channel",
                "2",
                "--width-s",
                "1e-6",
                "--delay-s",
                "0",
                "--leading-transition-s",
                "1e-8",
                "--trailing-transition-s",
                "1e-8",
            ]
        )
        pm = build_parser().parse_args(
            [
                "source",
                "pm-modulation-configure-v2",
                "--channel",
                "2",
                "--phase-deviation-deg",
                "90",
                "--internal-frequency-hz",
                "25",
            ]
        )
        fm = build_parser().parse_args(
            [
                "source",
                "fm-modulation-configure-v2",
                "--channel",
                "2",
                "--frequency-deviation-hz",
                "12500",
                "--internal-frequency-hz",
                "25",
            ]
        )
        pwm = build_parser().parse_args(
            [
                "source",
                "pwm-modulation-configure-v2",
                "--channel",
                "2",
                "--internal-frequency-hz",
                "25",
                "--width-deviation-s",
                "1e-6",
            ]
        )
        sweep = build_parser().parse_args(
            [
                "source",
                "sweep-configure-v2",
                "--channel",
                "2",
                "--start-hz",
                "100",
                "--stop-hz",
                "1000",
                "--spacing",
                "linear",
                "--steps",
                "101",
                "--sweep-time-s",
                "1",
            ]
        )
        burst = build_parser().parse_args(
            [
                "source",
                "burst-configure-v2",
                "--channel",
                "2",
                "--cycles",
                "12",
                "--phase-deg",
                "30",
                "--internal-period-s",
                "0.25",
                "--delay-s",
                "0.5",
            ]
        )

        self.assertEqual(basic.command, "basic-configure-v2")
        self.assertEqual(basic.channel, 2)
        self.assertEqual(basic.waveform, "square")
        self.assertEqual(basic.frequency_hz, 1000.0)
        self.assertEqual(basic.amplitude_vpp, 1.5)
        self.assertEqual(live.command, "basic-live-configure-v2")
        self.assertEqual(live.channel, 2)
        self.assertEqual(live.frequency_hz, 2000.0)
        self.assertIsNone(live.amplitude_vpp)
        self.assertEqual(output.command, "output-v2")
        self.assertEqual(output.channel, 2)
        self.assertEqual(output.state, "on")
        self.assertEqual(harmonics.command, "harmonics-configure-v2")
        self.assertEqual(harmonics.channel, 2)
        self.assertEqual(harmonics.order, 8)
        self.assertEqual(harmonics.preset, "odd")
        self.assertEqual(harmonic_disable.command, "harmonics-disable-v2")
        self.assertEqual(harmonic_disable.channel, 2)
        self.assertEqual(modulation.command, "modulation-configure-v2")
        self.assertEqual(modulation.channel, 2)
        self.assertEqual(modulation.depth_percent, 80.0)
        self.assertEqual(modulation.internal_frequency_hz, 25.0)
        self.assertEqual(pulse.command, "pulse-configure-v2")
        self.assertEqual(pulse.channel, 2)
        self.assertEqual(pulse.width_s, 1.0e-6)
        self.assertEqual(pulse.delay_s, 0.0)
        self.assertEqual(pulse.leading_transition_s, 1.0e-8)
        self.assertEqual(pulse.trailing_transition_s, 1.0e-8)
        self.assertEqual(pm.command, "pm-modulation-configure-v2")
        self.assertEqual(pm.channel, 2)
        self.assertEqual(pm.phase_deviation_deg, 90.0)
        self.assertEqual(pm.internal_frequency_hz, 25.0)
        self.assertEqual(fm.command, "fm-modulation-configure-v2")
        self.assertEqual(fm.channel, 2)
        self.assertEqual(fm.frequency_deviation_hz, 12_500.0)
        self.assertEqual(fm.internal_frequency_hz, 25.0)
        self.assertEqual(pwm.command, "pwm-modulation-configure-v2")
        self.assertEqual(pwm.channel, 2)
        self.assertEqual(pwm.internal_frequency_hz, 25.0)
        self.assertIsNone(pwm.duty_deviation_percent)
        self.assertEqual(pwm.width_deviation_s, 1.0e-6)
        self.assertEqual(sweep.command, "sweep-configure-v2")
        self.assertEqual(sweep.channel, 2)
        self.assertEqual(sweep.start_hz, 100.0)
        self.assertEqual(sweep.stop_hz, 1000.0)
        self.assertEqual(sweep.spacing, "linear")
        self.assertEqual(sweep.steps, 101)
        self.assertEqual(sweep.sweep_time_s, 1.0)
        self.assertEqual(burst.command, "burst-configure-v2")
        self.assertEqual(burst.channel, 2)
        self.assertEqual(burst.cycles, 12)
        self.assertEqual(burst.phase_deg, 30.0)
        self.assertEqual(burst.internal_period_s, 0.25)
        self.assertEqual(burst.delay_s, 0.5)

    def test_source_basic_live_configure_v2_dispatches_typed_request(self):
        payload = {
            "schema": "wavebench.source.operation.v1",
            "operation": "source.basic_live_configure_v2",
        }
        service = Mock()
        service.configure_basic_live_v2.return_value = (object(), payload)
        stdout = io.StringIO()

        with patch("wavebench.cli._load_source_service", return_value=service), redirect_stdout(stdout):
            code = main(
                [
                    "source",
                    "basic-live-configure-v2",
                    "--channel",
                    "2",
                    "--amplitude-vpp",
                    "1.5",
                ]
            )

        self.assertEqual(code, 0)
        request = service.configure_basic_live_v2.call_args.args[0]
        self.assertEqual(request.channel, 2)
        self.assertEqual(request.patch.amplitude_vpp.value, 1.5)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_source_harmonics_disable_v2_dispatches_typed_request(self):
        payload = {
            "schema": "wavebench.source.operation.v1",
            "operation": "source.harmonics_disable_v2",
        }
        service = Mock()
        service.disable_harmonics_v2.return_value = (object(), payload)
        stdout = io.StringIO()

        with patch("wavebench.cli._load_source_service", return_value=service), redirect_stdout(stdout):
            code = main(["source", "harmonics-disable-v2", "--channel", "2"])

        self.assertEqual(code, 0)
        request = service.disable_harmonics_v2.call_args.args[0]
        self.assertEqual(request.channel, 2)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_fetch_accepts_points(self):
        args = build_parser().parse_args(["scope", "fetch", "--points", "dmax"])
        self.assertEqual(args.command, "fetch")
        self.assertEqual(args.points, "dmax")
    def test_source_set_func_accepts_function(self):
        args = build_parser().parse_args(["source", "set-func", "--channel", "2", "squ"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "set-func")
        self.assertEqual(args.channel, 2)
        self.assertEqual(args.function, "squ")


    def test_source_set_func_accepts_triangle_alias(self):
        args = build_parser().parse_args(["source", "set-func", "--channel", "2", "triangle"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "set-func")
        self.assertEqual(args.function, "triangle")

    def test_source_set_vpp_accepts_value(self):
        args = build_parser().parse_args(["source", "set-vpp", "--channel", "2", "3.3"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "set-vpp")
        self.assertEqual(args.channel, 2)
        self.assertEqual(args.value_vpp, 3.3)


    def test_source_set_vpp_rejects_safety_limit_from_config(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "wavebench.toml"
            config.write_text("""
[connection]
resource = "TCPIP::scope::INSTR"

[scope]

[source]
resource = "TCPIP::source::INSTR"

[safety_limits]
max_source_vpp = 2.0
""", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["source", "set-vpp", "--config", str(config), "5.0"])
            self.assertEqual(code, 2)
            self.assertIn("安全上限已超出", stderr.getvalue())

    def test_source_set_duty_accepts_percent(self):
        args = build_parser().parse_args(["source", "set-duty", "--channel", "2", "25"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "set-duty")
        self.assertEqual(args.channel, 2)
        self.assertEqual(args.duty_percent, 25.0)


    def test_source_arb_probe_accepts_channel(self):
        args = build_parser().parse_args(["source", "arb-probe", "--channel", "2", "--probe-timeout-ms", "700"])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "arb-probe")
        self.assertEqual(args.channel, 2)
        self.assertEqual(args.probe_timeout_ms, 700)

    def test_source_arb_load_accepts_dry_run_options(self):
        args = build_parser().parse_args([
            "source", "arb-load",
            "--channel", "2",
            "--file", "waveform.npy",
            "--name", "EXAMPLE_ARB",
            "--amplitude", "1.0",
            "--frequency", "1000",
            "--offset", "0.0",
            "--sample-rate", "1000",
            "--max-points", "16384",
            "--output-on",
            "--export-payload", "payload.json",
            "--dry-run",
        ])
        self.assertEqual(args.domain, "source")
        self.assertEqual(args.command, "arb-load")
        self.assertEqual(args.channel, 2)
        self.assertEqual(args.file, "waveform.npy")
        self.assertEqual(args.name, "EXAMPLE_ARB")
        self.assertEqual(args.amplitude, 1.0)
        self.assertEqual(args.offset, 0.0)
        self.assertEqual(args.frequency, 1000.0)
        self.assertEqual(args.sample_rate, 1000.0)
        self.assertEqual(args.max_points, 16384)
        self.assertTrue(args.output_on)
        self.assertEqual(args.export_payload, "payload.json")
        self.assertTrue(args.dry_run)

    def test_source_arb_load_dry_run_prints_payload_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "waveform.npy"
            payload = root / "payload.json"
            np.save(path, np.array([-1.0, 0.0, 1.0]))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                status = main([
                    "source", "arb-load",
                    "--channel", "2",
                    "--file", str(path),
                    "--name", "EXAMPLE_ARB",
                    "--amplitude", "1.0",
                    "--frequency", "1000",
                    "--offset", "0.0",
                    "--export-payload", str(payload),
                    "--dry-run",
                ])

            output = stdout.getvalue()
            self.assertEqual(status, 0)
            self.assertIn("arb_name=EXAMPLE_ARB", output)
            self.assertIn("channel=2", output)
            self.assertIn("points=3", output)
            self.assertIn("dac14=0..16383", output)
            self.assertIn(f"payload={payload}", output)
            self.assertIn("dry_run=true", output)
            self.assertIn("upload=not_requested", output)
            self.assertIn('"format": "wavebench.arbitrary.v1"', payload.read_text(encoding="utf-8"))

    def test_sweep_discrete_accepts_frequencies_and_channels(self):
        args = build_parser().parse_args([
            "sweep", "discrete",
            "--source-channel", "2",
            "--scope-channel", "1",
            "--source-resource", "TCPIP::198.51.100.3::INSTR",
            "--frequencies", "1000,2000,5000",
            "--target-cycles", "8",
            "--frequency-tolerance", "0.02",
            "--source-func", "sin",
            "--source-vpp", "3.3",
            "--restore-source-state",
            "--no-csv",
        ])
        self.assertEqual(args.domain, "sweep")
        self.assertEqual(args.command, "discrete")
        self.assertEqual(args.source_channel, 2)
        self.assertEqual(args.scope_channel, 1)
        self.assertEqual(args.source_resource, "TCPIP::198.51.100.3::INSTR")
        self.assertEqual(args.frequencies, "1000,2000,5000")
        self.assertEqual(args.target_cycles, 8.0)
        self.assertEqual(args.frequency_tolerance, 0.02)
        self.assertEqual(args.source_func, "sin")
        self.assertEqual(args.source_vpp, 3.3)
        self.assertTrue(args.restore_source_state)
        self.assertTrue(args.no_csv)

    def test_run_check_accepts_plan_and_config(self):
        args = build_parser().parse_args([
            "run", "check", "--config", "wavebench.toml", "--plan", "plans/example.toml"
        ])
        self.assertEqual(args.domain, "run")
        self.assertEqual(args.command, "check")
        self.assertEqual(args.config, "wavebench.toml")
        self.assertEqual(args.plan, "plans/example.toml")

    def test_plugin_list_accepts_kind_filter(self):
        args = build_parser().parse_args(["plugin", "list", "--kind", "source", "--include-entry-points"])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "list")
        self.assertEqual(args.kind, "source")
        self.assertTrue(args.include_entry_points)

    def test_plugin_info_accepts_driver_id(self):
        args = build_parser().parse_args(["plugin", "info", "rigol.dg4202", "--include-entry-points"])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "info")
        self.assertEqual(args.driver_id, "rigol.dg4202")
        self.assertTrue(args.include_entry_points)

    def test_plugin_lifecycle_parsers(self):
        package = build_parser().parse_args(["plugin", "package", "check", "plugin.whl"])
        install = build_parser().parse_args(["plugin", "install", "plugin.whl", "--dry-run"])
        installed = build_parser().parse_args(["plugin", "installed"])
        info = build_parser().parse_args(["plugin", "info", "example.scope", "--installed"])
        remove = build_parser().parse_args(["plugin", "remove", "example.scope", "--dry-run"])
        upgrade = build_parser().parse_args(["plugin", "upgrade", "plugin.whl"])
        downgrade = build_parser().parse_args(["plugin", "downgrade", "plugin.whl"])
        recover = build_parser().parse_args(["plugin", "recover"])

        self.assertEqual(package.package_command, "check")
        self.assertTrue(install.dry_run)
        self.assertEqual(installed.command, "installed")
        self.assertTrue(info.installed)
        self.assertTrue(remove.dry_run)
        self.assertEqual(upgrade.command, "upgrade")
        self.assertEqual(downgrade.command, "downgrade")
        self.assertEqual(recover.command, "recover")

    def test_plugin_installed_info_rejects_plugin_loading_flags(self):
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["plugin", "info", "example.scope", "--installed", "--load"])
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "plugin",
                    "info",
                    "example.scope",
                    "--installed",
                    "--include-entry-points",
                ]
            )

    def test_plugin_doctor_accepts_entry_point_flag(self):
        args = build_parser().parse_args(["plugin", "doctor", "--include-entry-points"])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "doctor")
        self.assertTrue(args.include_entry_points)

    def test_plugin_market_search_accepts_query_and_index(self):
        args = build_parser().parse_args([
            "plugin", "market", "search", "rigol", "--index", "src/wavebench/plugins/market.example.json"
        ])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "market")
        self.assertEqual(args.market_command, "search")
        self.assertEqual(args.query, "rigol")
        self.assertEqual(args.index, "src/wavebench/plugins/market.example.json")

    def test_plugin_market_info_accepts_plugin_id_and_index(self):
        args = build_parser().parse_args([
            "plugin", "market", "info", "wavebench-rigol-dg4202", "--index", "market.json"
        ])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "market")
        self.assertEqual(args.market_command, "info")
        self.assertEqual(args.plugin_id, "wavebench-rigol-dg4202")
        self.assertEqual(args.index, "market.json")

    def test_plugin_scpi_check_accepts_path(self):
        args = build_parser().parse_args(["plugin", "scpi", "check", "plugin.toml"])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "scpi")
        self.assertEqual(args.scpi_command, "check")
        self.assertEqual(args.path, "plugin.toml")

    def test_plugin_scpi_doctor_accepts_probe_options(self):
        args = build_parser().parse_args([
            "plugin", "scpi", "doctor", "plugin.toml",
            "--probe",
            "--resource", "TCPIP::192.0.2.10::INSTR",
            "--timeout-ms", "250",
        ])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "scpi")
        self.assertEqual(args.scpi_command, "doctor")
        self.assertTrue(args.probe)
        self.assertEqual(args.resource, "TCPIP::192.0.2.10::INSTR")
        self.assertEqual(args.timeout_ms, 250)

    def test_plugin_scpi_info_accepts_path(self):
        args = build_parser().parse_args(["plugin", "scpi", "info", "plugin.toml"])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "scpi")
        self.assertEqual(args.scpi_command, "info")
        self.assertEqual(args.path, "plugin.toml")

    def test_plugin_scpi_probe_accepts_resource_and_backend(self):
        args = build_parser().parse_args([
            "plugin", "scpi", "probe", "plugin.toml",
            "--resource", "TCPIP::192.0.2.10::INSTR",
            "--backend", "rsinstrument",
            "--timeout-ms", "250",
        ])
        self.assertEqual(args.domain, "plugin")
        self.assertEqual(args.command, "scpi")
        self.assertEqual(args.scpi_command, "probe")
        self.assertEqual(args.path, "plugin.toml")
        self.assertEqual(args.resource, "TCPIP::192.0.2.10::INSTR")
        self.assertEqual(args.backend, "rsinstrument")
        self.assertEqual(args.timeout_ms, 250)

    def test_plugin_list_prints_builtin_plugins(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "list", "--kind", "source"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("driver_id	kind	origin	models	capabilities", output)
        self.assertIn("rigol.dg4202", output)
        self.assertIn("source.set_frequency", output)

    def test_plugin_info_prints_metadata(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "info", "rigol.dp800"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("driver_id=rigol.dp800", output)
        self.assertIn("kind=power", output)
        self.assertIn("power.protection", output)

    def test_plugin_installed_and_info_use_lifecycle_ledger(self):
        plugin = InstalledPlugin(
            driver_id="example.scope",
            distribution="wavebench-example-scope",
            version="0.1.0",
            status="healthy",
            wheel_sha256="a" * 64,
        )
        lifecycle = Mock()
        lifecycle.installed.return_value = (plugin,)
        lifecycle.info.return_value = plugin
        stdout = io.StringIO()

        with patch("wavebench.cli.PluginLifecycle", return_value=lifecycle):
            with redirect_stdout(stdout):
                list_code = main(["plugin", "installed"])
                info_code = main(["plugin", "info", "example.scope", "--installed"])

        self.assertEqual((list_code, info_code), (0, 0))
        output = stdout.getvalue()
        self.assertIn("example.scope\twavebench-example-scope\t0.1.0\thealthy", output)
        self.assertIn("wheel_sha256=" + "a" * 64, output)

    def test_plugin_lifecycle_mutation_prints_result(self):
        lifecycle = Mock()
        lifecycle.install.return_value = LifecycleResult(
            "would-install",
            "example.scope",
            "wavebench-example-scope",
            "0.1.0",
        )
        stdout = io.StringIO()

        with patch("wavebench.cli.PluginLifecycle", return_value=lifecycle):
            with redirect_stdout(stdout):
                code = main(["plugin", "install", "plugin.whl", "--dry-run"])

        self.assertEqual(code, 0)
        lifecycle.install.assert_called_once_with("plugin.whl", dry_run=True)
        self.assertIn("status=would-install", stdout.getvalue())

    def test_plugin_doctor_prints_ok_records(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "doctor"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("ok\trigol.dg4202\tmetadata valid", output)
        self.assertIn("ok\trohde-schwarz.rtm2032\tmetadata valid", output)

    def test_executable_plugin_info_loads_v2_descriptor(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "info", "ds1000z", "--load"])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("driver_id=rigol.ds1104", output)
        self.assertIn("aliases=ds1104, ds1000z", output)
        self.assertIn("executable_api=wavebench.instrument.v2", output)
        self.assertIn("resource_schemes=any", output)

    def test_executable_plugin_doctor_loads_descriptors(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "doctor", "--load"])

        self.assertEqual(code, 0)
        self.assertIn("可执行描述符有效", stdout.getvalue())

    def test_executable_plugin_doctor_isolates_bad_entry_point(self):
        entry_points = FakePluginEntryPoints(
            [
                FakePluginEntryPoint(
                    "broken.scope",
                    RuntimeError("boom"),
                    group="wavebench.instruments",
                )
            ]
        )
        stdout = io.StringIO()
        with patch("wavebench.instruments.registry.entry_points", return_value=entry_points):
            with redirect_stdout(stdout):
                code = main(["plugin", "doctor", "--load"])

        self.assertEqual(code, 2)
        output = stdout.getvalue()
        self.assertIn("error\tentry_point:broken.scope", output)
        self.assertIn("ok\trohde-schwarz.rtm2032", output)

    def test_plugin_list_can_include_entry_points(self):
        entry_points = FakePluginEntryPoints([FakePluginEntryPoint("example", make_cli_plugin())])
        stdout = io.StringIO()
        with patch("wavebench.plugins.registry.entry_points", return_value=entry_points):
            with redirect_stdout(stdout):
                code = main(["plugin", "list", "--include-entry-points"])
        self.assertEqual(code, 0)
        self.assertIn("example.scope\tscope\tentry_point", stdout.getvalue())

    def test_plugin_doctor_reports_bad_entry_point_and_exits_nonzero(self):
        entry_points = FakePluginEntryPoints([FakePluginEntryPoint("broken", RuntimeError("boom"))])
        stdout = io.StringIO()
        with patch("wavebench.plugins.registry.entry_points", return_value=entry_points):
            with redirect_stdout(stdout):
                code = main(["plugin", "doctor", "--include-entry-points"])
        self.assertEqual(code, 2)
        self.assertIn("error\tentry_point:broken\tboom", stdout.getvalue())

    def test_plugin_market_search_prints_default_index(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "market", "search", "rigol"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("plugin_id\tdriver_id\tkind\tpackage\tversion\tsummary", output)
        self.assertIn("wavebench-rigol-dg4202", output)

    def test_plugin_market_info_prints_default_index_entry(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "market", "info", "wavebench-rs-rtm2032"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("plugin_id=wavebench-rs-rtm2032", output)
        self.assertIn("driver_id=rohde-schwarz.rtm2032", output)
        self.assertIn("capabilities=scope.idn", output)

    def test_plugin_scpi_check_prints_ok(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "scpi", "check", "docs/project/reference/plugins/scpi-plugin.example.toml"])
        self.assertEqual(code, 0)
        self.assertIn("ok\texample.scope\tmetadata valid", stdout.getvalue())

    def test_plugin_scpi_doctor_requires_probe_with_resource(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main([
                "plugin", "scpi", "doctor", "docs/project/reference/plugins/scpi-plugin.example.toml",
                "--resource", "TCPIP::192.0.2.10::INSTR",
            ])
        self.assertEqual(code, 2)
        self.assertIn("--resource requires --probe", stderr.getvalue())

    def test_plugin_scpi_doctor_probe_prints_result(self):
        stdout = io.StringIO()
        with patch(
            "wavebench.cli.scpi_plugin_doctor_records",
            return_value=[
                ("ok", "example.scope", "metadata valid"),
                ("ok", "probe", "idn_response=Example,EX1,123"),
                ("ok", "probe", "idn matched declared patterns"),
            ],
        ):
            with redirect_stdout(stdout):
                code = main([
                    "plugin", "scpi", "doctor", "docs/project/reference/plugins/scpi-plugin.example.toml",
                    "--probe", "--resource", "TCPIP::192.0.2.10::INSTR",
                ])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("ok\tprobe\tidn_response=Example,EX1,123", output)

    def test_plugin_scpi_info_prints_metadata(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["plugin", "scpi", "info", "docs/project/reference/plugins/scpi-plugin.example.toml"])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("driver_id=example.scope", output)
        self.assertIn("origin=local", output)
        self.assertIn("scpi_idn_query=*IDN?", output)

    def test_plugin_scpi_probe_prints_result(self):
        from wavebench.plugins.scpi import ScpiProbeResult

        stdout = io.StringIO()
        with patch(
            "wavebench.cli.probe_scpi_plugin",
            return_value=ScpiProbeResult(
                driver_id="example.scope",
                resource="TCPIP::192.0.2.10::INSTR",
                backend="pyvisa",
                query="*IDN?",
                response="Example,EX1,123",
                matched=True,
            ),
        ):
            with redirect_stdout(stdout):
                code = main([
                    "plugin", "scpi", "probe", "docs/project/reference/plugins/scpi-plugin.example.toml",
                    "--resource", "TCPIP::192.0.2.10::INSTR",
                ])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("query=*IDN?", output)
        self.assertIn("idn_match=yes", output)

    def test_net_discover_accepts_scan_options(self):
        args = build_parser().parse_args([
            "net", "discover",
            "--subnet", "192.0.2.0/24",
            "--ports", "5025,5555,111",
            "--timeout-ms", "200",
            "--workers", "8",
            "--max-hosts", "512",
            "--no-idn",
            "--idn-only",
            "--no-visa",
        ])
        self.assertEqual(args.domain, "net")
        self.assertEqual(args.command, "discover")
        self.assertEqual(args.subnet, "192.0.2.0/24")
        self.assertEqual(args.ports, "5025,5555,111")
        self.assertEqual(args.timeout_ms, 200)
        self.assertEqual(args.workers, 8)
        self.assertEqual(args.max_hosts, 512)
        self.assertTrue(args.no_idn)
        self.assertTrue(args.idn_only)
        self.assertTrue(args.no_visa)

    def test_net_discover_prints_results(self):
        class StubResult:
            address = "192.0.2.161"
            port = 5025
            status = "idn"
            protocol = "scpi-socket"
            source = "network"
            resource = "TCPIP::192.0.2.161::5025::SOCKET"
            idn = "RIGOL TECHNOLOGIES,DP832,DP8A000000000,00.01.16"
            note = ""

        stdout = io.StringIO()
        with patch("wavebench.cli.discover_instruments", return_value=[StubResult()]):
            with redirect_stdout(stdout):
                code = main([
                    "net", "discover", "--subnet", "192.0.2.0/24", "--timeout-ms", "10", "--no-visa"
                ])
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("address	port	status	protocol", output)
        self.assertIn("192.0.2.161", output)
        self.assertIn("RIGOL TECHNOLOGIES,DP832", output)


    def test_run_check_applies_config_safety_limits_without_io(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "wavebench.toml"
            plan = root / "plan.toml"
            config.write_text("""
[connection]
resource = "TCPIP::203.0.113.10::INSTR"

[scope]

[source]
resource = "TCPIP::203.0.113.11::INSTR"

[safety_limits]
max_source_vpp = 2.0
""", encoding="utf-8")
            plan.write_text("""
[[steps]]
kind = "source.set_vpp"
channel = 2
value_vpp = 5.0
""", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                code = main(["run", "check", "--config", str(config), "--plan", str(plan)])

            self.assertEqual(code, 2)
            self.assertIn("安全上限已超出", stderr.getvalue())
            self.assertNotIn("TCPIP::203.0.113.11", stderr.getvalue())

    def test_run_check_prints_safety_limits_ok_when_plan_is_under_limit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "wavebench.toml"
            plan = root / "plan.toml"
            config.write_text("""
[connection]
resource = "TCPIP::203.0.113.10::INSTR"

[scope]

[source]
resource = "TCPIP::203.0.113.11::INSTR"

[safety_limits]
max_source_vpp = 10.0
""", encoding="utf-8")
            plan.write_text("""
[[steps]]
kind = "source.set_vpp"
channel = 2
value_vpp = 5.0
""", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = main(["run", "check", "--config", str(config), "--plan", str(plan)])

            self.assertEqual(code, 0)
            self.assertIn("safety_limits=ok / 安全上限=通过", stdout.getvalue())

    def test_run_verify_accepts_plan_and_config(self):
        args = build_parser().parse_args([
            "run", "verify", "--config", "wavebench.toml", "--plan", "plans/example.toml"
        ])
        self.assertEqual(args.domain, "run")
        self.assertEqual(args.command, "verify")
        self.assertEqual(args.config, "wavebench.toml")
        self.assertEqual(args.plan, "plans/example.toml")



    def test_run_verify_applies_safety_limits_before_io(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "wavebench.toml"
            plan = root / "plan.toml"
            config.write_text("""
[connection]
resource = "TCPIP::203.0.113.10::INSTR"

[scope]

[source]
resource = "TCPIP::203.0.113.11::INSTR"

[safety_limits]
max_source_vpp = 2.0
""", encoding="utf-8")
            plan.write_text("""
[[steps]]
kind = "source.set_vpp"
channel = 2
value_vpp = 5.0
""", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                code = main(["run", "verify", "--config", str(config), "--plan", str(plan)])

            self.assertEqual(code, 2)
            self.assertIn("安全上限已超出", stderr.getvalue())
            self.assertNotIn("TCPIP::203.0.113.11", stderr.getvalue())

    def test_run_schema_accepts_no_plan(self):
        args = build_parser().parse_args(["run", "schema"])
        self.assertEqual(args.domain, "run")
        self.assertEqual(args.command, "schema")

    def test_run_plan_accepts_plan_and_config(self):
        args = build_parser().parse_args([
            "run", "plan", "--config", "wavebench.toml", "--plan", "plans/example.toml"
        ])
        self.assertEqual(args.domain, "run")
        self.assertEqual(args.command, "plan")
        self.assertEqual(args.config, "wavebench.toml")
        self.assertEqual(args.plan, "plans/example.toml")


    def test_capture_inspect_accepts_path(self):
        args = build_parser().parse_args(["capture", "inspect", "data/raw/example"])
        self.assertEqual(args.domain, "capture")
        self.assertEqual(args.command, "inspect")
        self.assertEqual(args.path, "data/raw/example")

    def test_capture_inspect_accepts_fft(self):
        args = build_parser().parse_args([
            "capture", "inspect", "data/raw/example", "--fft", "--harmonics", "7",
            "--fft-expect-frequency", "1000", "--fft-frequency-tolerance", "0.02"
        ])
        self.assertEqual(args.domain, "capture")
        self.assertEqual(args.command, "inspect")
        self.assertTrue(args.fft)
        self.assertEqual(args.harmonics, 7)
        self.assertEqual(args.fft_expect_frequency, 1000.0)
        self.assertEqual(args.fft_frequency_tolerance, 0.02)

    def test_capture_inspect_fft_prints_spectrum_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "data" / "raw" / "fft_cap"
            capture.mkdir(parents=True)
            sample_rate = 1000.0
            samples = 1000
            time_s = np.arange(samples) / sample_rate
            voltage_v = np.sin(2 * np.pi * 50.0 * time_s)
            np.save(capture / "ch1.npy", np.column_stack((time_s, voltage_v)))
            (capture / "metadata.json").write_text(
                json.dumps(
                    {
                        "operation": {"command": "scope capture", "channel": 1},
                        "waveform": {
                            "summary": {
                                "channel": 1,
                                "samples": samples,
                                "x_increment_s": 1.0 / sample_rate,
                            }
                        },
                        "files": {"npy": "data/raw/fft_cap/ch1.npy"},
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                status = main([
                    "capture", "inspect", str(capture), "--fft", "--harmonics", "7",
                    "--fft-expect-frequency", "50", "--fft-frequency-tolerance", "0.01"
                ])

            output = stdout.getvalue()
            self.assertEqual(status, 0)
            self.assertIn("FFT", output)
            self.assertIn("CH1", output)
            self.assertIn("window=hann", output)
            self.assertIn("sample_rate≈1000 Hz", output)
            self.assertIn("resolution≈1 Hz", output)
            self.assertIn("peak_frequency≈50 Hz", output)
            self.assertIn("peak_frequency_error≈0.000%", output)
            self.assertIn("peak_frequency_ok=True", output)
            self.assertIn("noise_floor≈", output)
            self.assertIn("harmonic_7≈", output)

    def test_run_report_accepts_path_and_output(self):
        args = build_parser().parse_args(["run", "report", "data/runs/example", "--output", "report.html"])
        self.assertEqual(args.domain, "run")
        self.assertEqual(args.command, "report")
        self.assertEqual(args.path, "data/runs/example")
        self.assertEqual(args.output, "report.html")

    def test_run_report_accepts_pdf_options(self):
        args = build_parser().parse_args(
            ["run", "report", "data/runs/example", "--pdf", "--pdf-output", "report.pdf"]
        )

        self.assertTrue(args.pdf)
        self.assertEqual(args.pdf_output, "report.pdf")

    def test_run_report_writes_html_and_pdf_to_distinct_paths(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            html_path = Path(tmp) / "report.html"
            pdf_path = Path(tmp) / "report.pdf"
            stdout = io.StringIO()
            with patch("wavebench.cli.write_run_report_html", return_value=html_path) as write_html, patch(
                "wavebench.cli.write_run_report_pdf", return_value=pdf_path
            ) as write_pdf, redirect_stdout(stdout):
                code = main(
                    [
                        "run",
                        "report",
                        str(run_dir),
                        "--output",
                        str(html_path),
                        "--pdf",
                        "--pdf-output",
                        str(pdf_path),
                    ]
                )

            self.assertEqual(code, 0)
            write_html.assert_called_once()
            write_pdf.assert_called_once()
            self.assertIn(f"report={html_path}", stdout.getvalue())
            self.assertIn(f"pdf={pdf_path}", stdout.getvalue())

    def test_run_report_rejects_pdf_output_without_pdf_flag_and_path_collisions(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                missing_flag = main(["run", "report", str(run_dir), "--pdf-output", "report.pdf"])
            self.assertEqual(missing_flag, 2)
            self.assertIn("requires --pdf", stderr.getvalue())

            stderr = io.StringIO()
            same_path = Path(tmp) / "report.html"
            with redirect_stderr(stderr):
                collision = main(
                    [
                        "run",
                        "report",
                        str(run_dir),
                        "--output",
                        str(same_path),
                        "--pdf",
                        "--pdf-output",
                        str(same_path),
                    ]
                )
            self.assertEqual(collision, 2)
            self.assertIn("different paths", stderr.getvalue())

    def test_run_report_rejects_misleading_html_and_pdf_suffixes(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"status": "ok", "steps": []}), encoding="utf-8")
            cases = [
                (
                    [
                        "--output",
                        str(Path(tmp) / "html-named.pdf"),
                        "--pdf",
                        "--pdf-output",
                        str(Path(tmp) / "report.pdf"),
                    ],
                    "--output is an HTML path",
                ),
                (
                    [
                        "--output",
                        str(Path(tmp) / "report.html"),
                        "--pdf",
                        "--pdf-output",
                        str(Path(tmp) / "pdf-named.html"),
                    ],
                    "--pdf-output is a PDF path",
                ),
            ]

            for options, message in cases:
                stderr = io.StringIO()
                with patch("wavebench.cli.write_run_report_html") as write_html, patch(
                    "wavebench.cli.write_run_report_pdf"
                ) as write_pdf, redirect_stderr(stderr):
                    code = main(["run", "report", str(run_dir), *options])

                self.assertEqual(code, 2)
                self.assertIn(message, stderr.getvalue())
                write_html.assert_not_called()
                write_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
