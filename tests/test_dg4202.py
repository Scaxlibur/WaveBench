import unittest

from wavebench.drivers.dg4202 import DG4202Source
from wavebench.errors import DataError, InstrumentError


class FakeTransport:
    def __init__(self):
        self.writes = []
        self.byte_writes = []
        self.error_queue = ['0,"No error"']
        self.state = {
            "out": "ON",
            "func": "SIN",
            "freq": 5000.0,
            "volt": 5.0,
            "unit": "VPP",
            "offs": 0.0,
            "phas": 0.0,
            "mode": "SWE",
            "swe": "ON",
            "apply": '"SIN,5.000000E+03,5.000000E+00,0.000000E+00,0.000000E+00"',
            "duty": 50.0,
        }

    def write(self, command: str) -> None:
        self.writes.append(command)
        if command.startswith(":SOUR2:FREQ:MODE "):
            self.state["mode"] = command.split()[-1]
            self.state["swe"] = "OFF" if self.state["mode"] == "FIX" else "ON"
        elif command.startswith(":SOUR2:FREQ "):
            self.state["freq"] = float(command.split()[-1])
        elif command.startswith(":OUTP2 "):
            self.state["out"] = command.split()[-1]
        elif command.startswith(":SOUR2:FUNC "):
            self.state["func"] = command.split()[-1]
        elif command.startswith(":SOUR2:FUNC:SHAP "):
            self.state["func"] = command.split()[-1]
        elif command.startswith(":SOUR2:VOLT:OFFS "):
            self.state["offs"] = float(command.split()[-1])
        elif command.startswith(":SOUR2:VOLT:UNIT "):
            self.state["unit"] = command.split()[-1]
        elif command.startswith(":SOUR2:VOLT "):
            self.state["volt"] = float(command.split()[-1])
        elif command.startswith(":SOUR2:FUNC:SQU:DCYC "):
            self.state["duty"] = float(command.split()[-1])

    def write_bytes(self, command: bytes) -> None:
        self.byte_writes.append(command)

    def query(self, command: str) -> str:
        if command == "SYST:ERR?":
            if self.error_queue:
                return self.error_queue.pop(0)
            return '0,"No error"'
        mapping = {
            "*IDN?": "Rigol Technologies,DG4202,SN,FW",
            ":OUTP2?": self.state["out"],
            ":SOUR2:FUNC?": self.state["func"],
            ":SOUR2:FREQ?": str(self.state["freq"]),
            ":SOUR2:VOLT?": str(self.state["volt"]),
            ":SOUR2:VOLT:UNIT?": self.state["unit"],
            ":SOUR2:VOLT:OFFS?": str(self.state["offs"]),
            ":SOUR2:PHAS?": str(self.state["phas"]),
            ":SOUR2:FREQ:MODE?": self.state["mode"],
            ":SOUR2:SWE:STAT?": self.state["swe"],
            ":SOUR2:APPL?": self.state["apply"],
            ":SOUR2:FUNC:SQU:DCYC?": str(self.state["duty"]),
            ":SOUR2:FUNC:USER?": '"USER1"',
            ":SOUR2:ARB:SRAT?": "1000000",
        }
        if command not in mapping:
            self.error_queue.append('-113,"Undefined header"')
            return ""
        return mapping[command]

    def close(self) -> None:
        pass


class DG4202Tests(unittest.TestCase):
    def test_rejects_channels_outside_dual_channel_range_before_io(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)

        for channel in (0, 3):
            with self.subTest(channel=channel):
                with self.assertRaisesRegex(DataError, "DG4000 channel must be 1 or 2"):
                    driver.get_status(channel)

        self.assertEqual(transport.writes, [])

    def test_get_status_reads_basic_fields(self):
        driver = DG4202Source(transport=FakeTransport(), check_errors_after_ops=True)
        status = driver.get_status(2)
        self.assertEqual(status.channel, 2)
        self.assertEqual(status.output, "ON")
        self.assertEqual(status.frequency_mode, "SWE")
        self.assertEqual(status.frequency_hz, 5000.0)
        self.assertEqual(status.square_duty_cycle_percent, 50.0)

    def test_set_frequency_switches_to_fix_mode(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        status = driver.set_frequency(2, 1000.0, ensure_fix_mode=True, check_errors=True)
        self.assertEqual(transport.writes[0], ":SOUR2:FREQ:MODE FIX")
        self.assertEqual(transport.writes[1], ":SOUR2:FREQ 1000")
        self.assertEqual(status.frequency_mode, "FIX")
        self.assertEqual(status.frequency_hz, 1000.0)
        self.assertEqual(status.sweep_enabled, "OFF")

    def test_set_output_writes_requested_state(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        status = driver.set_output(2, False, check_errors=True)
        self.assertEqual(transport.writes[0], ":OUTP2 OFF")
        self.assertEqual(status.output, "OFF")

    def test_output_on_rejects_preexisting_error_before_any_write(self):
        transport = FakeTransport()
        transport.error_queue = ['-222,"Data out of range"', '0,"No error"']
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)

        with self.assertRaisesRegex(InstrumentError, "Data out of range"):
            driver.set_output(2, True, check_errors=True)

        self.assertEqual(transport.state["out"], "ON")
        self.assertEqual(transport.writes, [])

    def test_latched_emergency_output_off_does_not_require_full_status_snapshot(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        driver._configuration_writes_blocked = True

        original_query = transport.query

        def query(command: str) -> str:
            if command == ":SOUR2:FUNC?":
                raise InstrumentError("injected query failure")
            return original_query(command)

        transport.query = query

        with self.assertRaisesRegex(InstrumentError, "injected query failure"):
            driver.set_output(2, False, check_errors=True)

        self.assertEqual(transport.state["out"], "OFF")
        self.assertIn(":OUTP2 OFF", transport.writes)

    def test_set_function_writes_normalized_function(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        status = driver.set_function(2, "square", check_errors=True)
        self.assertEqual(transport.writes[0], ":SOUR2:FUNC SQU")
        self.assertEqual(status.function, "SQU")


    def test_set_function_accepts_triangle_alias_as_ramp(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        status = driver.set_function(2, "triangle", check_errors=True)
        self.assertEqual(transport.writes[0], ":SOUR2:FUNC RAMP")
        self.assertEqual(status.function, "RAMP")

    def test_set_amplitude_vpp_writes_unit_and_value(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        status = driver.set_amplitude_vpp(2, 3.3, check_errors=True)
        self.assertEqual(transport.writes[0], ":SOUR2:VOLT:UNIT VPP")
        self.assertEqual(transport.writes[1], ":SOUR2:VOLT 3.3")
        self.assertEqual(status.amplitude, 3.3)
        self.assertEqual(status.amplitude_unit, "VPP")

    def test_set_square_duty_cycle_writes_percent(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)
        status = driver.set_square_duty_cycle(2, 25.0, check_errors=True)
        self.assertEqual(transport.writes[0], ":SOUR2:FUNC:SQU:DCYC 25")
        self.assertEqual(status.square_duty_cycle_percent, 25.0)


    def test_upload_dg4000_dac14_block_writes_binary_then_user_function(self):
        from wavebench.arbitrary import build_dg4000_dac14_binary_block, load_arbitrary_waveform
        import numpy as np
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tri.npy"
            np.save(path, np.array([-1.0, 0.0, 1.0, 0.0]))
            waveform = load_arbitrary_waveform(path)
            block = build_dg4000_dac14_binary_block(waveform)
            transport = FakeTransport()
            transport.state["out"] = "OFF"
            transport.state["mode"] = "FIX"
            transport.state["swe"] = "OFF"
            driver = DG4202Source(transport=transport, check_errors_after_ops=True)

            status = driver.upload_dg4000_dac14_block(
                channel=2,
                block=block,
                playback_frequency_hz=1000.0,
                amplitude_vpp=1.2,
                offset_v=0.1,
                output_on=True,
            )

        self.assertEqual(transport.writes[0], "*CLS")
        self.assertEqual(len(transport.byte_writes), 1)
        self.assertTrue(transport.byte_writes[0].startswith(b":DATA:DAC VOLATILE,#"))
        self.assertIn(":SOUR2:FREQ 1000", transport.writes)
        self.assertIn(":SOUR2:VOLT:UNIT VPP", transport.writes)
        self.assertIn(":SOUR2:VOLT 1.2", transport.writes)
        self.assertIn(":SOUR2:VOLT:OFFS 0.1", transport.writes)
        self.assertIn(":SOUR2:FUNC:SHAP USER", transport.writes)
        self.assertIn(":OUTP2 ON", transport.writes)
        self.assertEqual(status.function, "USER")
        self.assertEqual(status.frequency_hz, 1000.0)

    def test_probe_arbitrary_queries_uses_query_only_candidates_and_reads_errors(self):
        transport = FakeTransport()
        driver = DG4202Source(transport=transport, check_errors_after_ops=True)

        results = driver.probe_arbitrary_queries(2)

        commands = [item.command for item in results]
        self.assertIn(":SOUR2:FUNC:USER?", commands)
        self.assertIn(":SOUR2:ARB:SRAT?", commands)
        self.assertTrue(all(command.endswith("?") for command in commands))
        accepted = {item.label: item.accepted for item in results}
        self.assertTrue(accepted["user_function"])
        self.assertTrue(accepted["arb_sample_rate"])
        self.assertFalse(accepted["source_data_catalog"])

    def test_probe_arbitrary_queries_rejects_non_query_candidate(self):
        driver = DG4202Source(transport=FakeTransport(), check_errors_after_ops=True)
        with self.assertRaises(Exception):
            driver.probe_arbitrary_queries(2, candidates=(("bad", ":SOUR{channel}:FUNC ARB"),))

    def test_set_square_duty_cycle_rejects_out_of_range_values(self):
        driver = DG4202Source(transport=FakeTransport(), check_errors_after_ops=True)
        with self.assertRaises(Exception):
            driver.set_square_duty_cycle(2, 100.0)


if __name__ == "__main__":
    unittest.main()
