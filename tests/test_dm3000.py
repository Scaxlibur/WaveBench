import unittest

from wavebench.drivers.dm3000 import DM3000Dmm
from wavebench.errors import DataError


class FakeTransport:
    def __init__(self):
        self.commands = []
        self.function_status = "DCV"

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
            return "0"
        if command == ":MEASure:VOLTage:DC:IMPedance?":
            return "10M"
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
        self.assertTrue(profile.auto_range)
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


if __name__ == "__main__":
    unittest.main()

class DM3058LanCompatibilityTests(unittest.TestCase):
    def test_dm3058_idn_uses_same_common_scpi_query(self):
        transport = FakeTransport()
        transport.query = lambda command: "Rigol Technologies,DM3058,<serial>,<firmware>" if command == "*IDN?" else "0"
        self.assertIn("DM3058", DM3000Dmm(transport).idn())
