from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.errors import TransportIOError
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import (
    BinaryResponseFraming,
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)
from wavebench.transport.pyvisa_transport import PyVisaTransport
from wavebench.transport.rsinstrument_transport import RsInstrumentTransport
from wavebench.transport.serial_transport import SerialTransport


def _retryable_failure(replay: ReplayPolicy) -> TransportIOError:
    return TransportIOError(
        "response did not start after a proven-safe exchange",
        operation="query",
        phase=TransportPhase.READING,
        replay_policy=replay,
        command_transmission=CommandTransmission.SENT,
        response_progress=ResponseProgress.NONE,
        synchronization=Synchronization.PROVEN,
        attempts=1,
    )


class _ReplayablePyVisaSession:
    def __init__(self, *, failure_replay: ReplayPolicy) -> None:
        self.failure_replay = failure_replay
        self.calls = 0
        self.failures_remaining = 1
        self.timeout = 1_000

    def _attempt(self, result: object) -> object:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise _retryable_failure(self.failure_replay)
        return result

    def query(self, command: str) -> str:
        result = "1.0,2.0" if command == "MEAS?" else "1" if command == "*OPC?" else "ok"
        return str(self._attempt(result))

    def query_binary_values(self, _command: str, *, datatype: str, container: type[bytes]) -> bytes:
        assert datatype == "B"
        assert container is bytes
        return bytes(self._attempt(b"data"))


class _ReplayableRsInstrumentSession:
    def __init__(self, *, failure_replay: ReplayPolicy) -> None:
        self.failure_replay = failure_replay
        self.calls = 0
        self.failures_remaining = 1
        self.visa_timeout = 1_000
        self.events = SimpleNamespace(io_events_include_data=True, on_read_handler=None)

    def _attempt(self, result: object) -> object:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise _retryable_failure(self.failure_replay)
        return result

    def query_str(self, _command: str) -> str:
        return str(self._attempt("ok"))

    def query_bin_or_ascii_float_list(self, _command: str) -> list[float]:
        return list(self._attempt([1.0, 2.0]))

    def query_bin_block(self, _command: str) -> bytes:
        return bytes(self._attempt(b"data"))

    def query_opc(self) -> str:
        return str(self._attempt("1"))


def _backend_with_retryable_first_read(
    backend: str,
    *,
    failure_replay: ReplayPolicy,
) -> tuple[object, object]:
    if backend == "pyvisa":
        session = _ReplayablePyVisaSession(failure_replay=failure_replay)
        return (
            PyVisaTransport(
                "TCPIP::example::INSTR",
                object(),
                session,
                CommandLogger(),
                read_retry_attempts=1,
                read_retry_delay_ms=0,
            ),
            session,
        )
    if backend == "rsinstrument":
        session = _ReplayableRsInstrumentSession(failure_replay=failure_replay)
        return (
            RsInstrumentTransport(
                "TCPIP::example::INSTR",
                session,
                CommandLogger(),
                read_retry_attempts=1,
                read_retry_delay_ms=0,
            ),
            session,
        )
    raise AssertionError(f"unexpected backend: {backend}")


def _call_text_query_entry(transport: object, entry: str, replay: ReplayPolicy) -> object:
    if entry == "query":
        return transport.query("TEXT?", replay=replay)  # type: ignore[attr-defined]
    if entry == "query_float_list":
        return transport.query_float_list("MEAS?", replay=replay)  # type: ignore[attr-defined]
    if entry == "query_bin_block":
        return transport.query_bin_block("DATA?", replay=replay)  # type: ignore[attr-defined]
    if entry == "query_opc":
        return transport.query_opc(replay=replay)  # type: ignore[attr-defined]
    raise AssertionError(f"unexpected entry: {entry}")


@pytest.mark.parametrize("backend", ("pyvisa", "rsinstrument"))
@pytest.mark.parametrize(
    ("entry", "expected"),
    (
        ("query", "ok"),
        ("query_float_list", [1.0, 2.0]),
        ("query_bin_block", b"data"),
        ("query_opc", "1"),
    ),
)
@pytest.mark.parametrize(
    ("replay", "expected_calls"),
    (
        (ReplayPolicy.NO_REPLAY, 1),
        (ReplayPolicy.SAFE_TO_REPLAY, 2),
        (ReplayPolicy.READ_CONTINUATION_ONLY, 0),
    ),
)
def test_visa_text_query_entries_have_exact_replay_send_counts(
    backend: str,
    entry: str,
    expected: object,
    replay: ReplayPolicy,
    expected_calls: int,
) -> None:
    transport, session = _backend_with_retryable_first_read(
        backend,
        failure_replay=replay,
    )

    if replay is ReplayPolicy.READ_CONTINUATION_ONLY:
        with pytest.raises(TransportIOError) as raised:
            _call_text_query_entry(transport, entry, replay)
        assert raised.value.attempts == 0
    elif replay is ReplayPolicy.NO_REPLAY:
        with pytest.raises(TransportIOError) as raised:
            _call_text_query_entry(transport, entry, replay)
        assert raised.value.attempts == 1
    else:
        assert _call_text_query_entry(transport, entry, replay) == expected

    assert session.calls == expected_calls  # type: ignore[attr-defined]


class _ReplayableSerialSession:
    def __init__(self, *, failure_replay: ReplayPolicy) -> None:
        self.failure_replay = failure_replay
        self.write_calls = 0
        self.writes: list[bytes] = []
        self.failures_remaining = 1

    def write(self, payload: bytes) -> int:
        self.write_calls += 1
        self.writes.append(payload)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise _retryable_failure(self.failure_replay)
        return len(payload)

    def flush(self) -> None:
        pass

    def read_until(self, _termination: bytes) -> bytes:
        return b"1.0,2.0\n"


def _call_serial_text_query_entry(
    transport: SerialTransport,
    entry: str,
    replay: ReplayPolicy,
) -> object:
    if entry == "query":
        return transport.query("MEAS?", replay=replay)
    if entry == "query_float_list":
        return transport.query_float_list("MEAS?", replay=replay)
    if entry == "query_opc":
        return transport.query_opc(replay=replay)
    raise AssertionError(f"unexpected entry: {entry}")


@pytest.mark.parametrize(
    ("entry", "expected"),
    (
        ("query", "1.0,2.0"),
        ("query_float_list", [1.0, 2.0]),
        ("query_opc", "1.0,2.0"),
    ),
)
@pytest.mark.parametrize(
    ("replay", "expected_calls"),
    (
        (ReplayPolicy.NO_REPLAY, 1),
        (ReplayPolicy.SAFE_TO_REPLAY, 2),
        (ReplayPolicy.READ_CONTINUATION_ONLY, 0),
    ),
)
def test_serial_text_query_entries_have_exact_replay_send_counts(
    entry: str,
    expected: object,
    replay: ReplayPolicy,
    expected_calls: int,
) -> None:
    session = _ReplayableSerialSession(failure_replay=replay)
    transport = SerialTransport(
        "/dev/ttyUSB0",
        session,
        CommandLogger(),
        read_retry_attempts=1,
        read_retry_delay_ms=0,
    )

    if replay is ReplayPolicy.READ_CONTINUATION_ONLY:
        with pytest.raises(TransportIOError) as raised:
            _call_serial_text_query_entry(transport, entry, replay)
        assert raised.value.attempts == 0
    elif replay is ReplayPolicy.NO_REPLAY:
        with pytest.raises(TransportIOError) as raised:
            _call_serial_text_query_entry(transport, entry, replay)
        assert raised.value.attempts == 1
    else:
        assert _call_serial_text_query_entry(transport, entry, replay) == expected

    assert session.write_calls == expected_calls
    assert len(session.writes) == expected_calls


@pytest.mark.parametrize("entry", ("query_bin_block", "query_binary"))
@pytest.mark.parametrize("replay", tuple(ReplayPolicy))
def test_serial_binary_query_entries_reject_before_any_send(
    entry: str,
    replay: ReplayPolicy,
) -> None:
    session = _ReplayableSerialSession(failure_replay=replay)
    transport = SerialTransport("/dev/ttyUSB0", session, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        if entry == "query_bin_block":
            transport.query_bin_block("DATA?", replay=replay)
        else:
            transport.query_binary(
                "DATA?",
                framing=BinaryResponseFraming.DEFINITE_BLOCK,
                max_bytes=16,
                replay=replay,
            )

    assert raised.value.attempts == 0
    assert session.write_calls == 0
