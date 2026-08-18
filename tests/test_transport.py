
import unittest

from wavebench.errors import TransportIOError
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)
from wavebench.transport.pyvisa_transport import PyVisaTransport


class FakePyVisaSession:
    def __init__(self):
        self.queries = []
        self.raw_writes = []
        self.reads = []

    def query(self, command: str) -> str:
        self.queries.append(command)
        if command == "VALUES?":
            return "1, 2.5, 3"
        if command == "*OPC?":
            return "1"
        if command == "EMPTY_THEN_READ?":
            return ""
        return ""

    def read(self) -> str:
        self.reads.append("read")
        return "DELAYED"

    def write_raw(self, command: bytes) -> None:
        self.raw_writes.append(command)

    def query_binary_values(self, command: str, datatype: str = "B", container=list):
        self.queries.append(command)
        if datatype == "B":
            return container([1, 2, 3])
        raise RuntimeError("binary float path unavailable")

    def close(self) -> None:
        pass


class FailingQuerySession(FakePyVisaSession):
    def query(self, command: str) -> str:
        raise TimeoutError("simulated timeout")


class FlakyQuerySession(FakePyVisaSession):
    def __init__(self):
        super().__init__()
        self.failures_remaining = 1

    def query(self, command: str) -> str:
        self.queries.append(command)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TimeoutError("transient timeout")
        return "OK"


class ProvenReplayableQuerySession(FlakyQuerySession):
    def query(self, command: str) -> str:
        self.queries.append(command)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TransportIOError(
                "response did not start and synchronization is proven",
                operation="query",
                phase=TransportPhase.READING,
                replay_policy=ReplayPolicy.SAFE_TO_REPLAY,
                command_transmission=CommandTransmission.SENT,
                response_progress=ResponseProgress.NONE,
                synchronization=Synchronization.PROVEN,
                attempts=1,
            )
        return "OK"


class ExhaustedProvenReplayableQuerySession(ProvenReplayableQuerySession):
    def __init__(self):
        super().__init__()
        self.failures_remaining = 2


class FailingBinaryQuerySession(FakePyVisaSession):
    def __init__(self):
        super().__init__()
        self.binary_attempts = 0

    def query_binary_values(self, command: str, datatype: str = "B", container=list):
        self.queries.append(command)
        self.binary_attempts += 1
        raise TimeoutError("binary transfer interrupted")


class FailingFloatListSession(FakePyVisaSession):
    def query(self, command: str) -> str:
        self.queries.append(command)
        raise TimeoutError("float-list transfer interrupted")


class FailingWriteSession(FakePyVisaSession):
    def __init__(self):
        super().__init__()
        self.writes = []

    def write(self, command: str) -> None:
        self.writes.append(command)
        raise OSError("simulated write failure")


class FakeResourceManager:
    def close(self) -> None:
        pass


class PyVisaTransportTests(unittest.TestCase):
    def test_query_float_list_falls_back_to_ascii_values(self):
        transport = PyVisaTransport("TCPIP::x::INSTR", FakeResourceManager(), FakePyVisaSession(), CommandLogger())

        self.assertEqual(transport.query_float_list("VALUES?"), [1.0, 2.5, 3.0])

    def test_query_does_not_continue_reading_after_empty_response(self):
        session = FakePyVisaSession()
        transport = PyVisaTransport("TCPIP::x::INSTR", FakeResourceManager(), session, CommandLogger())

        self.assertEqual(transport.query("EMPTY_THEN_READ?"), "")
        self.assertEqual(session.reads, [])

    def test_query_bin_block_and_opc(self):
        logger = CommandLogger()
        transport = PyVisaTransport("TCPIP::x::INSTR", FakeResourceManager(), FakePyVisaSession(), logger)

        self.assertEqual(transport.query_bin_block("DATA?"), b"\x01\x02\x03")
        self.assertEqual(transport.query_opc(), "1")
        self.assertTrue(
            any(
                entry.direction == "telemetry"
                and "operation=query_binary" in entry.text
                and "elapsed_ms=" in entry.text
                and "bytes=3" in entry.text
                for entry in logger.entries
            )
        )

    def test_binary_block_failure_is_not_replayed_in_same_session(self):
        session = FailingBinaryQuerySession()
        logger = CommandLogger()
        transport = PyVisaTransport(
            "TCPIP::x::INSTR",
            FakeResourceManager(),
            session,
            logger,
            read_retry_attempts=3,
            read_retry_delay_ms=0,
        )

        with self.assertRaises(TransportIOError) as raised:
            transport.query_bin_block("DATA?")

        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(raised.exception.replay_policy, ReplayPolicy.NO_REPLAY)
        self.assertEqual(session.binary_attempts, 1)
        self.assertEqual(session.queries, ["DATA?"])
        self.assertFalse(any(entry.direction == "retry" for entry in logger.entries))
        self.assertTrue(
            any(
                entry.direction == "telemetry"
                and "operation=query_binary" in entry.text
                and "status=failed" in entry.text
                and "replay=no_replay" in entry.text
                for entry in logger.entries
            )
        )

    def test_binary_float_failure_is_not_replayed_or_fallen_back_to_ascii(self):
        session = FailingFloatListSession()
        transport = PyVisaTransport(
            "TCPIP::x::INSTR",
            FakeResourceManager(),
            session,
            CommandLogger(),
            read_retry_attempts=3,
            read_retry_delay_ms=0,
        )

        with self.assertRaises(TransportIOError):
            transport.query_float_list("VALUES?")

        self.assertEqual(session.queries, ["VALUES?"])

    def test_query_timeout_is_wrapped_as_instrument_error(self):
        transport = PyVisaTransport("TCPIP::x::INSTR", FakeResourceManager(), FailingQuerySession(), CommandLogger())

        with self.assertRaisesRegex(TransportIOError, "pyvisa query failed") as raised:
            transport.query("OUTP?")
        self.assertEqual(raised.exception.attempts, 1)

    def test_safe_to_replay_does_not_retry_an_opaque_backend_timeout(self):
        session = FlakyQuerySession()
        transport = PyVisaTransport(
            "TCPIP::x::INSTR",
            FakeResourceManager(),
            session,
            CommandLogger(),
            read_retry_attempts=1,
            read_retry_delay_ms=0,
        )

        with self.assertRaises(TransportIOError) as raised:
            transport.query("OUTP?", replay=ReplayPolicy.SAFE_TO_REPLAY)

        self.assertEqual(raised.exception.synchronization.value, "unproven")
        self.assertEqual(session.queries, ["OUTP?"])

    def test_safe_to_replay_retries_only_after_structured_sync_proof(self):
        session = ProvenReplayableQuerySession()
        transport = PyVisaTransport(
            "TCPIP::x::INSTR",
            FakeResourceManager(),
            session,
            CommandLogger(),
            read_retry_attempts=1,
            read_retry_delay_ms=0,
        )

        self.assertEqual(
            transport.query("OUTP?", replay=ReplayPolicy.SAFE_TO_REPLAY),
            "OK",
        )
        self.assertEqual(session.queries, ["OUTP?", "OUTP?"])

    def test_safe_to_replay_reports_all_transmitted_attempts(self):
        session = ExhaustedProvenReplayableQuerySession()
        transport = PyVisaTransport(
            "TCPIP::x::INSTR",
            FakeResourceManager(),
            session,
            CommandLogger(),
            read_retry_attempts=1,
            read_retry_delay_ms=0,
        )

        with self.assertRaises(TransportIOError) as raised:
            transport.query("OUTP?", replay=ReplayPolicy.SAFE_TO_REPLAY)

        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(session.queries, ["OUTP?", "OUTP?"])

    def test_write_failure_is_wrapped_as_instrument_error(self):
        session = FailingWriteSession()
        transport = PyVisaTransport("TCPIP::x::INSTR", FakeResourceManager(), session, CommandLogger())

        with self.assertRaisesRegex(TransportIOError, "pyvisa write failed") as raised:
            transport.write("VOLT 1")
        self.assertEqual(session.writes, ["VOLT 1"])
        self.assertEqual(raised.exception.command_transmission.value, "unknown")
        self.assertEqual(raised.exception.synchronization.value, "unproven")

    def test_unsupported_continuation_fails_before_send(self):
        session = FakePyVisaSession()
        transport = PyVisaTransport(
            "TCPIP::x::INSTR",
            FakeResourceManager(),
            session,
            CommandLogger(),
        )

        with self.assertRaises(TransportIOError) as raised:
            transport.query("OUTP?", replay=ReplayPolicy.READ_CONTINUATION_ONLY)

        self.assertEqual(raised.exception.attempts, 0)
        self.assertEqual(raised.exception.command_transmission.value, "not_sent")
        self.assertEqual(session.queries, [])


if __name__ == "__main__":
    unittest.main()
