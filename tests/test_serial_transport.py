import unittest
from unittest.mock import patch

from wavebench.config import DmmConfig
from wavebench.errors import TransportIOError
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.serial_transport import SerialTransport


class FakeSerialSession:
    def __init__(self):
        self.writes = []
        self.closed = False
        self.read_until_expected = []

    def write(self, payload: bytes):
        self.writes.append(payload)

    def flush(self):
        pass

    def readline(self) -> bytes:
        return b"1.23e+00\n"

    def read_until(self, expected: bytes) -> bytes:
        self.read_until_expected.append(expected)
        return b"1.23e+00\n"

    def close(self):
        self.closed = True


class SerialTransportTests(unittest.TestCase):
    def test_open_applies_configured_framing_and_disables_flow_control(self):
        session = FakeSerialSession()
        config = DmmConfig(
            driver="dm3058",
            resource="/dev/serial/by-id/usb-test",
            backend="serial",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout_ms=3000,
            write_termination="crlf",
            read_termination="lf",
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )

        with patch("serial.Serial", return_value=session) as serial_open:
            transport = SerialTransport.open(config)

        serial_open.assert_called_once_with(
            port="/dev/serial/by-id/usb-test",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=3.0,
            write_timeout=3.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.assertEqual(transport.write_termination, b"\r\n")
        self.assertEqual(transport.read_termination, b"\n")

    def test_open_rejects_invalid_termination_before_opening_session(self):
        config = DmmConfig(
            "dm3058",
            "/dev/serial/by-id/usb-test",
            "serial",
            9600,
            8,
            "N",
            1,
            3000,
            write_termination="invalid",
        )

        with patch("serial.Serial") as serial_open:
            with self.assertRaisesRegex(Exception, "terminations must be lf or crlf"):
                SerialTransport.open(config)

        serial_open.assert_not_called()

    def test_write_appends_newline(self):
        session = FakeSerialSession()
        transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())
        transport.write("*IDN?")
        self.assertEqual(session.writes, [b"*IDN?\n"])

    def test_query_reads_one_ascii_line(self):
        session = FakeSerialSession()
        transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())
        self.assertEqual(transport.query("MEAS?"), "1.23e+00")
        self.assertEqual(session.writes, [b"MEAS?\n"])

    def test_query_float_list_uses_ascii_parser(self):
        session = FakeSerialSession()
        transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())
        self.assertEqual(transport.query_float_list("MEAS?"), [1.23])

    def test_dm3058_query_writes_crlf_and_reads_lf(self):
        session = FakeSerialSession()
        transport = SerialTransport(
            "/dev/ttyUSB0",
            session,
            CommandLogger(),
            write_termination=b"\r\n",
            read_termination=b"\n",
        )

        self.assertEqual(transport.query("*IDN?"), "1.23e+00")
        self.assertEqual(session.writes, [b"*IDN?\r\n"])
        self.assertEqual(session.read_until_expected, [b"\n"])

    def test_query_reads_configured_crlf_response(self):
        session = FakeSerialSession()
        session.read_until = lambda expected: b"Rigol Technologies,DM3058\r\n"
        transport = SerialTransport(
            "/dev/ttyUSB0",
            session,
            CommandLogger(),
            read_termination=b"\r\n",
        )

        self.assertEqual(transport.query("*IDN?"), "Rigol Technologies,DM3058")

    def test_query_rejects_empty_response_as_timeout(self):
        session = FakeSerialSession()
        session.read_until = lambda expected: b""
        transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())

        with self.assertRaisesRegex(TransportIOError, "timed out") as raised:
            transport.query("*IDN?")
        self.assertEqual(raised.exception.attempts, 1)

    def test_write_replaces_existing_line_ending_with_configured_termination(self):
        session = FakeSerialSession()
        transport = SerialTransport(
            "/dev/ttyUSB0",
            session,
            CommandLogger(),
            write_termination=b"\r\n",
        )

        transport.write("*IDN?\n")

        self.assertEqual(session.writes, [b"*IDN?\r\n"])

    def test_query_rejects_partial_response_without_read_termination(self):
        session = FakeSerialSession()
        session.read_until = lambda expected: b"Rigol Technologies,DM3058"
        transport = SerialTransport(
            "/dev/ttyUSB0",
            session,
            CommandLogger(),
            read_termination=b"\n",
        )

        with self.assertRaisesRegex(TransportIOError, "response ended before"):
            transport.query("*IDN?")

    def test_write_rejects_short_serial_write(self):
        session = FakeSerialSession()
        session.write = lambda payload: len(payload) - 1
        transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())

        with self.assertRaisesRegex(TransportIOError, "serial write failed"):
            transport.write("*IDN?")

    def test_query_retries_only_after_a_proven_zero_byte_transmission(self):
        session = FakeSerialSession()
        write_results = [0, None]

        def write(payload):
            session.writes.append(payload)
            return write_results.pop(0)

        session.write = write
        transport = SerialTransport(
            "/dev/ttyUSB0",
            session,
            CommandLogger(),
            read_retry_attempts=1,
            read_retry_delay_ms=0,
        )

        self.assertEqual(
            transport.query("MEAS?", replay=ReplayPolicy.SAFE_TO_REPLAY),
            "1.23e+00",
        )
        self.assertEqual(session.writes, [b"MEAS?\n", b"MEAS?\n"])

    def test_safe_query_does_not_retry_after_a_sent_command_times_out(self):
        session = FakeSerialSession()
        session.read_until = lambda expected: b""
        transport = SerialTransport(
            "/dev/ttyUSB0",
            session,
            CommandLogger(),
            read_retry_attempts=2,
            read_retry_delay_ms=0,
        )

        with self.assertRaises(TransportIOError) as raised:
            transport.query("MEAS?", replay=ReplayPolicy.SAFE_TO_REPLAY)

        self.assertEqual(raised.exception.command_transmission.value, "sent")
        self.assertEqual(raised.exception.response_progress.value, "none")
        self.assertEqual(raised.exception.synchronization.value, "unproven")
        self.assertEqual(session.writes, [b"MEAS?\n"])

    def test_continuation_rejects_before_serial_write(self):
        session = FakeSerialSession()
        transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())

        with self.assertRaises(TransportIOError) as raised:
            transport.query("MEAS?", replay=ReplayPolicy.READ_CONTINUATION_ONLY)

        self.assertEqual(raised.exception.attempts, 0)
        self.assertEqual(session.writes, [])


if __name__ == "__main__":
    unittest.main()
