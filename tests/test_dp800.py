import unittest
from threading import Barrier, Thread, get_ident
import time

from wavebench.errors import DataError, InstrumentError
from wavebench.drivers.dp800 import (
    DP800Power,
    parse_apply_response,
    parse_idn_model,
    parse_measure_all_response,
    parse_protection_value_response,
)


class FakeTransport:
    def __init__(self):
        self.queries = []
        self.writes = []
        self.fail_command = None
        self.fail_query_once = None
        self.fail_query_on_call = {}
        self.query_counts = {}
        self.fail_write_commands = set()
        self.ignored_write_commands = set()
        self.ignored_write_once = set()
        self.responses = {
            "*IDN?": "RIGOL TECHNOLOGIES,DP832A,SN,FW",
            ":APPL? CH1": "CH1:30V/3A,5.000,0.100",
            ":MEAS:ALL? CH1": "5.0114,0.0000,0.000",
            ":OUTP? CH1": "ON",
            ":OUTP:MODE? CH1": "CV",
            ":OUTP:OVP? CH1": "ON",
            ":OUTP:OVP:VAL? CH1": "6.000",
            ":OUTP:OVP:QUES? CH1": "NO",
            ":OUTP:OCP? CH1": "ON",
            ":OUTP:OCP:VAL? CH1": "0.5000",
            ":OUTP:OCP:QUES? CH1": "NO",
            "SYST:ERR?": '0,"No error"',
        }

    def write(self, command: str) -> None:
        self.writes.append(command)
        ignore_once = command in self.ignored_write_once
        self.ignored_write_once.discard(command)
        if command not in self.ignored_write_commands and not ignore_once:
            self._apply_write(command)
        if command in self.fail_write_commands:
            raise InstrumentError(f"injected ambiguous write failure for {command}")

    def _apply_write(self, command: str) -> None:
        if command.startswith(":APPL CH1,"):
            voltage, current = command.removeprefix(":APPL CH1,").split(",")
            self.responses[":APPL? CH1"] = f"CH1:30V/3A,{voltage},{current}"
        elif command.startswith(":OUTP CH1,"):
            self.responses[":OUTP? CH1"] = command.rsplit(",", 1)[1]
        elif command.startswith(":OUTP:OVP:VAL CH1,"):
            self.responses[":OUTP:OVP:VAL? CH1"] = command.rsplit(",", 1)[1]
        elif command.startswith(":OUTP:OVP CH1,"):
            self.responses[":OUTP:OVP? CH1"] = command.rsplit(",", 1)[1]
        elif command.startswith(":OUTP:OCP:VAL CH1,"):
            self.responses[":OUTP:OCP:VAL? CH1"] = command.rsplit(",", 1)[1]
        elif command.startswith(":OUTP:OCP CH1,"):
            self.responses[":OUTP:OCP? CH1"] = command.rsplit(",", 1)[1]

    def query(self, command: str) -> str:
        self.queries.append(command)
        self.query_counts[command] = self.query_counts.get(command, 0) + 1
        if command == self.fail_command:
            raise InstrumentError(f"injected failure for {command}")
        if command == self.fail_query_once:
            self.fail_query_once = None
            raise InstrumentError(f"injected one-shot failure for {command}")
        if self.query_counts[command] == self.fail_query_on_call.get(command):
            raise InstrumentError(f"injected call-count failure for {command}")
        return self.responses[command]

    def close(self) -> None:
        pass


class DP800Tests(unittest.TestCase):
    def test_parse_idn_model_maps_supported_channel_counts(self):
        self.assertEqual(parse_idn_model("RIGOL TECHNOLOGIES,DP811A,SN,FW"), ("DP811A", 1))
        self.assertEqual(parse_idn_model("RIGOL TECHNOLOGIES,DP821,SN,FW"), ("DP821", 2))
        self.assertEqual(parse_idn_model("RIGOL TECHNOLOGIES,DP832A,SN,FW"), ("DP832A", 3))
        with self.assertRaisesRegex(DataError, "unsupported DP800 model"):
            parse_idn_model("RIGOL TECHNOLOGIES,DP999,SN,FW")

    def test_parse_apply_response(self):
        rating, voltage, current = parse_apply_response("CH1:30V/3A,5.000,0.100")
        self.assertEqual(rating, "30V/3A")
        self.assertEqual(voltage, 5.0)
        self.assertEqual(current, 0.1)
        self.assertEqual(
            parse_apply_response("5.000,0.100", expected_channel=1, allow_targetless=True),
            (None, 5.0, 0.1),
        )

    def test_parse_apply_response_rejects_unexpected_format(self):
        with self.assertRaisesRegex(DataError, "unexpected DP800 APPL"):
            parse_apply_response("CH1:30V/3A,5.000")
        with self.assertRaisesRegex(DataError, "unexpected DP800 APPL"):
            parse_apply_response("CH1:30V/3A,5.000,0.100,extra")
        with self.assertRaisesRegex(DataError, "unexpected DP800 APPL.*channel"):
            parse_apply_response("CH2:30V/3A,5.000,0.100", expected_channel=1)
        with self.assertRaisesRegex(DataError, "confirmed single-channel target"):
            parse_apply_response("5.000,0.100", expected_channel=2)
        with self.assertRaisesRegex(DataError, "confirmed single-channel target"):
            parse_apply_response("5.000,0.100", expected_channel=1)
        with self.assertRaisesRegex(DataError, "unexpected DP800 APPL.*target"):
            parse_apply_response("CH1:bogus,5.000,0.100")
        with self.assertRaisesRegex(DataError, "must be finite"):
            parse_apply_response("CH1:30V/3A,nan,0.100")

    def test_parse_measure_all_response(self):
        voltage, current, power = parse_measure_all_response("5.0114,0.0000,0.000")
        self.assertEqual(voltage, 5.0114)
        self.assertEqual(current, 0.0)
        self.assertEqual(power, 0.0)

    def test_parse_measure_all_response_rejects_unexpected_format(self):
        with self.assertRaisesRegex(DataError, "unexpected DP800 MEAS:ALL"):
            parse_measure_all_response("5.0,0.1")
        with self.assertRaisesRegex(DataError, "unexpected DP800 MEAS:ALL"):
            parse_measure_all_response("5.0,0.1,0.5,extra")
        with self.assertRaisesRegex(DataError, "must be finite"):
            parse_measure_all_response("5.0,inf,0.1")

    def test_parse_protection_value_response(self):
        self.assertEqual(parse_protection_value_response("8.800"), 8.8)

    def test_get_status_reads_read_only_fields(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        status = driver.get_status(1)
        self.assertEqual(status.channel, 1)
        self.assertEqual(status.output, "ON")
        self.assertEqual(status.mode, "CV")
        self.assertEqual(status.rating, "30V/3A")
        self.assertEqual(status.set_voltage_v, 5.0)
        self.assertEqual(status.set_current_a, 0.1)
        self.assertEqual(status.measured_voltage_v, 5.0114)
        self.assertEqual(status.measured_current_a, 0.0)
        self.assertEqual(status.measured_power_w, 0.0)
        self.assertEqual(transport.queries, [
            "*IDN?",
            ":APPL? CH1",
            ":MEAS:ALL? CH1",
            ":OUTP? CH1",
            ":OUTP:MODE? CH1",
        ])

    def test_get_measurement_reads_measurement_only(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        measurement = driver.get_measurement(1)
        self.assertEqual(measurement.channel, 1)
        self.assertEqual(measurement.measured_voltage_v, 5.0114)
        self.assertEqual(measurement.measured_current_a, 0.0)
        self.assertEqual(measurement.measured_power_w, 0.0)
        self.assertEqual(transport.queries, ["*IDN?", ":MEAS:ALL? CH1"])

    def test_get_protection_status_reads_ovp_and_ocp(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        status = driver.get_protection_status(1)
        self.assertEqual(status.channel, 1)
        self.assertEqual(status.ovp_enabled, "ON")
        self.assertEqual(status.ovp_threshold_v, 6.0)
        self.assertEqual(status.ovp_tripped, "NO")
        self.assertEqual(status.ocp_enabled, "ON")
        self.assertEqual(status.ocp_threshold_a, 0.5)
        self.assertEqual(status.ocp_tripped, "NO")
        self.assertEqual(transport.queries, [
            "*IDN?",
            ":OUTP:OVP? CH1",
            ":OUTP:OVP:VAL? CH1",
            ":OUTP:OVP:QUES? CH1",
            ":OUTP:OCP? CH1",
            ":OUTP:OCP:VAL? CH1",
            ":OUTP:OCP:QUES? CH1",
        ])

    def test_set_protection_writes_thresholds_and_states(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        status = driver.set_protection(
            1,
            ovp_threshold_v=6.5,
            ovp_enabled=True,
            ocp_threshold_a=0.6,
            ocp_enabled=False,
            check_errors=True,
        )
        self.assertEqual(transport.writes, [
            ":OUTP:OVP:VAL CH1,6.5",
            ":OUTP:OCP CH1,OFF",
            ":OUTP:OCP:VAL CH1,0.6",
        ])
        self.assertEqual(status.ovp_threshold_v, 6.5)
        self.assertEqual(status.ocp_threshold_a, 0.6)
        self.assertEqual(status.ocp_enabled, "OFF")

    def test_set_voltage_current_limit_writes_apply_only(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        status = driver.set_voltage_current_limit(1, 3.3, 0.2, check_errors=True, settle_ms_after_set=0)
        self.assertEqual(transport.writes, [":APPL CH1,3.3,0.2"])
        self.assertEqual(status.output, "ON")
        self.assertNotIn(":OUTP CH1,ON", transport.writes)
        self.assertNotIn(":OUTP CH1,OFF", transport.writes)

    def test_set_voltage_current_limit_rejects_invalid_values(self):
        driver = DP800Power(transport=FakeTransport())
        with self.assertRaisesRegex(Exception, "voltage must be >= 0"):
            driver.set_voltage_current_limit(1, -1.0, 0.1)
        with self.assertRaisesRegex(Exception, "current limit must be > 0"):
            driver.set_voltage_current_limit(1, 1.0, 0.0)
        with self.assertRaisesRegex(DataError, "must be finite"):
            driver.set_voltage_current_limit(1, float("nan"), 0.1)
        with self.assertRaisesRegex(DataError, "must be finite"):
            driver.set_voltage_current_limit(1, 1.0, float("inf"))

    def test_set_output_writes_output_only(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        status = driver.set_output(1, False, check_errors=True, settle_ms_after_output=0)
        self.assertEqual(transport.writes, [":OUTP CH1,OFF"])
        self.assertEqual(status.channel, 1)
        self.assertNotIn(":APPL CH1,3.3,0.2", transport.writes)

    def test_appl_readback_failure_restores_without_latching(self):
        transport = FakeTransport()
        transport.fail_query_on_call[":APPL? CH1"] = 2
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "original setpoint was restored"):
            driver.set_voltage_current_limit(1, 3.3, 0.2, check_errors=False)
        self.assertFalse(driver.configuration_writes_blocked)
        self.assertEqual(transport.responses[":APPL? CH1"], "CH1:30V/3A,5,0.1")

    def test_appl_ambiguous_write_restores_and_latches(self):
        transport = FakeTransport()
        transport.fail_write_commands.add(":APPL CH1,3.3,0.2")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "write outcome is ambiguous"):
            driver.set_voltage_current_limit(1, 3.3, 0.2, check_errors=False)
        self.assertTrue(driver.configuration_writes_blocked)
        self.assertEqual(transport.responses[":APPL? CH1"], "CH1:30V/3A,5,0.1")
        with self.assertRaisesRegex(InstrumentError, "configuration writes are blocked"):
            driver.set_output(1, False, check_errors=False)

    def test_appl_restore_failure_latches(self):
        transport = FakeTransport()
        transport.fail_query_on_call[":APPL? CH1"] = 2
        transport.fail_write_commands.add(":APPL CH1,5,0.1")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "restoration is ambiguous"):
            driver.set_voltage_current_limit(1, 3.3, 0.2, check_errors=False)
        self.assertTrue(driver.configuration_writes_blocked)

    def test_output_failure_converges_to_off_without_latching(self):
        transport = FakeTransport()
        transport.ignored_write_once.add(":OUTP CH1,OFF")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "output was forced OFF"):
            driver.set_output(1, False, check_errors=False)
        self.assertFalse(driver.configuration_writes_blocked)
        self.assertEqual(transport.responses[":OUTP? CH1"], "OFF")

    def test_output_ambiguous_write_forces_off_and_latches(self):
        transport = FakeTransport()
        transport.responses[":OUTP? CH1"] = "OFF"
        transport.fail_write_commands.add(":OUTP CH1,ON")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "write outcome is ambiguous"):
            driver.set_output(1, True, check_errors=False)
        self.assertEqual(transport.responses[":OUTP? CH1"], "OFF")
        self.assertTrue(driver.configuration_writes_blocked)

    def test_output_recovery_failure_latches(self):
        transport = FakeTransport()
        transport.responses[":OUTP? CH1"] = "OFF"
        transport.ignored_write_commands.add(":OUTP CH1,ON")
        transport.fail_write_commands.add(":OUTP CH1,OFF")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "OFF recovery is ambiguous"):
            driver.set_output(1, True, check_errors=False)
        self.assertTrue(driver.configuration_writes_blocked)

    def test_protection_readback_failure_restores_without_latching(self):
        transport = FakeTransport()
        transport.ignored_write_commands.add(":OUTP:OCP:VAL CH1,0.6")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "original configuration was restored"):
            driver.set_protection(1, ocp_threshold_a=0.6, check_errors=False)
        self.assertFalse(driver.configuration_writes_blocked)
        self.assertEqual(float(transport.responses[":OUTP:OCP:VAL? CH1"]), 0.5)

    def test_protection_ambiguous_write_restores_and_latches(self):
        transport = FakeTransport()
        transport.fail_write_commands.add(":OUTP:OCP:VAL CH1,0.6")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "write outcome is ambiguous"):
            driver.set_protection(1, ocp_threshold_a=0.6, check_errors=False)
        self.assertTrue(driver.configuration_writes_blocked)
        self.assertEqual(transport.responses[":OUTP:OCP:VAL? CH1"], "0.5")

    def test_protection_later_write_failure_restores_all_fields_and_latches(self):
        transport = FakeTransport()
        transport.fail_write_commands.add(":OUTP:OCP:VAL CH1,0.6")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "write outcome is ambiguous"):
            driver.set_protection(
                1,
                ovp_threshold_v=6.5,
                ocp_threshold_a=0.6,
                check_errors=False,
            )
        self.assertEqual(float(transport.responses[":OUTP:OVP:VAL? CH1"]), 6.0)
        self.assertEqual(float(transport.responses[":OUTP:OCP:VAL? CH1"]), 0.5)
        self.assertTrue(driver.configuration_writes_blocked)

    def test_protection_restore_failure_latches(self):
        transport = FakeTransport()
        transport.ignored_write_commands.add(":OUTP:OCP:VAL CH1,0.6")
        transport.fail_write_commands.add(":OUTP:OCP CH1,ON")
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "restoration is ambiguous"):
            driver.set_protection(
                1,
                ocp_threshold_a=0.6,
                ocp_enabled=False,
                check_errors=False,
            )
        self.assertTrue(driver.configuration_writes_blocked)

    def test_new_trip_is_never_cleared_and_latches(self):
        class TripOnOvpWrite(FakeTransport):
            def _apply_write(self, command: str) -> None:
                super()._apply_write(command)
                if command == ":OUTP:OVP:VAL CH1,6.5":
                    self.responses[":OUTP:OVP:QUES? CH1"] = "YES"

        transport = TripOnOvpWrite()
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(InstrumentError, "restoration is ambiguous"):
            driver.set_protection(1, ovp_threshold_v=6.5, check_errors=False)
        self.assertTrue(driver.configuration_writes_blocked)
        self.assertFalse(any("CLEAR" in command for command in transport.writes))

    def test_set_output_rejects_invalid_channel(self):
        driver = DP800Power(transport=FakeTransport())
        with self.assertRaisesRegex(DataError, "channel must be an integer >= 1"):
            driver.set_output(0, True)

    def test_channel_count_fails_closed(self):
        transport = FakeTransport()
        transport.responses["*IDN?"] = "RIGOL TECHNOLOGIES,DP821A,SN,FW"
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(DataError, "CH3 is unavailable"):
            driver.get_status(3)
        self.assertEqual(transport.queries, ["*IDN?"])

    def test_unaccepted_models_are_read_only(self):
        transport = FakeTransport()
        transport.responses["*IDN?"] = "RIGOL TECHNOLOGIES,DP811A,SN,FW"
        transport.responses[":APPL?"] = "5.000,0.100"
        driver = DP800Power(transport=transport)
        self.assertEqual(driver.get_status(1).set_voltage_v, 5.0)
        with self.assertRaisesRegex(DataError, "writes are supported only"):
            driver.set_voltage_current_limit(1, 3.3, 0.2)
        with self.assertRaisesRegex(DataError, "writes are supported only"):
            driver.set_output(1, False)
        with self.assertRaisesRegex(DataError, "writes are supported only"):
            driver.set_protection(1, ovp_enabled=False)
        self.assertEqual(transport.writes, [])

    def test_protection_rejects_nonfinite_thresholds_before_write(self):
        transport = FakeTransport()
        driver = DP800Power(transport=transport)
        with self.assertRaisesRegex(DataError, "must be finite"):
            driver.set_protection(1, ovp_threshold_v=float("nan"))
        with self.assertRaisesRegex(DataError, "must be finite"):
            driver.set_protection(1, ocp_threshold_a=float("inf"))
        self.assertEqual(transport.writes, [])

    def test_instance_error_check_default_is_honored(self):
        transport = FakeTransport()
        DP800Power(transport=transport, check_errors_after_ops=False).set_output(1, False)
        self.assertNotIn("SYST:ERR?", transport.queries)

        transport = FakeTransport()
        DP800Power(transport=transport, check_errors_after_ops=False).set_output(
            1, False, check_errors=True
        )
        self.assertIn("SYST:ERR?", transport.queries)

    def test_single_channel_status_uses_targetless_apply_query(self):
        transport = FakeTransport()
        transport.responses["*IDN?"] = "RIGOL TECHNOLOGIES,DP811A,SN,FW"
        transport.responses[":APPL?"] = "5.000,0.100"
        status = DP800Power(transport=transport).get_status(1)
        self.assertIsNone(status.rating)
        self.assertEqual(status.set_voltage_v, 5.0)
        self.assertEqual(status.set_current_a, 0.1)
        self.assertEqual(transport.queries[:2], ["*IDN?", ":APPL?"])

    def test_all_enums_fail_closed(self):
        cases = (
            (":OUTP? CH1", "MAYBE", "status", "output state"),
            (":OUTP:MODE? CH1", "UNKNOWN", "status", "output mode"),
            (":OUTP:OVP? CH1", "MAYBE", "protection", "OVP state"),
            (":OUTP:OVP:QUES? CH1", "UNKNOWN", "protection", "OVP trip state"),
            (":OUTP:OCP? CH1", "MAYBE", "protection", "OCP state"),
            (":OUTP:OCP:QUES? CH1", "UNKNOWN", "protection", "OCP trip state"),
        )
        for command, response, operation, message in cases:
            with self.subTest(command=command):
                transport = FakeTransport()
                transport.responses[command] = response
                driver = DP800Power(transport=transport)
                with self.assertRaisesRegex(DataError, f"unexpected DP800 {message}"):
                    if operation == "status":
                        driver.get_status(1)
                    else:
                        driver.get_protection_status(1)
                self.assertEqual(transport.writes, [])

    def test_status_snapshot_fails_at_every_query_without_writes(self):
        for command in (":APPL? CH1", ":MEAS:ALL? CH1", ":OUTP? CH1", ":OUTP:MODE? CH1"):
            with self.subTest(command=command):
                transport = FakeTransport()
                transport.fail_command = command
                with self.assertRaisesRegex(InstrumentError, "injected failure"):
                    DP800Power(transport=transport).get_status(1)
                self.assertEqual(transport.writes, [])

    def test_public_io_operations_do_not_interleave_between_threads(self):
        class SlowTransport(FakeTransport):
            def __init__(self):
                super().__init__()
                self.query_threads = []

            def query(self, command: str) -> str:
                self.query_threads.append(get_ident())
                time.sleep(0.001)
                return super().query(command)

        transport = SlowTransport()
        driver = DP800Power(transport=transport)
        barrier = Barrier(3)
        errors = []

        def run(operation):
            barrier.wait()
            try:
                operation()
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [
            Thread(target=run, args=(lambda: driver.get_status(1),)),
            Thread(target=run, args=(lambda: driver.get_protection_status(1),)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        switches = sum(
            current != following
            for current, following in zip(transport.query_threads, transport.query_threads[1:])
        )
        self.assertEqual(errors, [])
        self.assertLessEqual(switches, 1)

    def test_protection_snapshot_fails_at_every_query_without_writes(self):
        commands = (
            ":OUTP:OVP? CH1",
            ":OUTP:OVP:VAL? CH1",
            ":OUTP:OVP:QUES? CH1",
            ":OUTP:OCP? CH1",
            ":OUTP:OCP:VAL? CH1",
            ":OUTP:OCP:QUES? CH1",
        )
        for command in commands:
            with self.subTest(command=command):
                transport = FakeTransport()
                transport.fail_command = command
                with self.assertRaisesRegex(InstrumentError, "injected failure"):
                    DP800Power(transport=transport).get_protection_status(1)
                self.assertEqual(transport.writes, [])


if __name__ == "__main__":
    unittest.main()
