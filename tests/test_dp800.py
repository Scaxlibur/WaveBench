import unittest

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

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command == self.fail_command:
            raise InstrumentError(f"injected failure for {command}")
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
            ":OUTP:OVP CH1,ON",
            ":OUTP:OCP:VAL CH1,0.6",
            ":OUTP:OCP CH1,OFF",
        ])
        self.assertEqual(status.ovp_threshold_v, 6.0)

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
