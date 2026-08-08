from __future__ import annotations

import pytest

from wavebench.errors import AccessDeniedError, InstrumentError
from wavebench.transport.guarded import GuardedAuditedTransport


class FakeTransport:
    resource = "TCPIP::private::INSTR"

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.binary_writes: list[bytes] = []
        self.queries: list[str] = []
        self.closed = 0
        self.fail_write = False

    def record_event(self, direction: str, text: str) -> None:
        pass

    def write(self, command: str) -> None:
        if self.fail_write:
            raise InstrumentError("write failed")
        self.writes.append(command)

    def write_bytes(self, command: bytes) -> None:
        self.binary_writes.append(command)

    def query(self, command: str) -> str:
        self.queries.append(command)
        return "ok"

    def query_float_list(self, command: str, *, timeout_ms: int | None = None) -> list[float]:
        self.queries.append(command)
        return [1.0, 2.0]

    def query_bin_block(self, command: str) -> bytes:
        self.queries.append(command)
        return b"data"

    def query_opc(self) -> str:
        self.queries.append("*OPC?")
        return "1"

    def close(self) -> None:
        self.closed += 1


def test_read_write_delegates_and_counts_without_leaking_command_or_resource() -> None:
    inner = FakeTransport()
    transport = GuardedAuditedTransport(inner)

    transport.write("SECRET:VALUE 1")
    transport.write_bytes(b"payload")
    assert transport.query("*IDN?") == "ok"
    assert transport.query_float_list("DATA?", timeout_ms=10) == [1.0, 2.0]
    assert transport.query_bin_block("WAVE?") == b"data"
    assert transport.query_opc() == "1"

    audit = transport.audit_snapshot()
    assert audit["schema"] == "wavebench.instrument_io.v1"
    assert audit["access"] == "read_write"
    assert audit["counters"] == {
        "query_calls": 3,
        "binary_query_calls": 1,
        "blocked_query_calls": 0,
        "blocked_binary_query_calls": 0,
        "write_requests": 1,
        "write_transmitted": 1,
        "write_completed": 1,
        "binary_write_requests": 1,
        "binary_write_transmitted": 1,
        "binary_write_completed": 1,
        "blocked_write_requests": 0,
        "blocked_binary_write_requests": 0,
        "instrument_mutation_writes": 2,
        "instrument_mutation_writes_completed": 2,
    }
    assert "SECRET" not in repr(audit)
    assert "private" not in repr(audit)


@pytest.mark.parametrize("mode", ["read_only", "disabled"])
def test_restricted_modes_block_writes_before_inner_transport(mode: str) -> None:
    inner = FakeTransport()
    transport = GuardedAuditedTransport(inner, access=mode)  # type: ignore[arg-type]

    with pytest.raises(AccessDeniedError, match=mode):
        transport.write("WRITE")
    with pytest.raises(AccessDeniedError, match=mode):
        transport.write_bytes(b"WRITE")
    assert inner.writes == []
    assert inner.binary_writes == []
    counters = transport.audit_snapshot()["counters"]
    assert counters["write_requests"] == 1
    assert counters["binary_write_requests"] == 1
    assert counters["write_transmitted"] == 0
    assert counters["binary_write_transmitted"] == 0
    assert counters["blocked_write_requests"] == 1
    assert counters["blocked_binary_write_requests"] == 1


def test_disabled_mode_blocks_queries_and_read_only_query_is_counted() -> None:
    disabled_inner = FakeTransport()
    disabled = GuardedAuditedTransport(disabled_inner, access="disabled")
    with pytest.raises(AccessDeniedError):
        disabled.query("*IDN?")
    with pytest.raises(AccessDeniedError):
        disabled.query_bin_block("DATA?")
    assert disabled_inner.queries == []
    disabled_counters = disabled.audit_snapshot()["counters"]
    assert disabled_counters["blocked_query_calls"] == 1
    assert disabled_counters["blocked_binary_query_calls"] == 1

    readonly_inner = FakeTransport()
    readonly = GuardedAuditedTransport(readonly_inner, access="read_only")
    assert readonly.query("*IDN?") == "ok"
    assert readonly.audit_snapshot()["counters"]["query_calls"] == 1


def test_failed_write_is_counted_as_transmitted_but_not_completed() -> None:
    inner = FakeTransport()
    inner.fail_write = True
    transport = GuardedAuditedTransport(inner)

    with pytest.raises(InstrumentError):
        transport.write("MAYBE_SENT")
    counters = transport.audit_snapshot()["counters"]
    assert counters["write_requests"] == 1
    assert counters["write_transmitted"] == 1
    assert counters["write_completed"] == 0


def test_close_is_idempotent() -> None:
    inner = FakeTransport()
    transport = GuardedAuditedTransport(inner)
    transport.close()
    transport.close()
    assert inner.closed == 1
