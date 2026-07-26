import unittest

from wavebench.drivers.dm3000 import DM3000Dmm
from wavebench.errors import DataError, InstrumentError


class FakeTransport:
    def __init__(self):
        self.commands = []
        self.function_status = "DCV"
        self.dcv_range = "0"
        self.acv_range = "0"
        self.dcv_impedance = "10M"
        self.calculation_function = "NONE"

    def query(self, command: str) -> str:
        self.commands.append(command)
        if command == "*IDN?":
            return "RIGOL TECHNOLOGIES,DM3068,DM3A000000000,01.00"
        if command == ":FUNCtion?":
            return self.function_status
        if command == ":MEASure:VOLTage:DC?":
            return "1.234500e+00"
        if command == ":MEASure:RESistance?":
            return "9.876000e+03"
        if command == ":MEASure:VOLTage:DC:RANGe?":
            return self.dcv_range
        if command == ":MEASure:VOLTage:AC:RANGe?":
            return self.acv_range
        if command == ":MEASure:VOLTage:DC:IMPedance?":
            return self.dcv_impedance
        trigger_responses = {
            ":TRIGger:SOURce?": "AUTO",
            ":TRIGger:AUTO:INTerval?": "400ms",
            ":TRIGger:AUTO:HOLD?": "OFF",
            ":TRIGger:AUTO:HOLD:SENSitivity?": "1",
            ":TRIGger:SINGle?": "1",
            ":TRIGger:EXT?": "RISE",
            ":TRIGger:VMComplete:POLar?": "POS",
            ":TRIGger:VMComplete:PULSewidth?": "7ms",
            ":CALCulate:FUNCtion?": self.calculation_function,
            ":CALCulate:STATistic:COUNt?": "0",
            ":CALCulate:DB:REFerence?": "0",
            ":CALCulate:DBM:REFerence?": "600",
            ":CALCulate:STATistic:AVERage?": "1.25",
            ":CALCulate:STATistic:MIN?": "1.0",
            ":CALCulate:STATistic:MAX?": "1.5",
        }
        if command in trigger_responses:
            return trigger_responses[command]
        if command == ":MEASure:RESistance:RANGe?":
            return "6"
        return "bad"

    def write(self, command: str) -> None:
        self.commands.append(command)
        mapping = {
            ":FUNCtion:VOLTage:DC": "DCV",
            ":FUNCtion:VOLTage:AC": "ACV",
            ":FUNCtion:CURRent:DC": "DCI",
            ":FUNCtion:CURRent:AC": "ACI",
            ":FUNCtion:RESistance": "RESISTANCE",
            ":FUNCtion:FRESistance": "FRESISTANCE",
            ":FUNCtion:FREQuency": "FREQUENCY",
            ":FUNCtion:PERiod": "PERIOD",
            ":FUNCtion:CONTinuity": "CONTINUITY",
            ":FUNCtion:DIODe": "DIODE",
            ":FUNCtion:CAPacitance": "CAPACITANCE",
        }
        if command in mapping:
            self.function_status = mapping[command]
        if command.startswith(":MEASure:VOLTage:DC "):
            self.dcv_range = command.rsplit(" ", 1)[1]
        if command.startswith(":MEASure:VOLTage:AC "):
            self.acv_range = command.rsplit(" ", 1)[1]
        if command.startswith(":MEASure:VOLTage:DC:IMPedance "):
            self.dcv_impedance = command.rsplit(" ", 1)[1]

    def close(self):
        pass


class DM3000DriverTests(unittest.TestCase):
    def test_idn_uses_common_scpi_query(self):
        transport = FakeTransport()
        dmm = DM3000Dmm(transport)
        self.assertIn("DM3068", dmm.idn())
        self.assertEqual(transport.commands, ["*IDN?"])

    def test_read_dcv_parses_value_and_unit(self):
        transport = FakeTransport()
        reading = DM3000Dmm(transport).read("dcv")
        self.assertEqual(reading.function, "dcv")
        self.assertEqual(reading.value, 1.2345)
        self.assertEqual(reading.unit, "V")
        self.assertEqual(transport.commands, [":MEASure:VOLTage:DC?"])

    def test_read_alias_and_resistance_unit(self):
        transport = FakeTransport()
        reading = DM3000Dmm(transport).read("ohm")
        self.assertEqual(reading.function, "res")
        self.assertEqual(reading.value, 9876.0)
        self.assertEqual(reading.unit, "ohm")

    def test_measurement_profile_is_query_only_and_typed(self):
        transport = FakeTransport()

        profile = DM3000Dmm(transport).measurement_profile()

        self.assertEqual(profile.function, "dcv")
        self.assertEqual(profile.range_code, 0)
        self.assertIsNone(profile.auto_range)
        self.assertEqual(profile.impedance, "10M")
        self.assertEqual(
            transport.commands,
            [
                ":FUNCtion?",
                ":MEASure:VOLTage:DC:RANGe?",
                ":MEASure:VOLTage:DC:IMPedance?",
            ],
        )

    def test_measurement_profile_omits_unsupported_range_query(self):
        transport = FakeTransport()
        transport.function_status = "CONT"

        profile = DM3000Dmm(transport).measurement_profile()

        self.assertEqual(profile.function, "continuity")
        self.assertIsNone(profile.range_code)
        self.assertIsNone(profile.auto_range)
        self.assertIsNone(profile.impedance)
        self.assertEqual(transport.commands, [":FUNCtion?"])

    def test_trigger_status_is_query_only_and_parses_units(self):
        transport = FakeTransport()

        status = DM3000Dmm(transport).trigger_status()

        self.assertEqual(status.source, "AUTO")
        self.assertEqual(status.auto_interval_s, 0.4)
        self.assertFalse(status.auto_hold)
        self.assertEqual(status.auto_hold_sensitivity, 1)
        self.assertEqual(status.single_count, 1)
        self.assertEqual(status.external_slope, "RISE")
        self.assertEqual(status.vmc_polarity, "POS")
        self.assertEqual(status.vmc_pulse_width_s, 0.007)
        self.assertEqual(
            transport.commands,
            [
                ":TRIGger:SOURce?",
                ":TRIGger:AUTO:INTerval?",
                ":TRIGger:AUTO:HOLD?",
                ":TRIGger:AUTO:HOLD:SENSitivity?",
                ":TRIGger:SINGle?",
                ":TRIGger:EXT?",
                ":TRIGger:VMComplete:POLar?",
                ":TRIGger:VMComplete:PULSewidth?",
            ],
        )

    def test_calculation_status_accepts_all_documented_modes(self):
        for raw in ("NONE", "NULL", "DB", "DBM", "AVERAGE", "MIN", "MAX", "TOTAL", "LIMIT"):
            with self.subTest(raw=raw):
                transport = FakeTransport()
                transport.calculation_function = raw

                status = DM3000Dmm(transport).calculation_status()

                self.assertEqual(status.function, raw.lower())
                self.assertEqual(status.statistic_count, 0)
                self.assertEqual(status.db_reference, 0.0)
                self.assertEqual(status.dbm_reference_ohm, 600.0)
                self.assertEqual(
                    transport.commands,
                    [
                        ":CALCulate:FUNCtion?",
                        ":CALCulate:STATistic:COUNt?",
                        ":CALCulate:DB:REFerence?",
                        ":CALCulate:DBM:REFerence?",
                    ],
                )

    def test_calculation_statistics_requires_matching_active_calculation(self):
        transport = FakeTransport()
        transport.calculation_function = "AVERAGE"
        original_query = transport.query

        def query(command: str) -> str:
            if command == ":CALCulate:STATistic:COUNt?":
                transport.commands.append(command)
                return "3"
            return original_query(command)

        transport.query = query

        statistics = DM3000Dmm(transport).calculation_statistics("average")

        self.assertEqual(statistics.function, "average")
        self.assertEqual(statistics.value, 1.25)
        self.assertEqual(statistics.count, 3)
        self.assertEqual(
            transport.commands,
            [
                ":CALCulate:FUNCtion?",
                ":CALCulate:STATistic:AVERage?",
                ":CALCulate:STATistic:COUNt?",
            ],
        )

    def test_calculation_statistics_rejects_nonmatching_active_calculation_before_statistic_query(self):
        transport = FakeTransport()

        with self.assertRaisesRegex(InstrumentError, "requires active function average"):
            DM3000Dmm(transport).calculation_statistics("average")

        self.assertEqual(transport.commands, [":CALCulate:FUNCtion?"])

    def test_trigger_status_rejects_invalid_unit_response(self):
        transport = FakeTransport()
        original_query = transport.query
        transport.query = lambda command: (
            "400" if command == ":TRIGger:AUTO:INTerval?" else original_query(command)
        )

        with self.assertRaisesRegex(DataError, "trigger auto interval"):
            DM3000Dmm(transport).trigger_status()

    def test_trigger_status_rejects_out_of_contract_discrete_response(self):
        transport = FakeTransport()
        original_query = transport.query
        transport.query = lambda command: (
            "UNKNOWN" if command == ":TRIGger:SOURce?" else original_query(command)
        )

        with self.assertRaisesRegex(DataError, "unsupported DMM trigger source"):
            DM3000Dmm(transport).trigger_status()

    def test_calculation_status_rejects_nonfinite_reference(self):
        transport = FakeTransport()
        original_query = transport.query
        transport.query = lambda command: (
            "nan" if command == ":CALCulate:DB:REFerence?" else original_query(command)
        )

        with self.assertRaisesRegex(DataError, "non-finite.*dB reference"):
            DM3000Dmm(transport).calculation_status()

    def test_unsupported_function_is_rejected_before_io(self):
        with self.assertRaisesRegex(DataError, "unsupported DMM function"):
            DM3000Dmm(FakeTransport()).read("temperature")

    def test_function_status_reads_and_normalizes_scpi_response(self):
        transport = FakeTransport()
        transport.function_status = "RESISTANCE"
        status = DM3000Dmm(transport).function_status()
        self.assertEqual(status, "res")
        self.assertEqual(transport.commands, [":FUNCtion?"])

    def test_function_status_accepts_dm3058_abbreviations(self):
        cases = {
            "RES": "res",
            "CONT": "continuity",
            "FREQ": "freq",
            "PERI": "period",
            "CAP": "cap",
            "2WR": "res",
            "FRES": "fres",
            "4WR": "fres",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                transport = FakeTransport()
                transport.function_status = raw
                self.assertEqual(DM3000Dmm(transport).function_status(), expected)

    def test_set_function_writes_and_returns_normalized_status(self):
        transport = FakeTransport()
        dmm = DM3000Dmm(transport)
        status = dmm.set_function("vac")
        self.assertEqual(status, "acv")
        self.assertEqual(transport.commands, [":FUNCtion:VOLTage:AC", ":FUNCtion?"])

    def test_all_function_selectors_send_exact_scpi_and_read_back(self):
        cases = {
            "dcv": ":FUNCtion:VOLTage:DC",
            "acv": ":FUNCtion:VOLTage:AC",
            "dci": ":FUNCtion:CURRent:DC",
            "aci": ":FUNCtion:CURRent:AC",
            "res": ":FUNCtion:RESistance",
            "fres": ":FUNCtion:FRESistance",
            "freq": ":FUNCtion:FREQuency",
            "period": ":FUNCtion:PERiod",
            "continuity": ":FUNCtion:CONTinuity",
            "diode": ":FUNCtion:DIODe",
            "cap": ":FUNCtion:CAPacitance",
        }
        for requested, command in cases.items():
            with self.subTest(requested=requested):
                transport = FakeTransport()
                self.assertEqual(DM3000Dmm(transport).set_function(requested), requested)
                self.assertEqual(transport.commands, [command, ":FUNCtion?"])

    def test_set_function_rejects_unsupported_function_before_io(self):
        transport = FakeTransport()
        with self.assertRaisesRegex(DataError, "unsupported DMM function"):
            DM3000Dmm(transport).set_function("temperature")
        self.assertEqual(transport.commands, [])

    def test_function_status_rejects_unknown_scpi_symbol(self):
        transport = FakeTransport()
        transport.function_status = "MYSTERY"
        with self.assertRaisesRegex(DataError, "unexpected DMM function status"):
            DM3000Dmm(transport).function_status()

    def test_read_rejects_nonfinite_values(self):
        for raw in ("nan", "+inf", "-inf", "1e9999"):
            with self.subTest(raw=raw):
                transport = FakeTransport()
                original_query = transport.query
                transport.query = lambda command, raw=raw: (
                    raw if command == ":MEASure:VOLTage:DC?" else original_query(command)
                )
                with self.assertRaisesRegex(DataError, "non-finite DM3000 reading"):
                    DM3000Dmm(transport).read("dcv")

    def test_set_voltage_range_writes_and_reads_back(self):
        transport = FakeTransport()
        transport.dcv_range = "2"

        result = DM3000Dmm(transport).set_voltage_range("dcv", 1)

        self.assertEqual(result.previous_range_code, 2)
        self.assertEqual(result.range_code, 1)
        self.assertEqual(
            transport.commands,
            [
                ":FUNCtion?",
                ":MEASure:VOLTage:DC:RANGe?",
                ":MEASure:VOLTage:DC 1",
                ":MEASure:VOLTage:DC:RANGe?",
            ],
        )

    def test_set_voltage_range_rejects_high_dcv_range_with_10g_before_write(self):
        transport = FakeTransport()
        transport.dcv_range = "2"
        transport.dcv_impedance = "10G"

        with self.assertRaisesRegex(InstrumentError, "require 10M impedance"):
            DM3000Dmm(transport).set_voltage_range("dcv", 3)

        self.assertEqual(
            transport.commands,
            [
                ":FUNCtion?",
                ":MEASure:VOLTage:DC:RANGe?",
                ":MEASure:VOLTage:DC:IMPedance?",
            ],
        )

    def test_ambiguous_range_write_restores_then_latches_all_configuration_writes(self):
        class AmbiguousFirstRangeWrite(FakeTransport):
            def __init__(self):
                super().__init__()
                self.dcv_range = "2"
                self.range_writes = 0

            def write(self, command: str) -> None:
                if command.startswith(":MEASure:VOLTage:DC "):
                    self.commands.append(command)
                    self.range_writes += 1
                    if self.range_writes == 1:
                        raise OSError("simulated timeout")
                    self.dcv_range = command.rsplit(" ", 1)[1]
                    return
                super().write(command)

        transport = AmbiguousFirstRangeWrite()
        driver = DM3000Dmm(transport)

        with self.assertRaisesRegex(InstrumentError, "write outcome is ambiguous"):
            driver.set_voltage_range("dcv", 1)

        self.assertEqual(transport.dcv_range, "2")
        self.assertTrue(driver.configuration_writes_blocked)
        commands_before = list(transport.commands)
        with self.assertRaisesRegex(InstrumentError, "writes are blocked"):
            driver.set_function("acv")
        with self.assertRaisesRegex(InstrumentError, "writes are blocked"):
            driver.set_dcv_impedance("10G")
        self.assertEqual(transport.commands, commands_before)

    def test_range_restore_failure_latches_writes(self):
        class FailedRangeRestore(FakeTransport):
            def __init__(self):
                super().__init__()
                self.dcv_range = "2"
                self.range_writes = 0

            def write(self, command: str) -> None:
                if command.startswith(":MEASure:VOLTage:DC "):
                    self.commands.append(command)
                    self.range_writes += 1
                    if self.range_writes == 1:
                        return
                    raise OSError("simulated restore timeout")
                super().write(command)

        driver = DM3000Dmm(FailedRangeRestore())

        with self.assertRaisesRegex(InstrumentError, "restoration is ambiguous"):
            driver.set_voltage_range("dcv", 1)

        self.assertTrue(driver.configuration_writes_blocked)

    def test_set_voltage_range_rejects_wrong_function_before_write(self):
        transport = FakeTransport()
        transport.function_status = "ACV"

        with self.assertRaisesRegex(InstrumentError, "requires active function dcv"):
            DM3000Dmm(transport).set_voltage_range("dcv", 1)

        self.assertEqual(transport.commands, [":FUNCtion?"])

    def test_set_dcv_impedance_is_range_gated(self):
        transport = FakeTransport()
        transport.dcv_range = "3"

        with self.assertRaisesRegex(InstrumentError, "requires range code 0, 1, or 2"):
            DM3000Dmm(transport).set_dcv_impedance("10G")

        self.assertEqual(
            transport.commands,
            [":FUNCtion?", ":MEASure:VOLTage:DC:RANGe?"],
        )

    def test_set_dcv_impedance_writes_and_reads_back(self):
        transport = FakeTransport()
        transport.dcv_range = "2"

        result = DM3000Dmm(transport).set_dcv_impedance("10g")

        self.assertEqual(result.previous_impedance, "10M")
        self.assertEqual(result.impedance, "10G")
        self.assertEqual(result.range_code, 2)

    def test_ambiguous_impedance_write_restores_then_latches(self):
        class AmbiguousFirstImpedanceWrite(FakeTransport):
            def __init__(self):
                super().__init__()
                self.dcv_range = "2"
                self.impedance_writes = 0

            def write(self, command: str) -> None:
                if command.startswith(":MEASure:VOLTage:DC:IMPedance "):
                    self.commands.append(command)
                    self.impedance_writes += 1
                    if self.impedance_writes == 1:
                        raise OSError("simulated timeout")
                    self.dcv_impedance = command.rsplit(" ", 1)[1]
                    return
                super().write(command)

        transport = AmbiguousFirstImpedanceWrite()
        driver = DM3000Dmm(transport)

        with self.assertRaisesRegex(InstrumentError, "write outcome is ambiguous"):
            driver.set_dcv_impedance("10G")

        self.assertEqual(transport.dcv_impedance, "10M")
        self.assertTrue(driver.configuration_writes_blocked)

    def test_impedance_restore_failure_latches_writes(self):
        class FailedImpedanceRestore(FakeTransport):
            def __init__(self):
                super().__init__()
                self.dcv_range = "2"
                self.impedance_writes = 0

            def write(self, command: str) -> None:
                if command.startswith(":MEASure:VOLTage:DC:IMPedance "):
                    self.commands.append(command)
                    self.impedance_writes += 1
                    if self.impedance_writes == 1:
                        return
                    raise OSError("simulated restore timeout")
                super().write(command)

        driver = DM3000Dmm(FailedImpedanceRestore())

        with self.assertRaisesRegex(InstrumentError, "restoration is ambiguous"):
            driver.set_dcv_impedance("10G")

        self.assertTrue(driver.configuration_writes_blocked)


if __name__ == "__main__":
    unittest.main()

class DM3058LanCompatibilityTests(unittest.TestCase):
    def test_dm3058_idn_uses_same_common_scpi_query(self):
        transport = FakeTransport()
        transport.query = lambda command: "Rigol Technologies,DM3058,<serial>,<firmware>" if command == "*IDN?" else "0"
        self.assertIn("DM3058", DM3000Dmm(transport).idn())
