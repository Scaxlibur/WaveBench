from __future__ import annotations

import pytest

from wavebench.errors import AccessDeniedError, InstrumentError, SessionHealthError, TransportIOError
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)
from wavebench.transport.session import (
    InstrumentSessionState,
    SessionHealth,
    SessionPurpose,
    SessionTransactionCoordinator,
)


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

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        self.queries.append(command)
        return "ok"

    def query_float_list(
        self,
        command: str,
        *,
        timeout_ms: int | None = None,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> list[float]:
        self.queries.append(command)
        return [1.0, 2.0]

    def query_bin_block(
        self,
        command: str,
        *,
        replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
    ) -> bytes:
        self.queries.append(command)
        return b"data"

    def query_opc(self, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
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
        "write_attempts": 1,
        "write_transmitted": 1,
        "write_completed": 1,
        "write_outcome_unknown": 0,
        "binary_write_requests": 1,
        "binary_write_attempts": 1,
        "binary_write_transmitted": 1,
        "binary_write_completed": 1,
        "binary_write_outcome_unknown": 0,
        "blocked_write_requests": 0,
        "blocked_binary_write_requests": 0,
        "blocked_session_io": 0,
        "session_health_transitions": 0,
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


def test_failed_write_is_unknown_and_poisoned_without_claiming_transmission() -> None:
    inner = FakeTransport()
    inner.fail_write = True
    transport = GuardedAuditedTransport(inner)

    with pytest.raises(InstrumentError):
        transport.write("MAYBE_SENT")
    counters = transport.audit_snapshot()["counters"]
    assert counters["write_requests"] == 1
    assert counters["write_attempts"] == 1
    assert counters["write_transmitted"] == 0
    assert counters["write_completed"] == 0
    assert counters["write_outcome_unknown"] == 1
    assert transport.session_state.health is SessionHealth.POISONED
    with pytest.raises(SessionHealthError):
        transport.query("AFTER_FAILURE?")
    assert inner.queries == []


def test_close_is_idempotent() -> None:
    inner = FakeTransport()
    transport = GuardedAuditedTransport(inner)
    transport.close()
    transport.close()
    assert inner.closed == 1
    assert transport.session_state.health is SessionHealth.CLOSED
    with pytest.raises(SessionHealthError):
        transport.query("AFTER_CLOSE?")


def test_close_releases_lease_once_even_when_backend_close_fails() -> None:
    class Lease:
        releases = 0

        def release(self) -> None:
            self.releases += 1

    class FailingClose(FakeTransport):
        def close(self) -> None:
            self.closed += 1
            raise RuntimeError("backend close failed")

    lease = Lease()
    transport = GuardedAuditedTransport(
        FailingClose(),
        lease=lease,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="backend close failed"):
        transport.close()
    assert transport.session_state.health is SessionHealth.CLOSED
    assert lease.releases == 1
    transport.close()
    assert lease.releases == 1


def test_uncertain_session_allows_only_active_bounded_authorization() -> None:
    inner = FakeTransport()
    state = InstrumentSessionState()
    transport = GuardedAuditedTransport(inner, session_state=state)
    state.degrade(SessionHealth.UNCERTAIN, reason="unknown_write")

    with pytest.raises(SessionHealthError):
        transport.query("NORMAL?")
    assert inner.queries == []

    coordinator = SessionTransactionCoordinator(state)
    with coordinator.authorize(
        operation_id="restore-1",
        purpose=SessionPurpose.RECOVERY,
        allowed_io={"query"},
        fields={"scope.timebase"},
        timeout_ms=1000,
        max_steps=1,
        evidence_fields={"query": {"scope.timebase"}},
    ):
        assert transport.query("TIMEBASE?") == "ok"
    assert inner.queries == ["TIMEBASE?"]
    assert state.health is SessionHealth.UNCERTAIN


def test_verification_authorization_requires_distinct_field_evidence() -> None:
    inner = FakeTransport()
    state = InstrumentSessionState()
    transport = GuardedAuditedTransport(inner, session_state=state)
    state.degrade(SessionHealth.UNCERTAIN, reason="unknown_write")
    coordinator = SessionTransactionCoordinator(state)

    with coordinator.authorize(
        operation_id="verify-1",
        purpose=SessionPurpose.VERIFICATION,
        allowed_io={"query"},
        fields={"scope.timebase", "scope.vertical"},
        timeout_ms=1000,
        max_steps=3,
        evidence_fields={
            "query": {"scope.timebase"},
        },
    ) as authorization:
        transport.query("TIMEBASE?")
        transport.query("TIMEBASE?")
        with pytest.raises(ValueError, match="did not cover fields"):
            coordinator.complete_verification(authorization)

    assert state.health is SessionHealth.UNCERTAIN


def test_verification_success_restores_health_only_for_mapped_evidence() -> None:
    inner = FakeTransport()
    state = InstrumentSessionState()
    transport = GuardedAuditedTransport(inner, session_state=state)
    state.degrade(SessionHealth.UNCERTAIN, reason="unknown_write")
    coordinator = SessionTransactionCoordinator(state)

    with coordinator.authorize(
        operation_id="verify-2",
        purpose=SessionPurpose.VERIFICATION,
        allowed_io={"query"},
        fields={"scope.timebase", "scope.vertical"},
        timeout_ms=1000,
        max_steps=2,
        evidence_fields={
            "query": {"scope.timebase", "scope.vertical"},
        },
    ) as authorization:
        transport.query("TIMEBASE?")
        coordinator.record_evidence(
            authorization,
            "query",
            {"scope.timebase", "scope.vertical"},
        )
        coordinator.complete_verification(authorization)

    assert state.health is SessionHealth.HEALTHY
    assert state.verified_fields == {"scope.timebase", "scope.vertical"}


def test_preflight_not_sent_failure_does_not_poison_session() -> None:
    class UnsupportedContinuation(FakeTransport):
        def query(
            self,
            command: str,
            *,
            replay: ReplayPolicy = ReplayPolicy.NO_REPLAY,
        ) -> str:
            raise TransportIOError(
                "continuation unsupported",
                operation="query",
                phase=TransportPhase.BEFORE_SEND,
                replay_policy=replay,
                command_transmission=CommandTransmission.NOT_SENT,
                response_progress=ResponseProgress.NONE,
                synchronization=Synchronization.PROVEN,
                attempts=0,
            )

    inner = UnsupportedContinuation()
    transport = GuardedAuditedTransport(inner)

    with pytest.raises(TransportIOError):
        transport.query("DATA?", replay=ReplayPolicy.READ_CONTINUATION_ONLY)

    assert transport.session_state.health is SessionHealth.HEALTHY
    assert transport.audit_snapshot()["counters"]["session_health_transitions"] == 0
