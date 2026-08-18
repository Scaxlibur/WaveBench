from __future__ import annotations

from types import SimpleNamespace

import pytest

from wavebench.errors import TransportIOError
from wavebench.logging import CommandLogger
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.rsinstrument_transport import (
    RsInstrumentTransport,
    _open_rsinstrument_session,
)


class FakeEvents:
    def __init__(self):
        self.io_events_include_data = True
        self.on_read_handler = None


class FakeSession:
    def __init__(self):
        self.visa_timeout = 1000
        self.events = FakeEvents()
        self.float_values = [1.0, 2.0]
        self.binary_data = b"abc"
        self.fail_float = False

    def query_bin_or_ascii_float_list(self, command):
        if self.fail_float:
            raise TimeoutError("interrupted")
        handler = self.events.on_read_handler
        if handler is not None:
            handler(
                SimpleNamespace(
                    transferred_size=16,
                    total_size=16,
                    chunk_ix=0,
                    end_of_transfer=True,
                )
            )
        return self.float_values

    def query_bin_block(self, command):
        return self.binary_data


class FakePyVisaSession:
    def __init__(self):
        self.timeout = 1000
        self.fail = False

    def query(self, command):
        if self.fail:
            raise TimeoutError("interrupted")
        return "1.0,2.0"


def test_explicit_socketio_selection_does_not_fall_back():
    calls = []

    class Constructor:
        def __new__(cls, *args):
            calls.append(args)
            return object()

    result = _open_rsinstrument_session(
        Constructor,
        "TCPIP::192.0.2.40::INSTR",
        select_visa="socketio",
    )

    assert result is not None
    assert calls == [
        (
            "TCPIP::192.0.2.40::INSTR",
            True,
            False,
            "SelectVisa=socketio,AddTermCharToWriteBinBlock=True,DataChunkSize=512",
        )
    ]


def test_explicit_visa_selection_does_not_enable_socket_binary_options():
    calls = []

    class Constructor:
        def __new__(cls, *args):
            calls.append(args)
            return object()

    _open_rsinstrument_session(
        Constructor,
        "TCPIP::192.0.2.40::INSTR",
        select_visa="rs",
    )

    assert calls == [
        (
            "TCPIP::192.0.2.40::INSTR",
            True,
            False,
            "SelectVisa=rs",
        )
    ]


def test_float_list_uses_call_timeout_and_restores_event_state():
    session = FakeSession()
    previous_events = []
    session.events.on_read_handler = previous_events.append
    logger = CommandLogger()
    transport = RsInstrumentTransport("TCPIP::x::INSTR", session, logger)

    assert transport.query_float_list("DATA?", timeout_ms=300_000) == [1.0, 2.0]

    assert session.visa_timeout == 1000
    assert session.events.io_events_include_data is True
    assert session.events.on_read_handler == previous_events.append
    assert len(previous_events) == 1
    telemetry = [entry.text for entry in logger.entries if entry.direction == "telemetry"]
    assert any("operation=query_float_list_progress" in item for item in telemetry)
    assert any(
        "operation=query_float_list" in item
        and "status=ok" in item
        and "bytes=16" in item
        and "items=2" in item
        and "throughput_mib_s=" in item
        for item in telemetry
    )
    assert all("192.0.2" not in item for item in telemetry)


def test_float_list_failure_is_nonreplayable_and_restores_timeout():
    session = FakeSession()
    session.fail_float = True
    logger = CommandLogger()
    transport = RsInstrumentTransport("TCPIP::x::INSTR", session, logger)

    with pytest.raises(TransportIOError) as raised:
        transport.query_float_list("DATA?", timeout_ms=300_000)

    assert raised.value.attempts == 1
    assert session.visa_timeout == 1000
    assert any(
        entry.direction == "telemetry"
        and "operation=query_float_list" in entry.text
        and "status=failed" in entry.text
        and "replay=no_replay" in entry.text
        for entry in logger.entries
    )


def test_binary_query_records_sanitized_size_telemetry():
    session = FakeSession()
    logger = CommandLogger()
    transport = RsInstrumentTransport("TCPIP::x::INSTR", session, logger)

    assert transport.query_bin_block("SYST:SET?") == b"abc"

    assert any(
        entry.direction == "telemetry"
        and "operation=query_binary" in entry.text
        and "bytes=3" in entry.text
        for entry in logger.entries
    )


@pytest.mark.parametrize("fail", [False, True])
def test_pyvisa_float_query_restores_timeout_when_previous_timeout_is_none(fail):
    from wavebench.transport.pyvisa_transport import PyVisaTransport

    session = FakePyVisaSession()
    session.timeout = None
    session.fail = fail
    transport = PyVisaTransport("TCPIP::x::INSTR", object(), session, CommandLogger())

    if fail:
        with pytest.raises(TransportIOError):
            transport.query_float_list("DATA?", timeout_ms=300_000)
    else:
        assert transport.query_float_list("DATA?", timeout_ms=300_000) == [1.0, 2.0]

    assert session.timeout is None


def test_rsinstrument_continuation_rejects_before_send():
    session = FakeSession()
    transport = RsInstrumentTransport("TCPIP::x::INSTR", session, CommandLogger())

    with pytest.raises(TransportIOError) as raised:
        transport.query_bin_block(
            "DATA?",
            replay=ReplayPolicy.READ_CONTINUATION_ONLY,
        )

    assert raised.value.attempts == 0
    assert raised.value.command_transmission.value == "not_sent"
